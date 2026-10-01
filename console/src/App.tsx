import { Suspense, lazy, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getToken, setToken } from "./api";
import CallDetail from "./pages/CallDetail";
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
import { cx } from "./ui";

const Skills = lazy(() => import("./pages/Skills"));   // Monaco (~3.5 MB) only loads here
const Agents = lazy(() => import("./pages/Agents"));
const Studio = lazy(() => import("./pages/Studio"));   // the flow canvas (React Flow)

const NAV = [
  { to: "/", label: "Dashboard", icon: "▦" },
  { to: "/agents", label: "Agents", icon: "◆" },
  { to: "/live", label: "Live calls", icon: "◉" },
  { to: "/calls", label: "Call history", icon: "☰" },
  { to: "/logs", label: "Logs", icon: "≡" },
  { to: "/providers", label: "Agent models", icon: "⚙" },
  { to: "/connections", label: "Connections", icon: "⇄" },
  { to: "/secrets", label: "Secrets", icon: "⚿" },
  { to: "/tools", label: "Tools", icon: "⚒" },
  { to: "/skills", label: "Skills", icon: "✎" },
  { to: "/evals", label: "Evals", icon: "✓" },
  { to: "/playground", label: "Playground", icon: "🎙" },
];

export default function App() {
  return (
    <div className="flex h-full min-h-0">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-panel md:flex">
        <div className="px-4 py-4">
          <div className="text-sm font-semibold">Voice Agent Platform</div>
          <div className="text-xs text-muted">Console</div>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-2">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className={({ isActive }) => cx("flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm",
                isActive ? "bg-soft font-medium" : "text-muted hover:bg-soft hover:text-ink")}>
              <span className="w-4 text-center">{n.icon}</span>{n.label}
            </NavLink>
          ))}
        </nav>
        <TokenBox />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
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
            <Route path="/agents/:id" element={<Studio />} />
            <Route path="/live" element={<Live />} />
            <Route path="/calls" element={<Calls />} />
            <Route path="/calls/:id" element={<CallDetail />} />
            <Route path="/logs" element={<Logs />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/connections" element={<Connections />} />
            <Route path="/secrets" element={<Secrets />} />
            <Route path="/tools" element={<Tools />} />
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

function TokenBox() {
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);
  return (
    <div className="border-t border-line p-3">
      <label className="text-[11px] text-muted">Console token (if the server requires one)</label>
      <div className="mt-1 flex gap-1.5">
        <input type="password" value={value} onChange={e => { setValue(e.target.value); setSaved(false); }}
          className="min-w-0 flex-1 !py-1 text-xs" placeholder="CONSOLE_TOKEN" />
        <button className="rounded-md bg-soft px-2 text-xs" onClick={() => { setToken(value); setSaved(true); location.reload(); }}>
          {saved ? "✓" : "Save"}
        </button>
      </div>
    </div>
  );
}
