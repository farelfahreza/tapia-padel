# Padel Racket Deal Finder

Finds used padel rackets on Vinted (vinted.fr) that are priced below what that
same model in that same condition usually goes for **on Vinted**, estimates the
margin after fees on both sides, scores the opportunity, and sends only the
good ones to Telegram. You decide whether to buy. The tool never buys, never
sells, and never lists anything.

## What this is (and what it is not)

Buying and reselling happen on the **same platform**. This is not cross-market
arbitrage: the edge comes from listings that are mispriced, mislabeled or
posted by someone who wants a quick sale. Those are rarer and thinner than
cross-platform gaps, so the system is deliberately conservative:

* profit is computed net of buy-side **and** sell-side costs, never as a raw
  price gap (see *Fees* below);
* observed prices are **asking** prices, so they are haircut by a configurable
  realization factor before being called a resale estimate;
* every estimate carries the number of observations behind it, and the whole
  score is multiplied by a confidence factor derived from that count;
* while the price database is still filling up, almost nothing scores well.
  That is the system working, not failing - the logs say so explicitly.

## Source: one collector, and it is fragile

Vinted has **no official public API**. The only collector here is the internal
JSON endpoint `/api/v2/catalog/items` - the one the Vinted web app itself calls
- scoped to catalog `4597` (padel racquets), in two modes:

* **browse** (no search text): pages through the category. The day-to-day mode
  that builds the price database and catches surprises.
* **targeted** (with search text): queries one model within the same category.
  Used to serve watchlist entries.

Consequences you should know about before running this:

* **It needs a session cookie.** Supplied through `VINTED_SESSION_COOKIE`,
  never written to disk. Cookies expire; when that happens the run stops with
  a clear error and you refresh the variable.
* **It is unofficial and in tension with Vinted's ToS.** The endpoint is
  undocumented and can change shape or close without notice.
* **It backs off, it never evades.** 401/403 stops the run (`SourceAuthError`),
  429 stops the run and honours `Retry-After` (`SourceRateLimited`). Requests
  go through a configurable per-minute rate limit and identify themselves
  honestly through `HTTP_USER_AGENT`. There is no anti-bot workaround of any
  kind, and there will not be one.
* **No HTML scraping, no headless browser.** Out of scope. The collector sits
  behind the `ListingSource` interface (`app/collection/base.py`), so if the
  JSON endpoint ever becomes unusable, a different collector can be added
  without touching pricing, scoring or notifications.

## Architecture

```
app/
  config.py              every tunable, loaded from env in one place
  main.py                CLI entry point for the Railway cron job
  pipeline.py            one run: commands -> collect -> store -> price -> score -> notify
  models/                typed models (pydantic): Listing, ResaleEstimate, Opportunity, ...
  collection/            base.py (ListingSource + typed errors), vinted.py, mock.py, rate_limit.py
  pricing/               fees.py (buy side / sell side), estimator.py (median + confidence)
  scoring/               scorer.py (weighted components, caps, explanation)
  notifications/         telegram.py, formatter.py, policy.py, commands.py, watchlist_match.py
  database/              connection.py, migrations.py, migrations/*.sql, store.py
  utils/                 normalize.py (racket names), dedup.py
tests/
```

The dependency direction is one way. `pricing` and `scoring` know nothing
about Vinted or Telegram; `notifications` knows nothing about how a price was
estimated; `collection` knows nothing about money. Validation happens once, at
the collection boundary - a listing that fails it never reaches pricing.

## Data flow

1. **Telegram commands.** `getUpdates` with an offset stored in Postgres, so
   commands are processed exactly once across runs. `/watch`, `/unwatch`,
   `/list`, `/help`.
2. **Collect.** Browse mode, then one targeted query per watchlist entry.
3. **Filter.** The condition allow-list is applied at query level *and* again
   immediately after fetch, before anything is priced.
4. **Store & dedup.** Listings are upserted on `(source, dedup_key)`; the key
   is the Vinted item id where available, the canonical URL next, and a hash of
   source + normalized title + price + location as a last resort.
5. **Observe.** A price observation is recorded only when a listing is new or
   its price actually moved - seeing the same unchanged listing every hour must
   not turn its own price into the market consensus.
6. **Estimate.** Median of observations for that model + condition inside the
   window, excluding the listing being evaluated, haircut by the realization
   factor. Falls back to other conditions of the same model (rescaled, and
   penalised in confidence) and otherwise returns "no estimate".
7. **Score.** Weighted components, then multiplied by the confidence factor,
   then capped.
8. **Decide & notify.** Two thresholds (below), at most N alerts per run.
9. **Commit and exit.** Or roll back, in a dry run.

## Scoring

| Component | Weight | Based on |
|---|---|---|
| price advantage | 35 | how far under the typical Vinted price for this model+condition |
| ROI | 25 | ROI after fees, held back by the absolute profit |
| condition | 15 | Vinted condition level |
| demand | 10 | how often the model is listed (proxy) |
| liquidity | 10 | listings that vanished after being seen more than once (proxy for sales, **not** confirmed sales) |
| location | 5 | `PREFERRED_LOCATIONS` match |

The sum is multiplied by the estimate's confidence factor (observation count,
observation spread, and a penalty for a borrowed-condition estimate), then
capped:

* no usable estimate -> capped at `COLD_START_SCORE_CAP` (default 15);
* profit below `MIN_PROFIT_EUR` or ROI below `MIN_ROI` -> capped at
  `UNPROFITABLE_SCORE_CAP` (default 40), which is under both alert thresholds.

Every component, the multiplier and every cap are stored in
`opportunities.score_breakdown` and printed in the Telegram alert, so any score
can be explained after the fact.

## Fees

How Vinted works today, and how the tool models it:

* **Buy side** - the buyer pays. I pay the listing price, the **5%** buyer
  protection fee, and the shipping. Shipping depends on where the racket ships
  from, so `BUY_SHIPPING_EUR` is the default and `BUY_SHIPPING_BY_LOCATION`
  holds `fragment:amount` overrides matched against the listing's location
  (`corse:9.00,belgique:8.00`).
* **Sell side** - nothing comes off the top. Vinted charges the seller no fee,
  and my buyer pays the shipping on the resale, so the only deduction is my own
  `PACKAGING_COST_EUR` (set it to 0 if you reuse the box it arrived in).

The 5% is therefore counted exactly once, on what I pay - never again on what I
receive. `SELLER_FEE_PCT`, `SELLER_FEE_FIXED_EUR` and `SELL_SHIPPING_COST_EUR`
all default to 0 but still exist and are still tested: a marketplace
introducing a seller fee is precisely the change that would otherwise wreck
every margin the tool reports without anyone noticing.

## Notification policy: two thresholds

* On the watchlist -> alerts at `WATCHLIST_SCORE_THRESHOLD` (default 60).
* Not on the watchlist -> alerts only at `EXCEPTIONAL_SCORE_THRESHOLD`
  (default 80), so good deals you never asked about still reach you.
* Every alert states which bucket it came from ("Matched: your watchlist
  (Nox AT10)" / "Not on watchlist - exceptional deal").

**The optional max price is a ceiling on top of the scoring, never a rule of
its own.** `/watch Metalbone max 100` does not mean "alert me about any
Metalbone under EUR 100" - a cheap racket with a poor margin still stays quiet.
It only adds "and never alert me above EUR 100" on top of the normal scoring;
a great margin above your ceiling is suppressed too.

## Telegram commands

```
/watch <racket> [max <price>]   e.g. /watch Nox AT10 max 90
/unwatch <racket>
/list
/help
```

Commands are read once per scheduled run and take effect on the next one. A
delay of one cycle is the price of not running a second always-on service.

## Database

Postgres, schema managed by plain versioned SQL in
`app/database/migrations/`, applied automatically at the start of each run.
Tables: `listings`, `racket_models`, `price_observations`, `opportunities`,
`notifications`, `watchlist`, `app_state` (holds the Telegram offset), plus
`schema_migrations`. Every table carries timestamps.

**Privacy (France / GDPR).** No seller names, handles, user ids or profile
URLs are stored anywhere - not in a column, not in a JSON blob. The only
seller-derived field is `seller_is_business`, a non-identifying boolean.
`location` is the listing's own city/country as displayed on the listing.
Marketplace credentials are never stored: the session cookie lives only in an
environment variable for the lifetime of the process.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in DATABASE_URL at minimum

python -m app.main --dry-run --source mock   # sample data, sends nothing, persists nothing
python -m app.main --migrate-only            # apply migrations and exit
python -m app.main                           # a real run
pytest                                       # tests
```

`--source mock` never contacts Vinted. `--dry-run` sends no Telegram message
and rolls back every write.

Postgres-backed tests are skipped unless you point them at a throwaway
database:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/padel_test pytest
```

## Deployment (Railway)

Each run is a single-shot process: it connects, collects, evaluates, notifies
and exits. Nothing is scheduled inside the process, and **nothing that must
survive between runs is written to local disk** - all state (dedup, price
observations, watchlist, Telegram offset) lives in Postgres. That makes it safe
to run as a Railway Cron Job with the command `python -m app.main`, with a
Postgres plugin attached and the variables from `.env.example` set on the
service.

## Deliberately not built yet

Sales-history data, price trends, AI-assisted condition analysis from photos,
AI-assisted listing classification, a second collector, automated buying or
selling. The seams are there - `ListingSource` for collection, the
`observations` argument of the estimator for a better price source - but none
of it is built, and the MVP does not depend on any of it.
