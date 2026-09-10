"""Telegram command parsing and handling.

Commands are read once per scheduled run via ``getUpdates`` with an offset
stored in Postgres. They take effect on the next run - one cycle of delay is
fine, and it saves running a second always-on service.

Supported:
    /watch <racket name> [max <price>]
    /unwatch <racket name>
    /list
    /help
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from app.models.watchlist import WatchlistEntry
from app.utils.normalize import normalized_query

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Padel Deal Finder commands:\n"
    "/watch <racket> [max <price>] - watch a racket, optionally with a budget "
    "ceiling. Example: /watch Nox AT10 max 90\n"
    "/unwatch <racket> - stop watching it\n"
    "/list - show the current watchlist\n"
    "/help - this message\n\n"
    "A max price is a ceiling on top of the deal scoring, not a rule of its own: "
    "a cheap racket with a poor resale margin still will not be alerted."
)

_MAX_PRICE_RE = re.compile(
    r"\bmax\s*[:=]?\s*(?:eur|euros?|€)?\s*(\d+(?:[.,]\d{1,2})?)\s*(?:eur|euros?|€)?\s*$",
    re.IGNORECASE,
)


class CommandName(str, Enum):
    WATCH = "watch"
    UNWATCH = "unwatch"
    LIST = "list"
    HELP = "help"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedCommand:
    name: CommandName
    argument: str = ""
    max_price_eur: float | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.name is not CommandName.UNKNOWN


def parse_command(text: str) -> ParsedCommand:
    """Parse one Telegram message. Never raises - malformed input is data."""
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return ParsedCommand(
            name=CommandName.UNKNOWN,
            error="Not a command. Send /help to see what I understand.",
        )

    head, _, rest = raw.partition(" ")
    # "/watch@my_bot" is what Telegram sends in groups.
    verb = head[1:].split("@", 1)[0].lower()
    argument = rest.strip()

    if verb == "help":
        return ParsedCommand(name=CommandName.HELP)
    if verb == "list":
        return ParsedCommand(name=CommandName.LIST)
    if verb in ("watch", "unwatch"):
        name = CommandName.WATCH if verb == "watch" else CommandName.UNWATCH
        max_price: float | None = None
        if name is CommandName.WATCH:
            match = _MAX_PRICE_RE.search(argument)
            if match:
                max_price = float(match.group(1).replace(",", "."))
                argument = argument[: match.start()].strip()
        elif _MAX_PRICE_RE.search(argument):
            # "/unwatch Nox AT10 max 90" - tolerate it, ignore the price part.
            argument = _MAX_PRICE_RE.sub("", argument).strip()

        if not argument:
            return ParsedCommand(
                name=name,
                error=f"Usage: /{verb} <racket name>"
                + (" [max <price>]" if name is CommandName.WATCH else ""),
            )
        if not normalized_query(argument):
            return ParsedCommand(
                name=name,
                argument=argument,
                error=f"'{argument}' does not contain anything I can match on.",
            )
        if max_price is not None and max_price <= 0:
            return ParsedCommand(
                name=name, argument=argument, error="Max price must be greater than 0."
            )
        return ParsedCommand(name=name, argument=argument, max_price_eur=max_price)

    return ParsedCommand(
        name=CommandName.UNKNOWN,
        argument=argument,
        error=f"Unknown command /{verb}. Send /help to see what I understand.",
    )


def format_watchlist(entries: list[WatchlistEntry]) -> str:
    if not entries:
        return "Your watchlist is empty. Add one with /watch Nox AT10 max 90"
    lines = ["Your watchlist:"]
    for entry in entries:
        budget = (
            f" - max EUR {entry.max_price_eur:.0f}"
            if entry.max_price_eur is not None
            else ""
        )
        lines.append(f"- {entry.query_text}{budget}")
    return "\n".join(lines)
