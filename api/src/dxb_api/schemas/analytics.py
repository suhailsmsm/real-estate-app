"""Analytics contracts.

The `methodology` and `caveats` fields are part of the payload on purpose: an
MCP client shows these to the model, which is what stops a gross yield being
reported as an achievable return (docs/API_DESIGN.md §7a).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from dxb_api.schemas.common import ResolvedEntity


class Anchor(BaseModel):
    """One end of the growth measurement."""

    value_aed_m2: float | None = None
    sample_size: int = 0
    midpoint: date | None = Field(
        None, description="Count-weighted centre of the anchor window."
    )
    months: int = 0


class MetricSummary(BaseModel):
    """The three headline metrics plus the evidence behind them."""

    capital_growth_cagr_pct: float | None = Field(
        None,
        description=(
            "Annualized growth of the median sale AED/m2 between count-"
            "weighted anchor windows. Null when the span is under a year or "
            "there is not enough data — never a guess."
        ),
    )
    gross_rental_yield_pct: float | None = Field(
        None,
        description=(
            "Median annual rent AED/m2 over median sale AED/m2, latest 12 "
            "months. GROSS: before service charges, vacancy and fees."
        ),
    )
    gross_total_return_pct: float | None = Field(
        None,
        description=(
            "capital_growth_cagr_pct + gross_rental_yield_pct, the "
            "NCREIF/MSCI income+capital decomposition. Null unless both halves "
            "exist — a missing half is never treated as zero."
        ),
    )
    median_sale_aed_m2: float | None = None
    median_rent_aed_m2_year: float | None = None
    years: float | None = Field(None, description="Span between anchor midpoints.")
    start: Anchor | None = None
    end: Anchor | None = None
    sale_sample_size: int = 0
    rent_sample_size: int = 0
    months_covered: int = 0
    month_from: date | None = None
    month_to: date | None = None


class MethodologyBlock(BaseModel):
    methodology: str
    caveats: list[str] = Field(
        ...,
        description="Limitations that must be repeated alongside any number here.",
    )


class RankedEntity(MetricSummary):
    id: int
    name_en: str

    # `None` on an area or project simply means "not applicable to this grain".
    usage: str | None = None
    sample_size: int | None = Field(
        None, description="Buildings: sales in the trailing 12 months."
    )
    sample_size_lifetime: int | None = None
    median_sale_aed_m2_lifetime: float | None = None
    cagr_sample_size: int | None = None
    sample_tier: str | None = Field(
        None,
        description=(
            "Buildings only: strong / thin / insufficient. Quote 'thin' and "
            "'insufficient' figures with that caveat attached."
        ),
    )
    income_metrics_unavailable: str | None = Field(
        None,
        description=(
            "Present on buildings: states that yield cannot exist at this "
            "grain, so a null is not read as missing data."
        ),
    )


class RankingResponse(MethodologyBlock):
    entity: str
    metric: str
    metric_field: str = Field(..., description="The response field ranked on.")
    items: list[RankedEntity]
    applied: dict
    entities_considered: int
    entities_excluded_insufficient_data: int = Field(
        ...,
        description=(
            "Entities dropped because the metric could not be computed "
            "honestly at this min_sample. A large number means the ranking "
            "covers a small slice of the city."
        ),
    )


class SeriesPoint(BaseModel):
    month: date
    sale_median_price_m2: float | None = None
    sale_cnt: int = 0
    rent_median_annual_m2: float | None = None
    rent_cnt: int = 0


class YoYStep(BaseModel):
    year: int
    value_aed_m2: float | None = None
    sample_size: int = 0
    yoy_change_pct: float | None = None


class GrowthResponse(MetricSummary, MethodologyBlock):
    entity: str
    id: int
    name_en: str | None = None
    series: list[SeriesPoint]
    yoy: list[YoYStep]
    consecutive_yoy_increases: int = Field(
        ...,
        description=(
            "Run of consecutive rising years ending at the most recent year — "
            "answers 'does this keep going up every year' directly."
        ),
    )
    resolved_entity: ResolvedEntity | None = None
    applied: dict


class CompareGroup(MetricSummary):
    value: str
    id: int | None = None
    name_en: str | None = None
    entities_pooled: int | None = Field(
        None, description="Areas pooled into this group (usage comparisons)."
    )
    resolved_entity: ResolvedEntity | None = None


class CompareResponse(MethodologyBlock):
    dimension: str
    groups: list[CompareGroup]
    applied: dict
