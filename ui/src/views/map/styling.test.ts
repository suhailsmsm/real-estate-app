import { describe, expect, it } from "vitest";
import { defaultViewState } from "../../core/viewstate";
import type { AreaFeature, AreaFeatureCollection } from "./types";
import {
  buildAreasParams,
  buildBuildingsParams,
  buildColorScale,
  buildHeightScale,
  buildRadiusScale,
  collectMetricValues,
  colorForValue,
  DOT_RADIUS_FLAT,
  legendStops,
  metricValue,
  METRIC_BY_SEMANTICS,
  NO_DATA_COLOR,
  partitionAreaFeatures,
  RED_RAMP,
  sampleCount,
  styleAreaFeatures,
  toNumberOrNull,
} from "./styling";

// Shapes below mirror what `/geo/areas?min_sample=1` actually returns
// (verified against the running stack, not guessed) — see MapView's report.

function areaFeature(overrides: Partial<AreaFeature["properties"]> = {}, boundary = true): AreaFeature {
  return {
    type: "Feature",
    id: overrides.area_id ?? 1,
    geometry: boundary
      ? { type: "Polygon", coordinates: [] }
      : { type: "Point", coordinates: [55.3, 25.1] },
    properties: {
      area_id: 1,
      name_en: "AL FURJAN",
      dld_area_code: "C-4",
      zone_name: null,
      has_boundary: boundary,
      ...overrides,
    },
  };
}

describe("toNumberOrNull", () => {
  it("coerces a numeric string (the format most of this API actually uses)", () => {
    expect(toNumberOrNull("15613.70")).toBe(15613.7);
  });
  it("passes a real number through unchanged (what /geo/areas sends live)", () => {
    expect(toNumberOrNull(15633.8)).toBe(15633.8);
  });
  it("treats null, undefined and '' as absence", () => {
    expect(toNumberOrNull(null)).toBeNull();
    expect(toNumberOrNull(undefined)).toBeNull();
    expect(toNumberOrNull("")).toBeNull();
  });
  it("preserves a real zero rather than collapsing it to null", () => {
    expect(toNumberOrNull(0)).toBe(0);
    expect(toNumberOrNull("0")).toBe(0);
  });
  it("rejects garbage instead of returning NaN", () => {
    expect(toNumberOrNull("not-a-number")).toBeNull();
  });
});

describe("metric selection per semantics", () => {
  const props = {
    area_id: 1,
    name_en: "X",
    dld_area_code: null,
    zone_name: null,
    has_boundary: true,
    sale_median_price_m2: "15613.70",
    rent_median_annual_m2: "1033.26",
    gross_yield_pct: "6.61",
    sale_cnt: "20",
    rent_cnt: "105",
  };

  it("sales picks sale_median_price_m2", () => {
    expect(METRIC_BY_SEMANTICS.sales.field).toBe("sale_median_price_m2");
    expect(metricValue(props, "sales")).toBe(15613.7);
  });
  it("rents picks rent_median_annual_m2", () => {
    expect(METRIC_BY_SEMANTICS.rents.field).toBe("rent_median_annual_m2");
    expect(metricValue(props, "rents")).toBe(1033.26);
  });
  it("yield picks gross_yield_pct", () => {
    expect(METRIC_BY_SEMANTICS.yield.field).toBe("gross_yield_pct");
    expect(metricValue(props, "yield")).toBe(6.61);
  });
  it("sampleCount follows the same per-semantics field mapping", () => {
    expect(sampleCount(props, "sales")).toBe(20);
    expect(sampleCount(props, "rents")).toBe(105);
  });
  it("a feature missing the field entirely (no qualifying mart row) yields null, not 0", () => {
    expect(metricValue({ area_id: 2, name_en: "Y", dld_area_code: null, zone_name: null, has_boundary: true }, "sales")).toBeNull();
  });
});

describe("partitionAreaFeatures — the honesty split", () => {
  it("routes has_boundary:true to polygons and has_boundary:false to dots", () => {
    const fc: AreaFeatureCollection = {
      type: "FeatureCollection",
      features: [areaFeature({ area_id: 1 }, true), areaFeature({ area_id: 2 }, false)],
    };
    const { polygons, dots } = partitionAreaFeatures(fc);
    expect(polygons.map((f) => f.properties.area_id)).toEqual([1]);
    expect(dots.map((f) => f.properties.area_id)).toEqual([2]);
  });

  it("never drops a feature and never puts a boundary-less one in the polygon bucket", () => {
    const fc: AreaFeatureCollection = {
      type: "FeatureCollection",
      features: [
        areaFeature({ area_id: 1 }, true),
        areaFeature({ area_id: 2 }, false),
        areaFeature({ area_id: 3 }, false),
        areaFeature({ area_id: 4 }, true),
      ],
    };
    const { polygons, dots } = partitionAreaFeatures(fc);
    expect(polygons).toHaveLength(2);
    expect(dots).toHaveLength(2);
    expect(polygons.every((f) => f.properties.has_boundary)).toBe(true);
    expect(dots.every((f) => !f.properties.has_boundary)).toBe(true);
    // every input id is accounted for somewhere
    const seen = [...polygons, ...dots].map((f) => f.properties.area_id).sort();
    expect(seen).toEqual([1, 2, 3, 4]);
  });

  it("handles an empty or missing collection without throwing", () => {
    expect(partitionAreaFeatures(undefined)).toEqual({ polygons: [], dots: [] });
    expect(partitionAreaFeatures({ type: "FeatureCollection", features: [] })).toEqual({
      polygons: [],
      dots: [],
    });
  });
});

describe("colour scale domain", () => {
  it("is built only from present, coercible values — strings and numbers mixed", () => {
    const features = [
      areaFeature({ area_id: 1, sale_median_price_m2: "10000" }),
      areaFeature({ area_id: 2, sale_median_price_m2: 20000 }),
      areaFeature({ area_id: 3 }), // no metric at all
    ];
    const values = collectMetricValues(features, "sales");
    expect(values.sort((a, b) => a - b)).toEqual([10000, 20000]);
  });

  it("a mostly-string payload does not collapse the domain to NaN", () => {
    // This is the exact bug the coercion exists to prevent: formatting a raw
    // string with a scale built on garbage silently paints everything one
    // colour.
    const features = [
      areaFeature({ area_id: 1, sale_median_price_m2: "511.03" }),
      areaFeature({ area_id: 2, sale_median_price_m2: "102727.97" }),
    ];
    const scale = buildColorScale(collectMetricValues(features, "sales"));
    expect(scale).not.toBeNull();
    expect(scale!.domain().every((n) => Number.isFinite(n))).toBe(true);
  });

  it("returns null when nothing in view has a value", () => {
    expect(buildColorScale([])).toBeNull();
  });

  it("legend stops are the real data-derived bounds, using every ramp colour", () => {
    const scale = buildColorScale([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    const stops = legendStops(scale!);
    expect(stops).toHaveLength(RED_RAMP.length);
    expect(stops.map((s) => s.color)).toEqual(RED_RAMP);
    expect(stops[0].from).toBeLessThanOrEqual(stops[0].to);
  });
});

describe("colorForValue — no-data vs. zero", () => {
  it("a real zero (gross_yield_pct legitimately hits 0 live) gets a ramp colour, not the no-data grey", () => {
    const scale = buildColorScale([0, 5, 10, 15, 20]);
    const color = colorForValue(scale, 0);
    expect(color).not.toBe(NO_DATA_COLOR);
    expect(RED_RAMP).toContain(color);
  });

  it("a missing value (null) always renders as the no-data sentinel, never a ramp colour", () => {
    const scale = buildColorScale([0, 5, 10, 15, 20]);
    expect(colorForValue(scale, null)).toBe(NO_DATA_COLOR);
  });

  it("with no scale at all (nothing in view qualifies), everything is no-data", () => {
    expect(colorForValue(null, 5)).toBe(NO_DATA_COLOR);
  });
});

describe("styleAreaFeatures", () => {
  it("distinguishes a no-metric feature from a zero-metric feature in the injected properties", () => {
    const scale = buildColorScale([0, 10, 20]);
    const heightScale = buildHeightScale([0, 10, 20]);
    const radiusScale = buildRadiusScale([0, 10, 20]);
    const ctx = { semantics: "yield" as const, encoding: "color" as const, colorScale: scale, heightScale, radiusScale };

    const zero = styleAreaFeatures([areaFeature({ area_id: 1, gross_yield_pct: 0 })], ctx)[0];
    const missing = styleAreaFeatures([areaFeature({ area_id: 2 })], ctx)[0];

    expect(zero.properties._metricValue).toBe(0);
    expect(missing.properties._metricValue).toBeNull();
    expect(zero.properties._color).not.toBe(NO_DATA_COLOR);
    expect(missing.properties._color).toBe(NO_DATA_COLOR);
  });

  it("only extrudes/sizes by value in height encoding — color encoding uses the flat dot radius", () => {
    const heightScale = buildHeightScale([5, 10]);
    const radiusScale = buildRadiusScale([5, 10]);
    const colorCtx = {
      semantics: "sales" as const,
      encoding: "color" as const,
      colorScale: buildColorScale([5, 10]),
      heightScale,
      radiusScale,
    };
    const styled = styleAreaFeatures([areaFeature({ area_id: 1, sale_median_price_m2: 10 })], colorCtx)[0];
    expect(styled.properties._height).toBe(0);
    expect(styled.properties._radius).toBe(DOT_RADIUS_FLAT);
  });

  it("sizes dots (never extrudes them, since fill-extrusion needs polygon geometry) in height encoding", () => {
    const heightScale = buildHeightScale([5, 10]);
    const radiusScale = buildRadiusScale([5, 10]);
    const heightCtx = {
      semantics: "sales" as const,
      encoding: "height" as const,
      colorScale: buildColorScale([5, 10]),
      heightScale,
      radiusScale,
    };
    const styled = styleAreaFeatures([areaFeature({ area_id: 1, sale_median_price_m2: 10 }, false)], heightCtx)[0];
    expect(styled.properties._radius).toBeGreaterThan(DOT_RADIUS_FLAT);
  });
});

describe("query param building", () => {
  it("builds /geo/areas params from map state, dropping nulls", () => {
    const map = defaultViewState().map;
    expect(buildAreasParams(map)).toEqual({
      geo_level: "polygon",
      usage: undefined,
      month_from: undefined,
      month_to: undefined,
      min_sample: undefined,
    });
  });

  it("carries usage/month/min_sample through when set", () => {
    const map = {
      ...defaultViewState().map,
      usage: "Residential",
      monthFrom: "2025-01-01",
      monthTo: "2025-06-01",
      minSample: 5,
    };
    expect(buildAreasParams(map)).toEqual({
      geo_level: "polygon",
      usage: "Residential",
      month_from: "2025-01-01",
      month_to: "2025-06-01",
      min_sample: 5,
    });
  });

  it("scopes /geo/buildings to the selected area when there is one", () => {
    const map = { ...defaultViewState().map, selectedAreaId: 299 };
    expect(buildBuildingsParams(map)).toEqual({ area_id: 299 });
  });

  it("omits area_id when nothing is selected, so the request falls back to all buildings", () => {
    expect(buildBuildingsParams(defaultViewState().map)).toEqual({ area_id: undefined });
  });
});
