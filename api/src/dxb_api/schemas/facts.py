from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    """Where a row came from. Present on every fact — provenance is
    load-bearing here, not decorative: `source_url` is the citable link."""

    source_id: int
    source_code: str
    is_government: bool
    source_url: str
    source_ref: str | None = None


class SaleTransaction(Provenance):
    id: int
    txn_number: str
    txn_key: str | None = Field(
        None,
        description=(
            "Normalized '{procedure}-{year}-{seq}'. Identifies the same Sales "
            "transaction across both sources; not unique for mortgages/gifts."
        ),
    )
    txn_date: datetime
    txn_group: str = Field(..., description="Sales | Mortgage | Gifts.")
    procedure_name: str | None = None
    is_offplan: bool
    is_freehold: bool | None = Field(
        None, description="Null means unknown — the historical CSV has no such column."
    )
    rooms: str | None = None
    parking: str | None = None
    area_id: int
    area_name_en: str
    project_id: int | None = None
    project_name_en: str | None = None
    usage: str | None = None
    prop_type: str | None = None
    prop_subtype: str | None = None
    actual_area_m2: Decimal | None = None
    amount_aed: Decimal
    price_per_m2: Decimal | None = None


class RentContract(Provenance):
    id: int
    contract_id: str | None = None
    line_number: int | None = None
    no_of_prop: int | None = Field(
        None,
        description=(
            "Properties covered by the contract. Amounts below are already "
            "divided by this, so they are per property."
        ),
    )
    registration_date: datetime | None = Field(
        None, description="When the contract was registered. Gateway rows only."
    )
    start_date: date | None = Field(
        None, description="Lease start. Can be years ahead of registration."
    )
    end_date: date | None = None
    version: str | None = Field(None, description="New | Renew.")
    is_freehold: bool | None = None
    rooms: str | None = None
    area_id: int
    area_name_en: str
    project_id: int | None = None
    project_name_en: str | None = None
    usage: str | None = None
    prop_type: str | None = None
    prop_subtype: str | None = None
    actual_area_m2: Decimal | None = None
    annual_amount_aed: Decimal = Field(..., description="Per property, per year.")
    contract_amount_aed: Decimal | None = None
    rent_per_m2_year: Decimal | None = None
