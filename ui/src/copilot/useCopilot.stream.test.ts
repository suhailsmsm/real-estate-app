/**
 * The copilot hook end to end, with a faked SSE response body.
 *
 * `parseSseBuffer` is unit-tested separately; what this covers is the part
 * that actually matters for trust — that a `view_state` event arriving over
 * the wire really does reach the store, and that a rejected one really does
 * not.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setAccessToken } from "../core/auth";
import { useStore } from "../core/store";
import { defaultViewState } from "../core/viewstate";
import { useCopilot } from "./useCopilot";

/** A Response whose body streams the given SSE frames, in chunks. */
function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  useStore.setState({
    state: defaultViewState(),
    past: [],
    future: [],
    lastPatchError: null,
  });
  setAccessToken(null);
});

describe("useCopilot", () => {
  it("streams prose into the transcript", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: text\ndata: {"text":"Marina is up 4%."}\n\n',
          'event: done\ndata: {"stop_reason":"end_turn"}\n\n',
        ]),
      ),
    );

    const { result } = renderHook(() => useCopilot());
    await act(async () => {
      await result.current.send("how is marina?");
    });

    await waitFor(() => {
      expect(result.current.turns).toHaveLength(2);
    });
    expect(result.current.turns[0]).toMatchObject({ role: "user", content: "how is marina?" });
    expect(result.current.turns[1].content).toBe("Marina is up 4%.");
  });

  it("applies a streamed view_state patch to the real store", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: view_state\ndata: {"patch":{"view":"dashboard","dashboard":{"entityIds":[292]}},"explanation":"Plotted Dubai Marina."}\n\n',
          'event: text\ndata: {"text":"Here it is."}\n\n',
        ]),
      ),
    );

    const { result } = renderHook(() => useCopilot());
    await act(async () => {
      await result.current.send("show me marina");
    });

    // The whole point: the model's patch actually moved the UI.
    await waitFor(() => {
      expect(useStore.getState().state.view).toBe("dashboard");
    });
    expect(useStore.getState().state.dashboard.entityIds).toEqual([292]);
    // ...and it is undoable, because it went through the normal reducer.
    expect(useStore.getState().canUndo()).toBe(true);
    // ...and the user is told what changed rather than it happening silently.
    expect(result.current.turns.at(-1)?.action).toBe("Plotted Dubai Marina.");
  });

  it("refuses an invalid patch and says so instead of moving the UI", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: view_state\ndata: {"patch":{"dashboard":{"metric":"invented"}},"explanation":"Charted it."}\n\n',
        ]),
      ),
    );

    const { result } = renderHook(() => useCopilot());
    await act(async () => {
      await result.current.send("chart something impossible");
    });

    await waitFor(() => expect(result.current.turns.length).toBeGreaterThan(1));
    expect(useStore.getState().state.dashboard.metric).toBe("sale_median_price_m2");
    expect(result.current.turns.at(-1)?.action).toMatch(/Could not update the view/);
  });

  it("surfaces a server error event as a visible failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          sseResponse([
            'event: error\ndata: {"message":"ANTHROPIC_API_KEY is unset."}\n\n',
          ]),
        ),
    );

    const { result } = renderHook(() => useCopilot());
    await act(async () => {
      await result.current.send("hi");
    });

    await waitFor(() => expect(result.current.turns.at(-1)?.error).toBe(true));
    expect(result.current.turns.at(-1)?.content).toContain("ANTHROPIC_API_KEY");
  });

  it("sends the access token and the current view state", async () => {
    setAccessToken("tok-abc");
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(['event: done\ndata: {}\n\n']));
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useCopilot());
    await act(async () => {
      await result.current.send("hello");
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/copilot/chat");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-abc");
    // Without the current view state, "add X to this chart" has nothing to add to.
    expect(JSON.parse(init.body as string).view_state.view).toBe("listing");
  });

  it("reports a non-streaming HTTP failure rather than hanging", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("upstream is down", { status: 502 })),
    );

    const { result } = renderHook(() => useCopilot());
    await act(async () => {
      await result.current.send("hi");
    });

    await waitFor(() => expect(result.current.turns.at(-1)?.error).toBe(true));
    expect(result.current.busy).toBe(false);
  });
});
