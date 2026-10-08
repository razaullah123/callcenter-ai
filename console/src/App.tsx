import { useQueryClient } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { AcceptInvite, Login, Setup, UserBadge, useAuth } from "./Auth";
import { Logo, LogoMark } from "./Brand";
import Calls from "./pages/Calls";
import Dashboard from "./pages/Dashboard";
import Evals from "./pages/Evals";
import Live from "./pages/Live";
import Logs from "./pages/Logs";
import Playground from "./pages/Playground";
import Connections from "./pages/Connections";
import Providers from "./pages/Providers";
import Secrets from "./pages/Secrets";
import Tools from "./pages/Tools";
import Knowledge from "./pages/Knowledge";
import ProjectSwitcher, { ProjectSettings } from "./Projects";
import { cx } from "./ui";

const Skills = lazy(() => import("./pages/Skills"));   // Monaco (~3.5 MB) only loads here
const Agents = lazy(() => import("./pages/Agents"));
const Studio = lazy(() => import("./pages/Studio"));   // the flow canvas (React Flow)
const Share = lazy(() => import("./pages/Share"));     // public page / embed widget (Publishing)

const Voices = lazy(() => import("./pages/Voices"));
const Numbers = lazy(() => import("./pages/Numbers"));
const BatchCalls = lazy(() => import("./pages/BatchCalls"));
const ApiKeys = lazy(() => import("./pages/ApiKeys"));
const BatchCall = lazy(() => import("./pages/BatchCall"));

// Lucide-style line icons (Hamsa's sidebar), as path lists
const I: Record<string, string[]> = {
  dashboard: ["M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8",
              "M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  voices: ["M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z", "M19 10v2a7 7 0 0 1-14 0v-2", "M12 19v3"],
  calls: ["M8 6h13", "M8 12h13", "M8 18h13", "M3 6h.01", "M3 12h.01", "M3 18h.01"],
  live: ["M4.9 19.1C1 15.2 1 8.8 4.9 4.9", "M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5", "M10 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
         "M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5", "M19.1 4.9C23 8.8 23 15.1 19.1 19"],
  logs: ["M15 12h-5", "M15 8h-5", "M19 17V5a2 2 0 0 0-2-2H4",
         "M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"],
  key: ["M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z",
        "M16 7.5a.5.5 0 1 0 1 0a.5.5 0 1 0-1 0"],
  lock: ["M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z", "M7 11V7a5 5 0 0 1 10 0v4"],
  agents: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M16 3.128a4 4 0 0 1 0 7.744", "M22 21v-2a4 4 0 0 0-3-3.87",
           "M5 7a4 4 0 1 0 8 0a4 4 0 1 0-8 0"],
  book: ["M12 7v14",
         "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"],
  tools: ["M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"],
  skills: ["M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z", "M14 2v4a2 2 0 0 0 2 2h4", "M16 13H8", "M16 17H8"],
  models: ["M4 4h16v16H4z", "M9 9h6v6H9z", "M9 1v3", "M15 1v3", "M9 20v3", "M15 20v3", "M20 9h3", "M20 14h3", "M1 9h3", "M1 14h3"],
  plug: ["M12 22v-5", "M9 8V2", "M15 8V2", "M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"],
  evals: ["M22 11.08V12a10 10 0 1 1-5.93-9.14", "M22 4 12 14.01l-3-3"],
  play: ["M6 3l14 9-14 9V3z"],
  phone: ["M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384"],
  plus: ["M5 12h14", "M12 5v14"],
};
const NavIcon = ({ name }: { name: string }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true">{(I[name] ?? []).map((d, i) => <path key={i} d={d} />)}</svg>
);

type NavItem = { to: string; label: string; icon: string; soon?: boolean };
// grouped like Hamsa's sidebar; "soon" items are on the plan (Phase 12.9), not built yet
const GROUPS: { title?: string; items: NavItem[] }[] = [
  { items: [
    { to: "/", label: "Dashboard", icon: "dashboard" }, { to: "/voices", label: "Voices", icon: "voices" },
    { to: "/calls", label: "Call history", icon: "calls" }, { to: "/live", label: "Live calls", icon: "live" },
    { to: "/logs", label: "Logs", icon: "logs" }, { to: "/api-keys", label: "API keys", icon: "key" },
    { to: "/secrets", label: "Secrets", icon: "lock" },
  ] },
  { title: "Agents", items: [
    { to: "/agents", label: "Agents", icon: "agents" }, { to: "/knowledge-base", label: "Knowledge base", icon: "book" },
    { to: "/tools", label: "Tools Templates", icon: "tools" }, { to: "/skills", label: "Skills", icon: "skills" },
    { to: "/connections", label: "Connections", icon: "plug" },
    // Commented out 2026-10-08 (Hamsa's sidebar has none of these; their routes below still work, so links and bookmarks
    // keep working — to bring one back, uncomment its line):
    //   { to: "/providers", label: "Agent models", icon: "models" },   // per-agent model setup: now in Agent Studio -> Global settings
    //   { to: "/evals", label: "Evals", icon: "evals" },               // test cases: now in Agent Studio -> Tests (publish gate)
    //   { to: "/playground", label: "Playground", icon: "play" },      // test call: now in Agent Studio's test panel
  ] },
  { title: "Telephony", items: [
    { to: "/numbers", label: "Phone numbers", icon: "plus" }, { to: "/batch-calls", label: "Batch calls", icon: "phone" },
  ] },
];
const NAV = GROUPS.flatMap(g => g.items).filter(i => !i.soon);

export default function App() {
  return (
    <Routes>
      <Route path="/invite/:token" element={<AcceptInvite />} />
      <Route path="*" element={<Gate />} />
    </Routes>
  );
}

/** Sign-in first (once accounts exist), then the console. */
function Gate() {
  const auth = useAuth();
  if (auth.isLoading) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (auth.error) return (                 // e.g. a server started before sign-in existed (no /api/auth/status)
    <div className="mx-auto max-w-md p-8 text-sm">
      <div className="text-base font-semibold">Can't reach the sign-in service</div>
      <p className="mt-2 text-muted">The server answered: {auth.error instanceof Error ? auth.error.message : String(auth.error)}.
        If the server was started before the latest update, restart it (python -m runtime.server), then reload this page.</p>
      <button className="mt-3 rounded-lg border border-line px-3 py-1.5" onClick={() => auth.refetch()}>Try again</button>
    </div>);
  const st = auth.data;
  if (!st || st.mode === "login") return <Login />;
  if (st.mode === "setup") return <Setup status={st} />;
  return <Shell status={st} />;
}

const COLLAPSE_KEY = "hmg-console-sidebar-collapsed";
const THEME_KEY = "hmg-console-theme";

/** A hover / focus label drawn on <body> (the sidebar scrolls, so an inline one would be clipped). */
function Tip({ label, side = "right", className, children }: { label: string; side?: "right" | "bottom"; className?: string; children: ReactNode }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const show = (e: React.SyntheticEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setAt(side === "right" ? { x: r.right + 8, y: r.top + r.height / 2 } : { x: r.left + r.width / 2, y: r.bottom + 6 });
  };
  return (
    <div className={className} onMouseEnter={show} onMouseLeave={() => setAt(null)} onFocus={show} onBlur={() => setAt(null)} onClick={() => setAt(null)}>
      {children}
      {at && createPortal(
        <div role="tooltip" style={{ position: "fixed", left: at.x, top: at.y, transform: side === "right" ? "translateY(-50%)" : "translateX(-50%)" }}
          className="pointer-events-none z-50 rounded-md bg-soft px-2.5 py-1.5 text-xs whitespace-nowrap text-ink shadow-md ring-1 ring-line">{label}</div>,
        document.body)}
    </div>
  );
}

const tbIcon = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{d}</svg>
);
const TB = {
  panel: tbIcon(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M9 3v18" /></>),
  docs: tbIcon(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" /></>),
  sun: tbIcon(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>),
  moon: tbIcon(<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z" />),
};

function useTheme() {
  const media = () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  const [dark, setDark] = useState(() => (document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : media()));
  const flip = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(THEME_KEY, next); } catch { /* private mode */ }
    setDark(!dark);
  };
  return { dark, flip };
}

function Shell({ status }: { status: NonNullable<ReturnType<typeof useAuth>["data"]> }) {
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { return false; } });
  const toggle = () => setCollapsed(c => { try { localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1"); } catch { /* private */ } return !c; });
  const theme = useTheme();
  useEffect(() => {          // Ctrl / ⌘ + B opens and closes the sidebar (not while typing)
    const key = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "b" || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.closest(".monaco-editor"))) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const qc = useQueryClient();
  useEffect(() => {          // the current project disappeared (removed from it): reload with the default one
    const again = () => qc.invalidateQueries();
    window.addEventListener("console-project", again);
    return () => window.removeEventListener("console-project", again);
  }, [qc]);
  return (
    <div className="flex h-full min-h-0">
      <aside className={cx("hidden shrink-0 flex-col border-r border-line bg-panel transition-[width] md:flex", collapsed ? "w-16" : "w-80 px-8")}>
        <div className={cx("flex h-14 shrink-0 items-center", collapsed ? "justify-center" : "px-4")}>
          {collapsed ? <LogoMark /> : <>
            <Logo className="h-10 w-10" />
            <span className="ml-2.5 min-w-0 leading-tight"><span className="block truncate text-sm font-semibold">Voice Agent Platform</span>
              <span className="block truncate text-[11px] text-muted">Cloud Solutions</span></span>
          </>}
        </div>
        <nav className={cx("flex flex-1 flex-col overflow-y-auto", collapsed ? "items-center px-0 pt-3.5 pb-2" : "px-2 py-2")} aria-label="Main">
          {GROUPS.map((g, gi) => (
            <div key={gi} className={cx(gi > 0 && !collapsed && "mt-8")}>
              {g.title && (collapsed ? <div className="h-8" aria-hidden="true" />
                : <div className="flex h-8 items-center px-2 text-xs font-medium text-nav/70">{g.title}</div>)}
              <div className="flex flex-col gap-1">
                {g.items.map(n => {
                  const item = n.soon ? (
                    <div title={collapsed ? undefined : `${n.label} — not built yet`} aria-disabled="true"
                      className={cx("flex h-8 cursor-default items-center gap-2 rounded-md text-sm text-muted opacity-60", collapsed ? "w-8 justify-center" : "px-2")}>
                      <NavIcon name={n.icon} />{!collapsed && <><span className="flex-1">{n.label}</span>
                        <span className="rounded bg-soft px-1.5 text-[10px] font-medium">Soon</span></>}
                    </div>
                  ) : (
                    <NavLink to={n.to} end={n.to === "/"} aria-label={collapsed ? n.label : undefined}
                      className={({ isActive }) => cx("flex h-8 items-center gap-2 rounded-md text-sm", collapsed ? "w-8 justify-center" : "px-2",
                        isActive ? "bg-accent text-accent-fg" : "text-nav hover:bg-soft")}>
                      <NavIcon name={n.icon} />{!collapsed && n.label}
                    </NavLink>
                  );
                  return collapsed ? <Tip key={n.to} label={n.soon ? `${n.label} — soon` : n.label}>{item}</Tip> : <div key={n.to}>{item}</div>;
                })}
              </div>
            </div>
          ))}
        </nav>
        <UserBadge status={status} collapsed={collapsed} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
          <Tip side="bottom" label={`${collapsed ? "Open" : "Close"} sidebar (Ctrl+B)`} className="hidden md:block">
            <button onClick={toggle} aria-label={collapsed ? "Open sidebar" : "Close sidebar"}
              className="flex h-8 w-8 items-center justify-center rounded-md text-ink hover:bg-soft">{TB.panel}</button>
          </Tip>
          <ProjectSwitcher />
          <div className="ml-auto flex items-center gap-2">
            <Tip side="bottom" label="API Documentation">
              <a href="/docs" target="_blank" rel="noreferrer" aria-label="API Documentation"
                className="flex h-9 w-9 items-center justify-center rounded-md border border-line bg-panel text-ink hover:bg-soft">{TB.docs}</a>
            </Tip>
            <Tip side="bottom" label={theme.dark ? "Switch to light theme" : "Switch to dark theme"}>
              <button onClick={theme.flip} aria-label="Toggle theme"
                className="flex h-9 w-9 items-center justify-center rounded-md border border-line text-ink hover:bg-soft">{theme.dark ? TB.moon : TB.sun}</button>
            </Tip>
          </div>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b border-line bg-panel px-3 py-2 md:hidden">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => cx("whitespace-nowrap rounded-md px-2.5 py-1 text-xs",
                isActive ? "bg-soft font-medium" : "text-muted")}>{n.label}</NavLink>
          ))}
        </nav>
        <main className="min-h-0 flex-1 overflow-auto px-4 py-5 md:px-6">
          <Suspense fallback={<div className="text-sm text-muted">Loading…</div>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/voices" element={<Voices />} />
            <Route path="/numbers" element={<Numbers />} />
            <Route path="/batch-calls" element={<BatchCalls />} />
            <Route path="/api-keys" element={<ApiKeys />} />
            <Route path="/batch-calls/:id" element={<BatchCall />} />
            <Route path="/project" element={<ProjectSettings />} />
            <Route path="/agents/:id" element={<Studio />} />
            <Route path="/agents/:id/share" element={<Share />} />
            <Route path="/live" element={<Live />} />
            <Route path="/calls" element={<Calls />} />
            <Route path="/calls/:id" element={<Calls />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/secrets" element={<Secrets />} />
            <Route path="/tools" element={<Tools />} />
            <Route path="/knowledge-base" element={<Knowledge />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/skills/:name" element={<Skills />} />
            <Route path="/evals" element={<Evals />} />
            <Route path="/playground" element={<Playground />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  );
}
