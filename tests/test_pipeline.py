"""End-to-end runs against the in-memory store and a fake Telegram client."""

from __future__ import annotations

import dataclasses

from app.collection.mock import MockSource
from app.config import AppConfig
from app.database.store import TELEGRAM_OFFSET_KEY, InMemoryStore
from app.models.listing import Condition
from app.notifications.telegram import TelegramUpdate
from app.pipeline import Pipeline


class FakeTelegram:
    """Records what would have been sent; can replay incoming commands."""

    def __init__(self, updates: list[TelegramUpdate] | None = None) -> None:
        self.updates = updates or []
        self.sent: list[str] = []
        self.enabled = True

    def send_message(self, text: str, chat_id: str | None = None) -> bool:
        self.sent.append(text)
        return True

    def get_updates(self, offset):
        return [u for u in self.updates if offset is None or u.update_id >= offset]

    def close(self) -> None:
        pass


def build(updates=None, **config_overrides):
    config = AppConfig()
    if config_overrides:
        config = dataclasses.replace(config, **config_overrides)
    store = InMemoryStore()
    telegram = FakeTelegram(updates)
    return config, store, telegram, Pipeline(config, store, MockSource(), telegram)


def test_a_full_run_collects_scores_and_alerts_on_the_exceptional_deal():
    _, store, telegram, pipeline = build()
    summary = pipeline.run()

    assert summary.collected > 0
    # The "Bon etat" and "Satisfaisant" listings never reach pricing.
    assert summary.rejected_by_condition == 2
    assert all(
        record["listing"].condition
        in (Condition.NEW_WITH_TAGS, Condition.NEW_WITHOUT_TAGS, Condition.VERY_GOOD)
        for record in store.listings.values()
    )
    assert summary.notified == 1
    message = telegram.sent[0]
    assert "PADEL OPPORTUNITY" in message
    assert "Bullpadel Vertex 04 Comfort" in message
    assert "Buy price: EUR 82" in message
    assert "Not on watchlist - exceptional deal" in message
    assert "estimate" in message


def test_alerts_state_the_bucket_and_stay_explainable():
    _, _, telegram, pipeline = build()
    pipeline.run()
    message = telegram.sent[0]
    assert "Matched:" in message
    assert "Score breakdown:" in message
    assert "confidence multiplier" in message
    assert "https://www.vinted.fr/items/1100" in message


def test_a_thin_price_history_produces_no_alert():
    _, store, telegram, pipeline = build()
    pipeline.run()
    # The Nox listings only have three comparable observations between them.
    nox = [
        opportunity
        for _, opportunity in store.opportunities
        if opportunity.listing.model_key.startswith("nox")
    ]
    assert nox
    assert all(opportunity.score < 60 for opportunity in nox)


def test_the_second_run_does_not_re_alert_the_same_listing():
    _, store, telegram, pipeline = build()
    pipeline.run()
    assert len(telegram.sent) == 1

    second = Pipeline(pipeline.config, store, MockSource(), telegram)
    summary = second.run()
    assert summary.notified == 0
    assert summary.new_listings == 0
    assert len(telegram.sent) == 1


def test_unchanged_listings_do_not_re_record_their_own_price():
    _, store, _, pipeline = build()
    pipeline.run()
    first_count = len(store.observations)
    Pipeline(pipeline.config, store, MockSource(), pipeline.telegram).run()
    assert len(store.observations) == first_count


def test_dry_run_sends_nothing_and_persists_nothing():
    config = dataclasses.replace(AppConfig(), dry_run=True)
    store = InMemoryStore()
    telegram = FakeTelegram()
    from app.notifications.telegram import TelegramClient

    real_client = TelegramClient(config.telegram, dry_run=True)
    summary = Pipeline(config, store, MockSource(), real_client).run()
    assert summary.evaluated > 0
    assert telegram.sent == []


def test_watch_command_lowers_the_bar_for_the_next_run():
    updates = [TelegramUpdate(update_id=10, chat_id="1", text="/watch Nox AT10 max 200")]
    config, store, telegram, pipeline = build(updates=updates)
    pipeline.run()

    assert store.watchlist()[0].query_text == "Nox AT10"
    assert store.watchlist()[0].max_price_eur == 200.0
    assert store.get_state(TELEGRAM_OFFSET_KEY) == "11"
    # The confirmation explains that the max price is a ceiling, not a rule.
    assert "ceiling" in telegram.sent[0]


def test_watchlist_entries_are_also_queried_in_targeted_mode():
    updates = [TelegramUpdate(update_id=1, chat_id="1", text="/watch Nox AT10")]
    _, _, telegram, pipeline = build(updates=updates)
    summary = pipeline.run()
    assert summary.collected > 0
    assert summary.commands_processed == 1


def test_commands_are_not_reprocessed_across_runs():
    updates = [TelegramUpdate(update_id=10, chat_id="1", text="/watch Nox AT10")]
    _, store, telegram, pipeline = build(updates=updates)
    pipeline.run()
    sent_after_first = len(telegram.sent)
    Pipeline(pipeline.config, store, MockSource(), telegram).run()
    assert len(telegram.sent) == sent_after_first


def test_unknown_command_gets_a_helpful_reply():
    updates = [TelegramUpdate(update_id=1, chat_id="1", text="/nope")]
    _, _, telegram, pipeline = build(updates=updates)
    pipeline.run()
    assert "/help" in telegram.sent[0]


def test_cold_start_run_is_quiet_and_says_why():
    """An empty price database must produce no alerts at all."""
    from app.collection.mock import MockSource as Source

    payload = {"items": Source().payload["items"][:1]}
    config = AppConfig()
    store = InMemoryStore()
    telegram = FakeTelegram()
    summary = Pipeline(config, store, Source(payload=payload), telegram).run()
    assert summary.evaluated == 1
    assert summary.without_estimate == 1
    assert summary.notified == 0
    assert telegram.sent == []
