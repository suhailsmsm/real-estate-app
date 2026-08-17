/**
 * The controls overlay: every knob in `state.map` except the camera (which
 * is driven by the map itself, see MapView's `moveend` handler) and
 * `selectedAreaId` (driven by clicks, see DetailPanel).
 *
 * Every control here dispatches `map/set` directly, its value always exactly
 * what's in ViewState — which is what makes a shared URL reproduce the same
 * panel. The one exception is whether the panel itself is folded: that's
 * transient UI chrome, not part of what's being shown, so it's ordinary
 * local state (`useCollapse`) rather than another ViewState field.
 */

import { useUsages } from "../../core/queries";
import { useStore } from "../../core/store";
import {
  MAP_ENCODINGS,
  MAP_GRANULARITIES,
  MAP_SEMANTICS,
  type MapEncoding,
  type MapGranularity,
  type MapSemantics,
} from "../../core/viewstate";
import { CollapseToggle, DateField, Field, useCollapse } from "../../ui/components";

const SEMANTICS_LABEL: Record<MapSemantics, string> = {
  sales: "Sales",
  rents: "Rents",
  yield: "Yield",
};

const GRANULARITY_LABEL: Record<MapGranularity, string> = {
  areas: "Areas",
  projects: "Projects",
  buildings: "Buildings",
};

const ENCODING_LABEL: Record<MapEncoding, string> = {
  color: "Colour",
  height: "Height (3D)",
};

export function Controls() {
  const map = useStore((s) => s.state.map);
  const dispatch = useStore((s) => s.dispatch);
  const { data: usages } = useUsages();
  const [open, toggleOpen] = useCollapse();

  return (
    <div className="map-controls panel">
      <div className="map-controls-head">
        <span className="panel-title">Filters</span>
        <span className="spacer" />
        <CollapseToggle open={open} onClick={toggleOpen} label="filters" />
      </div>
      {!open ? null : (
      <div className="map-controls-grid">
        <Field label="Semantics">
          <select
            aria-label="Semantics"
            value={map.semantics}
            onChange={(e) =>
              dispatch({ type: "map/set", patch: { semantics: e.target.value as MapSemantics } })
            }
          >
            {MAP_SEMANTICS.map((s) => (
              <option key={s} value={s}>
                {SEMANTICS_LABEL[s]}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Granularity">
          <select
            aria-label="Granularity"
            value={map.granularity}
            onChange={(e) =>
              dispatch({
                type: "map/set",
                patch: { granularity: e.target.value as MapGranularity },
              })
            }
          >
            {MAP_GRANULARITIES.map((g) => (
              <option key={g} value={g}>
                {GRANULARITY_LABEL[g]}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Encoding" help={map.encoding === "height" ? "Dots without a boundary are sized, not extruded" : undefined}>
          <div className="row" role="group" aria-label="Encoding">
            {MAP_ENCODINGS.map((enc) => (
              <button
                key={enc}
                type="button"
                className="btn sm"
                aria-pressed={map.encoding === enc}
                onClick={() => {
                  if (enc === map.encoding) return;
                  // A 3D encoding is invisible from directly overhead — bump
                  // the pitch when switching in, but never fight a pitch the
                  // user already set (or reset it going back to flat colour;
                  // that's the user's camera to keep).
                  const viewport =
                    enc === "height" && map.viewport.pitch === 0
                      ? { ...map.viewport, pitch: 55 }
                      : map.viewport;
                  dispatch({ type: "map/set", patch: { encoding: enc, viewport } });
                }}
              >
                {ENCODING_LABEL[enc]}
              </button>
            ))}
          </div>
        </Field>

        <Field label="Usage">
          <select
            aria-label="Usage"
            value={map.usage ?? ""}
            onChange={(e) =>
              dispatch({ type: "map/set", patch: { usage: e.target.value || null } })
            }
          >
            <option value="">All usages</option>
            {(usages ?? []).map((u) => (
              <option key={u.usage} value={u.usage}>
                {u.usage}
              </option>
            ))}
          </select>
        </Field>

        <DateField
          label="From"
          value={map.monthFrom}
          onChange={(monthFrom) => dispatch({ type: "map/set", patch: { monthFrom } })}
        />
        <DateField
          label="To"
          value={map.monthTo}
          onChange={(monthTo) => dispatch({ type: "map/set", patch: { monthTo } })}
        />

        <Field label="Min sample" help="Areas below this sale count are excluded server-side">
          <input
            aria-label="Min sample"
            type="number"
            min={0}
            value={map.minSample ?? ""}
            placeholder="default"
            onChange={(e) => {
              const v = e.target.value;
              dispatch({
                type: "map/set",
                patch: { minSample: v === "" ? null : Math.max(0, Number(v)) },
              });
            }}
          />
        </Field>
      </div>
      )}
    </div>
  );
}
