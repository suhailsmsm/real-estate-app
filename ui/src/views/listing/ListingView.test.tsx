import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../core/store";
import { defaultViewState, type ListingState } from "../../core/viewstate";
import { ListingView } from "./ListingView";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = [RegExp, () => { body: unknown; status?: number }];

/**
 * Routes the fetch mock by URL. Anything not explicitly stubbed (the entity
 * pickers' and usage dropdown's own background searches, mainly) gets a
 * harmless empty Page — the tests below only care about the entity's main
 * request and don't want to hand-stub every incidental dropdown query.
 */
function stubFetch(handlers: Handler[]) {
  const fn = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    for (const [re, handler] of handlers) {
      if (re.test(url)) {
        const { body, status } = handler();
        return Promise.resolve(jsonResponse(body, status));
      }
    }
    // A BARE ARRAY, not a paged envelope — this endpoint differs from every
    // other dimension endpoint, and mocking the envelope shape makes the
    // component crash on `.map` rather than fail a visible assertion.
    if (url.includes("/dimensions/usages")) return Promise.resolve(jsonResponse([]));
    return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 20, offset: 0, has_more: false }));
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function resetStore(listingPatch?: Partial<ListingState>) {
  const state = defaultViewState();
  if (listingPatch) Object.assign(state.listing, listingPatch);
  useStore.setState({ state, past: [], future: [], lastPatchError: null });
}

function renderListing() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ListingView />
    </QueryClientProvider>,
  );
}

const txnRow = {
  txn_date: "2026-01-15T00:00:00",
  area_name_en: "Downtown Dubai",
  project_name_en: "Burj Residences",
  amount_aed: "5199000.00",
  price_per_m2: "24000.00",
  actual_area_m2: "216.50",
  rooms: "2 B/R",
  usage: "Residential",
};

const buildingRow = {
  name_en: "Marina Tower",
  area_name_en: "Dubai Marina",
  project_name_en: "Marina Project",
  floors: 42,
  flats: 300,
  built_up_area: "12000.00",
  is_precise: true,
};

const areaRow = {
  name_en: "Dubai Marina",
  dld_area_code: "C-44",
  has_geo_data: true,
  geo_match_method: "osm",
};

describe("ListingView", () => {
  beforeEach(() => {
    resetStore();
  });

  it("switching entity changes the columns shown", async () => {
    stubFetch([
      [/\/facts\/transactions/, () => ({ body: { items: [txnRow], total: null, limit: 50, offset: 0, has_more: false } })],
      [/\/dimensions\/buildings\?/, () => ({ body: { items: [buildingRow], total: 1, limit: 50, offset: 0, has_more: false } })],
    ]);
    renderListing();

    expect(await screen.findByRole("columnheader", { name: /Amount/ })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Entity"), { target: { value: "buildings" } });

    expect(await screen.findByRole("columnheader", { name: /Floors/ })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /Amount/ })).not.toBeInTheDocument();
  });

  it("shows guidance and fires no request when a fact entity has no required filter set", async () => {
    resetStore({ entity: "transactions", filters: {} });
    const fetchMock = stubFetch([]);
    renderListing();

    expect(await screen.findByText(/needs at least one of/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/facts/transactions"))).toBe(false);
  });

  it("renders ambiguous-entity candidates from a 422 and lets the user pick one", async () => {
    resetStore({ entity: "transactions", filters: { area_id: 20 } });
    stubFetch([
      [
        /\/facts\/transactions/,
        () => ({
          status: 422,
          body: {
            error: "ambiguous_entity",
            message: "Area 20 was subdivided into 2 current areas",
            old_area_id: 20,
            candidates: [
              { id: 292, dld_area_code: "C-44", name_en: "DUBAI MARINA" },
              { id: 374, dld_area_code: "C-74", name_en: "JUMEIRAH BEACH RESIDENCE" },
            ],
          },
        }),
      ],
    ]);
    renderListing();

    const choice = await screen.findByRole("button", { name: /DUBAI MARINA/ });
    fireEvent.click(choice);

    expect(useStore.getState().state.listing.filters.area_id).toBe(292);
  });

  it("shows a real total for a dimension entity", async () => {
    resetStore({ entity: "areas", filters: {} });
    stubFetch([
      [/\/dimensions\/areas\?/, () => ({ body: { items: [areaRow], total: 3634, limit: 50, offset: 0, has_more: true } })],
    ]);
    renderListing();

    expect(await screen.findByText(/of 3,634/)).toBeInTheDocument();
  });

  it("never renders 'of null' for a fact entity, which has total: null by design", async () => {
    resetStore({ entity: "transactions", filters: { date_from: "2025-01-01" } });
    stubFetch([
      [/\/facts\/transactions/, () => ({ body: { items: [txnRow], total: null, limit: 50, offset: 0, has_more: true } })],
    ]);
    renderListing();

    expect(await screen.findByText(/more available/)).toBeInTheDocument();
    expect(screen.queryByText(/of null/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bnull\b/);
  });
});
