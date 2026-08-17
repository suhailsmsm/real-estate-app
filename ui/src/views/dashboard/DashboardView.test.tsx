import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../../core/store";
import { defaultViewState } from "../../core/viewstate";
import { DashboardView } from "./DashboardView";

// TimeSeriesChart calls into Observable Plot, which needs real layout
// measurement jsdom doesn't provide (see TimeSeriesChart.test.tsx for the
// dedicated data-wiring tests). Mocked out here so this file can focus on
// the view's data flow: fetching, the empty state, and the honesty
// requirements (caveats, ambiguous-entity recovery).
vi.mock("@observablehq/plot", () => ({
  line: vi.fn(() => ({ type: "line" })),
  ruleY: vi.fn(() => ({ type: "ruleY" })),
  plot: vi.fn(() => document.createElement("div")),
}));

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function martRow(overrides: Record<string, unknown> = {}) {
  return {
    entity_id: 1,
    name_en: "DUBAI MARINA",
    month: "2024-01-01",
    usage: "Residential",
    sale_cnt: 50,
    sale_median_price_m2: "15613.70",
    sale_p25_price_m2: null,
    sale_p75_price_m2: null,
    rent_cnt: 20,
    rent_median_annual_m2: "980.50",
    gross_yield_pct: "6.28",
    ...overrides,
  };
}

const METRICS_META_BODY = {
  methodology: "Capital growth is the CAGR of the median sale price per m2.",
  caveats: ["Gross, not net: excludes service charges, vacancy, management fees."],
};

/**
 * `/dimensions/usages` is the one dimension endpoint that returns a bare
 * array rather than a paged `{items: [...]}` envelope (core/queries.ts's
 * `useUsages` matches this exactly) — routed first so the generic catch-all
 * below doesn't wrap it and crash Controls' `.map` on the result.
 */
function mockFetch(handlers: (url: string) => Response | undefined) {
  return vi.fn((url: string) => {
    const u = String(url);
    const handled = handlers(u);
    if (handled) return Promise.resolve(handled);
    if (u.includes("/dimensions/usages")) return Promise.resolve(jsonResponse([]));
    return Promise.resolve(jsonResponse({ items: [] }));
  });
}

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DashboardView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useStore.setState({ state: defaultViewState(), past: [], future: [], lastPatchError: null });
});

describe("DashboardView — empty state", () => {
  it("invites the user to pick an entity and fires no request", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    renderDashboard();

    expect(screen.getByText(/Pick an area or project/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("DashboardView — with a selection", () => {
  beforeEach(() => {
    useStore.getState().dispatch({ type: "dashboard/set", patch: { entityIds: [1] } });
  });

  it("renders the analytics caveats and methodology rather than stripping them", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch((u) => {
        if (u.includes("/marts/area-monthly")) {
          return jsonResponse({
            items: [martRow()],
            limit: 1000,
            offset: 0,
            has_more: false,
            total: 1,
            applied: { min_sample: null, include_future: false },
            requested_ids: [1],
            returned_ids: [1],
            missing_ids: [],
          });
        }
        if (u.includes("/meta/metrics")) return jsonResponse(METRICS_META_BODY);
        return undefined;
      }),
    );

    const { container } = renderDashboard();

    await waitFor(() => {
      expect(container.textContent).toContain("How these numbers are calculated");
    });
    expect(container.textContent).toContain(METRICS_META_BODY.caveats[0]);
    expect(container.textContent).toContain(METRICS_META_BODY.methodology);
  });

  it("lets the user resolve a superseded-area ambiguity by swapping in a candidate", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      mockFetch((u) => {
        if (u.includes("/marts/area-monthly")) {
          return jsonResponse(
            {
              error: "ambiguous_entity",
              message: "Area 1 was subdivided into 2 current areas",
              old_area_id: 1,
              candidates: [
                { id: 292, dld_area_code: "C-44", name_en: "DUBAI MARINA" },
                { id: 374, dld_area_code: "C-74", name_en: "JUMEIRAH BEACH RESIDENCE" },
              ],
            },
            422,
          );
        }
        if (u.includes("/meta/metrics")) return jsonResponse(METRICS_META_BODY);
        return undefined;
      }),
    );

    renderDashboard();

    const candidateButton = await screen.findByRole("button", { name: /DUBAI MARINA \(C-44\)/i });
    await user.click(candidateButton);

    expect(useStore.getState().state.dashboard.entityIds).toEqual([292]);
  });
});
