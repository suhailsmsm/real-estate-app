/**
 * Prev/next + page-size paging.
 *
 * `total` is a real count only when `entity.hasTotal` is true (the small
 * dimension tables); the fact endpoints (transactions/rents) always return
 * `total: null` by design — computing a count over 12M rows on every page
 * load is not something those tables can afford. So this never assumes
 * `total` is present: with it, "1–50 of 3,634"; without it, "Showing 1–50"
 * plus "more available" when the server says so. Never "of null".
 */

import { formatInt } from "../../ui/format";

const PAGE_SIZES = [25, 50, 100, 200];

export function Pagination({
  offset,
  limit,
  rowCount,
  total,
  hasTotal,
  hasMore,
  onPageChange,
  onLimitChange,
}: {
  offset: number;
  limit: number;
  rowCount: number;
  total: number | null;
  hasTotal: boolean;
  hasMore: boolean;
  onPageChange: (offset: number) => void;
  onLimitChange: (limit: number) => void;
}) {
  const start = rowCount === 0 ? 0 : offset + 1;
  const end = offset + rowCount;
  const canPrev = offset > 0;
  const canNext = hasTotal ? total !== null && end < total : hasMore;

  const summary =
    rowCount === 0
      ? "No results"
      : hasTotal && total !== null
        ? `${formatInt(start)}–${formatInt(end)} of ${formatInt(total)}`
        : `Showing ${formatInt(start)}–${formatInt(end)}${hasMore ? " · more available" : ""}`;

  return (
    <div className="row listing-pagination">
      <span className="muted">{summary}</span>
      <span className="spacer" />
      <select aria-label="Rows per page" value={limit} onChange={(e) => onLimitChange(Number(e.target.value))}>
        {PAGE_SIZES.map((n) => (
          <option key={n} value={n}>
            {n} / page
          </option>
        ))}
      </select>
      <button type="button" className="btn sm" disabled={!canPrev} onClick={() => onPageChange(Math.max(0, offset - limit))}>
        Prev
      </button>
      <button type="button" className="btn sm" disabled={!canNext} onClick={() => onPageChange(offset + limit)}>
        Next
      </button>
    </div>
  );
}
