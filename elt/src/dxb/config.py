from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str) -> str:
    # .strip(): some .env loaders (VS Code's python.envFile, manual shell
    # sourcing) don't trim trailing whitespace the way Docker Compose's
    # env_file parser does — defensive only, does not touch inline comments.
    v = os.environ.get(name, "").strip()
    return v if v != "" else default


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    page_size: int
    throttle_seconds: float
    max_concurrency: int
    source_url: str

    schedule_hour: int
    schedule_minute: int

    job_attempts: int
    job_wait_min_seconds: float
    job_wait_max_seconds: float

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_starttls: bool
    alert_to: str
    alert_from: str

    data_raw_dir: str

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def get_settings() -> Settings:
    return Settings(
        db_host=_get("DXB_DB_HOST", "localhost"),
        db_port=int(_get("DXB_DB_PORT", "5432")),
        db_user=_get("POSTGRES_USER", "dxb"),
        db_password=_get("POSTGRES_PASSWORD", ""),
        db_name=_get("POSTGRES_DB", "dxb"),
        page_size=int(_get("DXB_PAGE_SIZE", "500")),
        throttle_seconds=float(_get("DXB_THROTTLE_SECONDS", "0.4")),
        # Undocumented gov't API behind Cloudflare, no published rate limits:
        # keep this moderate by default. Concurrency is the actual lever for
        # throughput here (per-page latency, not our throttle, is the
        # bottleneck) — but too high risks the client IP getting blocked,
        # which is worse than slow. Raise via env if the server tolerates it.
        max_concurrency=int(_get("DXB_MAX_CONCURRENCY", "5")),
        source_url=_get(
            "DXB_SOURCE_URL", "https://dubailand.gov.ae/en/open-data/real-estate-data/"
        ),
        schedule_hour=int(_get("DXB_SCHEDULE_HOUR", "2")),
        schedule_minute=int(_get("DXB_SCHEDULE_MINUTE", "30")),
        job_attempts=int(_get("DXB_JOB_ATTEMPTS", "3")),
        job_wait_min_seconds=float(_get("DXB_JOB_WAIT_MIN_SECONDS", "300")),
        job_wait_max_seconds=float(_get("DXB_JOB_WAIT_MAX_SECONDS", "3600")),
        smtp_host=_get("SMTP_HOST", ""),
        smtp_port=int(_get("SMTP_PORT", "587")),
        smtp_user=_get("SMTP_USER", ""),
        smtp_password=_get("SMTP_PASSWORD", ""),
        smtp_starttls=_get("SMTP_STARTTLS", "1") not in ("0", "false", "no"),
        alert_to=_get("ALERT_TO", ""),
        alert_from=_get("ALERT_FROM", "dxb-elt@localhost"),
        # Read-only volume mount for the one-off historical CSV import
        # (data/ is gitignored and not baked into the image — see
        # docker-compose.yml).
        data_raw_dir=_get("DXB_DATA_RAW", "/app/data/raw"),
    )
