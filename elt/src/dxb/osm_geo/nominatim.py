"""Rate-limited Nominatim client. OSM's usage policy for the public instance
caps requests at 1/second and requires a descriptive User-Agent — both
enforced here rather than left to the caller to remember.
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

BASE_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = (
    "dxb-estate-analytics/0.1 "
    "(one-off Dubai area geocoding; contact: serjdukareff@gmail.com)"
)

# Place-like Nominatim addresstype values worth considering a match; excludes
# POI noise (amenity/highway/shop/...) that free-text search can return for
# oddly-specific queries (verified empirically — "Al Barsha First" without
# this filter returns a school and a bus stop, not an area).
GOOD_ADDRESS_TYPES = {
    "suburb",
    "neighbourhood",
    "quarter",
    "residential",
    "city_district",
    "town",
    "village",
    "hamlet",
}


class NominatimClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        throttle_seconds: float = 1.1,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.throttle_seconds = throttle_seconds
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, transport=transport
        )
        self._last_call = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> NominatimClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        wait = self.throttle_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def search(self, query: str) -> list[dict]:
        self._throttle()
        resp = self._http.get(
            self.base_url,
            params={
                "q": query,
                "format": "jsonv2",
                "polygon_geojson": 1,
                "accept-language": "en",
                "countrycodes": "ae",
                "limit": 5,
            },
        )
        self._last_call = time.monotonic()
        resp.raise_for_status()
        return resp.json()
