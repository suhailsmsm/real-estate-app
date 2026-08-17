/**
 * The choropleth legend.
 *
 * Not decoration: a colour ramp with no numbers is unreadable, and a map
 * that mixes real polygons with fallback dots has to say so somewhere or the
 * distinction is invisible (task brief's honesty requirement). This is that
 * "somewhere" — it renders the actual data-derived bucket bounds and the
 * actual dot/polygon split, never invented numbers.
 */

import { formatCompact } from "../../ui/format";
import type { MapSemantics } from "../../core/viewstate";
import { CollapseToggle, useCollapse } from "../../ui/components";
import {
  buildColorScale,
  collectMetricValues,
  legendStops,
  METRIC_BY_SEMANTICS,
  NO_DATA_COLOR,
  partitionAreaFeatures,
} from "./styling";
import type { AreaFeatureCollection } from "./types";

function formatBound(v: number, unit: string): string {
  return unit === "%" ? `${v.toFixed(1)}%` : formatCompact(v);
}

export function Legend({
  data,
  semantics,
}: {
  data: AreaFeatureCollection | undefined;
  semantics: MapSemantics;
}) {
  const { polygons, dots } = partitionAreaFeatures(data);
  const all = [...polygons, ...dots];
  const values = collectMetricValues(all, semantics);
  const scale = buildColorScale(values);
  const spec = METRIC_BY_SEMANTICS[semantics];
  const withoutMetric = all.length - values.length;
  const [open, toggleOpen] = useCollapse();

  return (
    <div className="map-legend panel">
      <div className="map-legend-head">
        <span className="map-legend-title">
          {spec.label} <span className="faint">({spec.unit})</span>
        </span>
        <span className="spacer" />
        <CollapseToggle open={open} onClick={toggleOpen} label="legend" />
      </div>

      {!open ? null : (
        <>
          {scale ? (
            <div className="map-legend-ramp">
              {legendStops(scale).map((stop, i) => (
                <div className="map-legend-stop" key={i}>
                  <span className="map-legend-swatch" style={{ background: stop.color }} />
                  <span className="map-legend-range">
                    {formatBound(stop.from, spec.unit)}–{formatBound(stop.to, spec.unit)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="faint">No areas qualify under the current filters.</div>
          )}

          <div className="map-legend-stop">
            <span className="map-legend-swatch" style={{ background: NO_DATA_COLOR }} />
            <span className="map-legend-range">No qualifying data ({withoutMetric})</span>
          </div>

          <div className="map-legend-marks">
            <div className="row">
              <span className="map-legend-mark map-legend-mark-polygon" />
              <span>Boundary known — {polygons.length}</span>
            </div>
            <div className="row">
              <span className="map-legend-mark map-legend-mark-dot" />
              <span>
                No boundary, shown as a point — {dots.length} of {all.length}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
