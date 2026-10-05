import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, setProject, setToken, type AuthStatus } from "./api";
import { Logo } from "./Brand";
import { Button, cx } from "./ui";

// Console sign-in: the first account (owner of every existing project), sign in, accepting an invitation by link,
// and the user badge (bottom left, like Hamsa's initials) with Sign out.

const errorText = (e: unknown) => {
  if (e instanceof ApiError) {
    const d = e.detail as { errors?: string[]; message?: string } | string;
    return typeof d === "string" ? d : d?.errors?.join(" · ") ?? d?.message ?? e.message;
  }
  return e instanceof Error ? e.message : "";
};

export const initials = (name: string) =>
  name.split(/[\s._@-]+/).filter(Boolean).slice(0, 2).map(w => w[0]!.toUpperCase()).join("") || "?";

export function useAuth() {
  const q = useQuery({ queryKey: ["auth"], queryFn: api.authStatus, staleTime: 60_000, retry: 0 });
  const qc = useQueryClient();
  useEffect(() => {
    const again = () => qc.invalidateQueries({ queryKey: ["auth"] });
    window.addEventListener("console-auth", again);
    return () => window.removeEventListener("console-auth", again);
  }, [qc]);
  return q;
}

/** Store the session and start over (every query with the new identity). */
function useSignedIn() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return (token: string, project?: string) => {
    setToken(token);
    setProject(project ?? "");
    qc.resetQueries();
    navigate(project ? "/agents" : "/", { replace: true });
  };
}

function Shell({ title, sub, children }: { title: string; sub?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-bg p-4">
      <div className="w-full max-w-sm rounded-xl border border-line bg-panel p-6 shadow-sm">
        <div className="mb-4 flex items-center gap-3">
          <Logo className="h-14 w-14" />
          <div className="leading-tight"><div className="text-sm font-semibold">Voice Agent Platform</div>
            <div className="text-[11px] text-muted">Cloud Solutions</div></div>
        </div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
        <div className="mt-5">{children}</div>
      </div>
    </div>
  );
}

const Field = ({ label, children }: { label: string; children: React.ReactNode }) =>
  <label className="mb-3 block"><div className="mb-1 text-xs font-medium">{label}</div>{children}</label>;

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const done = useSignedIn();
  const login = useMutation({ mutationFn: () => api.login(email, password), onSuccess: r => done(r.token) });
  return (
    <Shell title="Sign in" sub="Welcome back! Access your voice agent workspace.">
      <form onSubmit={e => { e.preventDefault(); login.mutate(); }}>
        <Field label="Email"><input id="login-email" type="email" autoFocus className="w-full" value={email} onChange={e => setEmail(e.target.value)} placeholder="Enter your email address" /></Field>
        <Field label="Password"><input id="login-password" type="password" className="w-full" value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter your password" /></Field>
        {login.error && <div className="mb-3 text-xs text-bad">{errorText(login.error)}</div>}
        <Button kind="primary" type="submit" disabled={!email || !password || login.isPending}>{login.isPending ? "Signing in…" : "Sign in"}</Button>
      </form>
    </Shell>
  );
}

export function Setup({ status }: { status: AuthStatus }) {
  const [form, setForm] = useState({ name: "", email: "", password: "", token: "" });
  const done = useSignedIn();
  const setup = useMutation({
    mutationFn: () => { if (form.token) setToken(form.token); return api.setup({ name: form.name, email: form.email, password: form.password }); },
    onSuccess: r => done(r.token),
    // someone (or an earlier click) created the first account already: go to Sign in
    onError: e => { if (e instanceof ApiError && e.status === 409) window.dispatchEvent(new Event("console-auth")); },
  });
  return (
    <Shell title="Create your account" sub="You'll be the owner of every existing project. After this, everyone signs in and you invite your team from Project settings.">
      <form onSubmit={e => { e.preventDefault(); setup.mutate(); }}>
        <Field label="Name"><input id="setup-name" autoFocus className="w-full" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Email"><input id="setup-email" type="email" className="w-full" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Password (at least 8 characters)"><input id="setup-password" type="password" className="w-full" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></Field>
        {!status.open && <Field label="Console token (CONSOLE_TOKEN in .env)"><input id="setup-token" type="password" className="w-full" value={form.token} onChange={e => setForm({ ...form, token: e.target.value })} /></Field>}
        {setup.error && <div className="mb-3 text-xs text-bad">{errorText(setup.error)}</div>}
        <Button kind="primary" type="submit" disabled={!form.name || !form.email || form.password.length < 8 || setup.isPending}>
          {setup.isPending ? "Creating…" : "Create account"}</Button>
      </form>
    </Shell>
  );
}

export function AcceptInvite() {
  const { token = "" } = useParams();
  const info = useQuery({ queryKey: ["invitation", token], queryFn: () => api.invitation(token), retry: 0 });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const done = useSignedIn();
  const accept = useMutation({ mutationFn: () => api.acceptInvitation(token, { name, password }), onSuccess: r => done(r.token, r.project) });
  if (info.error) return <Shell title="Invitation"><div className="text-sm text-bad">{errorText(info.error)}</div></Shell>;
  const d = info.data;
  if (!d) return <Shell title="Invitation"><div className="text-sm text-muted">Loading…</div></Shell>;
  if (d.status !== "invited") return <Shell title="Invitation">
    <div className="text-sm">{d.status === "expired" ? "This invitation has expired. Ask for a new one." : "This invitation was already used."}</div></Shell>;
  return (
    <Shell title={`Join ${d.project}`} sub={<>{d.invited_by ?? "Someone"} invited <b>{d.email}</b> to this project.</>}>
      <form onSubmit={e => { e.preventDefault(); accept.mutate(); }}>
        {!d.has_account && <Field label="Your name"><input id="invite-name" autoFocus className="w-full" value={name} onChange={e => setName(e.target.value)} /></Field>}
        <Field label={d.has_account ? "Your password" : "Choose a password (at least 8 characters)"}>
          <input id="invite-password" type="password" autoFocus={d.has_account} className="w-full" value={password} onChange={e => setPassword(e.target.value)} /></Field>
        {accept.error && <div className="mb-3 text-xs text-bad">{errorText(accept.error)}</div>}
        <Button kind="primary" type="submit" disabled={!password || (!d.has_account && !name) || accept.isPending}>
          {accept.isPending ? "Joining…" : d.has_account ? "Sign in and join" : "Create account and join"}</Button>
      </form>
    </Shell>
  );
}

/** Bottom-left initials (Hamsa's "RU") with the signed-in user, change password, sign out. */
export function UserBadge({ status, collapsed }: { status: AuthStatus; collapsed: boolean }) {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState<{ current: string; next: string } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const change = useMutation({ mutationFn: () => api.updateMe({ current_password: pw!.current, password: pw!.next }), onSuccess: () => setPw(null) });
  const signOut = async () => { try { await api.logout(); } catch { /* already gone */ } setToken(""); setProject(""); qc.resetQueries(); navigate("/"); };
  const u = status.user;
  const label = u ? u.name : status.mode === "token" ? "Console token" : "Not signed in";
  return (
    <div ref={ref} className={cx("relative", collapsed ? "flex justify-center py-2" : "p-2")}>
      <button id="user-menu" onClick={() => setOpen(o => !o)} aria-label="Account"
        className={cx("flex items-center gap-2 rounded-lg text-left hover:bg-soft", collapsed ? "h-8 w-8 justify-center" : "w-full p-1.5")}>
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-soft text-xs font-semibold">{u ? initials(u.name) : "⚿"}</span>
        {!collapsed && <span className="min-w-0"><span className="block truncate text-sm font-medium">{label}</span>
          {u && <span className="block truncate text-[11px] text-muted">{u.email}</span>}</span>}
      </button>
      {open && (
        <div className="absolute bottom-full left-2 z-40 mb-1 w-64 rounded-lg border border-line bg-panel p-1.5 shadow-lg">
          {u ? <>
            <div className="px-2 py-1.5"><div className="text-sm font-medium">{u.name}</div><div className="text-xs text-muted">{u.email}</div></div>
            <div className="my-1 border-t border-line" />
            {pw ? (
              <form className="space-y-1.5 p-2" onSubmit={e => { e.preventDefault(); change.mutate(); }}>
                <input type="password" placeholder="Current password" className="w-full text-xs" value={pw.current} onChange={e => setPw({ ...pw, current: e.target.value })} />
                <input type="password" placeholder="New password (8+)" className="w-full text-xs" value={pw.next} onChange={e => setPw({ ...pw, next: e.target.value })} />
                {change.error && <div className="text-[11px] text-bad">{errorText(change.error)}</div>}
                <div className="flex justify-end gap-1"><Button kind="ghost" onClick={() => setPw(null)}>Cancel</Button>
                  <Button kind="primary" type="submit" disabled={!pw.current || pw.next.length < 8}>Save</Button></div>
              </form>
            ) : <button className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={() => setPw({ current: "", next: "" })}>Change password</button>}
            <button className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft" onClick={signOut}>Sign out</button>
          </> : <TokenForm onDone={() => setOpen(false)} />}
        </div>
      )}
    </div>
  );
}

function TokenForm({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  return (
    <form className={cx("space-y-1.5 p-2")} onSubmit={e => { e.preventDefault(); setToken(value); onDone(); location.reload(); }}>
      <div className="text-[11px] text-muted">Console token (scripts / automation)</div>
      <input type="password" value={value} onChange={e => setValue(e.target.value)} className="w-full text-xs" placeholder="CONSOLE_TOKEN" />
      <div className="flex justify-end"><Button kind="primary" type="submit" disabled={!value}>Use token</Button></div>
    </form>
  );
}
