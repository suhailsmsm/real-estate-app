/** One small card per selected entity: latest value, YoY change, sample size. */

import { SampleSize } from "../../ui/components";
import { formatPct } from "../../ui/format";
import { formatMetricLevel } from "./metricMeta";
import { colorForEntity, type EntitySeries } from "./transforms";
import type { DashboardMetric } from "../../core/viewstate";

function TrendBadge({ pct }: { pct: number | null }) {
  if (pct === null) return <span className="muted">–</span>;
  const cls = pct > 0 ? "trend-up" : pct < 0 ? "trend-down" : "";
  const sign = pct > 0 ? "+" : "";
  return <span className={cls}>{sign}{formatPct(pct)} YoY</span>;
}

export function SummaryStrip({ series, metric }: { series: EntitySeries[]; metric: DashboardMetric }) {
  if (series.length === 0) return null;
  return (
    <div className="summary-strip">
      {series.map((s) => (
        <div key={s.entityId} className="summary-card">
          <div className="row" style={{ gap: 6 }}>
            <span className="chip-swatch" style={{ background: colorForEntity(s.entityId) }} />
            <strong className="summary-card-name">{s.name}</strong>
          </div>
          {s.latestValue === null ? (
            <span className="muted">No data</span>
          ) : (
            <>
              <div className="summary-card-value">{formatMetricLevel(metric, s.latestValue)}</div>
              <div className="row" style={{ gap: 8, justifyContent: "space-between" }}>
                <TrendBadge pct={s.yoyChangePct} />
                <SampleSize n={s.sampleSize} />
              </div>
              {s.latestMonth ? <span className="faint">as of {s.latestMonth.slice(0, 7)}</span> : null}
            </>
          )}
        </div>
      ))}
    </div>
  );
}
