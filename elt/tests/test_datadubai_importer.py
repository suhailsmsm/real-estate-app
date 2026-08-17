"""Unit tests for dxb.datadubai.importer — streaming batch import."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dxb.datadubai import importer


def _write_csv(
    tmp_path: Path, name: str, header: list[str], rows: list[list[str]]
) -> Path:
    path = tmp_path / name
    lines = [",".join(header)] + [",".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


class _FakeModel:
    """MagicMock cannot serve dunder attributes, so the table stand-in is a
    plain class carrying __table__."""

    __table__ = "fake_table"


@pytest.fixture
def spec():
    """Minimal spec: mapper echoes the row, table/constraint are never touched
    because _upsert_facts is patched."""
    return {
        "mapper": lambda row, caches, sid, url: {"v": row["A"], "source_id": sid},
        "table": _FakeModel,
        "constraint": "ux_test",
        "key_cols": ["source_id", "v"],
        "update_cols": ["v"],
    }


def test_import_file_batches_at_boundary(tmp_path, spec, monkeypatch):
    monkeypatch.setattr(importer, "BATCH", 2)
    calls = []
    monkeypatch.setattr(
        importer,
        "_upsert_facts",
        lambda s, t, c, k, u, values: calls.append(len(values)) or len(values),
    )
    path = _write_csv(tmp_path, "t.csv", ["A"], [["1"], ["2"], ["3"], ["4"], ["5"]])

    report = importer.import_file(
        MagicMock(), path, spec, MagicMock(), source_id=7, source_url="u"
    )

    assert report["read"] == 5
    assert report["written"] == 5
    assert calls == [2, 2, 1]  # flushes at the batch boundary, remainder last


def test_import_file_counts_skipped_rows(tmp_path, spec, monkeypatch):
    spec["mapper"] = lambda row, caches, sid, url: (
        None if row["A"] == "2" else {"v": row["A"]}
    )
    monkeypatch.setattr(
        importer, "_upsert_facts", lambda s, t, c, k, u, values: len(values)
    )
    path = _write_csv(tmp_path, "t.csv", ["A"], [["1"], ["2"], ["3"]])

    report = importer.import_file(
        MagicMock(), path, spec, MagicMock(), source_id=1, source_url="u"
    )

    assert report["read"] == 3
    assert report["skipped"] == 1
    assert report["written"] == 2


def test_import_file_strips_bom(tmp_path, spec, monkeypatch):
    seen = {}
    spec["mapper"] = lambda row, caches, sid, url: (
        seen.update(keys=list(row)) or {"v": 1}
    )
    monkeypatch.setattr(importer, "_upsert_facts", lambda *a: 1)
    path = tmp_path / "bom.csv"
    path.write_bytes("﻿A,B\n1,2\n".encode())

    importer.import_file(MagicMock(), path, spec, MagicMock(), 1, "u")

    assert seen["keys"][0] == "A"  # not "﻿A"


def test_import_file_empty_file_writes_nothing(tmp_path, spec, monkeypatch):
    upserts = []
    monkeypatch.setattr(
        importer, "_upsert_facts", lambda s, t, c, k, u, v: upserts.append(v) or 0
    )
    path = _write_csv(tmp_path, "t.csv", ["A"], [])

    report = importer.import_file(MagicMock(), path, spec, MagicMock(), 1, "u")

    assert report["read"] == 0 and report["written"] == 0
    assert upserts == []  # no empty upsert issued


def test_import_dataset_raises_when_no_files(monkeypatch):
    monkeypatch.setattr(importer, "files_for", lambda ds: [])
    with pytest.raises(FileNotFoundError, match="no files matching"):
        importer.import_dataset(MagicMock(), "transactions", "u")


def test_import_dataset_aggregates_across_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "resolve_source_id", lambda s, code: 3)
    monkeypatch.setattr(importer, "DimCaches", lambda s: MagicMock())
    parts = [
        _write_csv(tmp_path, "p1.csv", ["A"], [["1"], ["2"]]),
        _write_csv(tmp_path, "p2.csv", ["A"], [["3"]]),
    ]
    monkeypatch.setattr(importer, "files_for", lambda ds: parts)
    monkeypatch.setattr(
        importer,
        "_SPEC",
        {
            "transactions": {
                "mapper": lambda row, caches, sid, url: {"v": row["A"]},
                "table": _FakeModel,
                "constraint": "c",
                "key_cols": ["v"],
                "update_cols": ["v"],
            }
        },
    )
    monkeypatch.setattr(
        importer, "_upsert_facts", lambda s, t, c, k, u, values: len(values)
    )

    report = importer.import_dataset(MagicMock(), "transactions", "u")

    assert report["read"] == 3
    assert report["written"] == 3
    assert len(report["files"]) == 2
