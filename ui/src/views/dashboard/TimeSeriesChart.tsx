/**
 * The comparison chart. One Plot.line per selected entity, month on x.
 *
 * Plot needs a real DOM to measure text and lay out axes, so this renders
 * imperatively into a ref rather than through JSX: build the plot, append
 * it, and — critically — remove it on cleanup. Without that cleanup every
 * re-render (a filter change, a resize) would stack another SVG on top of
 * the last one instead of replacing it.
 */

import * as Plot from "@observablehq/plot";
import { useEffect, useRef, useState } from "react";
import type { DashboardMetric, DashboardTransform } from "../../core/viewstate";
import { Note } from "../../ui/components";
import { axisTickFormat, METRIC_LABELS, METRIC_UNITS, TRANSFORM_LABELS } from "./metricMeta";
import { colorForEntity, type EntitySeries } from "./transforms";

function yLabel(metric: DashboardMetric, transform: DashboardTransform): string {
  if (transform === "level") return `${METRIC_LABELS[metric]} (${METRIC_UNITS[metric]})`;
  return `${METRIC_LABELS[metric]} · ${TRANSFORM_LABELS[transform]}`;
}

export function TimeSeriesChart({
  series,
  metric,
  transform,
}: {
  series: EntitySeries[];
  metric: DashboardMetric;
  transform: DashboardTransform;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);

  // Keep the plot's pixel width in step with its container rather than a
  // fixed number — the panel can be resized by the browser window, not just
  // by this component.
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) setWidth(Math.max(280, Math.round(w)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const tickFormat = axisTickFormat(metric, transform);
    const lines = series
      .filter((s) => s.points.length > 0)
      .map((s) =>
        Plot.line(s.points, {
          x: (p) => new Date(p.month),
          y: "value",
          stroke: colorForEntity(s.entityId),
          strokeWidth: 2,
          // Plot breaks the line at any point whose y channel is null/NaN —
          // that's the mechanism gap-filling in transforms.ts relies on to
          // render an absence as an absence, not an interpolated slope.
          curve: "linear",
        }),
      );

    const marks: Plot.Markish[] = [];
    if (transform !== "level") marks.push(Plot.ruleY([0], { stroke: "var(--edge-strong)" }));
    marks.push(...lines);

    const plot = Plot.plot({
      width,
      height: 340,
      marginLeft: 64,
      x: { type: "utc", label: null },
      y: { label: yLabel(metric, transform), tickFormat, grid: true },
      marks,
    });

    el.replaceChildren(plot);
    return () => {
      plot.remove();
    };
  }, [series, metric, transform, width]);

  const hasAnyData = series.some((s) => s.points.some((p) => p.value !== null));

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div ref={containerRef} style={{ width: "100%" }} />
      {!hasAnyData ? (
        <Note tone="info">No data for the current filters — try widening the month range or usage.</Note>
      ) : null}
    </div>
  );
}
