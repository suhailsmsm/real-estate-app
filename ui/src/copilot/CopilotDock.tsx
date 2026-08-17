/**
 * The copilot dialog — openable from anywhere, dockable over any view.
 *
 * It deliberately shows its work: which data tool is running, and what it
 * changed in the UI. An assistant that silently rearranges the screen is
 * unsettling; one that says "Plotted the five fastest-growing areas" and leaves
 * an Undo button next to it is a colleague.
 */

import { useEffect, useRef, useState } from "react";
import { useStore } from "../core/store";
import { Badge, Spinner } from "../ui/components";
import { useCopilot } from "./useCopilot";
import "./copilot.css";

const SUGGESTIONS = [
  "Which areas grew fastest in the last year?",
  "Compare Dubai Marina and JVC rental yields",
  "Show me the most expensive areas on the map",
];

export function CopilotDock() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const { turns, busy, toolActivity, send, stop, clear } = useCopilot();
  const undo = useStore((s) => s.undo);
  const canUndo = useStore((s) => s.past.length > 0);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, toolActivity]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && open) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = draft;
    setDraft("");
    void send(text);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="copilot-fab"
        onClick={() => setOpen(true)}
        aria-label="Open the analytics copilot"
      >
        Ask
      </button>
    );
  }

  return (
    <aside className="copilot" aria-label="Analytics copilot">
      <header className="copilot-head">
        <strong>Copilot</strong>
        <Badge>MCP data only</Badge>
        <span className="spacer" />
        {turns.length > 0 ? (
          <button type="button" className="btn subtle sm" onClick={clear}>
            Clear
          </button>
        ) : null}
        <button
          type="button"
          className="btn subtle sm"
          onClick={() => setOpen(false)}
          aria-label="Close copilot"
        >
          Close
        </button>
      </header>

      <div className="copilot-log" ref={logRef}>
        {turns.length === 0 ? (
          <div className="copilot-empty">
            <p className="muted">
              Ask about the data. Answers come from real Dubai Land Department
              records, and the copilot can reconfigure this view to show you what
              it found.
            </p>
            <div className="copilot-suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="btn sm"
                  onClick={() => void send(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {turns.map((t, i) => (
          <div key={i} className={`copilot-turn ${t.role}${t.error ? " error" : ""}`}>
            <div className="copilot-bubble">{t.content}</div>
            {t.action ? (
              <div className="copilot-action">
                <span>{t.action}</span>
                {canUndo ? (
                  <button type="button" className="btn sm" onClick={undo}>
                    Undo
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}

        {busy ? (
          <div className="copilot-turn assistant">
            <div className="copilot-bubble muted">
              <Spinner label={toolActivity ? `Running ${toolActivity}…` : "Thinking…"} />
            </div>
          </div>
        ) : null}
      </div>

      <form className="copilot-input" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about areas, projects, prices…"
          aria-label="Ask the copilot"
          disabled={busy}
        />
        {busy ? (
          <button type="button" className="btn" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="btn primary" disabled={!draft.trim()}>
            Ask
          </button>
        )}
      </form>
    </aside>
  );
}
