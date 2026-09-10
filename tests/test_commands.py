"""Telegram command parsing, including malformed input."""

import pytest

from app.models.watchlist import WatchlistEntry
from app.notifications.commands import (
    CommandName,
    format_watchlist,
    parse_command,
)


def test_watch_without_a_price():
    command = parse_command("/watch Nox AT10")
    assert command.ok
    assert command.name is CommandName.WATCH
    assert command.argument == "Nox AT10"
    assert command.max_price_eur is None


def test_watch_with_a_max_price():
    command = parse_command("/watch Nox AT10 max 90")
    assert command.ok
    assert command.argument == "Nox AT10"
    assert command.max_price_eur == 90.0


@pytest.mark.parametrize(
    "text",
    ["/watch Metalbone max 100", "/watch Metalbone max100", "/watch Metalbone max €100",
     "/watch Metalbone max 100€", "/watch Metalbone MAX 100", "/watch Metalbone max: 100"],
)
def test_max_price_spellings(text):
    command = parse_command(text)
    assert command.ok
    assert command.argument == "Metalbone"
    assert command.max_price_eur == 100.0


def test_decimal_max_price_with_a_comma():
    assert parse_command("/watch Bela max 99,50").max_price_eur == 99.5


def test_unwatch():
    command = parse_command("/unwatch Bullpadel Vertex")
    assert command.ok
    assert command.name is CommandName.UNWATCH
    assert command.argument == "Bullpadel Vertex"


def test_list_and_help_take_no_argument():
    assert parse_command("/list").name is CommandName.LIST
    assert parse_command("/help").name is CommandName.HELP


def test_bot_suffix_from_group_chats_is_tolerated():
    assert parse_command("/list@padel_deal_bot").name is CommandName.LIST


def test_watch_without_an_argument_is_an_error_with_usage():
    command = parse_command("/watch")
    assert not command.ok
    assert "Usage" in command.error


def test_unknown_command_points_at_help():
    command = parse_command("/buyitforme Nox AT10")
    assert not command.ok
    assert command.name is CommandName.UNKNOWN
    assert "/help" in command.error


def test_plain_text_is_not_a_command():
    command = parse_command("hey bot, find me a racket")
    assert not command.ok
    assert command.name is CommandName.UNKNOWN


def test_empty_message_is_handled():
    assert not parse_command("").ok
    assert not parse_command("   ").ok


def test_argument_that_normalizes_to_nothing_is_rejected():
    command = parse_command("/watch de la")
    assert not command.ok


def test_non_positive_max_price_is_rejected():
    command = parse_command("/watch Nox AT10 max 0")
    assert not command.ok


def test_formatting_an_empty_watchlist_explains_how_to_add_one():
    assert "/watch" in format_watchlist([])


def test_formatting_a_watchlist_shows_budgets():
    text = format_watchlist(
        [
            WatchlistEntry(query_text="Nox AT10", normalized_query="nox at10", max_price_eur=90),
            WatchlistEntry(query_text="Metalbone", normalized_query="metalbone"),
        ]
    )
    assert "Nox AT10 - max EUR 90" in text
    assert "- Metalbone" in text
