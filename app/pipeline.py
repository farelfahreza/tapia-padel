"""One scheduled run, start to finish.

Read Telegram commands -> collect -> filter -> store -> observe prices ->
estimate -> score -> decide -> notify -> exit. Stateless between runs: every
piece of state that matters lives in Postgres, nothing is written to local
disk, and the process is expected to be killed as soon as it returns.

Business logic lives in the modules this orchestrates; this file wires them
together and counts what happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.collection.base import (
    ListingSource,
    SourceAuthError,
    SourceError,
    SourceRateLimited,
    filter_accepted_conditions,
)
from app.config import AppConfig
from app.database.store import TELEGRAM_OFFSET_KEY, Store
from app.models.listing import Listing, PriceObservation
from app.models.opportunity import Opportunity
from app.models.watchlist import WatchlistEntry
from app.notifications.commands import (
    HELP_TEXT,
    CommandName,
    format_watchlist,
    parse_command,
)
from app.notifications.formatter import format_opportunity
from app.notifications.policy import decide
from app.notifications.telegram import TelegramClient
from app.notifications.watchlist_match import find_match
from app.pricing.estimator import ResalePriceEstimator
from app.pricing.fees import compute_economics
from app.scoring.scorer import OpportunityScorer
from app.utils.normalize import normalized_query

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    commands_processed: int = 0
    collected: int = 0
    rejected_by_condition: int = 0
    new_listings: int = 0
    price_changes: int = 0
    observations_recorded: int = 0
    evaluated: int = 0
    without_estimate: int = 0
    notified: int = 0
    suppressed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "commands_processed": self.commands_processed,
            "collected": self.collected,
            "rejected_by_condition": self.rejected_by_condition,
            "new_listings": self.new_listings,
            "price_changes": self.price_changes,
            "observations_recorded": self.observations_recorded,
            "evaluated": self.evaluated,
            "without_estimate": self.without_estimate,
            "notified": self.notified,
            "suppressed": self.suppressed,
            "errors": self.errors,
        }


class Pipeline:
    def __init__(
        self,
        config: AppConfig,
        store: Store,
        source: ListingSource,
        telegram: TelegramClient,
    ) -> None:
        self.config = config
        self.store = store
        self.source = source
        self.telegram = telegram
        self.estimator = ResalePriceEstimator(config.pricing)
        self.scorer = OpportunityScorer(config.scoring)
        self.summary = RunSummary()

    # -- entry point --------------------------------------------------------

    def run(self) -> RunSummary:
        if self.config.dry_run:
            logger.info("dry run: nothing will be sent or persisted")

        if self.config.telegram.commands_enabled:
            self._process_commands()

        watchlist = self.store.watchlist()
        logger.info("watchlist loaded", extra={"entries": len(watchlist)})

        listings = self._collect(watchlist)
        stored = self._persist(listings)
        opportunities = self._evaluate(stored)
        self._notify(opportunities, watchlist)

        if self.config.dry_run:
            self.store.rollback()
        else:
            self.store.commit()

        logger.info("run finished", extra=self.summary.as_dict())
        return self.summary

    # -- steps --------------------------------------------------------------

    def _process_commands(self) -> None:
        """Read /watch, /unwatch, /list, /help sent since the last run."""
        raw_offset = self.store.get_state(TELEGRAM_OFFSET_KEY)
        offset = int(raw_offset) if raw_offset else None
        updates = self.telegram.get_updates(offset)
        if not updates:
            return

        for update in updates:
            command = parse_command(update.text)
            reply = self._handle_command(command)
            self.telegram.send_message(reply, chat_id=update.chat_id)
            self.summary.commands_processed += 1
            logger.info(
                "command processed",
                extra={"command": command.name.value, "ok": command.ok},
            )

        self.store.set_state(TELEGRAM_OFFSET_KEY, str(max(u.update_id for u in updates) + 1))

    def _handle_command(self, command) -> str:
        if command.error:
            return command.error
        if command.name is CommandName.HELP:
            return HELP_TEXT
        if command.name is CommandName.LIST:
            return format_watchlist(self.store.watchlist())
        if command.name is CommandName.WATCH:
            created = self.store.add_watchlist_entry(
                command.argument,
                normalized_query(command.argument),
                command.max_price_eur,
            )
            budget = (
                f" with a max buy price of EUR {command.max_price_eur:.0f}"
                if command.max_price_eur is not None
                else ""
            )
            verb = "Watching" if created else "Updated"
            return (
                f"{verb} '{command.argument}'{budget}. "
                "It still has to clear the deal scoring to trigger an alert; "
                "the max price only adds a ceiling on top. Takes effect on the next run."
            )
        if command.name is CommandName.UNWATCH:
            removed = self.store.remove_watchlist_entry(normalized_query(command.argument))
            return (
                f"Stopped watching '{command.argument}'."
                if removed
                else f"'{command.argument}' was not on your watchlist."
            )
        return HELP_TEXT

    def _collect(self, watchlist: list[WatchlistEntry]) -> list[Listing]:
        """Browse mode plus one targeted query per watchlist entry."""
        collected: list[Listing] = []
        try:
            collected.extend(self.source.browse())
            for entry in watchlist:
                collected.extend(self.source.search(entry.query_text))
        except SourceAuthError as exc:
            # Expired or rejected session cookie. Stop; never retry harder.
            self.summary.errors.append(f"auth: {exc}")
            logger.error("collection stopped: source rejected our session", extra={"error": str(exc)})
        except SourceRateLimited as exc:
            # The source asked us to slow down. We stop for this run.
            self.summary.errors.append(f"rate limited: {exc}")
            logger.warning(
                "collection stopped: source rate limited us",
                extra={"error": str(exc), "retry_after": exc.retry_after_seconds},
            )
        except SourceError as exc:
            self.summary.errors.append(f"source: {exc}")
            logger.error("collection failed", extra={"error": str(exc)})

        kept, rejected = filter_accepted_conditions(
            collected, self.config.collection.accepted_conditions
        )
        # Two runs can return the same listing (browse + a targeted query).
        deduped: dict[str, Listing] = {}
        for listing in kept:
            deduped[listing.required_dedup_key] = listing

        self.summary.collected = len(deduped)
        self.summary.rejected_by_condition = rejected
        logger.info(
            "collection complete",
            extra={
                "raw": len(collected),
                "after_condition_filter": len(kept),
                "unique": len(deduped),
                "rejected_by_condition": rejected,
                "accepted_conditions": [
                    c.value for c in self.config.collection.accepted_conditions
                ],
            },
        )
        return list(deduped.values())

    def _persist(self, listings: list[Listing]) -> list[tuple[int, Listing]]:
        """Store listings, dedup them, and record price observations."""
        stored: list[tuple[int, Listing]] = []
        now = datetime.now(timezone.utc)
        for listing in listings:
            record = self.store.upsert_listing(listing)
            if record.is_new:
                self.summary.new_listings += 1
            price_moved = (
                record.previous_price_eur is not None
                and abs(record.previous_price_eur - listing.price_eur) >= 0.01
            )
            if price_moved:
                self.summary.price_changes += 1
            # One observation per listing per price: seeing the same unchanged
            # listing every hour must not make its own price look like the
            # market's consensus.
            if (record.is_new or price_moved) and listing.condition is not None:
                self.store.record_observation(
                    PriceObservation(
                        model_key=listing.required_model_key,
                        condition=listing.condition,
                        price_eur=listing.price_eur,
                        observed_at=now,
                        listing_dedup_key=listing.required_dedup_key,
                        source=listing.source,
                    )
                )
                self.summary.observations_recorded += 1
            stored.append((record.id, listing))
        return stored

    def _evaluate(self, stored: list[tuple[int, Listing]]) -> list[tuple[int, Opportunity]]:
        if not stored:
            return []
        model_keys = sorted({listing.required_model_key for _, listing in stored})
        since = datetime.now(timezone.utc) - timedelta(
            days=self.config.pricing.observation_window_days
        )
        observations = self.store.observations_for_models(model_keys, since)
        stats = self.store.model_stats(
            model_keys,
            demand_window_days=self.config.scoring.demand_window_days,
            stale_days=self.config.scoring.turnover_stale_days,
        )

        results: list[tuple[int, Opportunity]] = []
        for listing_id, listing in stored:
            model_key = listing.required_model_key
            estimate = self.estimator.estimate(
                model_key,
                listing.condition,
                observations.get(model_key, []),
                # A listing is never part of its own reference price.
                exclude_dedup_key=listing.required_dedup_key,
            )
            economics = compute_economics(
                listing.price_eur,
                estimate.estimated_resale_eur,
                self.config.fees,
                # Shipping I pay depends on where the racket ships from.
                location=listing.location,
            )
            opportunity = self.scorer.score(
                listing, estimate, economics, stats.get(model_key)
            )
            self.store.save_opportunity(listing_id, opportunity)
            self.summary.evaluated += 1
            if not estimate.usable:
                self.summary.without_estimate += 1
            results.append((listing_id, opportunity))

        if self.summary.without_estimate:
            logger.info(
                "listings without a usable resale estimate - this is expected while "
                "the price database is still filling up, not a failure",
                extra={
                    "without_estimate": self.summary.without_estimate,
                    "evaluated": self.summary.evaluated,
                    "min_observations": self.config.pricing.min_observations,
                },
            )
        return results

    def _notify(
        self,
        opportunities: list[tuple[int, Opportunity]],
        watchlist: list[WatchlistEntry],
    ) -> None:
        """Rank, apply the two-threshold policy, send at most N per run."""
        if not opportunities:
            return
        ranked = sorted(opportunities, key=lambda pair: pair[1].score, reverse=True)
        last_prices = self.store.last_notified_prices([pair[0] for pair in ranked])

        sent = 0
        for listing_id, opportunity in ranked:
            if sent >= self.config.notifications.max_notifications_per_run:
                logger.info(
                    "notification cap reached for this run",
                    extra={"cap": self.config.notifications.max_notifications_per_run},
                )
                break

            match = find_match(opportunity.listing, watchlist)
            decision = decide(
                opportunity,
                match,
                self.config.notifications,
                already_notified_price_eur=last_prices.get(listing_id),
            )
            if not decision.should_notify:
                self.summary.suppressed += 1
                logger.debug(
                    "not notifying",
                    extra={
                        "url": opportunity.listing.url,
                        "score": opportunity.score,
                        "reasons": list(decision.reasons),
                    },
                )
                continue

            message = format_opportunity(opportunity, decision)
            if self.telegram.send_message(message):
                assert decision.bucket is not None
                self.store.record_notification(
                    listing_id=listing_id,
                    bucket=decision.bucket.value,
                    matched_query=match.entry.query_text if match else None,
                    score=opportunity.score,
                    price_eur=opportunity.listing.price_eur,
                    message=message,
                )
                sent += 1
                self.summary.notified += 1
                logger.info(
                    "opportunity notified",
                    extra={
                        "url": opportunity.listing.url,
                        "score": opportunity.score,
                        "bucket": decision.bucket.value,
                        "estimated_resale_eur": opportunity.estimate.estimated_resale_eur,
                        "observations": opportunity.estimate.observations,
                    },
                )
