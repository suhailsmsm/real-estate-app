"""Unit tests for dxb.transform.keys — cross-source transaction key
normalization (docs/DATADUBAI_REBUILD_PLAN.md §1, finding #5)."""

from __future__ import annotations

import pytest

from dxb.transform.keys import txn_key_from_datadubai, txn_key_from_gateway


def test_the_verified_real_pair_normalizes_to_the_same_key():
    """The pair confirmed against live data: data.dubai 1-102-2026-59715 and
    gateway 102-59715-2026 are the same sale (2,000,000 AED / 100.19 m²)."""
    assert txn_key_from_datadubai("1-102-2026-59715") == "102-2026-59715"
    assert txn_key_from_gateway("102-59715-2026") == "102-2026-59715"
    assert txn_key_from_datadubai("1-102-2026-59715") == txn_key_from_gateway(
        "102-59715-2026"
    )


def test_datadubai_layout_group_proc_year_seq():
    # group is dropped; procedure/year/seq are carried through in order
    assert txn_key_from_datadubai("3-9-2004-223") == "9-2004-223"
    assert txn_key_from_datadubai("2-13-2026-19136") == "13-2026-19136"


def test_gateway_layout_proc_seq_year():
    assert txn_key_from_gateway("102-58304-2026") == "102-2026-58304"


@pytest.mark.parametrize("bad", [None, "", "   ", "1-2-3", "1-2-3-4-5", "nonsense"])
def test_datadubai_rejects_wrong_shapes(bad):
    assert txn_key_from_datadubai(bad) is None


@pytest.mark.parametrize("bad", [None, "", "1-2", "1-2-3-4", "nonsense"])
def test_gateway_rejects_wrong_shapes(bad):
    assert txn_key_from_gateway(bad) is None


def test_empty_components_rejected():
    assert txn_key_from_datadubai("1--2026-5") is None
    assert txn_key_from_gateway("102--2026") is None


def test_whitespace_tolerated():
    assert txn_key_from_gateway("  102-59715-2026  ") == "102-2026-59715"
