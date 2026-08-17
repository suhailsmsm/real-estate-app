import { describe, expect, it } from "vitest";
import { reducer } from "./reducer";
import { decodeViewState, encodeViewState } from "./url";
import { defaultViewState } from "./viewstate";

/** encode → decode must be lossless for anything a user can reach. */
function roundTrip(s: ReturnType<typeof defaultViewState>) {
  return decodeViewState(encodeViewState(s));
}

describe("URL codec", () => {
  it("encodes only the time-relative default, not every default field", () => {
    const qs = encodeViewState(defaultViewState());
    // The default listing filter is "the last 12 months", which is relative to
    // *today*. Pinning it into the link is deliberate: a URL shared today and
    // opened next month must show the same rows, not silently slide its window.
    // Everything else is at its default and is omitted, keeping links short.
    expect(qs).toMatch(/^\?f\.date_from=\d{4}-\d{2}-\d{2}$/);
  });

  it("omits view-level fields that are at their defaults", () => {
    const qs = encodeViewState(defaultViewState());
    expect(qs).not.toContain("view=");
    expect(qs).not.toContain("entity=");
    expect(qs).not.toContain("limit=");
  });

  it("round-trips a configured listing view", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setEntity", entity: "transactions" });
    s = reducer(s, { type: "listing/setFilter", key: "area_id", value: 274 });
    s = reducer(s, { type: "listing/setFilter", key: "usage", value: "Residential" });
    s = reducer(s, { type: "listing/setSort", sort: { column: "amount_aed", desc: true } });
    s = reducer(s, { type: "listing/setColumns", columns: ["txn_date", "amount_aed"] });

    const back = roundTrip(s);
    expect(back.listing.filters.area_id).toBe(274);
    expect(back.listing.filters.usage).toBe("Residential");
    expect(back.listing.sort).toEqual({ column: "amount_aed", desc: true });
    expect(back.listing.columns).toEqual(["txn_date", "amount_aed"]);
  });

  it("preserves numeric filter types across the round trip", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setFilter", key: "price_min", value: 500000 });
    const back = roundTrip(s);
    // A string "500000" would be a different request and would break numeric
    // comparisons downstream.
    expect(back.listing.filters.price_min).toBe(500000);
    expect(typeof back.listing.filters.price_min).toBe("number");
  });

  it("preserves boolean filter types across the round trip", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setFilter", key: "is_offplan", value: true });
    expect(roundTrip(s).listing.filters.is_offplan).toBe(true);
  });

  it("round-trips a dashboard comparison", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "setView", view: "dashboard" });
    s = reducer(s, {
      type: "dashboard/set",
      patch: { entityIds: [274, 292, 318], metric: "gross_yield_pct", transform: "yoy_pct" },
    });
    const back = roundTrip(s);
    expect(back.view).toBe("dashboard");
    expect(back.dashboard.entityIds).toEqual([274, 292, 318]);
    expect(back.dashboard.metric).toBe("gross_yield_pct");
    expect(back.dashboard.transform).toBe("yoy_pct");
  });

  it("round-trips the map camera within display precision", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "setView", view: "map" });
    s = reducer(s, {
      type: "map/set",
      patch: {
        encoding: "height",
        semantics: "yield",
        viewport: { lng: 55.1234, lat: 25.0987, zoom: 12.5, pitch: 45, bearing: 30 },
      },
    });
    const back = roundTrip(s);
    expect(back.map.encoding).toBe("height");
    expect(back.map.semantics).toBe("yield");
    expect(back.map.viewport.lng).toBeCloseTo(55.1234, 3);
    expect(back.map.viewport.pitch).toBe(45);
  });

  it("falls back to defaults for an unparseable link rather than throwing", () => {
    // A URL is untrusted input a user can hand-edit; a bad one should open the
    // app, not show an error page.
    expect(() => decodeViewState("?view=telepathy&entity=💥&limit=NaN")).not.toThrow();
    const s = decodeViewState("?view=telepathy&limit=NaN");
    expect(s.view).toBe(defaultViewState().view);
  });

  it("ignores an out-of-range value from a hand-edited URL", () => {
    const s = decodeViewState("?view=listing&limit=999999");
    expect(s.listing.limit).toBe(defaultViewState().listing.limit);
  });

  it("keeps filter keys namespaced so they cannot collide with view keys", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setFilter", key: "q", value: "marina" });
    // A bare `?q=` would be ambiguous with a future top-level search param.
    expect(encodeViewState(s)).toContain("f.q=marina");
  });
});
