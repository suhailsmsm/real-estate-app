/**
 * The data grid itself: TanStack Table (headless) for the row model, our own
 * header row for sorting UI so its markup matches theme.css's `th.sortable`
 * / `.dir` classes exactly instead of fighting the library's default markup.
 *
 * Sorting is client-side by construction (ViewState's `sort` field is
 * documented as ordering the loaded page only, not the full result set) —
 * `ListingView` renders a permanent note next to this table saying so, so a
 * user paging through thousands of rows never mistakes a sorted page for a
 * global top-N.
 */

import { useMemo } from "react";
import { flexRender } from "@tanstack/react-table";
// v9 restructured the API around `useTable` + explicit `features`; the v8-shaped
// `useReactTable`/`createColumnHelper` surface this component uses now lives
// under the package's separate `/legacy` compat entry point, not the main one.
import { getCoreRowModel, legacyCreateColumnHelper, useLegacyTable } from "@tanstack/react-table/legacy";
import type { ColumnDef, Row } from "../../core/entities";
import type { Sort } from "../../core/viewstate";
import { compareBy, formatCell, isNumericFormat } from "../../ui/format";

const columnHelper = legacyCreateColumnHelper<Row>();

/** Click cycles asc -> desc -> unsorted; a different column always starts at asc. */
function nextSort(current: Sort | null, columnId: string): Sort | null {
  if (!current || current.column !== columnId) return { column: columnId, desc: false };
  if (!current.desc) return { column: columnId, desc: true };
  return null;
}

export function ListingTable({
  columns: visibleColumns,
  rows,
  sort,
  onSortChange,
}: {
  columns: ColumnDef[];
  rows: Row[];
  sort: Sort | null;
  onSortChange: (sort: Sort | null) => void;
}) {
  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const col = visibleColumns.find((c) => c.id === sort.column);
    if (!col) return rows;
    const cmp = compareBy(col.format);
    const dir = sort.desc ? -1 : 1;
    return [...rows].sort((a, b) => cmp(a[col.id], b[col.id]) * dir);
  }, [rows, sort, visibleColumns]);

  const tableColumns = useMemo(
    () =>
      visibleColumns.map((col) =>
        columnHelper.accessor(col.id, {
          id: col.id,
          cell: (info) => formatCell(info.getValue(), col.format),
        }),
      ),
    [visibleColumns],
  );

  const table = useLegacyTable({
    data: sortedRows,
    columns: tableColumns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (visibleColumns.length === 0) {
    return <p className="muted">No columns selected — pick at least one from Columns above.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            {visibleColumns.map((col) => {
              const active = sort?.column === col.id;
              return (
                <th
                  key={col.id}
                  className={`sortable${isNumericFormat(col.format) ? " num" : ""}`}
                  onClick={() => onSortChange(nextSort(sort, col.id))}
                  aria-sort={active ? (sort?.desc ? "descending" : "ascending") : undefined}
                >
                  {col.label}
                  {active ? <span className="dir">{sort?.desc ? "▼" : "▲"}</span> : null}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell, i) => {
                const col = visibleColumns[i];
                return (
                  <td key={cell.id} className={isNumericFormat(col.format) ? "num" : undefined}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
