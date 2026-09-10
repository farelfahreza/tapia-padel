"""Telegram transport. Knows nothing about rackets, prices or scores.

Two responsibilities: send a message, and read the commands sent to the bot
since the last run. The ``getUpdates`` offset lives in Postgres so commands
are not reprocessed across cron runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from app.config import TelegramConfig

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4096


@dataclass(frozen=True)
class TelegramUpdate:
    update_id: int
    chat_id: str
    text: str


class TelegramClient:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        dry_run: bool = False,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return self.config.configured

    def send_message(self, text: str, chat_id: str | None = None) -> bool:
        target = chat_id or self.config.chat_id
        body = text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 3] + "..."

        if self.dry_run:
            logger.info("dry run: telegram message not sent", extra={"preview": body})
            return True
        if not self.enabled:
            logger.warning("telegram not configured, message dropped")
            return False

        try:
            response = self.session.post(
                self._url("sendMessage"),
                json={
                    "chat_id": target,
                    "text": body,
                    "disable_web_page_preview": False,
                },
                timeout=self.config.timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.error("telegram send failed", extra={"error": str(exc)})
            return False
        if response.status_code >= 400:
            logger.error(
                "telegram send rejected",
                extra={"status": response.status_code, "body": response.text[:300]},
            )
            return False
        return True

    def get_updates(self, offset: int | None) -> list[TelegramUpdate]:
        """Fetch pending commands. Short poll only - this is a cron job."""
        if not self.enabled:
            logger.info("telegram not configured, skipping command polling")
            return []
        params: dict[str, Any] = {"timeout": 0, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            response = self.session.get(
                self._url("getUpdates"), params=params, timeout=self.config.timeout_seconds
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error("telegram getUpdates failed", extra={"error": str(exc)})
            return []

        updates: list[TelegramUpdate] = []
        for raw in payload.get("result", []) or []:
            message = raw.get("message") or {}
            text = message.get("text")
            chat = message.get("chat") or {}
            if not text or chat.get("id") is None:
                continue
            updates.append(
                TelegramUpdate(
                    update_id=int(raw["update_id"]),
                    chat_id=str(chat["id"]),
                    text=str(text),
                )
            )
        return updates

    def close(self) -> None:
        self.session.close()

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"
