"""HTTP client for the DLD open-data gateway.

Request tier of the two-tier retry design (plan §3): transient 5xx / network
errors / HTML error pages are retried here with exponential backoff; whole-run
failures are handled by the pipeline-level tenacity wrapper.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)

BASE_URL = "https://gateway.dubailand.gov.ae/open-data"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 dxb-analytics/0.1"
)


class DldError(Exception):
    """Retryable gateway failure (5xx, HTML error page, bad envelope)."""


class DldClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        page_size: int = 500,
        throttle_seconds: float = 0.4,
        max_concurrency: int = 1,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.throttle_seconds = throttle_seconds
        self.max_concurrency = max(1, max_concurrency)
        # httpx.Client's connection pool is documented thread-safe for
        # concurrent requests; pool size must cover in-flight concurrency.
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            transport=transport,
            limits=httpx.Limits(max_connections=self.max_concurrency + 2),
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DldClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, DldError)),
        before_sleep=lambda rs: log.warning(
            "retrying %s (attempt %s): %s",
            rs.args[1] if len(rs.args) > 1 else "?",
            rs.attempt_number,
            rs.outcome.exception() if rs.outcome else "?",
        ),
    )
    def post(self, endpoint: str, payload: dict) -> list[dict]:
        """One gateway call; returns the result rows. Raises DldError on any
        non-JSON / non-200 response (the gateway serves an HTML page on errors,
        e.g. when dates are not MM/DD/YYYY)."""
        resp = self._http.post(f"{self.base_url}/{endpoint}", json=payload)
        if resp.status_code != 200:
            raise DldError(f"{endpoint}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise DldError(f"{endpoint}: non-JSON response (HTML error page?)") from e
        if data.get("responseCode") != 200:
            raise DldError(f"{endpoint}: responseCode={data.get('responseCode')}")
        return (data.get("response") or {}).get("result") or []

    def _page_payload(self, payload: dict, skip: int) -> dict:
        return {**payload, "P_TAKE": str(self.page_size), "P_SKIP": str(skip)}

    def pages(self, endpoint: str, payload: dict) -> Iterator[tuple[dict, list[dict]]]:
        """Iterate all pages for a filter payload, yielding (request, rows).

        Every row carries TOTAL (full count for the filter); the first page is
        always fetched alone to learn TOTAL, then remaining pages are fetched
        with up to `max_concurrency` requests in flight (default 1 = the
        original fully-serial behavior). Order is not preserved when
        max_concurrency > 1 — callers don't need it: staging is hash-deduped
        and facts upsert on natural keys, so pages can land in any order.
        """
        first_payload = self._page_payload(payload, 0)
        rows = self.post(endpoint, first_payload)
        if not rows:
            return
        yield first_payload, rows

        total = int(rows[0].get("TOTAL") or 0)
        if len(rows) < self.page_size or (total and self.page_size >= total):
            return

        remaining_skips = list(
            range(self.page_size, total or self.page_size, self.page_size)
        )
        if not remaining_skips:
            return

        if self.max_concurrency <= 1:
            for skip in remaining_skips:
                time.sleep(self.throttle_seconds)
                page_payload = self._page_payload(payload, skip)
                page_rows = self.post(endpoint, page_payload)
                if not page_rows:
                    return
                yield page_payload, page_rows
            return

        yield from self._pages_concurrent(endpoint, payload, remaining_skips)

    def _pages_concurrent(
        self, endpoint: str, payload: dict, skips: list[int]
    ) -> Iterator[tuple[dict, list[dict]]]:
        def fetch(skip: int) -> tuple[dict, list[dict]]:
            if self.throttle_seconds:
                time.sleep(self.throttle_seconds)
            page_payload = self._page_payload(payload, skip)
            return page_payload, self.post(endpoint, page_payload)

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = [pool.submit(fetch, skip) for skip in skips]
            try:
                for future in as_completed(futures):
                    page_payload, page_rows = future.result()
                    if page_rows:
                        yield page_payload, page_rows
            finally:
                for f in futures:
                    f.cancel()
