"""Postgres connection helper.

One connection per run. The process is single-shot: connect, work, commit,
exit. No pool, no background threads.
"""

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)


def connect(database_url: str) -> psycopg.Connection:
    if not database_url:
        raise ValueError("DATABASE_URL is not set")
    conn = psycopg.connect(database_url, autocommit=False)
    logger.info("database connected")
    return conn
