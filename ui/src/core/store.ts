/**
 * The app store: ViewState, history, and URL synchronisation.
 *
 * Deliberately thin. All the logic lives in `reducer.ts` (pure) and `url.ts`
 * (pure); this only holds the current value, keeps the address bar in step,
 * and remembers enough past states to support undo.
 *
 * Undo exists because of the copilot (docs/UI_PLAN.md §5): a bot-authored
 * patch is a *proposal*, and the user needs a one-click way back. Since both
 * clicks and patches go through the same reducer, undo works identically for
 * either — there is no separate "undo the AI" path to get wrong.
 */

import { create } from "zustand";
import { type Action, reducer, validatePatch, type DeepPartial } from "./reducer";
import { decodeViewState, encodeViewState } from "./url";
import { defaultViewState, type ViewState } from "./viewstate";

/** Deep enough to undo a exploratory session, bounded so it can't grow forever. */
const HISTORY_LIMIT = 50;

interface StoreState {
  state: ViewState;
  past: ViewState[];
  future: ViewState[];
  /** Set when a copilot patch is rejected, so the UI can surface why. */
  lastPatchError: string | null;

  dispatch: (action: Action) => void;
  /** The copilot's entry point. Returns false (and explains) if invalid. */
  applyCopilotPatch: (patch: DeepPartial<ViewState>) => { ok: boolean; error?: string };
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;
  /** Re-read the address bar — for back/forward navigation. */
  syncFromUrl: () => void;
}

function currentSearch(): string {
  return typeof window === "undefined" ? "" : window.location.search;
}

function pushUrl(state: ViewState): void {
  if (typeof window === "undefined") return;
  const qs = encodeViewState(state);
  const next = `${window.location.pathname}${qs}`;
  if (next !== `${window.location.pathname}${window.location.search}`) {
    window.history.pushState(null, "", next);
  }
}

/**
 * Camera moves fire continuously while panning; recording each one would bury
 * every meaningful step under hundreds of viewport frames. They still update
 * state and the URL — they just don't become their own undo entry.
 */
function isHistoryWorthy(action: Action): boolean {
  if (action.type !== "map/set") return true;
  const keys = Object.keys(action.patch);
  return !(keys.length === 1 && keys[0] === "viewport");
}

export const useStore = create<StoreState>((set, get) => ({
  state: typeof window === "undefined" ? defaultViewState() : decodeViewState(currentSearch()),
  past: [],
  future: [],
  lastPatchError: null,

  dispatch: (action) => {
    const { state, past } = get();
    const next = reducer(state, action);
    if (next === state) return;
    set({
      state: next,
      past: isHistoryWorthy(action) ? [...past, state].slice(-HISTORY_LIMIT) : past,
      future: [],
      lastPatchError: null,
    });
    pushUrl(next);
  },

  applyCopilotPatch: (patch) => {
    const { state, past } = get();
    const result = validatePatch(state, patch);
    if (!result.ok) {
      set({ lastPatchError: result.error ?? "Invalid patch" });
      return { ok: false, error: result.error };
    }
    set({
      state: result.state,
      past: [...past, state].slice(-HISTORY_LIMIT),
      future: [],
      lastPatchError: null,
    });
    pushUrl(result.state);
    return { ok: true };
  },

  undo: () => {
    const { past, state, future } = get();
    if (!past.length) return;
    const prev = past[past.length - 1];
    set({ state: prev, past: past.slice(0, -1), future: [state, ...future] });
    pushUrl(prev);
  },

  redo: () => {
    const { future, state, past } = get();
    if (!future.length) return;
    const next = future[0];
    set({ state: next, past: [...past, state], future: future.slice(1) });
    pushUrl(next);
  },

  canUndo: () => get().past.length > 0,
  canRedo: () => get().future.length > 0,

  syncFromUrl: () => {
    const decoded = decodeViewState(currentSearch());
    if (JSON.stringify(decoded) !== JSON.stringify(get().state)) {
      set({ state: decoded });
    }
  },
}));

/** Wire browser back/forward to the store. Called once, from the app shell. */
export function attachHistoryListener(): () => void {
  if (typeof window === "undefined") return () => {};
  const onPop = () => useStore.getState().syncFromUrl();
  window.addEventListener("popstate", onPop);
  return () => window.removeEventListener("popstate", onPop);
}
