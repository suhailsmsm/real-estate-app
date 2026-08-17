/**
 * Cell formatters, shared by every view.
 *
 * One subtlety worth centralising rather than rediscovering per view: the API
 * serialises Postgres NUMERIC as a JSON *string* ("5199000.00"), not a number,
 * because that is the only way to carry exact decimals through JSON. Every
 * numeric formatter here therefore accepts `string | number` and coerces once.
 * Formatting such a value with `toLocaleString()` directly — the obvious thing
 * to reach for — silently returns the raw string unformatted.
 */

import type { ColumnFormat } from "../core/entities";

/** What an absent value looks like. An en dash, not "null" or an empty cell. */
export const EMPTY = "–";

function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

const int = new Intl.NumberFormat("en-AE", { maximumFractionDigits: 0 });
const dec2 = new Intl.NumberFormat("en-AE", { maximumFractionDigits: 2 });
const dec1 = new Intl.NumberFormat("en-AE", { maximumFractionDigits: 1 });

export function formatMoney(v: unknown): string {
  const n = toNumber(v);
  return n === null ? EMPTY : int.format(n);
}

/** Compact form for axis ticks and tight chips: 5.2M, 840k. */
export function formatCompact(v: unknown): string {
  const n = toNumber(v);
  if (n === null) return EMPTY;
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${dec1.format(n / 1e9)}B`;
  if (abs >= 1e6) return `${dec1.format(n / 1e6)}M`;
  if (abs >= 1e3) return `${dec1.format(n / 1e3)}k`;
  return dec1.format(n);
}

export function formatInt(v: unknown): string {
  const n = toNumber(v);
  return n === null ? EMPTY : int.format(n);
}

export function formatNumber(v: unknown): string {
  const n = toNumber(v);
  return n === null ? EMPTY : dec2.format(n);
}

export function formatArea(v: unknown): string {
  const n = toNumber(v);
  return n === null ? EMPTY : `${dec1.format(n)}`;
}

export function formatPct(v: unknown): string {
  const n = toNumber(v);
  return n === null ? EMPTY : `${dec1.format(n)}%`;
}

/** ISO date in, `YYYY-MM-DD` out. Never reformats into a locale order — this
 * data is Gulf-region and DD/MM vs MM/DD ambiguity has already bitten this
 * project once (CLAUDE.md, the DLD gateway's MM/DD/YYYY). */
export function formatDate(v: unknown): string {
  if (!v) return EMPTY;
  const s = String(v);
  return s.length >= 10 ? s.slice(0, 10) : s;
}

export function formatDateTime(v: unknown): string {
  if (!v) return EMPTY;
  const s = String(v);
  if (s.length < 16) return formatDate(v);
  return `${s.slice(0, 10)} ${s.slice(11, 16)}`;
}

export function formatBool(v: unknown): string {
  // "" is absence, not falsehood. Rendering it as "No" would assert that a
  // building is *not* freehold when the truth is that we don't know — the API
  // is explicit that a null here means unknown, not false.
  if (v === null || v === undefined || v === "") return EMPTY;
  return v ? "Yes" : "No";
}

export function formatCell(value: unknown, format: ColumnFormat): string {
  switch (format) {
    case "money":
      return formatMoney(value);
    case "int":
      return formatInt(value);
    case "number":
      return formatNumber(value);
    case "area":
      return formatArea(value);
    case "pct":
      return formatPct(value);
    case "date":
      return formatDate(value);
    case "datetime":
      return formatDateTime(value);
    case "bool":
      return formatBool(value);
    default:
      return value === null || value === undefined || value === "" ? EMPTY : String(value);
  }
}

/** Numeric columns are right-aligned so digits line up for comparison. */
export function isNumericFormat(format: ColumnFormat): boolean {
  return ["money", "int", "number", "area", "pct"].includes(format);
}

/**
 * Sort comparator matching a column's format. Numeric columns must compare as
 * numbers even though the API sent strings — otherwise "9" sorts after "10".
 */
export function compareBy(format: ColumnFormat) {
  const numeric = isNumericFormat(format);
  return (a: unknown, b: unknown): number => {
    if (a === null || a === undefined || a === "") return 1; // blanks last
    if (b === null || b === undefined || b === "") return -1;
    if (numeric) {
      const na = toNumber(a) ?? 0;
      const nb = toNumber(b) ?? 0;
      return na - nb;
    }
    return String(a).localeCompare(String(b), "en");
  };
}
