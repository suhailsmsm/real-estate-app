"""The UI half of the copilot's tool surface.

The data half comes from the MCP server (seven curated analytics tools). This
module adds the tools that let the model *show* its answer, not just say it:
"which areas grew fastest last year?" should reconfigure the dashboard to plot
exactly those areas alongside the prose.

The mechanism is deliberately narrow. There is exactly one way to change the
UI — emit a ViewState patch — and it travels the same path a user's click
takes: through the client's reducer, validated by the same zod schema, landing
in the same undo history (docs/UI_PLAN.md §5). There is no privileged back
door, so there is nothing the copilot can do that a user could not do and
cannot undo.

The schema below mirrors `ui/src/core/viewstate.ts`. It is duplicated rather
than generated because the two serve different masters: this one teaches a
model what is worth asking for, the TypeScript one is the enforcement boundary.
The client re-validates every patch regardless of what this says, so a drift
between them degrades to "the model proposed something invalid and was told
no" — never to an invalid UI state.
"""

from __future__ import annotations

from typing import Any

import jsonschema

from .providers.base import ToolSchema

LISTING_ENTITIES = ["transactions", "rents", "buildings", "projects", "areas"]
DASHBOARD_METRICS = [
    "sale_median_price_m2",
    "rent_median_annual_m2",
    "gross_yield_pct",
    "sale_cnt",
    "rent_cnt",
]
DASHBOARD_TRANSFORMS = ["level", "mom_pct", "yoy_pct"]
MAP_GRANULARITIES = ["areas", "projects", "buildings"]
MAP_SEMANTICS = ["sales", "rents", "yield"]
MAP_ENCODINGS = ["color", "height"]

_LISTING_PATCH = {
    "type": "object",
    "description": "Table view configuration.",
    "properties": {
        "entity": {"type": "string", "enum": LISTING_ENTITIES},
        "filters": {
            "type": "object",
            "description": (
                "Filter values, using the REST API's own parameter names "
                "(area_id, project_id, date_from, date_to, usage, txn_group, "
                "price_min, price_max, q, ...). Transactions and rents REQUIRE "
                "at least one of their scoping filters or the request is "
                "rejected: transactions need one of area_id/building_id/"
                "date_from/project_id; rents need one of area_id/project_id/"
                "start_date_from."
            ),
            "additionalProperties": True,
        },
        "columns": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Visible column ids, in order. Omit to keep the current set."
            ),
        },
        "sort": {
            "type": ["object", "null"],
            "properties": {
                "column": {"type": "string"},
                "desc": {"type": "boolean"},
            },
            "required": ["column", "desc"],
            "additionalProperties": False,
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        "offset": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}

_DASHBOARD_PATCH = {
    "type": "object",
    "description": "Trend and comparison chart configuration.",
    "properties": {
        "entityType": {"type": "string", "enum": ["area", "project"]},
        "entityIds": {
            "type": "array",
            "items": {"type": "integer"},
            "description": (
                "Numeric ids to plot, one series each. Resolve names to ids "
                "with the find_entity tool first — never guess an id."
            ),
        },
        "metric": {"type": "string", "enum": DASHBOARD_METRICS},
        "transform": {"type": "string", "enum": DASHBOARD_TRANSFORMS},
        "monthFrom": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "monthTo": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "usage": {"type": ["string", "null"]},
        "minSample": {"type": ["integer", "null"], "minimum": 0},
        "includeFuture": {"type": "boolean"},
    },
    "additionalProperties": False,
}

_MAP_PATCH = {
    "type": "object",
    "description": "Map configuration.",
    "properties": {
        "granularity": {"type": "string", "enum": MAP_GRANULARITIES},
        "semantics": {"type": "string", "enum": MAP_SEMANTICS},
        "encoding": {
            "type": "string",
            "enum": MAP_ENCODINGS,
            "description": (
                "'color' is a flat choropleth; 'height' extrudes areas in 3D."
            ),
        },
        "monthFrom": {"type": ["string", "null"]},
        "monthTo": {"type": ["string", "null"]},
        "usage": {"type": ["string", "null"]},
        "minSample": {"type": ["integer", "null"], "minimum": 0},
        "selectedAreaId": {"type": ["integer", "null"]},
    },
    "additionalProperties": False,
}

SET_VIEW_STATE = ToolSchema(
    name="set_view_state",
    description=(
        "Reconfigure the user interface to illustrate your answer. Send only "
        "the fields you want to change — everything else is left alone.\n\n"
        "Use this when showing beats telling: after ranking areas, plot them; "
        "after finding a project, filter the table to it; when the question is "
        "geographic, switch to the map. Do not call it for a question that is "
        "fully answered in one sentence, and do not call it more than once for "
        "the same answer.\n\n"
        "The user sees the change immediately and can undo it in one click, so "
        "prefer being helpful over being cautious — but never use it to hide "
        "an inconvenient result."
    ),
    schema={
        "type": "object",
        "properties": {
            "view": {
                "type": "string",
                "enum": ["listing", "dashboard", "map"],
                "description": "Which view to show.",
            },
            "listing": _LISTING_PATCH,
            "dashboard": _DASHBOARD_PATCH,
            "map": _MAP_PATCH,
            "explanation": {
                "type": "string",
                "description": (
                    "One short sentence for the user about what you changed and "
                    "why, e.g. 'Plotted the five fastest-growing areas.'"
                ),
            },
        },
        "additionalProperties": False,
    },
)

UI_TOOLS: list[ToolSchema] = [SET_VIEW_STATE]
UI_TOOL_NAMES = {t.name for t in UI_TOOLS}


class PatchRejected(ValueError):
    """A UI patch that failed server-side sanity checks."""


def extract_patch(tool_input: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Split a `set_view_state` call into (patch, explanation).

    `explanation` is prose for the user and must not reach the reducer — it is
    not part of ViewState, and a strict client validator rejects the whole
    patch if it arrives with unknown keys.

    Validated here against the exact same schema declared to the model
    (`SET_VIEW_STATE.schema`) — every field type, enum, and
    `additionalProperties: False` constraint, at every nesting level, not
    just the top one. This used to be a much shallower hand-rolled check
    (top-level key names only), which meant a bad value anywhere below the
    top level — e.g. an invented `dashboard.metric` — sailed straight
    through: the model was told "Patch sent to the interface" as if it had
    succeeded, and only the *client's* own zod validation ever caught it,
    by which point the turn had already ended with no way to tell the model
    to retry. Anthropic's and OpenAI's function calling mostly (not always)
    honors a schema's enum constraints on its own; local models served
    through Ollama are considerably less reliable about it, which is why
    this went unnoticed until a local-model chat actually exercised it.
    Validating the identical schema server-side, rather than duplicating
    the enum lists in a second hand-written check, is what keeps this from
    drifting out of sync with what the model was actually told.
    """
    if not isinstance(tool_input, dict):
        raise PatchRejected("Tool input must be an object")

    try:
        jsonschema.validate(instance=tool_input, schema=SET_VIEW_STATE.schema)
    except jsonschema.ValidationError as exc:
        path = ".".join(str(p) for p in exc.path) or "<root>"
        raise PatchRejected(f"Invalid value at {path}: {exc.message}") from exc

    explanation = tool_input.get("explanation")
    patch = {k: v for k, v in tool_input.items() if k != "explanation"}

    if not patch:
        raise PatchRejected("Patch is empty — nothing to change")

    return patch, explanation if isinstance(explanation, str) else None
