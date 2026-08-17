"""The UI patch contract.

These guard the boundary that makes the copilot safe to give to a user: it can
only change the interface through a ViewState patch, that patch travels the
same reducer a click does, and anything malformed is refused with a reason the
model can act on rather than being half-applied.
"""

from __future__ import annotations

import pytest

from dxb_copilot.ui_tools import (
    SET_VIEW_STATE,
    UI_TOOL_NAMES,
    PatchRejected,
    extract_patch,
)


def test_tool_is_named_and_schema_is_closed():
    assert SET_VIEW_STATE.name in UI_TOOL_NAMES
    schema = SET_VIEW_STATE.schema
    # additionalProperties=False so a model inventing a field gets a validation
    # error from the SDK rather than having it silently ignored.
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "view",
        "listing",
        "dashboard",
        "map",
        "explanation",
    }


def test_extract_splits_patch_from_explanation():
    patch, explanation = extract_patch(
        {
            "view": "dashboard",
            "dashboard": {"entityIds": [274, 292]},
            "explanation": "Plotted the two areas you asked about.",
        }
    )
    assert patch == {"view": "dashboard", "dashboard": {"entityIds": [274, 292]}}
    assert explanation == "Plotted the two areas you asked about."


def test_explanation_never_reaches_the_reducer():
    # The client validates ViewState strictly; an unknown `explanation` key
    # would fail the whole patch, so it must be stripped here.
    patch, _ = extract_patch({"view": "map", "explanation": "Switched to the map."})
    assert "explanation" not in patch


def test_missing_explanation_is_fine():
    patch, explanation = extract_patch({"view": "listing"})
    assert patch == {"view": "listing"}
    assert explanation is None


def test_empty_patch_is_rejected():
    with pytest.raises(PatchRejected, match="empty"):
        extract_patch({"explanation": "I changed nothing at all."})


def test_unknown_top_level_key_is_rejected():
    # Refused rather than filtered: a model asking for something we do not
    # support should be told so, not silently given a no-op.
    with pytest.raises(PatchRejected, match="Additional properties"):
        extract_patch({"view": "map", "database": {"drop": True}})


def test_unknown_nested_key_is_rejected_too():
    # additionalProperties: False applies at every level of the schema, not
    # just the top — a hand-rolled top-level-only check (the previous
    # implementation) would have let this straight through.
    with pytest.raises(PatchRejected, match="Additional properties"):
        extract_patch({"view": "dashboard", "dashboard": {"bogusField": 1}})


def test_invalid_enum_value_is_rejected_with_the_field_path():
    # The exact failure reported live: a model (a local Ollama model, less
    # reliable about respecting a schema's enum than Anthropic/OpenAI's
    # function calling) sent a dashboard.metric value outside
    # DASHBOARD_METRICS. This must be caught here, server-side, with enough
    # detail for the model to correct itself — not silently accepted and
    # only rejected later by the client's own zod validation, by which
    # point the turn has already ended and the model has no chance to retry.
    with pytest.raises(PatchRejected, match=r"dashboard\.metric"):
        extract_patch(
            {"view": "dashboard", "dashboard": {"metric": "not_a_real_metric"}}
        )


def test_valid_enum_values_across_all_three_views_pass(monkeypatch):
    # The success side of the same check — every documented enum value must
    # keep working, not just get rejected less.
    for tool_input in (
        {
            "view": "dashboard",
            "dashboard": {"metric": "gross_yield_pct", "transform": "yoy_pct"},
        },
        {"view": "map", "map": {"granularity": "buildings", "encoding": "height"}},
        {"view": "listing", "listing": {"entity": "rents"}},
    ):
        extract_patch(tool_input)  # must not raise


def test_non_object_input_is_rejected():
    with pytest.raises(PatchRejected):
        extract_patch(["view", "map"])  # type: ignore[arg-type]


def test_schema_documents_the_required_fact_filters():
    # Transactions and rents 422 without a scoping filter. If the model does
    # not know that, its first patch reliably produces a broken table.
    described = SET_VIEW_STATE.schema["properties"]["listing"]["properties"]["filters"][
        "description"
    ]
    assert "date_from" in described
    assert "start_date_from" in described
