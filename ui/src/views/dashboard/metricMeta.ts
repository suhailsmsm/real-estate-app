/** Presentation metadata for the five dashboard metrics — labels, units, formatting. */

import { formatCompact, formatInt, formatPct } from "../../ui/format";
import type { DashboardMetric, DashboardTransform } from "../../core/viewstate";

export const METRIC_LABELS: Record<DashboardMetric, string> = {
  sale_median_price_m2: "Sale price / m2",
  rent_median_annual_m2: "Rent / m2 / year",
  gross_yield_pct: "Gross yield",
  sale_cnt: "Sale count",
  rent_cnt: "Rent count",
};

export const METRIC_UNITS: Record<DashboardMetric, string> = {
  sale_median_price_m2: "AED/m2",
  rent_median_annual_m2: "AED/m2/yr",
  gross_yield_pct: "%",
  sale_cnt: "sales",
  rent_cnt: "contracts",
};

export const TRANSFORM_LABELS: Record<DashboardTransform, string> = {
  level: "Level",
  mom_pct: "Month over month %",
  yoy_pct: "Year over year %",
};

/** Formats a *level* value (never a mom/yoy percentage — that's always formatPct). */
export function formatMetricLevel(metric: DashboardMetric, v: number | null): string {
  if (v === null) return "–";
  switch (metric) {
    case "gross_yield_pct":
      return formatPct(v);
    case "sale_cnt":
    case "rent_cnt":
      return formatInt(v);
    default:
      return formatCompact(v);
  }
}

/** Y-axis tick formatter for the chart, which depends on the transform, not just the metric. */
export function axisTickFormat(metric: DashboardMetric, transform: DashboardTransform) {
  if (transform !== "level") return (v: number) => formatPct(v);
  return (v: number) => formatMetricLevel(metric, v);
}
