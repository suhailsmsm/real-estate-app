/**
 * Copilot chat over Server-Sent Events.
 *
 * `EventSource` cannot be used here: it only issues GETs, and a conversation
 * plus the current ViewState is far too much to put in a query string. So this
 * POSTs and parses the SSE framing off the response body itself.
 *
 * Patches from the model go through `applyCopilotPatch`, which runs the same
 * validation and lands in the same undo history as a user's click — the bot
 * gets no privileged path into the UI (docs/UI_PLAN.md §5).
 */

import { useCallback, useRef, useState } from "react";
import { getAccessToken } from "../core/auth";
import { useStore } from "../core/store";
import type { DeepPartial } from "../core/reducer";
import type { ViewState } from "../core/viewstate";

export const COPILOT_BASE = "/copilot";

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  /** What the copilot changed, shown inline so the action is visible, not silent. */
  action?: string;
  error?: boolean;
}

interface SseEvent {
  event: string;
  data: unknown;
}

/**
 * Split a raw SSE buffer into complete events.
 *
 * Returns the leftover tail: a chunk boundary can land mid-event, and parsing
 * a half-received frame would drop it entirely.
 */
export function parseSseBuffer(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const frames = buffer.split("\n\n");
  const rest = frames.pop() ?? "";

  for (const frame of frames) {
    let name = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) continue;
    try {
      events.push({ event: name, data: JSON.parse(dataLines.join("\n")) });
    } catch {
      // A malformed frame is skipped rather than killing the stream — the rest
      // of the answer is still worth showing.
    }
  }
  return { events, rest };
}

export function useCopilot() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [toolActivity, setToolActivity] = useState<string | null>(null);
  const applyCopilotPatch = useStore((s) => s.applyCopilotPatch);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setToolActivity(null);
  }, []);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busy) return;

      const history = [...turns, { role: "user" as const, content: question }];
      setTurns(history);
      setBusy(true);
      setToolActivity(null);

      const controller = new AbortController();
      abortRef.current = controller;

      // Read state at send time rather than subscribing, so the copilot sees
      // exactly what the user was looking at when they hit enter.
      const viewState = useStore.getState().state;
      const token = getAccessToken();

      try {
        const res = await fetch(`${COPILOT_BASE}/chat`, {
          method: "POST",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            messages: history.map((t) => ({ role: t.role, content: t.content })),
            view_state: viewState,
          }),
        });

        if (!res.ok || !res.body) {
          const detail = await res.text().catch(() => "");
          throw new Error(detail || `Copilot request failed (${res.status})`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let assistant = "";
        let action: string | undefined;
        // Tracked rather than set once at the error site: the stream can carry
        // more events after an error, and the final flush below would otherwise
        // rewrite the turn without the flag — rendering a failure as an
        // ordinary answer.
        let errored = false;

        const flush = () =>
          setTurns([
            ...history,
            { role: "assistant", content: assistant || "…", action, error: errored },
          ]);

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const { events, rest } = parseSseBuffer(buffer);
          buffer = rest;

          for (const ev of events) {
            const data = ev.data as Record<string, unknown>;
            if (ev.event === "text") {
              assistant += String(data.text ?? "");
              setToolActivity(null);
              flush();
            } else if (ev.event === "tool") {
              setToolActivity(String(data.name ?? "working"));
            } else if (ev.event === "view_state") {
              const result = applyCopilotPatch(
                data.patch as DeepPartial<ViewState>,
              );
              action = result.ok
                ? (data.explanation as string) || "Updated the view."
                : `Could not update the view: ${result.error}`;
              flush();
            } else if (ev.event === "error") {
              assistant = assistant
                ? `${assistant}\n\n${String(data.message ?? "")}`
                : String(data.message ?? "Something went wrong.");
              errored = true;
              flush();
            }
          }
        }
        flush();
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        setTurns([
          ...history,
          { role: "assistant", content: (err as Error).message, error: true },
        ]);
      } finally {
        setBusy(false);
        setToolActivity(null);
        abortRef.current = null;
      }
    },
    [applyCopilotPatch, busy, turns],
  );

  const clear = useCallback(() => setTurns([]), []);

  return { turns, busy, toolActivity, send, stop, clear };
}
