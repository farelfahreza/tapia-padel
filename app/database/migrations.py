"""Plain versioned SQL migrations.

No ORM and no migration framework: a directory of numbered .sql files and a
table recording which ones ran. The application never issues CREATE TABLE
itself. Migrations run at the start of every cron run - they are idempotent,
and it keeps deployment to "push and go".
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def migration_files(directory: Path | None = None) -> list[Path]:
    directory = directory or MIGRATIONS_DIR
    return sorted(directory.glob("*.sql"))


def run_migrations(conn: psycopg.Connection, directory: Path | None = None) -> list[str]:
    """Apply pending migrations in order. Returns the versions applied."""
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_MIGRATIONS)
        cur.execute("SELECT version FROM schema_migrations")
        done = {row[0] for row in cur.fetchall()}

        for path in migration_files(directory):
            version = path.stem
            if version in done:
                continue
            logger.info("applying migration", extra={"version": version})
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
            )
            applied.append(version)
    conn.commit()
    if not applied:
        logger.info("schema up to date")
    return applied
