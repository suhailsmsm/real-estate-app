/**
 * The store's history behaviour.
 *
 * This exists because of a specific claim the copilot design rests on: a
 * bot-authored patch is a *proposal*, and the user can always take it back —
 * because it travels the same reducer and the same history as a click. That is
 * a safety property, so it is tested rather than asserted.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./store";
import { defaultViewState } from "./viewstate";

function reset() {
  window.history.replaceState(null, "", "/");
  useStore.setState({
    state: defaultViewState(),
    past: [],
    future: [],
    lastPatchError: null,
  });
}

const s = () => useStore.getState();

beforeEach(reset);

describe("dispatch and history", () => {
  it("records a step so it can be undone", () => {
    s().dispatch({ type: "setView", view: "map" });
    expect(s().state.view).toBe("map");
    expect(s().canUndo()).toBe(true);

    s().undo();
    expect(s().state.view).toBe("listing");
  });

  it("redoes what was undone", () => {
    s().dispatch({ type: "setView", view: "dashboard" });
    s().undo();
    expect(s().canRedo()).toBe(true);
    s().redo();
    expect(s().state.view).toBe("dashboard");
  });

  it("drops the redo stack once a new change is made", () => {
    s().dispatch({ type: "setView", view: "map" });
    s().undo();
    s().dispatch({ type: "setView", view: "dashboard" });
    // Redoing to "map" after branching elsewhere would resurrect a state the
    // user deliberately left.
    expect(s().canRedo()).toBe(false);
  });

  it("ignores a no-op action rather than filling history with nothing", () => {
    s().dispatch({ type: "listing/setEntity", entity: "transactions" }); // already
    expect(s().canUndo()).toBe(false);
  });

  it("bounds history so a long session cannot grow it forever", () => {
    for (let i = 0; i < 80; i++) {
      s().dispatch({ type: "listing/setPage", offset: i * 50 });
    }
    expect(s().past.length).toBeLessThanOrEqual(50);
  });

  it("writes the change into the address bar", () => {
    s().dispatch({ type: "setView", view: "map" });
    expect(window.location.search).toContain("view=map");
  });

  it("restores the address bar on undo", () => {
    s().dispatch({ type: "setView", view: "map" });
    s().undo();
    expect(window.location.search).not.toContain("view=map");
  });
});

describe("map camera is deliberately not an undo step", () => {
  it("does not record a viewport-only move", () => {
    // Panning fires continuously; recording each frame would bury every
    // meaningful step under hundreds of camera positions.
    s().dispatch({
      type: "map/set",
      patch: { viewport: { lng: 55.1, lat: 25.1, zoom: 12, pitch: 0, bearing: 0 } },
    });
    expect(s().canUndo()).toBe(false);
    // ...but the state itself still moved, so a shared link is accurate.
    expect(s().state.map.viewport.zoom).toBe(12);
  });

  it("still records a viewport move bundled with a real change", () => {
    s().dispatch({
      type: "map/set",
      patch: {
        encoding: "height",
        viewport: { lng: 55.1, lat: 25.1, zoom: 12, pitch: 45, bearing: 0 },
      },
    });
    expect(s().canUndo()).toBe(true);
  });
});

describe("copilot patches — the safety property", () => {
  it("applies a valid patch", () => {
    const r = s().applyCopilotPatch({ view: "dashboard", dashboard: { entityIds: [274] } });
    expect(r.ok).toBe(true);
    expect(s().state.dashboard.entityIds).toEqual([274]);
  });

  it("is undoable exactly like a user's own change", () => {
    // THE claim the copilot design rests on. If this fails, a bot-authored
    // change is not a proposal the user can take back.
    s().applyCopilotPatch({ view: "map", map: { encoding: "height" } });
    expect(s().canUndo()).toBe(true);

    s().undo();
    expect(s().state.view).toBe("listing");
    expect(s().state.map.encoding).toBe("color");
  });

  it("undoes a bot change and a user change through the same stack", () => {
    s().dispatch({ type: "setView", view: "dashboard" });
    s().applyCopilotPatch({ dashboard: { entityIds: [292] } });

    s().undo();
    expect(s().state.dashboard.entityIds).toEqual([]);
    s().undo();
    expect(s().state.view).toBe("listing");
  });

  it("rejects an invalid patch without touching state or history", () => {
    const before = s().state;
    const r = s().applyCopilotPatch({ dashboard: { metric: "invented_metric" as never } });

    expect(r.ok).toBe(false);
    expect(s().state).toBe(before);
    expect(s().canUndo()).toBe(false);
    // The reason is kept so the UI can say what went wrong rather than
    // silently doing nothing.
    expect(s().lastPatchError).toMatch(/metric/);
  });

  it("clears a previous rejection once a valid patch lands", () => {
    s().applyCopilotPatch({ listing: { limit: 99999 } });
    expect(s().lastPatchError).not.toBeNull();
    s().applyCopilotPatch({ view: "map" });
    expect(s().lastPatchError).toBeNull();
  });
});

describe("syncFromUrl", () => {
  it("adopts state from the address bar for back/forward navigation", () => {
    window.history.replaceState(null, "", "/?view=map&enc=height");
    s().syncFromUrl();
    expect(s().state.view).toBe("map");
    expect(s().state.map.encoding).toBe("height");
  });

  it("does nothing when the URL already matches, so it cannot loop", () => {
    const before = s().state;
    s().syncFromUrl();
    expect(s().state).toBe(before);
  });
});
