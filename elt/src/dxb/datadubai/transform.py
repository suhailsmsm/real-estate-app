"""data.dubai CSV row -> canonical fact dict.

Reuses the shared DimCaches / coercion helpers from transform.dld; only the
column layout and the two source-specific quirks are new:

  * transactions carry no freehold flag  -> is_freehold stays NULL
  * rent amounts are a whole-contract total repeated on every property line
    -> divided by no_of_prop so each row is genuine per-property economics
       (verified: 0 of 917k contracts vary across their lines)
"""

from __future__ import annotations

from datetime import datetime

from dxb.transform.dld import DimCaches, to_float, to_int, to_text
from dxb.transform.keys import txn_key_from_datadubai

# ------------------------------------------------------------- transactions

SALE_KEY_COLS = ["source_id", "txn_number", "txn_date", "actual_area_m2"]
SALE_UPDATE_COLS = [
    "txn_key",
    "txn_group",
    "procedure_name",
    "is_offplan",
    "is_freehold",
    "property_type_id",
    "rooms",
    "parking",
    "area_id",
    "project_id",
    "building_id",
    "parcel_id",
    "amount_aed",
    "source_ref",
]


def sale_values(
    row: dict, caches: DimCaches, source_id: int, source_url: str
) -> dict | None:
    txn_number = to_text(row.get("transaction_id"))
    date_raw = to_text(row.get("instance_date"))  # ISO YYYY-MM-DD
    amount = to_float(row.get("actual_worth"))
    area_id = caches.area(row.get("area_name_en"), row.get("area_name_ar"))
    if not txn_number or not date_raw or amount is None or area_id is None:
        return None
    try:
        txn_date = datetime.fromisoformat(date_raw)
    except ValueError:
        return None
    return {
        "txn_number": txn_number,
        "txn_key": txn_key_from_datadubai(txn_number),
        "txn_date": txn_date,
        "txn_group": to_text(row.get("trans_group_en")) or "Unknown",
        "procedure_name": to_text(row.get("procedure_name_en")),
        "is_offplan": to_text(row.get("reg_type_en")) == "Off-Plan Properties",
        # No freehold column exists in this dataset at all — NULL is "unknown",
        # not "no".
        "is_freehold": None,
        "property_type_id": caches.ptype(
            row.get("property_usage_en"),
            row.get("property_type_en"),
            row.get("property_sub_type_en"),
        ),
        "rooms": to_text(row.get("rooms_en")),
        "parking": to_text(row.get("has_parking")),
        "area_id": area_id,
        "project_id": (
            project_id := caches.project(
                row.get("project_name_en"),
                master_name=to_text(row.get("master_project_en")),
                area_id=area_id,
                name_ar=row.get("project_name_ar"),
            )
        ),
        "building_id": caches.building(
            row.get("building_name_en"),
            area_id=area_id,
            project_id=project_id,
            name_ar=row.get("building_name_ar"),
        ),
        "parcel_id": None,  # not present in this dataset
        "actual_area_m2": to_float(row.get("procedure_area")),
        "amount_aed": amount,
        "source_id": source_id,
        "source_url": source_url,
        "source_ref": txn_number,
    }


# ------------------------------------------------------------------- rents

RENT_KEY_COLS = ["source_id", "source_ref"]
RENT_UPDATE_COLS = [
    "registration_date",
    "start_date",
    "end_date",
    "contract_id",
    "line_number",
    "no_of_prop",
    "version",
    "is_freehold",
    "property_type_id",
    "rooms",
    "area_id",
    "project_id",
    "parcel_id",
    "actual_area_m2",
    "annual_amount_aed",
    "contract_amount_aed",
]


def _per_property(total: float | None, no_of_prop: int | None) -> float | None:
    """Contract totals are repeated on every property line; divide to get the
    per-property amount. Guarded so a missing/zero count is a no-op."""
    if total is None:
        return None
    n = no_of_prop if no_of_prop and no_of_prop > 0 else 1
    return total / n


def _to_date(value):
    text = to_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def rent_values(
    row: dict, caches: DimCaches, source_id: int, source_url: str
) -> dict | None:
    contract_id = to_text(row.get("contract_id"))
    line_number = to_int(row.get("line_number"))
    start_date = _to_date(row.get("contract_start_date"))
    annual_total = to_float(row.get("annual_amount"))
    area_id = caches.area(row.get("area_name_en"), row.get("area_name_ar"))
    if not contract_id or line_number is None or start_date is None:
        return None
    if annual_total is None or area_id is None:
        return None

    no_of_prop = to_int(row.get("no_of_prop"))
    is_free_hold = to_text(row.get("is_free_hold"))
    return {
        # Gateway-only field: data.dubai has no registration date and we do
        # not overload it with the start date.
        "registration_date": None,
        "start_date": start_date,
        "end_date": _to_date(row.get("contract_end_date")),
        "contract_id": contract_id,
        "line_number": line_number,
        "no_of_prop": no_of_prop,
        "version": to_text(row.get("contract_reg_type_en")),  # New / Renew
        "is_freehold": (is_free_hold == "1") if is_free_hold is not None else None,
        "property_type_id": caches.ptype(
            row.get("property_usage_en"),
            row.get("ejari_property_type_en"),
            row.get("ejari_property_sub_type_en"),
        ),
        "rooms": to_text(row.get("ejari_property_sub_type_en")),
        "area_id": area_id,
        "project_id": caches.project(
            row.get("project_name_en"),
            master_name=to_text(row.get("master_project_en")),
            area_id=area_id,
            name_ar=row.get("project_name_ar"),
        ),
        "parcel_id": None,  # not present in this dataset
        "actual_area_m2": to_float(row.get("actual_area")),
        "annual_amount_aed": _per_property(annual_total, no_of_prop),
        "contract_amount_aed": _per_property(
            to_float(row.get("contract_amount")), no_of_prop
        ),
        "source_id": source_id,
        "source_url": source_url,
        # Verified unique across all 10 parts (10,249,312 / 10,249,312).
        "source_ref": f"{contract_id}:{line_number}",
    }
