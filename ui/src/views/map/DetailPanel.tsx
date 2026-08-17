/**
 * Detail panel for the clicked area.
 *
 * Looks the feature up in whatever `/geo/areas` payload is already loaded
 * rather than firing a second request — the map already has everything this
 * needs (name, code, latest metrics) as feature properties.
 */

import { Badge, Panel, SampleSize } from "../../ui/components";
import { formatCompact, formatMoney, formatPct } from "../../ui/format";
import { metricValue, sampleCount, METRIC_BY_SEMANTICS } from "./styling";
import type { AreaFeatureCollection } from "./types";
import type { MapSemantics } from "../../core/viewstate";

export function DetailPanel({
  data,
  areaId,
  semantics,
  onClose,
}: {
  data: AreaFeatureCollection | undefined;
  areaId: number;
  semantics: MapSemantics;
  onClose: () => void;
}) {
  const feature = data?.features.find((f) => f.properties.area_id === areaId);

  if (!feature) {
    // The clicked feature came from a source built from this same payload,
    // so this only happens mid-refetch — not worth a full ErrorNote for a
    // state that resolves itself a moment later.
    return (
      <Panel title="Area" actions={<button className="btn subtle sm" onClick={onClose}>Close</button>}>
        <span className="faint">Loading…</span>
      </Panel>
    );
  }

  const p = feature.properties;
  const spec = METRIC_BY_SEMANTICS[semantics];
  const value = metricValue(p, semantics);
  const n = sampleCount(p, semantics);

  return (
    <Panel
      title={p.name_en}
      actions={
        <button className="btn subtle sm" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="stack">
        <div className="row" style={{ flexWrap: "wrap" }}>
          {p.dld_area_code ? <Badge>{p.dld_area_code}</Badge> : null}
          <Badge tone={p.has_boundary ? "ok" : "warn"}>
            {p.has_boundary ? "boundary" : "point only"}
          </Badge>
          {p.zone_name ? <Badge>{p.zone_name}</Badge> : null}
        </div>

        <div className="map-detail-metrics">
          <div className="map-detail-metric">
            <span className="map-field-label">{spec.label}</span>
            <span className="map-detail-value">
              {value === null ? "–" : spec.unit === "%" ? formatPct(value) : formatCompact(value)}
              <span className="faint"> {spec.unit}</span>
            </span>
            <SampleSize n={n} />
          </div>

          {p.metric_month ? (
            <div className="faint">
              As of {p.metric_month}
              {p.usage ? ` · ${p.usage}` : ""}
            </div>
          ) : (
            <div className="faint">No qualifying mart data for the current filters.</div>
          )}
        </div>

        {p.metric_month ? (
          <div className="map-detail-all-metrics">
            <div>
              <span className="map-field-label">Sale</span>
              <span>{formatMoney(p.sale_median_price_m2)} AED/m²</span>
              <SampleSize n={sampleCount(p, "sales")} />
            </div>
            <div>
              <span className="map-field-label">Rent</span>
              <span>{formatMoney(p.rent_median_annual_m2)} AED/m²/yr</span>
              <SampleSize n={sampleCount(p, "rents")} />
            </div>
            <div>
              <span className="map-field-label">Yield</span>
              <span>{formatPct(p.gross_yield_pct)}</span>
            </div>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
