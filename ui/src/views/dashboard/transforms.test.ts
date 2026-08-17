import { describe, expect, it } from "vitest";
import {
  buildDashboardSeries,
  colorForEntity,
  computeChangeSeries,
  metricSampleSize,
  metricValue,
  type MartRow,
} from "./transforms";

function row(overrides: Partial<MartRow> = {}): MartRow {
  return {
    entity_id: 1,
    name_en: "TEST AREA",
    month: "2024-01-01",
    usage: "Residential",
    sale_cnt: 12,
    sale_median_price_m2: "15613.70",
    sale_p25_price_m2: null,
    sale_p75_price_m2: null,
    rent_cnt: 8,
    rent_median_annual_m2: "980.50",
    gross_yield_pct: "6.28",
    ...overrides,
  };
}

describe("metricValue", () => {
  it("coerces the API's stringified numerics rather than passing the string through", () => {
    const r = row();
    expect(metricValue(r, "sale_median_price_m2")).toBe(15613.7);
    expect(typeof metricValue(r, "sale_median_price_m2")).toBe("number");
    expect(metricValue(r, "gross_yield_pct")).toBe(6.28);
  });

  it("returns null for a missing value rather than NaN", () => {
    expect(metricValue(row({ sale_median_price_m2: null }), "sale_median_price_m2")).toBeNull();
  });
});

describe("metricSampleSize", () => {
  it("takes the weaker leg's count for the yield ratio", () => {
    expect(metricSampleSize(row({ sale_cnt: 20, rent_cnt: 5 }), "gross_yield_pct")).toBe(5);
    expect(metricSampleSize(row({ sale_cnt: 3, rent_cnt: 40 }), "gross_yield_pct")).toBe(3);
  });

  it("uses the matching count for sale/rent metrics", () => {
    expect(metricSampleSize(row({ sale_cnt: 20 }), "sale_median_price_m2")).toBe(20);
    expect(metricSampleSize(row({ rent_cnt: 8 }), "rent_median_annual_m2")).toBe(8);
  });
});

describe("computeChangeSeries", () => {
  it("computes percentage change at the given lag", () => {
    const result = computeChangeSeries([100, 110, 121], 1);
    expect(result[0]).toBeNull();
    expect(result[1]).toBeCloseTo(10, 5);
    expect(result[2]).toBeCloseTo(10, 5);
  });

  it("guards against divide-by-zero instead of returning Infinity/NaN", () => {
    expect(computeChangeSeries([0, 50, 100], 1)).toEqual([null, null, 100]);
  });

  it("propagates a gap as a break (null), never interpolating across it", () => {
    expect(computeChangeSeries([100, null, 120], 1)).toEqual([null, null, null]);
  });

  it("returns null before the series is long enough to have a comparison point", () => {
    expect(computeChangeSeries([100, 110], 12)).toEqual([null, null]);
  });
});

describe("buildDashboardSeries", () => {
  it("coerces every point's string value into a number", () => {
    const rows = [
      row({ month: "2024-01-01", sale_median_price_m2: "15613.70" }),
      row({ month: "2024-02-01", sale_median_price_m2: "16000.00" }),
    ];
    const [series] = buildDashboardSeries(rows, [1], new Map(), "sale_median_price_m2", "level");
    expect(series.points.map((p) => p.value)).toEqual([15613.7, 16000]);
    expect(series.points.every((p) => p.value === null || typeof p.value === "number")).toBe(true);
  });

  it("fills a missing month with a null value, breaking the line instead of skipping the gap", () => {
    const rows = [row({ month: "2024-01-01" }), row({ month: "2024-03-01" })]; // February missing
    const [series] = buildDashboardSeries(rows, [1], new Map(), "sale_median_price_m2", "level");
    expect(series.points.map((p) => p.month)).toEqual(["2024-01-01", "2024-02-01", "2024-03-01"]);
    expect(series.points[1].value).toBeNull();
  });

  it("computes YoY off the gap-filled level series so the 12-month lag lines up with the real calendar", () => {
    const rows = [
      row({ month: "2023-01-01", sale_median_price_m2: "10000" }),
      row({ month: "2024-01-01", sale_median_price_m2: "11000" }),
    ];
    const [series] = buildDashboardSeries(rows, [1], new Map(), "sale_median_price_m2", "yoy_pct");
    const last = series.points[series.points.length - 1];
    expect(last.month).toBe("2024-01-01");
    expect(last.value).toBeCloseTo(10, 5);
    // The 11 filled-but-empty months in between must stay breaks, not 0%.
    expect(series.points.slice(1, -1).every((p) => p.value === null)).toBe(true);
  });

  it("returns an empty series for an entity with no rows, not an error", () => {
    const [series] = buildDashboardSeries([], [999], new Map(), "sale_median_price_m2", "level");
    expect(series.points).toEqual([]);
    expect(series.latestValue).toBeNull();
    expect(series.sampleSize).toBeNull();
  });

  it("names an entity from the provided map, falling back to the row, then the bare id", () => {
    const rows = [row({ entity_id: 5, name_en: "FROM ROW" })];
    const fromMap = buildDashboardSeries(rows, [5], new Map([[5, "FROM MAP"]]), "sale_median_price_m2", "level");
    expect(fromMap[0].name).toBe("FROM MAP");

    const fromRow = buildDashboardSeries(rows, [5], new Map(), "sale_median_price_m2", "level");
    expect(fromRow[0].name).toBe("FROM ROW");

    const fromId = buildDashboardSeries([], [42], new Map(), "sale_median_price_m2", "level");
    expect(fromId[0].name).toBe("#42");
  });

  it("picks the better-sampled row when more than one usage lands on the same entity/month", () => {
    const rows = [
      row({ month: "2024-01-01", usage: "Residential", sale_cnt: 3, sale_median_price_m2: "9000" }),
      row({ month: "2024-01-01", usage: "Commercial", sale_cnt: 30, sale_median_price_m2: "20000" }),
    ];
    const [series] = buildDashboardSeries(rows, [1], new Map(), "sale_median_price_m2", "level");
    expect(series.points).toHaveLength(1);
    expect(series.points[0].value).toBe(20000);
  });

  it("reports latest value, month and sample size from the level series regardless of the chart's transform", () => {
    const rows = [row({ month: "2024-01-01", sale_cnt: 42, sale_median_price_m2: "12345.6" })];
    const [series] = buildDashboardSeries(rows, [1], new Map(), "sale_median_price_m2", "mom_pct");
    expect(series.latestValue).toBe(12345.6);
    expect(series.latestMonth).toBe("2024-01-01");
    expect(series.sampleSize).toBe(42);
  });
});

describe("colorForEntity", () => {
  it("is stable for a given id across calls", () => {
    expect(colorForEntity(7)).toBe(colorForEntity(7));
  });

  it("gives different entities visually distinct colours", () => {
    expect(colorForEntity(1)).not.toBe(colorForEntity(2));
  });
});
