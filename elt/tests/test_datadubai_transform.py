"""Unit tests for dxb.datadubai.transform — the two data.dubai row mappers.

The rent mapper carries the load-bearing correctness rule: amounts in the
source are a whole-contract total repeated on every property line, so they
must be divided by no_of_prop.
"""

from __future__ import annotations

from datetime import date, datetime

from conftest import StubCaches

from dxb.datadubai import transform as dd

# --------------------------------------------------------------- transactions


def _txn_row(**overrides) -> dict:
    base = {
        "transaction_id": "1-102-2026-59715",
        "instance_date": "2026-07-07",
        "actual_worth": "2000000.00",
        "trans_group_en": "Sales",
        "procedure_name_en": "Sell",
        "reg_type_en": "Existing Properties",
        "property_usage_en": "Residential",
        "property_type_en": "Unit",
        "property_sub_type_en": "Flat",
        "rooms_en": "2 B/R",
        "has_parking": "1",
        "area_name_en": "Al Jadaf",
        "area_name_ar": None,
        "project_name_en": "Some Project",
        "project_name_ar": None,
        "master_project_en": None,
        "building_name_en": "Lake Terrace",
        "building_name_ar": None,
        "procedure_area": "100.19",
    }
    base.update(overrides)
    return base


def test_sale_maps_fields_and_txn_key():
    caches = StubCaches(area_id=10, ptype_id=20, project_id=30)
    v = dd.sale_values(_txn_row(), caches, source_id=7, source_url="http://src")

    assert v["txn_number"] == "1-102-2026-59715"
    assert v["txn_key"] == "102-2026-59715"  # cross-source key
    assert v["txn_date"] == datetime(2026, 7, 7)
    assert v["amount_aed"] == 2000000.0
    assert v["actual_area_m2"] == 100.19
    assert v["is_offplan"] is False
    # transactions carry no freehold column at all
    assert v["is_freehold"] is None
    assert v["parcel_id"] is None
    assert v["area_id"] == 10 and v["project_id"] == 30
    assert v["building_id"] == 50  # captured from building_name_en
    assert v["source_id"] == 7 and v["source_ref"] == "1-102-2026-59715"


def test_sale_captures_building_scoped_to_area_and_project():
    caches = StubCaches(area_id=10, project_id=30, building_id=50)
    dd.sale_values(_txn_row(), caches, 1, "u")
    assert caches.building_calls == [("Lake Terrace", 10, 30)]


def test_sale_offplan_flag():
    v = dd.sale_values(
        _txn_row(reg_type_en="Off-Plan Properties"), StubCaches(), 1, "u"
    )
    assert v["is_offplan"] is True


def test_sale_iso_date_required():
    # DD-MM-YYYY (the old Kaggle layout) must not silently parse
    assert (
        dd.sale_values(_txn_row(instance_date="07-07-2026"), StubCaches(), 1, "u")
        is None
    )


def test_sale_skips_on_missing_required_fields():
    assert dd.sale_values(_txn_row(transaction_id=""), StubCaches(), 1, "u") is None
    assert dd.sale_values(_txn_row(actual_worth=""), StubCaches(), 1, "u") is None
    assert dd.sale_values(_txn_row(instance_date=""), StubCaches(), 1, "u") is None
    assert dd.sale_values(_txn_row(), StubCaches(area_id=None), 1, "u") is None


# ---------------------------------------------------------------------- rents


def _rent_row(**overrides) -> dict:
    base = {
        "contract_id": "CNT2131853223",
        "line_number": "2",
        "no_of_prop": "1",
        "contract_start_date": "2025-07-16",
        "contract_end_date": "2026-07-15",
        "annual_amount": "72000.00",
        "contract_amount": "72000.00",
        "actual_area": "12.30",
        "contract_reg_type_en": "New",
        "is_free_hold": "0",
        "property_usage_en": "Residential",
        "ejari_property_type_en": "Unit",
        "ejari_property_sub_type_en": "2 bed rooms+hall",
        "area_name_en": "Al Goze Third",
        "area_name_ar": None,
        "project_name_en": "",
        "project_name_ar": None,
        "master_project_en": None,
    }
    base.update(overrides)
    return base


def test_rent_maps_fields_single_property():
    caches = StubCaches(area_id=11, ptype_id=21, project_id=31)
    v = dd.rent_values(_rent_row(), caches, source_id=5, source_url="http://src")

    assert v["contract_id"] == "CNT2131853223"
    assert v["line_number"] == 2
    assert v["no_of_prop"] == 1
    assert v["source_ref"] == "CNT2131853223:2"
    assert v["start_date"] == date(2025, 7, 16)
    assert v["end_date"] == date(2026, 7, 15)
    # gateway-only column stays NULL — never overloaded with the start date
    assert v["registration_date"] is None
    assert v["version"] == "New"
    assert v["is_freehold"] is False
    assert v["actual_area_m2"] == 12.30
    assert v["source_id"] == 5
    # no_of_prop == 1 -> unchanged
    assert v["annual_amount_aed"] == 72000.0
    assert v["contract_amount_aed"] == 72000.0


def test_rent_divides_amounts_by_no_of_prop():
    """The correctness rule: a 3-property contract repeats the 72,000 total on
    every line, so each property's rent is 24,000 — not 72,000."""
    v = dd.rent_values(_rent_row(no_of_prop="3"), StubCaches(), 1, "u")
    assert v["annual_amount_aed"] == 24000.0
    assert v["contract_amount_aed"] == 24000.0


def test_rent_divides_large_portfolio():
    # the real 209-property labor-camp lease from the analysis
    v = dd.rent_values(
        _rent_row(
            no_of_prop="209", annual_amount="386232.00", contract_amount="386232.00"
        ),
        StubCaches(),
        1,
        "u",
    )
    assert round(v["annual_amount_aed"], 2) == 1848.0


def test_rent_zero_or_missing_no_of_prop_is_a_noop_not_a_crash():
    for bad in ("0", ""):
        v = dd.rent_values(_rent_row(no_of_prop=bad), StubCaches(), 1, "u")
        assert v["annual_amount_aed"] == 72000.0  # divided by 1


def test_rent_null_actual_area_allowed():
    """~12.4% of contracts have no area; the row is still valid (it just
    yields no per-m2 rent)."""
    v = dd.rent_values(_rent_row(actual_area=""), StubCaches(), 1, "u")
    assert v is not None
    assert v["actual_area_m2"] is None
    assert v["annual_amount_aed"] == 72000.0


def test_rent_freehold_flag():
    assert (
        dd.rent_values(_rent_row(is_free_hold="1"), StubCaches(), 1, "u")["is_freehold"]
        is True
    )
    assert (
        dd.rent_values(_rent_row(is_free_hold="0"), StubCaches(), 1, "u")["is_freehold"]
        is False
    )


def test_rent_skips_on_missing_required_fields():
    assert dd.rent_values(_rent_row(contract_id=""), StubCaches(), 1, "u") is None
    assert dd.rent_values(_rent_row(line_number=""), StubCaches(), 1, "u") is None
    assert (
        dd.rent_values(_rent_row(contract_start_date=""), StubCaches(), 1, "u") is None
    )
    assert dd.rent_values(_rent_row(annual_amount=""), StubCaches(), 1, "u") is None
    assert dd.rent_values(_rent_row(), StubCaches(area_id=None), 1, "u") is None
