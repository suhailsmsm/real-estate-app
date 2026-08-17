import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ColumnDef, Row } from "../../core/entities";
import type { Sort } from "../../core/viewstate";
import { formatMoney } from "../../ui/format";
import { ListingTable } from "./ListingTable";

const columns: ColumnDef[] = [
  { id: "area_name_en", label: "Area", format: "text" },
  { id: "amount_aed", label: "Amount", format: "money" },
];

// Numeric values arrive from the API as JSON strings ("5199000.00"), not
// numbers — this is the shape a real response actually has.
const rows: Row[] = [
  { area_name_en: "High", amount_aed: "9000000.00" },
  { area_name_en: "Low", amount_aed: "100000.00" },
];

/** Sort is owned by the caller in real use (ViewState); a tiny harness stands in for that here. */
function Harness() {
  const [sort, setSort] = useState<Sort | null>(null);
  return <ListingTable columns={columns} rows={rows} sort={sort} onSortChange={setSort} />;
}

function bodyRows() {
  const table = screen.getByRole("table");
  return within(table).getAllByRole("row").slice(1); // drop the header row
}

describe("ListingTable", () => {
  it("renders rows and formats a string-decimal money cell using the shared formatter", () => {
    render(<Harness />);
    expect(screen.getByText(formatMoney("9000000.00"))).toBeInTheDocument();
    expect(screen.getByText(formatMoney("100000.00"))).toBeInTheDocument();
    // Raw, unformatted string values must not leak into the cell.
    expect(screen.queryByText("9000000.00")).not.toBeInTheDocument();
  });

  it("sorts ascending on the first header click and reverses on the second", () => {
    render(<Harness />);
    const header = screen.getByRole("columnheader", { name: /Amount/ });

    fireEvent.click(header);
    expect(within(bodyRows()[0]).getByText("Low")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Amount/ })).toHaveAttribute("aria-sort", "ascending");

    fireEvent.click(screen.getByRole("columnheader", { name: /Amount/ }));
    expect(within(bodyRows()[0]).getByText("High")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Amount/ })).toHaveAttribute("aria-sort", "descending");

    // Third click clears sort back to the original (server) order.
    fireEvent.click(screen.getByRole("columnheader", { name: /Amount/ }));
    expect(within(bodyRows()[0]).getByText("High")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Amount/ })).not.toHaveAttribute("aria-sort");
  });
});
