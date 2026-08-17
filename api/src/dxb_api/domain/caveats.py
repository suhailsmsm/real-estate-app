"""The limitations every analytics response must carry.

These ship *in the payload*, not just in the docs, because the primary
consumer is an LLM that will otherwise present a gross yield as an achievable
return. Stating the caveats next to the number is what makes the number
honest (docs/API_DESIGN.md §7a).
"""

from __future__ import annotations

METHODOLOGY = (
    "Capital growth is the CAGR of the median sale price per m2, measured "
    "between count-weighted anchor windows of up to 12 months at each end of "
    "the requested range. Gross rental yield is the count-weighted median "
    "annual rent per m2 over the count-weighted median sale price per m2, "
    "both from the most recent 12 months with data. Gross total return is "
    "their sum, mirroring the NCREIF NPI / MSCI decomposition "
    "(total = income + capital)."
)

CAVEATS = [
    "Gross, not net: excludes service charges (commonly 15-25% of gross rent), "
    "vacancy, management fees, the 4% DLD transfer fee and agency commission. "
    "Treat the yield as an upper bound, not an achievable return.",
    "Mix-shift bias: appreciation compares medians of *different* properties "
    "transacting each month, not the same asset over time. A shift toward "
    "larger or more luxurious units raises the median with zero underlying "
    "appreciation. Repeat-sales indexing is not possible here because no "
    "stable property identifier exists across transactions.",
    "Sale stock is not rental stock: the yield divides a median rent by a "
    "median price drawn from different property sets in the same area. Most "
    "public yield figures do the same, but it is not a per-asset yield.",
    "No leverage, taxes, or timing of cash flows: these are not IRR or "
    "cash-on-cash, the metrics an actual investor optimizes.",
]

SMALL_SAMPLE = (
    "Some months in this range have very few transactions; medians there are "
    "noise. Raise min_sample to exclude them."
)

FUTURE_DATED = (
    "Rent contracts may be start-dated years ahead (advance leases). Months "
    "after today are excluded unless include_future=true."
)


def block(extra: list[str] | None = None) -> dict:
    return {"methodology": METHODOLOGY, "caveats": CAVEATS + list(extra or [])}
