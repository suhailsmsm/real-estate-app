from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class MartRow(BaseModel):
    """One (entity, month, usage) cell of the monthly time series.

    Always read `sale_cnt` / `rent_cnt` before trusting a median: some months
    contain a single transaction, and its "median" is just that one price.
    """

    entity_id: int
    name_en: str
    month: date = Field(..., description="First day of the month.")
    usage: str
    sale_cnt: int | None = Field(None, description="Sales behind the sale medians.")
    sale_median_price_m2: Decimal | None = None
    sale_p25_price_m2: Decimal | None = None
    sale_p75_price_m2: Decimal | None = Field(
        None, description="With p25, the spread — a wide band means a mixed month."
    )
    rent_cnt: int | None = Field(None, description="Contracts behind the rent median.")
    rent_median_annual_m2: Decimal | None = None
    gross_yield_pct: Decimal | None = Field(
        None, description="rent_median_annual_m2 / sale_median_price_m2, gross."
    )


class BuildingSummaryRow(BaseModel):
    """One (building, usage) summary — NOT a month of a time series.

    Deliberately a different shape from `MartRow`. At building level a monthly
    grain collapses into noise (42% of cells would hold one sale), so what is
    published is a current price level, lifetime coverage, and a coarse trend
    that is null unless the sample supports it.

    There are no rent or yield fields and there never will be: the source rent
    data carries no building identifier to join on.
    """

    entity_id: int
    name_en: str
    area_id: int | None = None
    area_name_en: str | None = None
    usage: str

    sale_cnt_12m: int | None = Field(
        None, description="Sales in the trailing 12 months — the price level's sample."
    )
    median_price_m2_12m: Decimal | None = None
    p25_price_m2_12m: Decimal | None = None
    p75_price_m2_12m: Decimal | None = None

    sale_cnt_total: int = Field(..., description="Lifetime sales on record.")
    median_price_m2_all: Decimal | None = None
    first_sale: date | None = None
    last_sale: date | None = None

    price_m2_cagr_pct: Decimal | None = Field(
        None,
        description=(
            "Null unless the history spans >= 2 years AND both anchor windows "
            "hold >= 5 sales. A null here means 'cannot be stated honestly', "
            "not 'zero growth'."
        ),
    )
    cagr_years: Decimal | None = None
    cagr_sample_size: int | None = None
    sample_tier: str = Field(
        ...,
        description=(
            "strong (>=20 sales in 12m) / thin (>=5) / insufficient. Quote "
            "'thin' and 'insufficient' figures with that caveat attached."
        ),
    )


class MartApplied(BaseModel):
    min_sample: int | None = None
    include_future: bool = False


class BuildingSummaryPage(BaseModel):
    items: list[BuildingSummaryRow]
    limit: int
    offset: int
    has_more: bool
    total: int | None = None
    applied: MartApplied
    grain: str = Field(
        ..., description="Stated in the payload so a client cannot mistake it."
    )
    sales_only: str
    requested_ids: list[int] | None = None
    returned_ids: list[int] | None = None
    missing_ids: list[int] | None = None


class MartPage(BaseModel):
    items: list[MartRow]
    limit: int
    offset: int
    has_more: bool
    total: int | None = None
    applied: MartApplied
    requested_ids: list[int] | None = Field(
        None, description="The ids asked for, echoed back."
    )
    returned_ids: list[int] | None = Field(
        None, description="Ids that actually produced rows."
    )
    missing_ids: list[int] | None = Field(
        None,
        description=(
            "Requested ids with no matching mart rows. Surface these as "
            "'no data' per selection rather than dropping the series."
        ),
    )
