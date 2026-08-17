/**
 * Shared presentational primitives.
 *
 * Kept deliberately small and unopinionated — enough that the three views look
 * like one application, not so much that a view has to fight the abstraction
 * to show something specific to it. Styling lives in theme.css; these only
 * supply structure and the class names.
 */

import { useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../core/client";

/**
 * A plain `<input type="date">`, matching the listing view's own date filter
 * exactly — the previous version of this control was a two-`<select>` custom
 * month picker, replaced after the direct request to make every view's date
 * controls consistent. The native calendar's own year-grid (click the
 * header, not the ‹ › steppers) turns out to reach a distant year faster
 * than the custom control did anyway, so nothing about the original
 * fast-year-access goal is lost.
 *
 * Value is the ISO date this app's ViewState uses (`YYYY-MM-DD`) or `null`
 * for unset — `month_from`/`month_to` on the API accept any date, not only
 * the first of a month, so no normalization happens here.
 */
export function DateField({
  label,
  value,
  onChange,
  help,
}: {
  label: string;
  value: string | null;
  onChange: (value: string | null) => void;
  help?: string;
}) {
  return (
    <Field label={label} help={help}>
      <input
        aria-label={label}
        type="date"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
      />
    </Field>
  );
}

/**
 * A single toggle button that shows/hides whatever the caller renders
 * conditionally on the `open` value it manages. Deliberately just the
 * button, not a wrapping "Collapsible" container: every view that needs this
 * already has its own `<Panel>`/overlay chrome with its own header, and a
 * second nested panel wrapper would double up borders and shadows. This
 * drops into an existing header's `actions` slot (or anywhere else) instead.
 *
 * State lives in the caller, not here, and is ordinary `useState` rather
 * than ViewState — this is transient UI chrome ("is this panel folded"),
 * not part of what's being shown, the same judgment already made for the
 * copilot dock's open/closed state.
 */
export function CollapseToggle({
  open,
  onClick,
  label,
}: {
  open: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className="btn sm subtle collapse-toggle"
      onClick={onClick}
      aria-expanded={open}
    >
      <span className={`collapse-chevron${open ? "" : " collapsed"}`} aria-hidden="true">
        ▾
      </span>
      {open ? `Hide ${label}` : `Show ${label}`}
    </button>
  );
}

/** `useState` plus the toggle callback, so call sites are one line instead of
 * three — used at every fold point (listing filters, dashboard filters, the
 * map's controls and legend overlays). */
export function useCollapse(defaultOpen = true): [boolean, () => void] {
  const [open, setOpen] = useState(defaultOpen);
  return [open, () => setOpen((v) => !v)];
}

/**
 * A field's caveat text used to render as a line below the input — correct,
 * but it meant two fields side by side in the same row went out of vertical
 * alignment the moment only one of them had help text, since row height
 * followed whichever field's text happened to wrap. `help` is now a small
 * info glyph next to the label with a CSS-only hover/focus tooltip
 * (`.help-icon`, theme.css), so a field's height never depends on whether it
 * has help at all.
 */
export function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <span className="field-label-row">
        <label>{label}</label>
        {help ? (
          <span className="help-icon" tabIndex={0} data-tooltip={help} aria-label={help}>
            i
          </span>
        ) : null}
      </span>
      {children}
    </div>
  );
}

export function Panel({
  title,
  actions,
  children,
  flush,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="panel">
      {title !== undefined ? (
        <header className="panel-head">
          <span className="panel-title">{title}</span>
          <span className="spacer" />
          {actions}
        </header>
      ) : null}
      {/* Skip the body entirely when there's nothing to show — a fold point
          (§ CollapseToggle) passes `null` for its children, and an empty
          padded div would look like a rendering glitch, not "collapsed". */}
      {children == null ? null : <div className={flush ? "" : "panel-body"}>{children}</div>}
    </section>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn";
}) {
  return <span className={`badge ${tone === "neutral" ? "" : tone}`}>{children}</span>;
}

export function Note({
  tone = "info",
  children,
}: {
  tone?: "info" | "warn" | "bad";
  children: ReactNode;
}) {
  return (
    <div className={`note ${tone}`} role={tone === "bad" ? "alert" : undefined}>
      {children}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" role="status" aria-live="polite">
      <span className="spinner" />
      {label ? <span className="muted">{label}</span> : null}
    </span>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="stack" style={{ padding: 24, textAlign: "center", alignItems: "center" }}>
      <strong>{title}</strong>
      {hint ? <span className="muted">{hint}</span> : null}
    </div>
  );
}

/**
 * Renders an API failure usefully rather than as a stack trace.
 *
 * The ambiguous-area case gets special treatment on purpose: when DLD split an
 * old area into several, the server tells us exactly which ones — showing those
 * as choices turns a dead end into the next click
 * (docs/AREA_CODE_MIGRATION_ANALYSIS.md).
 */
export function ErrorNote({
  error,
  onPickArea,
}: {
  error: unknown;
  onPickArea?: (areaId: number) => void;
}) {
  if (error instanceof ApiError && error.ambiguousCandidates.length > 0) {
    return (
      <Note tone="warn">
        <div className="stack">
          <span>{error.message}</span>
          <div className="row" style={{ flexWrap: "wrap" }}>
            {error.ambiguousCandidates.map((c) => (
              <button
                key={c.id}
                type="button"
                className="btn sm"
                onClick={() => onPickArea?.(c.id)}
                disabled={!onPickArea}
              >
                {c.name_en}
                {c.dld_area_code ? ` (${c.dld_area_code})` : ""}
              </button>
            ))}
          </div>
        </div>
      </Note>
    );
  }
  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "Request failed";
  return <Note tone="bad">{message}</Note>;
}

/**
 * The caveats and methodology the analytics API attaches to every aggregate.
 *
 * Rendered, never stripped: yields here are gross and medians are mix-shift
 * prone, and a number shown without that context is a number that will be
 * misread (docs/UI_PLAN.md §4.2).
 */
export function Caveats({
  caveats,
  methodology,
}: {
  caveats?: string[] | null;
  methodology?: string | null;
}) {
  if (!caveats?.length && !methodology) return null;
  return (
    <details className="note info">
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        How these numbers are calculated{caveats?.length ? ` · ${caveats.length} caveats` : ""}
      </summary>
      <div className="stack" style={{ marginTop: 8 }}>
        {methodology ? <p style={{ margin: 0 }}>{methodology}</p> : null}
        {caveats?.length ? (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </details>
  );
}

/**
 * Sample size, shown next to any aggregate.
 *
 * Flags thin samples rather than presenting a median of three sales with the
 * same confidence as a median of three thousand.
 */
export function SampleSize({ n, threshold = 10 }: { n: number | null | undefined; threshold?: number }) {
  if (n === null || n === undefined) return null;
  const thin = n < threshold;
  return (
    <Badge tone={thin ? "warn" : "neutral"}>
      n={n}
      {thin ? " · thin" : ""}
    </Badge>
  );
}
