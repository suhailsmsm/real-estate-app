import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SummaryStrip } from "./SummaryStrip";
import type { EntitySeries } from "./transforms";

function series(overrides: Partial<EntitySeries> = {}): EntitySeries {
  return {
    entityId: 1,
    name: "DUBAI MARINA",
    points: [{ month: "2024-01-01", value: 15000 }],
    latestValue: 15000,
    latestMonth: "2024-01-01",
    yoyChangePct: 8.4,
    sampleSize: 42,
    ...overrides,
  };
}

describe("SummaryStrip", () => {
  it("renders nothing when there are no entities", () => {
    const { container } = render(<SummaryStrip series={[]} metric="sale_median_price_m2" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows sample size for every selected entity", () => {
    render(
      <SummaryStrip
        series={[series({ entityId: 1, name: "A", sampleSize: 42 }), series({ entityId: 2, name: "B", sampleSize: 3 })]}
        metric="sale_median_price_m2"
      />,
    );
    // A's sample is comfortably above the thin-sample threshold; B's is not
    // (SampleSize flags n < 10 as "thin"), so both must still render n=.
    expect(screen.getByText(/n=42/)).toBeInTheDocument();
    expect(screen.getByText(/n=3/)).toBeInTheDocument();
    expect(screen.getByText(/thin/)).toBeInTheDocument();
  });

  it("flags an entity with no data instead of showing a misleading blank value", () => {
    render(
      <SummaryStrip
        series={[series({ latestValue: null, latestMonth: null, yoyChangePct: null, sampleSize: null })]}
        metric="sale_median_price_m2"
      />,
    );
    expect(screen.getByText("No data")).toBeInTheDocument();
  });

  it("shows the YoY change alongside the latest value", () => {
    render(<SummaryStrip series={[series({ yoyChangePct: -5.2 })]} metric="sale_median_price_m2" />);
    expect(screen.getByText(/-5\.2%/)).toBeInTheDocument();
  });
});
