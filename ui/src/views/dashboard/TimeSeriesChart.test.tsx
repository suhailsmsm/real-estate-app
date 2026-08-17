import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TimeSeriesChart } from "./TimeSeriesChart";
import type { EntitySeries } from "./transforms";

// Observable Plot needs real layout measurement jsdom doesn't provide, so the
// module is mocked and assertions target the DATA handed to it — what
// actually matters — rather than the rendered SVG (per the task brief).
const lineCalls: unknown[] = [];
let plotNode: HTMLDivElement;

vi.mock("@observablehq/plot", () => ({
  line: vi.fn((data: unknown, options: unknown) => {
    lineCalls.push({ data, options });
    return { type: "line" };
  }),
  ruleY: vi.fn(() => ({ type: "ruleY" })),
  plot: vi.fn(() => {
    plotNode = document.createElement("div");
    plotNode.className = "mock-plot";
    return plotNode;
  }),
}));

function series(overrides: Partial<EntitySeries> = {}): EntitySeries {
  return {
    entityId: 1,
    name: "A",
    points: [
      { month: "2024-01-01", value: 100 },
      { month: "2024-02-01", value: null },
      { month: "2024-03-01", value: 120 },
    ],
    latestValue: 120,
    latestMonth: "2024-03-01",
    yoyChangePct: null,
    sampleSize: 10,
    ...overrides,
  };
}

afterEach(() => {
  lineCalls.length = 0;
});

describe("TimeSeriesChart", () => {
  it("passes each entity's own point data — including gap nulls — straight through to Plot.line", () => {
    render(<TimeSeriesChart series={[series()]} metric="sale_median_price_m2" transform="level" />);
    expect(lineCalls).toHaveLength(1);
    const { data } = lineCalls[0] as { data: { month: string; value: number | null }[] };
    expect(data.map((d) => d.value)).toEqual([100, null, 120]);
  });

  it("skips a line entirely for an entity with no data rather than erroring", () => {
    render(
      <TimeSeriesChart
        series={[series({ entityId: 2, points: [] })]}
        metric="sale_median_price_m2"
        transform="level"
      />,
    );
    expect(lineCalls).toHaveLength(0);
  });

  it("removes the previous plot node on re-render instead of stacking another one", () => {
    const { rerender, container } = render(
      <TimeSeriesChart series={[series()]} metric="sale_median_price_m2" transform="level" />,
    );
    const firstNode = container.querySelector(".mock-plot");
    expect(firstNode).toBeTruthy();

    rerender(<TimeSeriesChart series={[series()]} metric="sale_median_price_m2" transform="mom_pct" />);
    const plotNodes = container.querySelectorAll(".mock-plot");
    expect(plotNodes).toHaveLength(1);
  });
});
