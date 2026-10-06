import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, getDefaultProject, getProject, setDefaultProject, setProject, type AuditRow, type Invitation,
  type Member, type Project } from "./api";
import { Button, Card, cx, fmtTime } from "./ui";

// Hamsa's project switcher (top bar, left): "My projects" (you own them, ★ = the one you open with) and "Invited
// projects" (someone invited you; the owner is shown, ✎ gives it your own label), then Project settings, Create
// project, Rename label / Rename project, Copy project ID. Project settings: Team members (invite, remove), Audit log.

const COLORS = ["bg-violet-600", "bg-red-600", "bg-sky-600", "bg-amber-600", "bg-rose-600", "bg-teal-600", "bg-indigo-600"];
const colorOf = (id: string) => COLORS[[...id].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORS.length];

export function Avatar({ p, size = "h-6 w-6 text-[11px]" }: { p: { id: string; name: string }; size?: string }) {
  return <span className={cx("flex shrink-0 items-center justify-center rounded-md font-semibold text-white", size, colorOf(p.id))}>
    {(p.name.trim()[0] ?? "?").toUpperCase()}</span>;
}

const errorText = (e: unknown) => {
  if (e instanceof ApiError) {
    const d = e.detail as { errors?: string[] } | string;
    return typeof d === "string" ? d : d?.errors?.join(" · ") ?? e.message;
  }
  return e instanceof Error ? e.message : "";
};

/** Switch the whole console to another project. */
export function useSwitchProject() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return (id: string, to = "/agents") => { setProject(id); qc.resetQueries(); navigate(to); };
}

function Dialog({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[18vh]" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="w-full max-w-md rounded-xl border border-line bg-panel p-4 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-base font-semibold">{title}</div>
          <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function CreateProjectDialog({ current, onClose }: { current: Project | undefined; onClose: () => void }) {
  const [name, setName] = useState("");
  const [copy, setCopy] = useState(true);
  const switchTo = useSwitchProject();
  const create = useMutation({ mutationFn: () => api.createProject(name, copy), onSuccess: p => { onClose(); switchTo(p.id); } });
  return (
    <Dialog title="Create project" onClose={onClose}>
      <form onSubmit={e => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <label className="block"><div className="mb-1 text-xs font-medium">Name</div>
          <input id="project-name" autoFocus className="w-full" placeholder="My project" value={name} onChange={e => setName(e.target.value)} /></label>
        <label className="mt-3 flex items-start gap-2 text-xs">
          <input id="project-copy" type="checkbox" className="mt-0.5" checked={copy} onChange={e => setCopy(e.target.checked)} />
          <span>Start with {current ? <b>{current.name}</b> : "this project"}'s connections, secrets, tool library and MCP servers
            <span className="block text-muted">Agents, skills and phone numbers are not copied. Without this the project starts empty.</span></span>
        </label>
        {create.error && <div className="mt-2 text-xs text-bad">{errorText(create.error)}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" type="submit" disabled={!name.trim() || create.isPending}>{create.isPending ? "Creating…" : "Create"}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function RenameProjectDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const [name, setName] = useState(project.name);
  const qc = useQueryClient();
  const rename = useMutation({ mutationFn: () => api.renameProject(project.id, name), onSuccess: () => { qc.invalidateQueries({ queryKey: ["projects"] }); onClose(); } });
  return (
    <Dialog title="Rename project" onClose={onClose}>
      <form onSubmit={e => { e.preventDefault(); if (name.trim()) rename.mutate(); }}>
        <p className="mb-3 text-xs text-muted">Everyone using this console sees the new name. The project ID ({project.id}) stays the same.</p>
        <label className="block"><div className="mb-1 text-xs font-medium">Name</div>
          <input id="project-rename" autoFocus className="w-full" value={name} onChange={e => setName(e.target.value)} onFocus={e => e.target.select()} /></label>
        {rename.error && <div className="mt-2 text-xs text-bad">{errorText(rename.error)}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" type="submit" disabled={!name.trim() || name === project.name || rename.isPending}>Save</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function useProjects() {
  const q = useQuery({ queryKey: ["projects"], queryFn: api.projects, staleTime: 30_000 });
  const current = q.data?.find(p => p.id === getProject()) ?? q.data?.find(p => p.default)
    ?? q.data?.find(p => p.platform_default) ?? q.data?.[0];
  return { q, current };
}

/** What a member calls the project: their own label, else its name. */
export const shownName = (p: Project) => p.label || p.name;

export function RenameLabelDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const [label, setLabel] = useState(project.label || project.name);
  const qc = useQueryClient();
  const save = useMutation({ mutationFn: () => api.setProjectLabel(project.id, label === project.name ? "" : label),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["projects"] }); onClose(); } });
  return (
    <Dialog title="Rename label" onClose={onClose}>
      <form onSubmit={e => { e.preventDefault(); save.mutate(); }}>
        <p className="mb-3 text-xs text-muted">This label is only visible to you. It does not change the project name for other members.</p>
        <label className="block"><div className="mb-1 text-xs font-medium">Label</div>
          <input id="project-label" autoFocus className="w-full" value={label} onChange={e => setLabel(e.target.value)} onFocus={e => e.target.select()} /></label>
        {save.error && <div className="mt-2 text-xs text-bad">{errorText(save.error)}</div>}
        <div className="mt-4 flex justify-end gap-2">
          <Button kind="ghost" onClick={onClose}>Cancel</Button>
          <Button kind="primary" type="submit" disabled={save.isPending}>Save</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const t = document.createElement("textarea");
  t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
  return Promise.resolve();
}

export default function ProjectSwitcher() {
  const { q, current } = useProjects();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [dialog, setDialog] = useState<"create" | "rename" | "label" | null>(null);
  const [labelFor, setLabelFor] = useState<Project | null>(null);
  const [copied, setCopied] = useState(false);
  const [localDef, setLocalDef] = useState(getDefaultProject());
  const ref = useRef<HTMLDivElement>(null);
  const switchTo = useSwitchProject();
  const navigate = useNavigate();
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  // the stored project was removed / never existed / no access: fall back to the default one
  useEffect(() => { if (q.data && current && current.id !== getProject()) setProject(current.id); }, [q.data, current]);
  const accounts = (q.data ?? []).some(p => p.default !== null);
  const isDefault = (p: Project) => accounts ? !!p.default : localDef === p.id;
  const makeDefault = async (p: Project) => {
    if (accounts) { await api.setDefaultProjectOnServer(p.id); qc.invalidateQueries({ queryKey: ["projects"] }); }
    else { setDefaultProject(p.id); setLocalDef(p.id); }
  };
  const mine = (q.data ?? []).filter(p => p.mine), invited = (q.data ?? []).filter(p => !p.mine);
  const item = "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-soft disabled:opacity-50";
  const row = (p: Project) => (
    <div key={p.id} className={cx("group flex items-center gap-2.5 rounded-md px-2 py-1.5 hover:bg-soft", p.id === current?.id && "bg-soft/60")}>
      <button role="menuitem" className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
        onClick={() => { setOpen(false); if (p.id !== current?.id) switchTo(p.id); }}>
        <Avatar p={p} />
        <span className="min-w-0"><span className="block truncate text-sm font-medium">{shownName(p)}</span>
          {!p.mine && p.owner && <span className="block truncate text-[10px] text-muted">{p.owner}</span>}</span>
      </button>
      {!p.mine && <button title="Rename label" aria-label="Rename label" onClick={() => { setLabelFor(p); setDialog("label"); setOpen(false); }}
        className="text-xs text-muted opacity-0 hover:text-ink group-hover:opacity-100">✎</button>}
      {p.mine && <button title={isDefault(p) ? "Default project (opens first)" : "Make default"} aria-label="Default project"
        onClick={() => makeDefault(p)}
        className={cx("text-sm", isDefault(p) ? "text-amber-500" : "text-muted opacity-0 group-hover:opacity-100")}>{isDefault(p) ? "★" : "☆"}</button>}
      {p.id === current?.id && <span className="text-sm text-accent-text" aria-label="current project">✓</span>}
    </div>
  );
  return (
    <div ref={ref} className="relative">
      <button id="project-switcher" onClick={() => setOpen(o => !o)} aria-label="Switch project"
        className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-soft" aria-haspopup="menu" aria-expanded={open}>
        {current ? <Avatar p={current} /> : <span className="h-6 w-6 rounded-md bg-soft" />}
        <span className="max-w-[16rem] truncate text-sm font-semibold">{current ? shownName(current) : "…"}</span>
        {current && current.mine && isDefault(current) && <span className="text-xs text-amber-500">★</span>}
        <span className="text-xs text-muted">⇅</span>
      </button>
      {open && (
        <div role="menu" className="absolute left-0 top-full z-40 mt-1 w-72 rounded-lg border border-line bg-panel p-1.5 shadow-lg">
          <div className="px-2 pb-1 pt-1 text-[11px] font-medium text-muted">My projects</div>
          {mine.length ? mine.map(row) : <div className="px-2 py-1 text-xs text-muted">None yet</div>}
          {invited.length > 0 && <>
            <div className="my-1 border-t border-line" />
            <div className="px-2 pb-1 pt-1 text-[11px] font-medium text-muted">Invited projects</div>
            {invited.map(row)}
          </>}
          <div className="my-1 border-t border-line" />
          <button role="menuitem" className={item} onClick={() => { setOpen(false); navigate("/project"); }}><span className="w-4 text-center">👥</span>Project settings</button>
          <button role="menuitem" className={item} onClick={() => { setOpen(false); setDialog("create"); }}><span className="w-4 text-center">＋</span>Create project</button>
          <div className="my-1 border-t border-line" />
          {current && !current.mine
            ? <button role="menuitem" className={item} onClick={() => { setOpen(false); setLabelFor(current); setDialog("label"); }}><span className="w-4 text-center">✎</span>Rename label</button>
            : <button role="menuitem" className={item} disabled={!current} onClick={() => { setOpen(false); setDialog("rename"); }}><span className="w-4 text-center">✎</span>Rename project</button>}
          <button role="menuitem" className={item} disabled={!current}
            onClick={() => { if (current) copyText(current.id).then(() => { setCopied(true); setTimeout(() => { setCopied(false); setOpen(false); }, 900); }); }}>
            <span className="w-4 text-center">⧉</span>{copied ? "Copied ✓" : "Copy project ID"}</button>
        </div>
      )}
      {dialog === "create" && <CreateProjectDialog current={current} onClose={() => setDialog(null)} />}
      {dialog === "rename" && current && <RenameProjectDialog project={current} onClose={() => setDialog(null)} />}
      {dialog === "label" && labelFor && <RenameLabelDialog project={labelFor} onClose={() => setDialog(null)} />}
    </div>
  );
}

const AUDIT: Record<string, string> = {
  "project.created": "Created the project", "project.renamed": "Renamed the project",
  "agent.created": "Created an agent", "agent.renamed": "Renamed an agent", "agent.deleted": "Deleted an agent",
  publish: "Published", "publish.override": "Published without passing checks", activate: "Made an older version live",
  "draft.discarded": "Discarded a draft", "case.created": "Added a test case", "case.changed": "Changed a test case",
  "case.deleted": "Deleted a test case", "connection.created": "Added a connection", "connection.changed": "Changed a connection",
  "connection.deleted": "Deleted a connection", "secret.set": "Saved a secret", "secret.deleted": "Deleted a secret",
  "tool.changed": "Changed a tool", "tool.removed": "Removed a tool", "mcp.added": "Added an MCP server",
  "mcp.changed": "Changed an MCP server", "mcp.deleted": "Deleted an MCP server", "route.set": "Set a phone route",
  "route.deleted": "Removed a phone route", "member.invited": "Invited a member", "member.joined": "Joined the project",
  "member.removed": "Removed a member", "member.invitation_revoked": "Revoked an invitation",
  "voice.changed": "Changed an agent's voice (draft)",
};

function auditWhat(a: AuditRow): string {
  const d = a.detail as Record<string, unknown> & { override?: { reason?: string } };
  const parts: string[] = [];
  if (a.agent_id) parts.push(`agent ${a.agent_id}`);
  if (d.version) parts.push(`v${d.version}`);
  for (const k of ["name", "email", "pattern", "case", "pid", "sid", "from", "to", "note"]) if (d[k] && typeof d[k] !== "object") parts.push(`${k === "pid" || k === "sid" ? "" : k + ": "}${d[k]}`);
  if (d.override?.reason) parts.push(`reason: ${d.override.reason}`);
  return parts.join(" · ");
}

function AuditLog({ project }: { project: string }) {
  const q = useQuery({ queryKey: ["project-audit", project], queryFn: () => api.projectAudit(project) });
  const [kind, setKind] = useState("");
  const rows = (q.data ?? []).filter(a => !kind || a.action.startsWith(kind));
  return (
    <Card title="Audit log" actions={
      <select id="audit-kind" value={kind} onChange={e => setKind(e.target.value)} className="!py-1 text-xs">
        <option value="">Everything</option><option value="publish">Publishing</option><option value="agent.">Agents</option>
        <option value="connection.">Connections</option><option value="secret.">Secrets</option><option value="mcp.">MCP servers</option>
        <option value="tool.">Tools</option><option value="route.">Phone routes</option><option value="case.">Test cases</option>
        <option value="member.">Members</option><option value="project.">Project</option>
      </select>}>
      {!rows.length ? <div className="py-6 text-center text-sm text-muted">{q.isLoading ? "Loading…" : "No activity recorded yet."}</div> : (
        <table className="w-full text-sm"><tbody>
          {rows.map(a => (
            <tr key={a.id} className="border-b border-line align-top last:border-0">
              <td className="whitespace-nowrap py-1.5 pr-3 text-xs text-muted">{fmtTime(a.ts)}</td>
              <td className="py-1.5 pr-3 text-xs">{a.actor ?? "—"}</td>
              <td className={cx("py-1.5 pr-3 text-xs font-medium", a.action === "publish.override" && "text-warn")}>{AUDIT[a.action] ?? a.action}</td>
              <td className="py-1.5 text-xs text-muted" dir="auto">{auditWhat(a)}</td>
            </tr>))}
        </tbody></table>)}
    </Card>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (status === "joined") return <span className="inline-flex items-center gap-1 rounded-md bg-good/10 px-1.5 py-0.5 text-[11px] font-medium text-good">● Joined</span>;
  return (
    <span className="flex flex-col items-start gap-1">
      <span className="inline-flex items-center gap-1 rounded-md bg-bad/10 px-1.5 py-0.5 text-[11px] font-medium text-bad">● Invited</span>
      {status === "expired" && <span className="rounded-md bg-bad/10 px-1.5 py-0.5 text-[11px] font-medium text-bad">Invitation expired</span>}
    </span>
  );
}

function RowMenu({ items }: { items: { label: string; danger?: boolean; onClick: () => void }[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  if (!items.length) return null;
  return (
    <div ref={ref} className="relative inline-block">
      <button aria-label="More actions" className="rounded px-2 text-muted hover:bg-soft hover:text-ink" onClick={() => setOpen(o => !o)}>⋯</button>
      {open && <div className="absolute right-0 z-30 mt-1 w-48 rounded-lg border border-line bg-panel p-1 shadow-lg">
        {items.map(i => <button key={i.label} onClick={() => { setOpen(false); i.onClick(); }}
          className={cx("block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-soft", i.danger && "text-bad")}>{i.label}</button>)}
      </div>}
    </div>
  );
}

function LinkBox({ link, emailed, email }: { link: string; emailed: boolean; email: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-2">
      <p className="text-sm">{emailed ? <>An invitation was emailed to <b>{email}</b>. You can also share this link:</>
        : <>Send this link to <b>{email}</b> (email sending isn't set up — SMTP_* in .env). It works for 7 days:</>}</p>
      <div className="flex gap-2">
        <input id="invite-link" readOnly className="min-w-0 flex-1 font-mono text-xs" value={link} onFocus={e => e.target.select()} />
        <Button onClick={() => copyText(link).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })}>{copied ? "Copied ✓" : "Copy link"}</Button>
      </div>
    </div>
  );
}

function InviteDialog({ project, onClose }: { project: string; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const qc = useQueryClient();
  const invite = useMutation({ mutationFn: () => api.invite(project, email.trim()),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["members", project] }); qc.invalidateQueries({ queryKey: ["project-audit", project] }); } });
  return (
    <Dialog title="Invite member" onClose={onClose}>
      {invite.data ? <>
        <LinkBox link={invite.data.link} emailed={invite.data.emailed} email={invite.data.invitation.email} />
        <div className="mt-4 flex justify-end"><Button kind="primary" onClick={onClose}>Done</Button></div>
      </> : (
        <form onSubmit={e => { e.preventDefault(); if (email.trim()) invite.mutate(); }}>
          <label className="block"><div className="mb-1 text-xs font-medium">Email</div>
            <input id="invite-email" type="email" autoFocus className="w-full" placeholder="colleague@example.com" value={email} onChange={e => setEmail(e.target.value)} /></label>
          <p className="mt-2 text-[11px] text-muted">They join as <b>Admin</b>: they can change everything in this project and invite others.</p>
          {invite.error && <div className="mt-2 text-xs text-bad">{errorText(invite.error)}</div>}
          <div className="mt-4 flex justify-end gap-2">
            <Button kind="ghost" onClick={onClose}>Cancel</Button>
            <Button kind="primary" type="submit" disabled={!email.trim() || invite.isPending}>{invite.isPending ? "Inviting…" : "Invite"}</Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function TeamMembers({ project, inviting, onInvited }: { project: string; inviting: boolean; onInvited: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["members", project], queryFn: () => api.members(project) });
  const [link, setLink] = useState<{ link: string; emailed: boolean; email: string } | null>(null);
  const [confirm, setConfirm] = useState<{ text: string; run: () => Promise<unknown> } | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["members", project] }); qc.invalidateQueries({ queryKey: ["project-audit", project] }); };
  const act = useMutation({ mutationFn: (f: () => Promise<unknown>) => f(), onSuccess: () => { setConfirm(null); refresh(); } });
  const d = q.data;
  type Row = (Member & { kind: "member" }) | (Invitation & { kind: "invite"; name: string });
  const rows: Row[] = [...(d?.members ?? []).map(m => ({ ...m, kind: "member" as const })),
    ...(d?.invitations ?? []).map(i => ({ ...i, kind: "invite" as const, name: i.email.split("@")[0] }))];
  return (
    <Card title="Team members">
      {!d ? <div className="text-sm text-muted">{q.error ? errorText(q.error) : "Loading…"}</div> : !d.accounts ? (
        <p className="text-sm text-muted">Nobody has an account yet. Sign-in starts when the first account is created.</p>
      ) : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted"><th className="py-2 font-medium">Name</th><th className="py-2 font-medium">Email</th>
            <th className="py-2 font-medium">Role</th><th className="py-2 font-medium">Status</th><th /></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.kind + (r.kind === "member" ? r.user_id : r.id)} className="border-t border-line align-middle">
                <td className="py-2.5">{r.name}{r.kind === "member" && r.you && <span className="ml-1 text-xs text-muted">(you)</span>}</td>
                <td className="py-2.5 text-muted">{r.email}</td>
                <td className="py-2.5 capitalize">{r.role}</td>
                <td className="py-2.5"><StatusBadge status={r.status} /></td>
                <td className="py-2.5 text-right">
                  {d.can_manage && <RowMenu items={r.kind === "member"
                    ? (r.role === "owner" || r.you ? [] : [{ label: "Remove", danger: true,
                        onClick: () => setConfirm({ text: `Remove ${r.email} from this project?`, run: () => api.removeMember(project, r.user_id) }) }])
                    : [{ label: r.status === "expired" ? "Send a new invitation" : "New invitation link",
                         onClick: () => api.resendInvite(project, r.id).then(x => { setLink({ ...x, email: r.email }); refresh(); }) },
                       { label: "Revoke invitation", danger: true,
                         onClick: () => setConfirm({ text: `Revoke the invitation for ${r.email}?`, run: () => api.revokeInvite(project, r.id) }) }]} />}
                </td>
              </tr>))}
          </tbody>
        </table>
      )}
      {confirm && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-bad/40 bg-bad/5 px-3 py-2 text-sm">
          <span>{confirm.text}</span>
          <span className="flex gap-2"><Button kind="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button kind="danger" onClick={() => act.mutate(confirm.run)} disabled={act.isPending}>Yes</Button></span>
        </div>)}
      {act.error && <div className="mt-2 text-xs text-bad">{errorText(act.error)}</div>}
      {link && <Dialog title="Invitation link" onClose={() => setLink(null)}><LinkBox {...link} />
        <div className="mt-4 flex justify-end"><Button kind="primary" onClick={() => setLink(null)}>Done</Button></div></Dialog>}
      {inviting && <InviteDialog project={project} onClose={onInvited} />}
    </Card>
  );
}

export function ProjectSettings() {
  const { q, current } = useProjects();
  const [tab, setTab] = useState<"team" | "audit" | "overview">("team");
  const [renaming, setRenaming] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [copied, setCopied] = useState(false);
  const members = useQuery({ queryKey: ["members", current?.id], queryFn: () => api.members(current!.id), enabled: !!current });
  if (!current) return <div className="text-sm text-muted">{q.error ? errorText(q.error) : "Loading…"}</div>;
  const rows: [string, React.ReactNode][] = [
    ["Agents", current.agents], ["Connections (LLM / STT / TTS)", current.connections], ["MCP servers", current.mcp_servers],
    ["Tools in the library", current.tools], ["Secrets", current.secrets],
    ["Phone numbers / routes", current.routes.length ? current.routes.join(", ") : "—"],
  ];
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line pb-4">
        <div>
          <h1 className="text-2xl font-semibold">Project settings</h1>
          <p className="text-sm text-muted">Manage your project members and invitations.</p>
        </div>
        {members.data?.can_manage && members.data.accounts &&
          <Button kind="primary" onClick={() => { setTab("team"); setInviting(true); }}>👤＋ Invite member</Button>}
      </div>
      <div className="flex gap-1 border-b border-line">
        {([["team", "Team members"], ["audit", "Audit log"], ["overview", "Overview"]] as const).map(([t, l]) => (
          <button key={t} onClick={() => setTab(t)} className={cx("-mb-px border-b-2 px-3 py-2 text-sm", tab === t ? "border-accent font-medium" : "border-transparent text-muted hover:text-ink")}>{l}</button>))}
      </div>
      {tab === "team" && <TeamMembers project={current.id} inviting={inviting} onInvited={() => setInviting(false)} />}
      {tab === "audit" && <AuditLog project={current.id} />}
      {tab === "overview" && <>
      <Card title="Project" actions={current.mine ? <Button onClick={() => setRenaming(true)}>✎ Rename</Button> : undefined}>
        <div className="flex items-center gap-3">
          <Avatar p={current} size="h-10 w-10 text-base" />
          <div className="min-w-0">
            <div className="text-base font-semibold">{current.name}{current.label && <span className="ml-2 text-sm font-normal text-muted">(your label: {current.label})</span>}</div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <span className="font-mono">ID: {current.id}</span>
              <button className="text-accent-text hover:underline" onClick={() => copyText(current.id).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); })}>
                {copied ? "Copied ✓" : "Copy"}</button>
              {current.created_at && <span>· created {fmtTime(current.created_at)}</span>}
              {current.owner && <span>· owner {current.owner}</span>}
              <span>· your role: {current.role}</span>
            </div>
          </div>
        </div>
      </Card>
      <Card title="What's in this project">
        <table className="w-full text-sm"><tbody>
          {rows.map(([k, v]) => <tr key={k} className="border-b border-line last:border-0"><td className="py-2 text-muted">{k}</td><td className="py-2 text-right">{v}</td></tr>)}
        </tbody></table>
      </Card>
      </>}
      {renaming && <RenameProjectDialog project={current} onClose={() => setRenaming(false)} />}
    </div>
  );
}
