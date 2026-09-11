"""Mock source for dry runs and tests.

Reads a JSON fixture shaped like a real ``/api/v2/catalog/items`` response and
runs it through the exact same parser and validation path as the live
collector - so a dry run exercises the real boundary, not a shortcut.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.collection.base import ListingSource
from app.collection.vinted import SOURCE_NAME, parse_items
from app.models.listing import Listing
from app.utils.normalize import matches_query

logger = logging.getLogger(__name__)

DEFAULT_FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "vinted_catalog_sample.json"


class MockSource(ListingSource):
    name = SOURCE_NAME

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        fixture_path: Path | str | None = None,
        base_url: str = "https://www.vinted.fr",
    ) -> None:
        if payload is None:
            path = Path(fixture_path) if fixture_path else DEFAULT_FIXTURE
            payload = json.loads(path.read_text(encoding="utf-8"))
            logger.info("mock source loaded", extra={"fixture": str(path)})
        self.payload = payload
        self.base_url = base_url

    def _all(self) -> list[Listing]:
        listings, skipped = parse_items(self.payload.get("items", []), self.base_url)
        if skipped:
            logger.warning("mock fixture had unparseable items", extra={"skipped": skipped})
        return listings

    def browse(self, *, max_pages: int | None = None) -> list[Listing]:
        return self._all()

    def search(self, query: str, *, max_pages: int | None = None) -> list[Listing]:
        return [item for item in self._all() if matches_query(item.title, query, item.source_brand)]
