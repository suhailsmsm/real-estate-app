import { lazy, Suspense, useEffect, useState } from "react";
import { CopilotDock } from "./copilot/CopilotDock";
import { login, restoreSession } from "./core/client";
import { attachHistoryListener, useStore } from "./core/store";
import { VIEWS, type ViewName } from "./core/viewstate";
import { ErrorNote, Field, Spinner } from "./ui/components";

// Split per view. MapLibre and Observable Plot are the two heaviest things in
// the bundle, and someone who only opens a table should not pay to download a
// WebGL mapping engine. Each view arrives on first use instead.
const ListingView = lazy(() =>
  import("./views/listing/ListingView").then((m) => ({ default: m.ListingView })),
);
const DashboardView = lazy(() =>
  import("./views/dashboard/DashboardView").then((m) => ({ default: m.DashboardView })),
);
const MapView = lazy(() =>
  import("./views/map/MapView").then((m) => ({ default: m.MapView })),
);

const VIEW_LABELS: Record<ViewName, string> = {
  listing: "Listing",
  dashboard: "Dashboard",
  map: "Map",
};

function LoginForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-box">
      <form className="panel" style={{ width: 320 }} onSubmit={submit}>
        <div className="panel-head">
          <span className="panel-title">Sign in</span>
        </div>
        <div className="panel-body stack">
          <Field label="Username">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>
          {error ? <ErrorNote error={error} /> : null}
          <button className="btn primary" type="submit" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const view = useStore((s) => s.state.view);
  const dispatch = useStore((s) => s.dispatch);
  const undo = useStore((s) => s.undo);
  const redo = useStore((s) => s.redo);
  const past = useStore((s) => s.past.length);
  const future = useStore((s) => s.future.length);

  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => attachHistoryListener(), []);

  useEffect(() => {
    // The access token died with the last tab; trade the stored refresh token
    // for a new one before deciding whether to show the login form.
    restoreSession()
      .then(setAuthed)
      .catch(() => setAuthed(false));
  }, []);

  if (authed === null) {
    return (
      <div className="center-box">
        <Spinner label="Restoring session…" />
      </div>
    );
  }

  if (!authed) return <LoginForm onDone={() => setAuthed(true)} />;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <svg className="brand-logo" viewBox="0 0 200 132" aria-hidden="true">
            <g fill="none" stroke="#ffffff" strokeWidth="4" strokeLinejoin="round" strokeLinecap="round">
              {/* Same skyline as public/favicon.svg. White + bold on request —
                  needs a dark topbar background to actually read; see .topbar. */}
              <path
                d="M5,126 L9,68 L16,50 C21,62 28,80 29,102 C28,116 26,122 23,126
                   L46,126 L46,90 L49,90 L49,80 L55,80 L61,68 L61,80 L64,80 L64,126
                   L84,126 L84,99 L87,99 L87,81 L90,81 L90,65 L93,65 L93,51 L96,51 L96,25
                   L98,25 L98,3 L100,2 L102,8 L102,30 L104,30 L104,56 L107,56 L107,70
                   L110,70 L110,86 L113,86 L113,104 L116,104 L116,126
                   L134,126 L134,62 L144,48 L156,62 L156,126
                   L168,126 L168,78 L172,78 L178,60 L184,78 L190,78 L190,126"
              />
              <path d="M17,52 L20,54 L24,32 Z" />
            </g>
          </svg>
          Dubai estate <span>analytics</span>
        </span>
        <nav className="tabs">
          {VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              className="tab"
              aria-current={v === view ? "page" : undefined}
              onClick={() => dispatch({ type: "setView", view: v })}
            >
              {VIEW_LABELS[v]}
            </button>
          ))}
        </nav>
        <div className="topbar-actions">
          <button
            className="btn subtle sm"
            onClick={undo}
            disabled={!past}
            title="Undo — works the same for your changes and the copilot's"
          >
            Undo
          </button>
          <button className="btn subtle sm" onClick={redo} disabled={!future}>
            Redo
          </button>
        </div>
      </header>

      <main className={`viewport${view === "map" ? " flush" : ""}`}>
        <Suspense
          fallback={
            <div className="center-box">
              <Spinner label="Loading view…" />
            </div>
          }
        >
          {view === "listing" ? <ListingView /> : null}
          {view === "dashboard" ? <DashboardView /> : null}
          {view === "map" ? <MapView /> : null}
        </Suspense>
      </main>

      <CopilotDock />
    </div>
  );
}
