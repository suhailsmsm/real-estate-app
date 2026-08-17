"""Unit tests for dxb.cli's interactive area-split review loop
(`list-area-splits`): arrows move the highlight, Enter toggles the
highlighted pair (approve if pending, revert if already approved),
Esc/Ctrl-C quits. `questionary.select` is faked to drive the interaction —
no real terminal, no real DB (session is a MagicMock; `dxb.area_codes`'s
functions are monkeypatched so only the CLI's own wiring is under test)."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import questionary

from dxb import area_codes, cli
from dxb.db import engine as db_engine


@contextmanager
def _session_ctx(session):
    yield session


def _row(old_id, new_id, reviewed):
    return SimpleNamespace(
        old_area_id=old_id,
        new_area_id=new_id,
        reviewed=reviewed,
        evidence_project_overlap_pct=99.4,
        evidence_txn_count=747,
        evidence_project_ids=[1, 2, 3],
        first_seen_new_code="2026-07-20",
    )


def _fake_select_picking_first(monkeypatch):
    """Simulate the user landing on the first (only) choice and hitting
    Enter — the arrow-key navigation itself is questionary's own, not ours,
    so there is nothing of ours to test there."""

    def fake_select(message, choices):
        return SimpleNamespace(ask=lambda: choices[0].value)

    monkeypatch.setattr(questionary, "select", fake_select)


def _fake_select_quitting(monkeypatch):
    def fake_select(message, choices):
        return SimpleNamespace(ask=lambda: None)

    monkeypatch.setattr(questionary, "select", fake_select)


def test_list_area_splits_approves_the_highlighted_pending_pair(monkeypatch):
    row = _row(20, 292, reviewed=False)
    calls = {"n": 0}

    def fake_all_evidence(session):
        calls["n"] += 1
        return [row] if calls["n"] == 1 else []

    approved = []

    def fake_approve(session, old_area_id, new_area_id):
        approved.append((old_area_id, new_area_id))
        row.reviewed = True
        return {"found": True, "already_reviewed": False}

    monkeypatch.setattr(area_codes, "all_evidence", fake_all_evidence)
    monkeypatch.setattr(area_codes, "approve_area_split", fake_approve)
    monkeypatch.setattr(area_codes, "revert_area_split", MagicMock())
    monkeypatch.setattr(db_engine, "get_session", lambda: _session_ctx(MagicMock()))
    _fake_select_picking_first(monkeypatch)

    cli.list_area_splits()

    assert approved == [(20, 292)]
    area_codes.revert_area_split.assert_not_called()


def test_list_area_splits_reverts_the_highlighted_approved_pair(monkeypatch):
    row = _row(20, 292, reviewed=True)
    calls = {"n": 0}

    def fake_all_evidence(session):
        calls["n"] += 1
        return [row] if calls["n"] == 1 else []

    reverted = []

    def fake_revert(session, old_area_id, new_area_id):
        reverted.append((old_area_id, new_area_id))
        row.reviewed = False
        return {"found": True, "already_reverted": False}

    monkeypatch.setattr(area_codes, "all_evidence", fake_all_evidence)
    monkeypatch.setattr(area_codes, "approve_area_split", MagicMock())
    monkeypatch.setattr(area_codes, "revert_area_split", fake_revert)
    monkeypatch.setattr(db_engine, "get_session", lambda: _session_ctx(MagicMock()))
    _fake_select_picking_first(monkeypatch)

    cli.list_area_splits()

    assert reverted == [(20, 292)]
    area_codes.approve_area_split.assert_not_called()


def test_list_area_splits_quits_without_touching_anything_on_escape(monkeypatch):
    row = _row(20, 292, reviewed=False)

    monkeypatch.setattr(area_codes, "all_evidence", lambda session: [row])
    monkeypatch.setattr(area_codes, "approve_area_split", MagicMock())
    monkeypatch.setattr(area_codes, "revert_area_split", MagicMock())
    monkeypatch.setattr(db_engine, "get_session", lambda: _session_ctx(MagicMock()))
    _fake_select_quitting(monkeypatch)

    cli.list_area_splits()

    area_codes.approve_area_split.assert_not_called()
    area_codes.revert_area_split.assert_not_called()


def test_list_area_splits_with_no_evidence_never_prompts(monkeypatch):
    monkeypatch.setattr(area_codes, "all_evidence", lambda session: [])
    monkeypatch.setattr(db_engine, "get_session", lambda: _session_ctx(MagicMock()))

    def fail_select(*args, **kwargs):
        raise AssertionError("should never prompt with nothing to review")

    monkeypatch.setattr(questionary, "select", fail_select)

    cli.list_area_splits()  # must return cleanly, not raise
