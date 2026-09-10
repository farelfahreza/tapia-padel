"""Entry point for the Railway cron job.

    python -m app.main                  # a real run
    python -m app.main --dry-run        # evaluate, send nothing, persist nothing
    python -m app.main --source mock    # use sample data, never touch Vinted
    python -m app.main --migrate-only   # apply migrations and exit

Single shot: it connects, works, and exits. Nothing is scheduled inside the
process and nothing is written to local disk, so Railway can kill the
container the moment it returns.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

from app.collection.base import ListingSource, SourceAuthError
from app.collection.mock import MockSource
from app.collection.vinted import VintedCatalogSource
from app.config import AppConfig
from app.database.store import InMemoryStore, PostgresStore, Store
from app.logging_setup import setup_logging
from app.notifications.telegram import TelegramClient
from app.pipeline import Pipeline

logger = logging.getLogger("app.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Padel racket deal finder")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate everything but send no Telegram messages and persist nothing",
    )
    parser.add_argument(
        "--source",
        choices=("vinted", "mock"),
        default="vinted",
        help="'mock' reads sample listings from a fixture and never contacts Vinted",
    )
    parser.add_argument("--fixture", default=None, help="path to a mock source fixture")
    parser.add_argument(
        "--migrate-only", action="store_true", help="apply migrations and exit"
    )
    return parser.parse_args(argv)


def build_source(config: AppConfig, args: argparse.Namespace) -> ListingSource:
    if args.source == "mock":
        logger.info("using mock source - no request will be made to Vinted")
        return MockSource(fixture_path=args.fixture)
    return VintedCatalogSource(config.collection)


def build_store(config: AppConfig) -> tuple[Store, object | None]:
    """Postgres when configured, in-memory otherwise (dry runs only)."""
    if config.database_url:
        from app.database.connection import connect
        from app.database.migrations import run_migrations

        conn = connect(config.database_url)
        run_migrations(conn)
        return PostgresStore(conn), conn
    if not config.dry_run:
        raise SystemExit("DATABASE_URL is required for a real run")
    logger.warning(
        "no DATABASE_URL: running against an in-memory store. Nothing survives "
        "this process - dry runs only."
    )
    return InMemoryStore(), None


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - optional locally, absent on Railway
        pass

    args = parse_args(argv)
    config = AppConfig.from_env()
    if args.dry_run:
        config = dataclasses.replace(config, dry_run=True)

    setup_logging(config.log_level, config.log_format)
    logger.info(
        "starting run",
        extra={
            "dry_run": config.dry_run,
            "source": args.source,
            "catalog_id": config.collection.catalog_id,
            "accepted_conditions": [c.value for c in config.collection.accepted_conditions],
        },
    )

    if args.migrate_only:
        _, conn = build_store(config)
        if conn is not None:
            conn.close()
        logger.info("migrations applied")
        return 0

    store, conn = build_store(config)
    source: ListingSource | None = None
    telegram = TelegramClient(config.telegram, dry_run=config.dry_run)
    try:
        source = build_source(config, args)
    except SourceAuthError as exc:
        # No session cookie at all: nothing to collect, and no amount of
        # retrying will change that.
        logger.error("cannot build source", extra={"error": str(exc)})
        if conn is not None:
            conn.close()
        return 2

    try:
        summary = Pipeline(config, store, source, telegram).run()
    except Exception:  # noqa: BLE001 - a cron run must log why it died
        logger.exception("run failed")
        store.rollback()
        return 1
    finally:
        source.close()
        telegram.close()
        if conn is not None:
            conn.close()

    return 0 if not summary.errors else 3


if __name__ == "__main__":
    sys.exit(main())
