import { describe, expect, it } from "vitest";
import {
  compareBy,
  EMPTY,
  formatCell,
  formatCompact,
  formatDate,
  formatDateTime,
} from "./format";

describe("formatters handle the API's string decimals", () => {
  it("formats a NUMERIC-as-string amount", () => {
    // The API sends "5199000.00", not 5199000 — toLocaleString on the raw
    // string would return it unformatted.
    expect(formatCell("5199000.00", "money")).toBe("5,199,000");
  });

  it("formats a string price per m²", () => {
    expect(formatCell("44634.27", "money")).toBe("44,634");
  });

  it("formats numbers and strings identically", () => {
    expect(formatCell(1234.5, "number")).toBe(formatCell("1234.50", "number"));
  });

  it("renders every empty form as a single placeholder", () => {
    for (const v of [null, undefined, ""]) {
      expect(formatCell(v, "money")).toBe(EMPTY);
      expect(formatCell(v, "text")).toBe(EMPTY);
      expect(formatCell(v, "bool")).toBe(EMPTY);
    }
  });

  it("distinguishes false from missing", () => {
    // A building that is definitively not freehold must not look like one
    // whose freehold status is unknown.
    expect(formatCell(false, "bool")).toBe("No");
    expect(formatCell(null, "bool")).toBe(EMPTY);
  });

  it("does not reformat dates into an ambiguous locale order", () => {
    // DD/MM vs MM/DD ambiguity has already bitten this project once.
    expect(formatDate("2026-07-20T13:10:52+04:00")).toBe("2026-07-20");
    expect(formatDateTime("2026-07-20T13:10:52+04:00")).toBe("2026-07-20 13:10");
  });
});

describe("formatCompact", () => {
  it("scales to human units", () => {
    expect(formatCompact(5_199_000)).toBe("5.2M");
    expect(formatCompact(840_000)).toBe("840k");
    expect(formatCompact(1_250_000_000)).toBe("1.3B");
    expect(formatCompact(42)).toBe("42");
  });

  it("handles negatives", () => {
    expect(formatCompact(-1_500_000)).toBe("-1.5M");
  });
});

describe("compareBy", () => {
  it("sorts numeric string columns numerically, not lexically", () => {
    const rows = ["9", "10", "100", "2"];
    expect([...rows].sort(compareBy("money"))).toEqual(["2", "9", "10", "100"]);
  });

  it("sorts text alphabetically", () => {
    expect(["Marina", "Al Barsha", "Zabeel"].sort(compareBy("text"))).toEqual([
      "Al Barsha",
      "Marina",
      "Zabeel",
    ]);
  });

  it("puts blanks last regardless of direction", () => {
    const sorted = ["5", null, "3"].sort(compareBy("money"));
    expect(sorted[sorted.length - 1]).toBeNull();
  });
});
