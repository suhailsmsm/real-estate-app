"""Rate-limited client for Makani's public `find-place` search.

Makani is Dubai's official addressing system; its `find-place` endpoint is
Google-Places-backed, so unlike OSM it actually covers Dubai's residential
buildings (measured 75% validated vs OSM's 6% — see
docs/PROJECT_GEO_ENRICHMENT.md §6). It is public and needs no auth, but it is
an **undocumented internal endpoint**, so it gets the same treatment as the DLD
gateway: a polite throttle, a descriptive User-Agent, and tenacity retries.

The response is Google-Places-shaped:
    {"candidates": [{"geometry": {"location": {"lat": .., "lng": ..}},
                     "name": "...", "types": ["establishment", ...]}, ...]}
"""

from __future__ import annotations

import logging
import time

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

BASE_URL = "https://www.makani.ae/MakaniWSFBSearchUAEPass/api/api/find-place"
# The endpoint is behind the makani.ae web app; mirror its browser headers so
# it does not reject the request as an unknown client.
HEADERS = {
    "User-Agent": (
        "dxb-estate-analytics/0.1 "
        "(Dubai building geocoding; contact: serjdukareff@gmail.com)"
    ),
    "Origin": "https://www.makani.ae",
    "Referer": "https://www.makani.ae/desktop/",
}

# Place-ish Google types worth accepting for a building. Broad on purpose (a
# building resolves to an establishment / premise / point_of_interest); the
# area-containment check downstream is what actually rejects wrong matches, not
# this list.
GOOD_TYPES = {
    "establishment",
    "premise",
    "point_of_interest",
    "subpremise",
    "street_address",
    "landmark",
    "building",
}


class MakaniClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        throttle_seconds: float = 0.4,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.throttle_seconds = throttle_seconds
        self._http = httpx.Client(headers=HEADERS, timeout=timeout, transport=transport)
        self._last_call = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> MakaniClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self.throttle_seconds - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def find_place(self, text: str) -> list[dict]:
        """Return the raw candidate list for a free-text place query."""
        self._throttle()
        resp = self._http.get(self.base_url, params={"text": text, "lang": "E"})
        self._last_call = time.monotonic()
        resp.raise_for_status()
        data = resp.json()
        return data.get("candidates", []) if isinstance(data, dict) else []


def pick_candidate(candidates: list[dict]) -> dict | None:
    """First candidate of an acceptable type that carries a coordinate.

    Ordering is left as Makani returned it (relevance-ranked); the real filter
    is area containment applied by the caller, so this only drops obvious
    type-noise and coordinateless rows.
    """
    for cand in candidates:
        types = cand.get("types") or []
        if GOOD_TYPES.isdisjoint(types):
            continue
        loc = (cand.get("geometry") or {}).get("location") or {}
        if loc.get("lat") is not None and loc.get("lng") is not None:
            return cand
    return None
