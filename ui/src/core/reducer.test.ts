import { describe, expect, it } from "vitest";
import { defaultColumns, missingRequiredFilter } from "./entities";
import { deepMerge, reducer, validatePatch } from "./reducer";
import { defaultViewState } from "./viewstate";

describe("deepMerge", () => {
  it("merges nested objects without dropping siblings", () => {
    const base = { a: 1, nested: { x: 1, y: 2 } };
    expect(deepMerge(base, { nested: { y: 99 } })).toEqual({ a: 1, nested: { x: 1, y: 99 } });
  });

  it("replaces arrays wholesale rather than merging by index", () => {
    // Index-merging would make it impossible to shorten a selection: patching
    // [1] onto [1,2,3] must mean "just 1", not "1,2,3 with the first replaced".
    expect(deepMerge({ ids: [1, 2, 3] }, { ids: [1] })).toEqual({ ids: [1] });
  });

  it("ignores undefined values so a patch can't blank a field by accident", () => {
    expect(deepMerge({ a: 1 }, { a: undefined })).toEqual({ a: 1 });
  });

  it("treats null as a value to set, not a merge target", () => {
    expect(deepMerge({ a: { b: 1 } as unknown }, { a: null })).toEqual({ a: null });
  });
});

describe("reducer — listing", () => {
  it("switching entity clears entity-specific filters, columns and sort", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setFilter", key: "txn_group", value: "Sales" });
    s = reducer(s, { type: "listing/setSort", sort: { column: "amount_aed", desc: true } });

    s = reducer(s, { type: "listing/setEntity", entity: "rents" });

    // txn_group is not a rents parameter — carrying it over would 422.
    expect(s.listing.filters.txn_group).toBeUndefined();
    expect(s.listing.sort).toBeNull();
    expect(s.listing.columns).toEqual(defaultColumns("rents"));
  });

  it("switching to a facts entity seeds the filter its endpoint requires", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setEntity", entity: "rents" });
    // Otherwise the very first request after the switch fails with a 422 and
    // the app looks broken through no fault of the user.
    expect(missingRequiredFilter("rents", s.listing.filters)).toBe(false);
  });

  it("switching to a dimension entity needs no seeded filter", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setEntity", entity: "areas" });
    expect(missingRequiredFilter("areas", s.listing.filters)).toBe(false);
    expect(s.listing.filters).toEqual({});
  });

  it("is a no-op when the entity is unchanged", () => {
    const s = defaultViewState();
    expect(reducer(s, { type: "listing/setEntity", entity: s.listing.entity })).toBe(s);
  });

  it("setting a filter returns to the first page", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setPage", offset: 200 });
    s = reducer(s, { type: "listing/setFilter", key: "usage", value: "Residential" });
    expect(s.listing.offset).toBe(0);
  });

  it("clearing a filter removes the key rather than storing an empty string", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "listing/setFilter", key: "usage", value: "Residential" });
    s = reducer(s, { type: "listing/setFilter", key: "usage", value: "" });
    // An empty string would be serialized into the query as `usage=`, which is
    // a different request from omitting it.
    expect("usage" in s.listing.filters).toBe(false);
  });

  it("never allows a negative page offset", () => {
    const s = reducer(defaultViewState(), { type: "listing/setPage", offset: -50 });
    expect(s.listing.offset).toBe(0);
  });
});

describe("reducer — view switching preserves each view's config", () => {
  it("keeps dashboard state when switching away and back", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "dashboard/set", patch: { entityIds: [274, 292] } });
    s = reducer(s, { type: "setView", view: "map" });
    s = reducer(s, { type: "setView", view: "dashboard" });
    expect(s.dashboard.entityIds).toEqual([274, 292]);
  });
});

describe("validatePatch — the copilot's front door", () => {
  it("accepts a well-formed patch", () => {
    const r = validatePatch(defaultViewState(), {
      view: "dashboard",
      dashboard: { entityIds: [274], metric: "gross_yield_pct" },
    });
    expect(r.ok).toBe(true);
    expect(r.state.dashboard.entityIds).toEqual([274]);
    expect(r.state.dashboard.metric).toBe("gross_yield_pct");
  });

  it("rejects an unknown enum value and leaves state untouched", () => {
    const before = defaultViewState();
    const r = validatePatch(before, {
      dashboard: { metric: "sale_price_per_furlong" as never },
    });
    expect(r.ok).toBe(false);
    expect(r.state).toBe(before);
    expect(r.error).toMatch(/dashboard\.metric/);
  });

  it("rejects a wrong-typed field", () => {
    const r = validatePatch(defaultViewState(), {
      listing: { limit: "fifty" as never },
    });
    expect(r.ok).toBe(false);
  });

  it("rejects out-of-range values rather than clamping them silently", () => {
    // 10_000 rows would be a denial-of-service against our own browser.
    expect(validatePatch(defaultViewState(), { listing: { limit: 10000 } }).ok).toBe(false);
  });

  it("applies a patch wholly or not at all", () => {
    const before = defaultViewState();
    const r = validatePatch(before, {
      // First half valid, second half not — neither may land.
      view: "map",
      map: { granularity: "continents" as never },
    });
    expect(r.ok).toBe(false);
    expect(r.state.view).toBe(before.view);
  });

  it("merges rather than replacing untouched siblings", () => {
    let s = defaultViewState();
    s = reducer(s, { type: "dashboard/set", patch: { usage: "Residential" } });
    const r = validatePatch(s, { dashboard: { entityIds: [1] } });
    expect(r.ok).toBe(true);
    expect(r.state.dashboard.usage).toBe("Residential");
  });
});
