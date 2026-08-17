/** The filter/metric/transform toolbar above the chart. */

import { useUsages } from "../../core/queries";
import type { Action } from "../../core/reducer";
import { DASHBOARD_METRICS, DASHBOARD_TRANSFORMS } from "../../core/viewstate";
import type { DashboardState } from "../../core/viewstate";
import { DateField, Field } from "../../ui/components";
import { METRIC_LABELS, TRANSFORM_LABELS } from "./metricMeta";

export function Controls({
  state,
  dispatch,
}: {
  state: DashboardState;
  dispatch: (action: Action) => void;
}) {
  const usages = useUsages();

  function set(patch: Partial<DashboardState>) {
    dispatch({ type: "dashboard/set", patch });
  }

  return (
    <div className="toolbar">
      <Field label="Metric">
        <select value={state.metric} onChange={(e) => set({ metric: e.target.value as DashboardState["metric"] })}>
          {DASHBOARD_METRICS.map((m) => (
            <option key={m} value={m}>
              {METRIC_LABELS[m]}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Transform">
        <select
          value={state.transform}
          onChange={(e) => set({ transform: e.target.value as DashboardState["transform"] })}
        >
          {DASHBOARD_TRANSFORMS.map((t) => (
            <option key={t} value={t}>
              {TRANSFORM_LABELS[t]}
            </option>
          ))}
        </select>
      </Field>

      <DateField label="From" value={state.monthFrom} onChange={(monthFrom) => set({ monthFrom })} />
      <DateField label="To" value={state.monthTo} onChange={(monthTo) => set({ monthTo })} />

      <Field label="Usage">
        <select
          value={state.usage ?? ""}
          onChange={(e) => set({ usage: e.target.value || null })}
        >
          <option value="">All</option>
          {(usages.data ?? []).map((u) => (
            <option key={u.usage} value={u.usage}>
              {u.usage}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Min sample" help="Drop months below this many transactions">
        <input
          type="number"
          min={0}
          value={state.minSample ?? ""}
          onChange={(e) => set({ minSample: e.target.value === "" ? null : Number(e.target.value) })}
          style={{ width: 80 }}
        />
      </Field>

      <Field label="Future">
        <label className="row" style={{ gap: 6 }}>
          <input
            type="checkbox"
            checked={state.includeFuture}
            onChange={(e) => set({ includeFuture: e.target.checked })}
          />
          <span className="muted">Include future-dated months</span>
        </label>
      </Field>
    </div>
  );
}
