"""Cross-source transaction key normalization.

The two sources encode the same DLD transaction with different id layouts:

    data.dubai  "{group}-{procedure}-{year}-{seq}"   1-102-2026-59715
    gateway     "{procedure}-{seq}-{year}"           102-59715-2026

Both normalize to "{procedure}-{year}-{seq}". Verified against live data:
data.dubai 1-102-2026-59715 and gateway 102-59715-2026 are the same sale
(2,000,000 AED / 100.19 m²).

Scope caveat (docs/DATADUBAI_REBUILD_PLAN.md §1, finding #6): this holds for
Sales. Mortgage/gift numbering diverges between the sources, so the key is a
validation/merge tool for Sales — never an identity constraint.
"""

from __future__ import annotations


def _norm(procedure: str, year: str, seq: str) -> str:
    return f"{procedure}-{year}-{seq}"


def txn_key_from_datadubai(txn_id: str | None) -> str | None:
    """'1-102-2026-59715' -> '102-2026-59715'."""
    if not txn_id:
        return None
    parts = str(txn_id).strip().split("-")
    if len(parts) != 4:
        return None
    _group, procedure, year, seq = parts
    if not (procedure and year and seq):
        return None
    return _norm(procedure, year, seq)


def txn_key_from_gateway(txn_number: str | None) -> str | None:
    """'102-59715-2026' -> '102-2026-59715'."""
    if not txn_number:
        return None
    parts = str(txn_number).strip().split("-")
    if len(parts) != 3:
        return None
    procedure, seq, year = parts
    if not (procedure and year and seq):
        return None
    return _norm(procedure, year, seq)
