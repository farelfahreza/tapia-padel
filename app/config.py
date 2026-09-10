"""Single place where every tunable lives.

Everything the business logic can be steered with is loaded from the
environment here and passed down explicitly. No module reads ``os.environ``
on its own, so there is exactly one file to look at when a number needs
changing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence

from app.models.listing import Condition

DEFAULT_ACCEPTED_CONDITIONS: tuple[Condition, ...] = (
    Condition.NEW_WITH_TAGS,
    Condition.NEW_WITHOUT_TAGS,
    Condition.VERY_GOOD,
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str, default: Sequence[str] = ()) -> list[str]:
    raw = _env(name)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class FeeConfig:
    """Buy side and sell side modelled separately, on purpose.

    Buying and reselling happen on the same platform, so it is easy to either
    double-count a fee or forget one entirely. Keeping the two sides apart
    makes it obvious which cost belongs where.
    """

    # What I pay on top of the listing price when buying.
    buyer_protection_pct: float = 0.05
    buyer_protection_fixed_eur: float = 0.70
    buy_shipping_eur: float = 4.50
    packaging_cost_eur: float = 1.00
    # What Vinted takes out of the resale before it reaches me.
    seller_fee_pct: float = 0.0
    seller_fee_fixed_eur: float = 0.0
    sell_shipping_cost_eur: float = 0.0

    @staticmethod
    def from_env() -> "FeeConfig":
        return FeeConfig(
            buyer_protection_pct=_env_float("BUYER_PROTECTION_PCT", 0.05),
            buyer_protection_fixed_eur=_env_float("BUYER_PROTECTION_FIXED_EUR", 0.70),
            buy_shipping_eur=_env_float("BUY_SHIPPING_EUR", 4.50),
            packaging_cost_eur=_env_float("PACKAGING_COST_EUR", 1.00),
            seller_fee_pct=_env_float("SELLER_FEE_PCT", 0.0),
            seller_fee_fixed_eur=_env_float("SELLER_FEE_FIXED_EUR", 0.0),
            sell_shipping_cost_eur=_env_float("SELL_SHIPPING_COST_EUR", 0.0),
        )


@dataclass(frozen=True)
class PricingConfig:
    # Observed listing prices are asking prices, never confirmed sales.
    realization_factor: float = 0.92
    observation_window_days: int = 180
    min_observations: int = 3
    target_observations: int = 12
    medium_confidence_observations: int = 5
    high_confidence_observations: int = 10

    @staticmethod
    def from_env() -> "PricingConfig":
        return PricingConfig(
            realization_factor=_env_float("RESALE_REALIZATION_FACTOR", 0.92),
            observation_window_days=_env_int("OBSERVATION_WINDOW_DAYS", 180),
            min_observations=_env_int("MIN_OBSERVATIONS", 3),
            target_observations=_env_int("TARGET_OBSERVATIONS", 12),
            medium_confidence_observations=_env_int("MEDIUM_CONFIDENCE_OBSERVATIONS", 5),
            high_confidence_observations=_env_int("HIGH_CONFIDENCE_OBSERVATIONS", 10),
        )


@dataclass(frozen=True)
class ScoringConfig:
    weight_price_advantage: float = 35.0
    weight_profit: float = 25.0
    weight_condition: float = 10.0
    weight_demand: float = 10.0
    weight_liquidity: float = 10.0
    weight_location: float = 10.0

    target_discount: float = 0.35
    target_roi: float = 0.40
    target_profit_eur: float = 40.0
    demand_target_listings: int = 8
    liquidity_target_signals: int = 5
    #: Window used to count how often a model shows up (demand proxy).
    demand_window_days: int = 90
    #: A listing not seen for this long, after being seen more than once, is
    #: counted as a turnover signal (sold or delisted - a proxy, not a fact).
    turnover_stale_days: int = 14
    preferred_locations: tuple[str, ...] = ("paris", "ile-de-france")

    min_profit_eur: float = 15.0
    min_roi: float = 0.20
    cold_start_score_cap: float = 15.0
    unprofitable_score_cap: float = 40.0

    @property
    def total_weight(self) -> float:
        return (
            self.weight_price_advantage
            + self.weight_profit
            + self.weight_condition
            + self.weight_demand
            + self.weight_liquidity
            + self.weight_location
        )

    @staticmethod
    def from_env() -> "ScoringConfig":
        return ScoringConfig(
            weight_price_advantage=_env_float("WEIGHT_PRICE_ADVANTAGE", 35.0),
            weight_profit=_env_float("WEIGHT_PROFIT", 25.0),
            weight_condition=_env_float("WEIGHT_CONDITION", 10.0),
            weight_demand=_env_float("WEIGHT_DEMAND", 10.0),
            weight_liquidity=_env_float("WEIGHT_LIQUIDITY", 10.0),
            weight_location=_env_float("WEIGHT_LOCATION", 10.0),
            target_discount=_env_float("TARGET_DISCOUNT", 0.35),
            target_roi=_env_float("TARGET_ROI", 0.40),
            target_profit_eur=_env_float("TARGET_PROFIT_EUR", 40.0),
            demand_target_listings=_env_int("DEMAND_TARGET_LISTINGS", 8),
            liquidity_target_signals=_env_int("LIQUIDITY_TARGET_SIGNALS", 5),
            demand_window_days=_env_int("DEMAND_WINDOW_DAYS", 90),
            turnover_stale_days=_env_int("TURNOVER_STALE_DAYS", 14),
            preferred_locations=tuple(
                loc.lower()
                for loc in _env_list(
                    "PREFERRED_LOCATIONS", ("paris", "ile-de-france")
                )
            ),
            min_profit_eur=_env_float("MIN_PROFIT_EUR", 15.0),
            min_roi=_env_float("MIN_ROI", 0.20),
            cold_start_score_cap=_env_float("COLD_START_SCORE_CAP", 15.0),
            unprofitable_score_cap=_env_float("UNPROFITABLE_SCORE_CAP", 40.0),
        )


@dataclass(frozen=True)
class NotificationConfig:
    """The two-threshold ("soft") policy lives here.

    A watchlisted racket earns a ping at a lower score; anything else has to
    be an exceptional deal to be worth interrupting me for.
    """

    watchlist_score_threshold: float = 60.0
    exceptional_score_threshold: float = 80.0
    min_observations_to_notify: int = 5
    renotify_price_drop_pct: float = 0.10
    max_notifications_per_run: int = 10

    @staticmethod
    def from_env() -> "NotificationConfig":
        return NotificationConfig(
            watchlist_score_threshold=_env_float("WATCHLIST_SCORE_THRESHOLD", 60.0),
            exceptional_score_threshold=_env_float("EXCEPTIONAL_SCORE_THRESHOLD", 80.0),
            min_observations_to_notify=_env_int("MIN_OBSERVATIONS_TO_NOTIFY", 5),
            renotify_price_drop_pct=_env_float("RENOTIFY_PRICE_DROP_PCT", 0.10),
            max_notifications_per_run=_env_int("MAX_NOTIFICATIONS_PER_RUN", 10),
        )


@dataclass(frozen=True)
class CollectionConfig:
    base_url: str = "https://www.vinted.fr"
    catalog_id: int = 4597
    session_cookie: str = ""
    user_agent: str = "tapia-padel-deal-finder/0.1 (+mailto:unset@example.com)"
    requests_per_minute: float = 10.0
    per_page: int = 96
    browse_max_pages: int = 3
    search_max_pages: int = 1
    timeout_seconds: float = 20.0
    backoff_seconds: float = 60.0
    # Best-effort Vinted status_id values for the accepted conditions. Used to
    # filter at query level so excluded listings are not fetched at all; the
    # post-fetch filter still runs, so a stale id here is harmless.
    status_ids: tuple[int, ...] = (6, 1, 2)
    accepted_conditions: tuple[Condition, ...] = DEFAULT_ACCEPTED_CONDITIONS

    @staticmethod
    def from_env() -> "CollectionConfig":
        raw_conditions = _env_list(
            "ACCEPTED_CONDITIONS", [c.value for c in DEFAULT_ACCEPTED_CONDITIONS]
        )
        accepted: list[Condition] = []
        for value in raw_conditions:
            try:
                accepted.append(Condition(value))
            except ValueError as exc:  # pragma: no cover - config error path
                raise ValueError(
                    f"ACCEPTED_CONDITIONS contains unknown condition {value!r}. "
                    f"Valid values: {[c.value for c in Condition]}"
                ) from exc
        status_ids = tuple(int(v) for v in _env_list("VINTED_STATUS_IDS", ["6", "1", "2"]))
        return CollectionConfig(
            base_url=_env("VINTED_BASE_URL", "https://www.vinted.fr").rstrip("/"),
            catalog_id=_env_int("VINTED_CATALOG_ID", 4597),
            session_cookie=os.environ.get("VINTED_SESSION_COOKIE", "").strip(),
            user_agent=_env(
                "HTTP_USER_AGENT",
                "tapia-padel-deal-finder/0.1 (+mailto:unset@example.com)",
            ),
            requests_per_minute=_env_float("VINTED_REQUESTS_PER_MINUTE", 10.0),
            per_page=_env_int("VINTED_PER_PAGE", 96),
            browse_max_pages=_env_int("VINTED_BROWSE_MAX_PAGES", 3),
            search_max_pages=_env_int("VINTED_SEARCH_MAX_PAGES", 1),
            timeout_seconds=_env_float("VINTED_TIMEOUT_SECONDS", 20.0),
            backoff_seconds=_env_float("VINTED_BACKOFF_SECONDS", 60.0),
            status_ids=status_ids,
            accepted_conditions=tuple(accepted) or DEFAULT_ACCEPTED_CONDITIONS,
        )


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    timeout_seconds: float = 15.0
    commands_enabled: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @staticmethod
    def from_env() -> "TelegramConfig":
        return TelegramConfig(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            timeout_seconds=_env_float("TELEGRAM_TIMEOUT_SECONDS", 15.0),
            commands_enabled=_env_bool("TELEGRAM_COMMANDS_ENABLED", True),
        )


@dataclass(frozen=True)
class AppConfig:
    database_url: str = ""
    dry_run: bool = False
    log_level: str = "INFO"
    log_format: str = "json"
    fees: FeeConfig = field(default_factory=FeeConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)

    @staticmethod
    def from_env() -> "AppConfig":
        return AppConfig(
            database_url=os.environ.get("DATABASE_URL", "").strip(),
            dry_run=_env_bool("DRY_RUN", False),
            log_level=_env("LOG_LEVEL", "INFO").upper(),
            log_format=_env("LOG_FORMAT", "json").lower(),
            fees=FeeConfig.from_env(),
            pricing=PricingConfig.from_env(),
            scoring=ScoringConfig.from_env(),
            notifications=NotificationConfig.from_env(),
            collection=CollectionConfig.from_env(),
            telegram=TelegramConfig.from_env(),
        )
