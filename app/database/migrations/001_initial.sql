-- Initial schema for the padel racket deal finder.
--
-- Everything that must survive between cron runs lives here: listings and
-- their dedup state, the observed price database, scored opportunities, sent
-- notifications, the watchlist, and the Telegram getUpdates offset. The
-- process itself keeps nothing on local disk.
--
-- No seller identity is stored anywhere in this schema: no name, handle,
-- user id or profile URL. The only seller-derived column is a
-- non-identifying boolean (professional vs private).

CREATE TABLE IF NOT EXISTS listings (
    id                 BIGSERIAL PRIMARY KEY,
    source             TEXT        NOT NULL,
    external_id        TEXT,
    dedup_key          TEXT        NOT NULL,
    url                TEXT        NOT NULL,
    title              TEXT        NOT NULL,
    title_normalized   TEXT        NOT NULL,
    brand              TEXT,
    model_key          TEXT        NOT NULL,
    price_eur          NUMERIC(10, 2) NOT NULL CHECK (price_eur > 0),
    currency           TEXT        NOT NULL DEFAULT 'EUR',
    -- One of Vinted's five fixed condition levels. Never free text.
    condition          TEXT,
    location           TEXT,
    published_at       TIMESTAMPTZ,
    -- Non-identifying aggregate attribute only (GDPR).
    seller_is_business BOOLEAN,
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seen_count         INTEGER     NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT listings_source_dedup_key_uniq UNIQUE (source, dedup_key)
);

CREATE INDEX IF NOT EXISTS listings_model_key_idx    ON listings (model_key);
CREATE INDEX IF NOT EXISTS listings_last_seen_at_idx ON listings (last_seen_at);
CREATE INDEX IF NOT EXISTS listings_condition_idx    ON listings (condition);

-- One row per racket model we have ever seen, keyed by the normalized model
-- key. Cheap to maintain and useful for the demand factor.
CREATE TABLE IF NOT EXISTS racket_models (
    id            BIGSERIAL PRIMARY KEY,
    model_key     TEXT        NOT NULL UNIQUE,
    brand         TEXT,
    display_name  TEXT,
    listings_seen INTEGER     NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The price database. Asking prices observed on Vinted, never confirmed
-- sales. One row per (listing, price) so a re-priced listing is recorded
-- without overwriting its own history.
CREATE TABLE IF NOT EXISTS price_observations (
    id                BIGSERIAL PRIMARY KEY,
    model_key         TEXT        NOT NULL,
    condition         TEXT        NOT NULL,
    price_eur         NUMERIC(10, 2) NOT NULL CHECK (price_eur > 0),
    source            TEXT        NOT NULL,
    listing_dedup_key TEXT        NOT NULL,
    observed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS price_observations_lookup_idx
    ON price_observations (model_key, condition, observed_at DESC);

CREATE TABLE IF NOT EXISTS opportunities (
    id                        BIGSERIAL PRIMARY KEY,
    listing_id                BIGINT NOT NULL REFERENCES listings (id) ON DELETE CASCADE,
    score                     NUMERIC(5, 2) NOT NULL,
    -- Every figure below is an estimate. The column names say so.
    estimated_resale_eur      NUMERIC(10, 2),
    estimate_observations     INTEGER     NOT NULL DEFAULT 0,
    estimate_confidence       TEXT        NOT NULL,
    estimate_method           TEXT        NOT NULL,
    buy_total_cost_eur        NUMERIC(10, 2) NOT NULL,
    expected_net_proceeds_eur NUMERIC(10, 2),
    expected_profit_eur       NUMERIC(10, 2),
    roi                       NUMERIC(8, 4),
    -- Full, decomposable score explanation.
    score_breakdown           JSONB       NOT NULL,
    evaluated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS opportunities_listing_idx ON opportunities (listing_id);
CREATE INDEX IF NOT EXISTS opportunities_score_idx   ON opportunities (score DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id                      BIGSERIAL PRIMARY KEY,
    listing_id              BIGINT NOT NULL REFERENCES listings (id) ON DELETE CASCADE,
    channel                 TEXT        NOT NULL DEFAULT 'telegram',
    -- 'watchlist' or 'exceptional' - which threshold triggered the alert.
    bucket                  TEXT        NOT NULL,
    matched_watchlist_query TEXT,
    score                   NUMERIC(5, 2) NOT NULL,
    price_eur               NUMERIC(10, 2) NOT NULL,
    message                 TEXT,
    sent_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS notifications_listing_idx ON notifications (listing_id, sent_at DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    id               BIGSERIAL PRIMARY KEY,
    query_text       TEXT        NOT NULL,
    normalized_query TEXT        NOT NULL UNIQUE,
    -- Optional budget ceiling, applied on top of the scoring.
    max_price_eur    NUMERIC(10, 2),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Small key/value table. Currently holds the Telegram getUpdates offset so
-- commands are processed exactly once across cron runs.
CREATE TABLE IF NOT EXISTS app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
