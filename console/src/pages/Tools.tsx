import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api, getProject, isSecretRef, type LibraryTool, type McpServer, type SecretRow, type ToolAuth, type ToolLibrary,
  type ToolPolicy, type ToolTestOverride,
} from "../api";
import { copyText } from "../Projects";
import { CI, CopyButton, RowMenu, usePopover } from "../table";
import { Badge, Button, Empty, ErrorBox, cx } from "../ui";

// Tool library (Hamsa's "Integration Tools" / Tools Templates): tool cards on the left (collections, drafts, a ⋮ menu
// per card), the selected tool on the right — its settings, parameters, the policy the platform enforces, a test
// dialog — and the add / edit form in the same pane.

const kindTone = (k: string) => (k === "write" ? "warn" : k === "send" ? "info" : "neutral") as "warn" | "info" | "neutral";
const json = (v: unknown) => (v == null ? "" : JSON.stringify(v, null, 2));
const SOURCE: Record<string, string> = { http: "API Request", mcp: "MCP Server", local: "Built-in", web: "Web Tool" };
const NEW_HTTP: ToolPolicy = { kind: "read", source: "http", description: "", input_schema: { type: "object", properties: {}, required: [] },
                               http: { method: "POST", url: "", headers: { "Content-Type": "application/json" } } };
/** A web tool runs in the visitor's browser: no URL or authentication, only what the agent may call it with. */
const NEW_WEB: ToolPolicy = { kind: "read", source: "web", description: "", input_schema: { type: "object", properties: {}, required: [] }, timeout_s: 10 };
const NAME_MAX = 64, DESC_MAX = 1000;

const tico = (d: ReactNode, cls = "h-4 w-4") => (
  <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round">{d}</svg>
);
const TI = {
  code: tico(<path d="m16 18 6-6-6-6M8 6l-6 6 6 6" />, "h-5 w-5"),
  bolt: tico(<path d="M13 2 3 14h9l-1 8 10-12h-9z" />, "h-5 w-5"),
  cube: tico(<><path d="M21 16V8l-9-5-9 5v8l9 5z" /><path d="m3.3 7 8.7 5 8.7-5M12 22V12" /></>, "h-5 w-5"),
  globe: tico(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>),
  info: tico(<><circle cx="12" cy="12" r="9" /><path d="M12 16v-4M12 8h.01" /></>),
  gear: tico(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>),
  shield: tico(<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />),
  hash: tico(<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18" />),
  clock: tico(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  server: tico(<><rect x="2" y="3" width="20" height="8" rx="2" /><rect x="2" y="13" width="20" height="8" rx="2" /><path d="M6 7h.01M6 17h.01" /></>),
  key: tico(<><circle cx="7.5" cy="15.5" r="5.5" /><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3" /></>),
  lock: tico(<><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>),
  play: tico(<path d="m6 3 14 9-14 9z" />),
  plus: tico(<path d="M12 5v14M5 12h14" />),
  chev: tico(<path d="m6 9 6 6 6-6" />),
  chevR: tico(<path d="m9 6 6 6-6 6" />),
  back: tico(<path d="m15 18-6-6 6-6" />),
  edit: tico(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></>),
  folder: tico(<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2z" />),
  trash: tico(<><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" /></>),
  spark: tico(<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />),
};
const CONFIRM_HELP: Record<string, string> = {
  none: "Runs as soon as the agent calls it",
  affirm: "The caller must say yes before it runs",
  readback: "The agent reads the details back; it runs on the caller's “yes” in a later turn",
};
const AUTH_HELP: Record<string, string> = {
  none: "No authentication required. Use this for public APIs that don't require credentials.",
  bearer: "Sends Authorization: Bearer <token>. Keep the token in a secret.",
  token: "Sends Authorization: Token <token> (Django REST framework style). Keep the token in a secret.",
  basic: "Sends Authorization: Basic with a username and password.",
  api_key: "Sends the key in a header of your choice (e.g. X-API-Key).",
};
const AUTH_LABEL: Record<string, string> = { none: "No Authentication", bearer: "Bearer Token", token: "Token", basic: "Basic Authentication", api_key: "API Key" };
const icon = (source: string) => (source === "http" ? TI.code : source === "mcp" ? TI.bolt : source === "web" ? TI.globe : TI.cube);
const label = (cls = "") => cx("mb-1 block text-sm font-medium", cls);

// ---------------------------------------------------------------- JSON schema helpers

type Prop = { type?: string | string[]; description?: string; enum?: unknown[]; items?: { type?: string }; [k: string]: unknown };
type Schema = { type?: string; properties?: Record<string, Prop>; required?: string[]; [k: string]: unknown };
const PTYPES = ["string", "number", "integer", "boolean", "array", "object"];
const PNAME = /^[A-Za-z_][A-Za-z0-9_.-]*$/;

/** Why a parameters schema isn't usable, or null when it is. */
function schemaProblem(s: unknown): string | null {
  if (!s || typeof s !== "object" || Array.isArray(s)) return "The schema must be a JSON object";
  const sc = s as Schema;
  if (sc.type !== "object") return 'The top level needs "type": "object"';
  const props = sc.properties ?? {};
  if (typeof props !== "object" || Array.isArray(props)) return '"properties" must be an object';
  for (const [k, v] of Object.entries(props)) {
    if (!PNAME.test(k)) return `Parameter name “${k}”: letters, digits and _ only`;
    const types = Array.isArray(v?.type) ? v.type : [v?.type];
    if (!v || typeof v !== "object" || !types.every(t => typeof t === "string" && PTYPES.includes(t))) return `“${k}” needs a type (${PTYPES.join(", ")})`;
  }
  if (sc.required && (!Array.isArray(sc.required) || sc.required.some(r => !(r in props)))) return '"required" lists a parameter that doesn\'t exist';
  return null;
}
const propCount = (s: unknown) => Object.keys(((s ?? {}) as Schema).properties ?? {}).length;

// ---------------------------------------------------------------- secrets in tool settings

/** A setting that may be a plain value, a secret reference, or a secret to create when the form is saved. */
type NewSecret = { newSecret: { name: string; value: string } };
const isNewSecret = (v: unknown): v is NewSecret => !!v && typeof v === "object" && "newSecret" in (v as object);
const secretName = (s: string) => s.toUpperCase().replace(/[^A-Z0-9_]/g, "_").replace(/^[^A-Z]+/, "").slice(0, 64);

/** Stores the secrets typed into the form and swaps them for references. */
async function materialize<T>(v: T): Promise<T> {
  if (isNewSecret(v)) {
    const { name, value } = v.newSecret;
    if (!/^[A-Z][A-Z0-9_]{1,63}$/.test(name)) throw new Error(`Secret name “${name}”: capital letters, digits and _ (e.g. MY_API_KEY)`);
    if (!value) throw new Error(`Type the value of the new secret ${name}`);
    await api.putSecret(name, value);
    return { secret: name } as T;
  }
  if (Array.isArray(v)) return (await Promise.all(v.map(materialize))) as T;
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v)) out[k] = await materialize(x);
    return out as T;
  }
  return v;
}
/** For a draft kept in the browser: never keep the value of a secret being typed. */
function withoutSecretValues<T>(v: T): T {
  if (isNewSecret(v)) return { newSecret: { name: v.newSecret.name, value: "" } } as T;
  if (Array.isArray(v)) return v.map(withoutSecretValues) as T;
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, withoutSecretValues(x)])) as T;
  return v;
}

function SecretInput({ id, value, onChange, secrets, placeholder, preferSecret }: {
  id: string; value: unknown; onChange: (v: unknown) => void; secrets: SecretRow[]; placeholder?: string; preferSecret?: boolean;
}) {
  const ref = isSecretRef(value) ? value.secret : null;
  const fresh = isNewSecret(value) ? value.newSecret : null;
  const secretMode = ref !== null || fresh !== null;
  const toggle = (
    <button type="button" onClick={() => onChange(secretMode ? "" : (secrets.length ? { secret: secrets[0].name } : { newSecret: { name: "", value: "" } }))}
      title={secretMode ? "Type a plain value instead" : "Use a secret (stored encrypted)"} aria-label={secretMode ? "Use a plain value" : "Use a secret"}
      className={cx("shrink-0 rounded-md border border-line p-2 hover:bg-soft", secretMode ? "text-accent-text" : "text-muted")}>{secretMode ? TI.lock : TI.key}</button>
  );
  if (!secretMode) return (
    <div className="flex gap-2">
      <input id={id} className="min-w-0 flex-1" value={String(value ?? "")} placeholder={placeholder ?? (preferSecret ? "a plain value — or use a secret →" : "")}
        onChange={e => onChange(e.target.value)} />{toggle}
    </div>
  );
  return (
    <div className="flex flex-wrap gap-2">
      <select id={id} className="min-w-0 flex-1" value={fresh ? "__new" : ref ?? ""}
        onChange={e => onChange(e.target.value === "__new" ? { newSecret: { name: "", value: "" } } : { secret: e.target.value })}>
        {ref !== null && !secrets.some(s => s.name === ref) && <option value={ref}>🔒 {ref} (not set)</option>}
        {secrets.map(s => <option key={s.name} value={s.name}>🔒 {s.name} {s.hint}</option>)}
        <option value="__new">+ New secret…</option>
      </select>
      {toggle}
      {fresh && (
        <div className="flex w-full gap-2">
          <input aria-label="New secret name" className="w-2/5 font-mono text-xs" placeholder="MY_API_KEY" value={fresh.name}
            onChange={e => onChange({ newSecret: { ...fresh, name: secretName(e.target.value) } })} />
          <input aria-label="New secret value" type="password" autoComplete="off" className="min-w-0 flex-1" placeholder="value (stored encrypted)"
            value={fresh.value} onChange={e => onChange({ newSecret: { ...fresh, value: e.target.value } })} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- small pieces

function Section({ icon: ic, title, children, actions }: { icon: ReactNode; title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-panel p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 font-semibold"><span className="text-muted">{ic}</span>{title}</h3>{actions}
      </div>
      {children}
    </section>
  );
}

function Fact({ icon: ic, label: l, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <span className="mt-0.5 text-muted">{ic}</span>
      <div className="min-w-0 flex-1"><div className="text-xs text-muted">{l}</div><div className="mt-0.5 text-sm">{children}</div></div>
    </div>
  );
}

function Dialog({ title, sub, onClose, children, footer, wide }: {
  title: string; sub?: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-[6vh]" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-label={title} className={cx("flex max-h-[88vh] w-full flex-col rounded-xl border border-line bg-panel shadow-xl", wide ? "max-w-4xl" : "max-w-2xl")}>
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div><h2 className="text-lg font-semibold">{title}</h2>{sub && <p className="mt-0.5 text-sm text-muted">{sub}</p>}</div>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-muted hover:bg-soft hover:text-ink">✕</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

function AddMenu({ onApi, onMcp, onWeb }: { onApi: () => void; onMcp: () => void; onWeb: () => void }) {
  const { open, setOpen, ref } = usePopover();
  const item = (ic: ReactNode, title: string, sub: string, onClick?: () => void) => (
    <button disabled={!onClick} onClick={() => { setOpen(false); onClick?.(); }} title={onClick ? undefined : sub}
      className="flex w-full items-start gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-soft disabled:cursor-not-allowed disabled:opacity-50">
      <span className="mt-0.5 text-muted">{ic}</span>
      <span className="min-w-0 flex-1"><span className="flex items-center gap-1.5 text-sm">{title}
        {!onClick && <span className="rounded bg-soft px-1.5 text-[10px] font-medium">Soon</span>}</span>
        <span className="block text-[11px] text-muted">{sub}</span></span>
    </button>
  );
  return (
    <div ref={ref} className="relative">
      <button id="add-tool" onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-accent-fg hover:opacity-90">{TI.plus}Add New Tool</button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 w-64 rounded-lg border border-line bg-panel p-1 shadow-lg">
          {item(TI.code, "API Request", "Call an HTTP endpoint", onApi)}
          {item(TI.bolt, "MCP Server", "Connect a server; add its tools", onMcp)}
          {item(TI.globe, "Web Tool", "Runs in the visitor's browser — your website registers the function", onWeb)}
        </div>
      )}
    </div>
  );
}

function ToolCard({ t, active, onClick, menu }: { t: LibraryTool; active: boolean; onClick: () => void; menu: ReactNode }) {
  const live = t.available && t.used_by.length > 0;
  return (
    <div role="button" tabIndex={0} onClick={onClick} onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onClick())}
      className={cx("group w-full cursor-pointer rounded-xl border p-3 text-left transition",
        active ? "border-accent bg-accent/5 ring-1 ring-accent" : "border-line bg-panel hover:border-accent/60")}>
      <div className="flex gap-2.5">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent/15 text-accent-text">{icon(t.source)}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-1">
            <div className="min-w-0 flex-1 truncate text-sm font-medium" title={t.name}>{t.name}</div>
            <div className={cx("-mt-1 -mr-1 shrink-0", !active && "opacity-0 group-hover:opacity-100 focus-within:opacity-100")}>{menu}</div>
          </div>
          {t.description && <div className="line-clamp-2 text-xs text-muted">{t.description}</div>}
          <div className="mt-1.5 flex flex-wrap gap-1">
            <span className="rounded border border-line bg-soft px-1.5 py-0.5 text-[10px]">{SOURCE[t.source] ?? t.source}</span>
            {!t.available ? <span className="rounded bg-bad/15 px-1.5 py-0.5 text-[10px] text-bad">Not connected</span>
              : live ? <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] font-medium text-accent-fg">In use</span>
              : <span className="rounded bg-soft px-1.5 py-0.5 text-[10px] text-muted">Unused</span>}
            {t.policy.kind !== "read" && <Badge tone={kindTone(t.policy.kind)}>{t.policy.kind}</Badge>}
            {t.policy.enabled === false && <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn">Inactive</span>}
            {t.policy.async && <span className="rounded border border-line bg-soft px-1.5 py-0.5 text-[10px]">Async</span>}
          </div>
        </div>
      </div>
    </div>
  );
}

function DraftCard({ d, active, onClick }: { d: Draft; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cx("w-full rounded-xl border border-dashed p-3 text-left transition",
      active ? "border-accent bg-accent/5 ring-1 ring-accent" : "border-line bg-soft/40 hover:border-accent/60")}>
      <div className="flex gap-2.5">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent/15 text-accent-text">{TI.code}</span>
        <div className="min-w-0 flex-1">
          <div className={cx("truncate text-sm", d.name ? "font-medium" : "italic text-muted")}>{d.name || "Enter tool name…"}</div>
          <div className={cx("line-clamp-2 text-xs", !d.policy.description && "italic", "text-muted")}>{d.policy.description || "Enter tool description…"}</div>
          <div className="mt-1.5 flex gap-1">
            <span className="rounded border border-line bg-soft px-1.5 py-0.5 text-[10px]">{SOURCE[d.policy.source ?? "http"]}</span>
            <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn">Draft</span>
          </div>
        </div>
      </div>
    </button>
  );
}

function headerValue(v: unknown) {
  if (isSecretRef(v)) return <span className="inline-flex items-center gap-1">{TI.lock}<span className="font-mono">{v.secret}</span></span>;
  return <span className="font-mono">{String(v)}</span>;
}

function authOf(p: ToolPolicy) {
  const a = p.http?.auth;
  if (a && a.type !== "none") {
    const part = a.type === "bearer" || a.type === "token" ? a.token : a.type === "basic" ? a.password : a.value;
    return `${AUTH_LABEL[a.type]}${isSecretRef(part) ? ` · secret ${part.secret}` : ""}${a.type === "api_key" ? ` · ${a.header}` : ""}`;
  }
  const h = Object.entries(p.http?.headers ?? {}).find(([k]) => k.toLowerCase() === "authorization");
  if (!h) return "No Authentication";
  if (isSecretRef(h[1])) return `Authorization header from secret ${h[1].secret}`;
  const v = String(h[1]).toLowerCase();
  return v.startsWith("bearer") ? "Bearer Token" : v.startsWith("basic") ? "Basic Authentication" : "Authorization header";
}

// ---------------------------------------------------------------- test dialog

/** How the website that embeds the agent supplies the function (the dashboard only defines the tool). */
function WebRegistration({ name, props }: { name: string; props: string[] }) {
  const args = props.length ? `{ ${props.join(", ")} }` : "args";
  const code = `<script src="${window.location.origin}/embed.js" data-token="YOUR_SHARE_TOKEN"></script>
<script>
  VoiceAgent.registerTools({
    ${name}: async function (${args}) {
      // do the work in the page, then tell the agent what happened
      return { ok: true };   // a string or a JSON object goes back to the agent
    }
  });
</script>`;
  return (
    <Section icon={TI.globe} title="Register it on your website" actions={<CopyButton text={code} label="Copy snippet" />}>
      <p className="mb-2 text-sm text-muted">The agent can call this tool only on a web call whose page registered a function with this exact name. The page gets the arguments the agent
        chose as one object, and may answer with text or JSON (up to 20,000 characters, within the timeout). On a phone call, or if the page didn't register it, the agent never
        sees the tool and a flow's tool node takes its failure path. Use the embed widget from the Share page — <span className="font-mono">YOUR_SHARE_TOKEN</span> is its token.</p>
      <pre className="overflow-x-auto rounded-lg bg-soft p-3 font-mono text-[11px] leading-relaxed">{code}</pre>
    </Section>
  );
}

function TestDialog({ t, onClose }: { t: LibraryTool; onClose: () => void }) {
  const p = t.policy;
  const schema = (t.input_schema ?? p.input_schema ?? {}) as Schema;
  const props = Object.entries(schema.properties ?? {});
  const runs = p.kind === "read";
  const [values, setValues] = useState<Record<string, string>>({});
  const [adv, setAdv] = useState(false);
  const http = t.source === "http";
  const base = { url: p.http?.url ?? "", method: p.http?.method ?? "GET", timeout: String(p.timeout_s ?? 10),
                 headers: Object.entries(p.http?.headers ?? {}).map(([k, v]) => ({ k, v: v as unknown })) };
  const [ov, setOv] = useState(base);
  const [problem, setProblem] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; ms: number; data?: unknown; error?: string } | null>(null);
  const run = useMutation({ mutationFn: (x: { args: Record<string, unknown>; override?: ToolTestOverride }) => api.testTool(t.name, x.args, x.override),
                            onSuccess: setResult });
  const typeOf = (v: Prop) => (Array.isArray(v.type) ? v.type.find(x => x !== "null") ?? "string" : v.type ?? "string");
  const execute = () => {
    const args: Record<string, unknown> = {};
    for (const [k, v] of props) {
      const raw = (values[k] ?? "").trim();
      if (!raw) { if (schema.required?.includes(k)) { setProblem(`Fill in ${k} (required)`); return; } continue; }
      const ty = typeOf(v);
      if (ty === "number" || ty === "integer") {
        if (Number.isNaN(Number(raw))) { setProblem(`${k} must be a number`); return; }
        args[k] = Number(raw);
      } else if (ty === "boolean") args[k] = raw === "true";
      else if (ty === "array" || ty === "object") {
        try { args[k] = JSON.parse(raw); } catch { setProblem(`${k}: write it as JSON (e.g. ${ty === "array" ? '["a", "b"]' : '{"a": 1}'})`); return; }
      } else args[k] = raw;
    }
    let override: ToolTestOverride | undefined;
    if (http && adv) {
      const to = Number(ov.timeout);
      if (!(to >= 1 && to <= 60)) { setProblem("Timeout: 1 to 60 seconds"); return; }
      override = {};
      if (ov.url !== base.url) override.url = ov.url;
      if (ov.method !== base.method) override.method = ov.method;
      if (ov.timeout !== base.timeout) override.timeout_s = to;
      if (json(ov.headers) !== json(base.headers)) override.headers = Object.fromEntries(ov.headers.filter(h => h.k.trim()).map(h => [h.k.trim(), h.v]));
    }
    setProblem(null);
    run.mutate({ args, override });
  };
  return (
    <Dialog wide title={`Test Tool: ${t.name}`} sub="Test your tool with custom parameters and review the response structure" onClose={onClose}
      footer={<>
        <span className="text-xs text-muted">{result ? <>Last run: <span className={result.ok ? "text-good" : "text-bad"}>{result.ok ? "Success" : "Failed"}</span> · {result.ms} ms</> : "No test data available"}</span>
        <Button onClick={onClose}>Close</Button>
      </>}>
      <div className="space-y-4">
        <div className="rounded-xl border border-line bg-soft/40 p-4">
          <div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold">Tool Information</span>
            <span className="rounded border border-line bg-panel px-1.5 py-0.5 text-[10px] uppercase">{SOURCE[t.source] ?? t.source} · {p.kind}</span></div>
          <p className="mt-1.5 text-sm text-muted">{t.description || p.description || "—"}</p>
        </div>

        {http && (
          <div className="rounded-xl border border-line">
            <button onClick={() => setAdv(!adv)} className="flex w-full items-center justify-between gap-2 p-4 text-left">
              <span><span className="block text-sm font-semibold">Advanced Configuration</span>
                <span className="text-xs text-muted">Override tool settings for this test only</span></span>
              <span className={cx("rounded-md p-1.5", adv ? "bg-soft" : "")}>{adv ? TI.chev : TI.chevR}</span>
            </button>
            {adv && (
              <div className="space-y-3 border-t border-line p-4">
                <label className="block"><span className={label()}>API URL <span className="text-xs font-normal text-muted">(default: {base.url})</span></span>
                  <input id="test-url" className="w-full font-mono text-xs" value={ov.url} onChange={e => setOv({ ...ov, url: e.target.value })} /></label>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block"><span className={label()}>HTTP Method <span className="text-xs font-normal text-muted">(default: {base.method})</span></span>
                    <select id="test-method" className="w-full" value={ov.method} onChange={e => setOv({ ...ov, method: e.target.value })}>
                      {["GET", "POST", "PUT", "PATCH", "DELETE"].map(m => <option key={m}>{m}</option>)}</select></label>
                  <label className="block"><span className={label()}>Request Timeout (seconds) <span className="text-xs font-normal text-muted">(default: {base.timeout})</span></span>
                    <input id="test-timeout" type="number" min={1} max={60} className="w-full" value={ov.timeout} onChange={e => setOv({ ...ov, timeout: e.target.value })} />
                    <span className="mt-1 block text-[11px] text-muted">Request timeout in seconds (1-60)</span></label>
                </div>
                <div className="text-xs text-muted">Authentication: {authOf(p)} (as saved)</div>
                <div>
                  <div className="mb-1 flex items-center justify-between"><span className="text-sm font-medium">Custom HTTP Headers</span>
                    <Button onClick={() => setOv({ ...ov, headers: [...ov.headers, { k: "", v: "" }] })}>+ Add Header</Button></div>
                  <div className="space-y-2">
                    {ov.headers.map((h, i) => (
                      <div key={i} className="flex gap-2">
                        <input aria-label="Header name" className="min-w-0 flex-1" value={h.k} onChange={e => setOv({ ...ov, headers: ov.headers.map((x, j) => j === i ? { ...x, k: e.target.value } : x) })} />
                        {isSecretRef(h.v) ? <span className="flex min-w-0 flex-1 items-center gap-1 rounded-lg border border-line bg-soft px-2.5 text-xs">{TI.lock}{h.v.secret}</span>
                          : <input aria-label="Header value" className="min-w-0 flex-1" value={String(h.v)} onChange={e => setOv({ ...ov, headers: ov.headers.map((x, j) => j === i ? { ...x, v: e.target.value } : x) })} />}
                        <button aria-label="Remove header" onClick={() => setOv({ ...ov, headers: ov.headers.filter((_, j) => j !== i) })} className="rounded p-2 text-bad hover:bg-bad/10">{TI.trash}</button>
                      </div>
                    ))}
                    {!ov.headers.length && <p className="text-xs text-muted">No headers.</p>}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="rounded-xl border border-line p-4">
          <div className="mb-3 text-sm font-semibold">Test Parameters</div>
          {!props.length && <p className="mb-3 text-sm text-muted">This tool takes no parameters.</p>}
          <div className="space-y-3">
            {props.map(([k, v]) => {
              const ty = typeOf(v), req = schema.required?.includes(k);
              const id = `test-arg-${k}`;
              return (
                <div key={k}>
                  <div className="mb-1 flex items-center justify-between gap-2"><label htmlFor={id} className="font-mono text-sm font-medium">{k}</label>
                    <span className="flex items-center gap-1.5 text-[11px] text-muted">{ty}<span className={cx("rounded px-1.5 py-0.5", req ? "bg-bad/10 text-bad" : "bg-soft")}>{req ? "Required" : "Optional"}</span></span></div>
                  {v.enum ? (
                    <select id={id} className="w-full" value={values[k] ?? ""} onChange={e => setValues({ ...values, [k]: e.target.value })}>
                      <option value="">—</option>{v.enum.map(x => <option key={String(x)}>{String(x)}</option>)}</select>
                  ) : ty === "boolean" ? (
                    <select id={id} className="w-full" value={values[k] ?? ""} onChange={e => setValues({ ...values, [k]: e.target.value })}>
                      <option value="">—</option><option>true</option><option>false</option></select>
                  ) : ty === "array" || ty === "object" ? (
                    <textarea id={id} rows={2} className="w-full font-mono text-xs" placeholder={ty === "array" ? '["…"]' : '{"…": "…"}'}
                      value={values[k] ?? ""} onChange={e => setValues({ ...values, [k]: e.target.value })} />
                  ) : (
                    <input id={id} type={ty === "number" || ty === "integer" ? "number" : "text"} className="w-full" value={values[k] ?? ""}
                      onChange={e => setValues({ ...values, [k]: e.target.value })} />
                  )}
                  {v.description && <p className="mt-1 text-[11px] text-muted">{v.description}</p>}
                </div>
              );
            })}
          </div>
          {problem && <div className="mt-3"><ErrorBox error={problem} /></div>}
          <ErrorBox error={run.error} />
          <button id="test-execute" disabled={!runs || run.isPending} onClick={execute}
            className="mt-4 w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
            {run.isPending ? "Running…" : "Execute Test"}</button>
          {!runs && <p className="mt-2 text-xs text-warn">This is a {p.kind} tool — it books, changes or sends something, so it doesn't run from here.
            Try it in a test call (with TOOLS_MODE=hybrid it is simulated).</p>}
        </div>

        {result && (
          <div className="rounded-xl border border-line p-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-semibold">Response <Badge tone={result.ok ? "good" : "bad"}>{result.ok ? "success" : "failed"}</Badge>
                <span className="text-xs font-normal text-muted">{result.ms} ms</span></span>
              <CopyButton text={json(result.data ?? result.error)} label="Copy response" />
            </div>
            <pre className="max-h-80 overflow-auto rounded-lg bg-soft p-3 text-[11px]">{json(result.data ?? result.error)}</pre>
          </div>
        )}

        <div className="rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
          <div className="mb-1 font-semibold">How to use this test tool</div>
          <ul className="list-disc space-y-0.5 pl-5">
            <li>Fill in the parameter values above</li>
            <li>Click Execute Test to run the call</li>
            <li>Review the response to understand the data structure</li>
            <li>Test results are for preview only and are not saved; only read tools run here</li>
          </ul>
        </div>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- parameters (schema) editor

type Row = { name: string; type: string; required: boolean; description: string; enumText: string; itemsType: string; extra: Prop };
const rowsOf = (s: Schema): Row[] => Object.entries(s.properties ?? {}).map(([name, v]) => ({
  name, type: (Array.isArray(v.type) ? v.type[0] : v.type) ?? "string", required: !!s.required?.includes(name),
  description: v.description ?? "", enumText: (v.enum ?? []).map(String).join(", "), itemsType: v.items?.type ?? "string", extra: v,
}));
function schemaOf(rows: Row[], base: Schema): Schema {
  const properties: Record<string, Prop> = {};
  for (const r of rows) {
    const { enum: _e, items: _i, ...rest } = r.extra;
    void _e; void _i;
    const p: Prop = { ...rest, type: r.type, description: r.description || undefined };
    if (r.type === "string" && r.enumText.trim()) p.enum = r.enumText.split(",").map(x => x.trim()).filter(Boolean);
    if (r.type === "array") p.items = { ...(r.extra.items ?? {}), type: r.itemsType };
    if (!p.description) delete p.description;
    properties[r.name.trim()] = p;
  }
  return { ...base, type: "object", properties, required: rows.filter(r => r.required).map(r => r.name.trim()) };
}

function SchemaDialog({ init, toolName, description, onSave, onClose }: {
  init: Schema; toolName: string; description: string; onSave: (s: Schema) => void; onClose: () => void;
}) {
  const [mode, setMode] = useState<"visual" | "json">("visual");
  const [rows, setRows] = useState<Row[]>(rowsOf(init));
  const [text, setText] = useState(json(init));
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const current = (): Schema | null => {
    if (mode === "visual") {
      const names = rows.map(r => r.name.trim());
      if (names.some(n => !n)) { setErr("Every parameter needs a name"); return null; }
      if (new Set(names).size !== names.length) { setErr("Two parameters have the same name"); return null; }
      return schemaOf(rows, init);
    }
    try { return JSON.parse(text) as Schema; } catch (e) { setErr(`Invalid JSON: ${(e as Error).message}`); return null; }
  };
  const switchTo = (m: "visual" | "json") => {
    if (m === mode) return;
    const s = current();
    if (!s) return;
    const problem = schemaProblem(s);
    if (m === "visual" && problem) { setErr(problem); return; }
    setErr(null);
    if (m === "json") setText(json(s)); else setRows(rowsOf(s));
    setMode(m);
  };
  const save = () => {
    const s = current();
    if (!s) return;
    const problem = schemaProblem(s);
    if (problem) { setErr(problem); return; }
    onSave(s);
  };
  const prompt = `Write the JSON Schema for the parameters of a tool that an AI voice agent calls.
Tool name: ${toolName || "(not named yet)"}
What it does: ${description || "(describe it)"}

Rules: reply with the JSON only. The top level is {"type": "object", "properties": {...}, "required": [...]}.
Each property has a "type" (string, number, integer, boolean, array or object) and a "description" that tells the
agent what to put there (format, units, an example). Use "enum" for a fixed list of choices. Use the parameter
names the API expects.`;
  const set = (i: number, patch: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <Dialog wide title="Request Body Parameters" sub="Define the parameters the agent fills in when it calls the tool" onClose={onClose}
      footer={<>
        <span className="text-xs text-muted">{mode === "visual" ? `${rows.length} parameter${rows.length === 1 ? "" : "s"}` : "JSON Schema"}</span>
        <div className="flex gap-2"><Button onClick={onClose}>Cancel</Button><Button kind="primary" onClick={save}>Save</Button></div>
      </>}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <button onClick={() => { void copyText(prompt); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
          title="Copy this prompt and paste it into your AI assistant (ChatGPT, Claude, …) to generate a valid schema"
          className="flex items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 dark:border-violet-900 dark:bg-violet-950/40 dark:text-violet-300">
          {TI.spark}{copied ? "Copied!" : "AI Prompt"}</button>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 text-xs text-muted">Visual
            <button role="switch" aria-checked={mode === "json"} aria-label="JSON mode" onClick={() => switchTo(mode === "json" ? "visual" : "json")}
              className={cx("relative h-5 w-9 rounded-full transition", mode === "json" ? "bg-accent" : "bg-line")}>
              <span className={cx("absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all", mode === "json" ? "left-[18px]" : "left-0.5")} />
            </button>JSON</span>
          {mode === "visual"
            ? <Button onClick={() => setRows([...rows, { name: "", type: "string", required: false, description: "", enumText: "", itemsType: "string", extra: {} }])}>+ Add Parameter</Button>
            : <Button onClick={() => { try { setText(json(JSON.parse(text))); setErr(null); } catch (e) { setErr(`Invalid JSON: ${(e as Error).message}`); } }}>Format</Button>}
        </div>
      </div>
      {err && <div className="mb-3"><ErrorBox error={err} /></div>}
      {mode === "json" ? (
        <div className="relative">
          <textarea id="schema-json" rows={18} spellCheck={false} className="w-full font-mono text-xs" value={text} onChange={e => setText(e.target.value)} />
          <span className="absolute top-2 right-2"><CopyButton text={text} label="Copy JSON" /></span>
        </div>
      ) : (
        <div className="space-y-3">
          {rows.map((r, i) => (
            <div key={i} className="rounded-xl border border-line p-4">
              <div className="grid gap-3 sm:grid-cols-[1fr_9rem_auto_auto] sm:items-end">
                <label className="block"><span className={label()}>Name *</span>
                  <input aria-label={`Parameter ${i + 1} name`} className="w-full font-mono" value={r.name} onChange={e => set(i, { name: e.target.value.replace(/\s/g, "_") })} /></label>
                <label className="block"><span className={label()}>Type *</span>
                  <select aria-label={`Parameter ${i + 1} type`} className="w-full" value={r.type} onChange={e => set(i, { type: e.target.value })}>
                    {PTYPES.map(x => <option key={x} value={x}>{x[0].toUpperCase() + x.slice(1)}</option>)}</select></label>
                <label className="flex items-center gap-1.5 pb-2 text-sm"><input type="checkbox" checked={r.required} onChange={e => set(i, { required: e.target.checked })} />Required</label>
                <button aria-label={`Remove parameter ${i + 1}`} onClick={() => setRows(rows.filter((_, j) => j !== i))} className="mb-1 rounded p-2 text-bad hover:bg-bad/10">{TI.trash}</button>
              </div>
              <label className="mt-3 block"><span className={label()}>Description *</span>
                <input aria-label={`Parameter ${i + 1} description`} className="w-full" value={r.description} placeholder="What the agent should put here (format, units, an example)"
                  onChange={e => set(i, { description: e.target.value })} /></label>
              {r.type === "string" && (
                <label className="mt-3 block"><span className={label()}>Allowed values <span className="text-xs font-normal text-muted">(optional, comma separated)</span></span>
                  <input aria-label={`Parameter ${i + 1} allowed values`} className="w-full" value={r.enumText} onChange={e => set(i, { enumText: e.target.value })} /></label>
              )}
              {r.type === "array" && (
                <label className="mt-3 block"><span className={label()}>Items are</span>
                  <select className="w-48" value={r.itemsType} onChange={e => set(i, { itemsType: e.target.value })}>
                    {PTYPES.filter(x => x !== "array").map(x => <option key={x}>{x}</option>)}</select></label>
              )}
            </div>
          ))}
          {!rows.length && <Empty>No parameters — the tool is called without arguments. Add one, or paste a schema in JSON mode.</Empty>}
        </div>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------- the selected tool

function ToolDetail({ t, lib, startRemoving, onEdit, onChanged }: {
  t: LibraryTool; lib: ToolLibrary; startRemoving: boolean; onEdit: () => void; onChanged: () => void;
}) {
  const p = t.policy;
  const [testing, setTesting] = useState(false);
  const [removing, setRemoving] = useState(startRemoving);
  const [removeFrom, setRemoveFrom] = useState("");
  const remove = useMutation({
    mutationFn: () => api.deleteTool(t.name, removeFrom === "__library" ? undefined : removeFrom),
    onSuccess: () => { setRemoving(false); setRemoveFrom(""); onChanged(); },
  });
  const schema = (t.input_schema ?? p.input_schema ?? {}) as Schema;
  const props = Object.entries(schema.properties ?? {});
  const headers = (p.http?.headers ?? {}) as Record<string, unknown>;
  const agentName = (id: string) => lib.agents.find(a => a.id === id)?.name ?? id;
  const url = p.http?.url ?? "";
  const confirm = p.confirm ?? (p.kind === "read" ? "none" : "affirm");
  return (
    <div className="space-y-4">
      <section className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-line bg-panel p-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-accent text-accent-fg">{icon(t.source)}</span>
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold">{t.name}</h2>
            <div className="mt-1 flex flex-wrap gap-1">
              {!t.available ? <Badge tone="bad">Not connected</Badge> : t.used_by.length
                ? <span className="rounded bg-accent px-1.5 py-0.5 text-[11px] font-medium text-accent-fg">In use</span>
                : <Badge>Unused</Badge>}
              <Badge tone={kindTone(p.kind)}>{p.kind}</Badge>
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <Button onClick={onEdit}><span className="flex items-center gap-1.5">{TI.edit}Edit</span></Button>
          <Button kind="ghost" onClick={() => setRemoving(x => !x)}><span className="flex items-center gap-1.5 text-bad">{TI.trash}Delete</span></Button>
        </div>
        {removing && (
          <div className="flex w-full flex-wrap items-center gap-2 rounded-lg bg-bad/5 p-3 text-sm">
            <span>Remove</span>
            <select id="tool-remove-from" className="text-sm" value={removeFrom} onChange={e => setRemoveFrom(e.target.value)}>
              <option value="">choose…</option>
              {t.used_by.map(a => <option key={a} value={a}>from {agentName(a)} (new release)</option>)}
              {t.used_by.length === 0 && <option value="__library">from the library</option>}
            </select>
            <Button kind="danger" disabled={!removeFrom || remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "Removing…" : "Remove"}</Button>
            <Button kind="ghost" onClick={() => setRemoving(false)}>Cancel</Button>
            {t.used_by.length > 0 && <span className="w-full text-xs text-muted">A tool leaves the library once no agent uses it.</span>}
            <div className="w-full"><ErrorBox error={remove.error} /></div>
          </div>
        )}
      </section>

      <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-panel p-5">
        <div><h3 className="font-semibold">Test Tool</h3>
          <p className="text-sm text-muted">Test this tool with sample parameters and review the response
            {p.kind !== "read" && " — only read tools run here; this one changes or sends something"}
            {t.source === "web" && " — a web tool runs in the visitor's browser, so it can't run from here: open your site (or the Share page) and talk to the agent"}</p></div>
        <Button onClick={() => setTesting(true)} disabled={t.source === "web"} title={t.source === "web" ? "Runs in the visitor's browser" : undefined}>Open Test Tool</Button>
      </section>
      {testing && <TestDialog t={t} onClose={() => setTesting(false)} />}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section icon={TI.info} title="Tool Information">
          <div className="space-y-3">
            <Fact icon={TI.info} label="Description"><span className="text-accent-text">{t.description || p.description || "—"}</span></Fact>
            <Fact icon={TI.code} label="Tool Type"><span className="rounded border border-line bg-soft px-1.5 py-0.5 text-xs">{SOURCE[t.source] ?? t.source}</span></Fact>
            <Fact icon={TI.folder} label="Collection"><span className="font-mono text-xs">{t.group}</span></Fact>
            <Fact icon={TI.cube} label="Used by">{t.used_by.length ? t.used_by.map(agentName).join(", ") : <span className="text-muted">No agent</span>}</Fact>
          </div>
        </Section>
        <Section icon={TI.gear} title="Tool Settings">
          <div className="space-y-3">
            {t.source === "http" ? <>
              <Fact icon={TI.server} label="URL">
                <div className="flex items-center gap-1"><span className="truncate rounded bg-soft px-2 py-1 font-mono text-xs" title={url}>{url}</span><CopyButton text={url} label="Copy URL" /></div>
              </Fact>
              <Fact icon={TI.code} label="Method"><span className="rounded border border-line px-1.5 py-0.5 font-mono text-xs">{p.http?.method ?? "GET"}</span></Fact>
              <Fact icon={TI.key} label="Authentication"><span className="text-accent-text">{authOf(p)}</span></Fact>
            </> : t.source === "mcp" ? (
              <Fact icon={TI.server} label="MCP server">{t.server ?? "—"} {!t.available && <Badge tone="bad">not connected</Badge>}</Fact>
            ) : t.source === "web" ? <Fact icon={TI.globe} label="Runs">in the visitor's browser — web calls only, never on a phone call</Fact>
              : <Fact icon={TI.cube} label="Runs">inside the platform (Python)</Fact>}
            <Fact icon={TI.clock} label="Timeout"><span className="text-accent-text">{p.timeout_s != null ? `${p.timeout_s} seconds` : "default (10 seconds)"}</span></Fact>
            {p.cache_ttl ? <Fact icon={TI.clock} label="Cache">{p.cache_ttl} seconds</Fact> : null}
          </div>
        </Section>
      </div>

      {t.source === "http" && (
        <Section icon={TI.hash} title="Headers">
          {Object.keys(headers).length ? (
            <div className="overflow-x-auto rounded-lg border border-line">
              <table className="w-full text-sm">
                <thead><tr className="border-b border-line bg-soft/60 text-xs text-muted"><th className="px-3 py-2 text-center font-medium">Header Name</th><th className="px-3 py-2 text-center font-medium">Header Value</th></tr></thead>
                <tbody>{Object.entries(headers).map(([k, v]) => (
                  <tr key={k} className="border-b border-line last:border-0"><td className="px-3 py-2 text-center">{k}</td><td className="px-3 py-2 text-center text-xs">{headerValue(v)}</td></tr>))}</tbody>
              </table>
            </div>
          ) : <p className="text-sm text-muted">No headers.</p>}
        </Section>
      )}

      {t.source === "web" && <WebRegistration name={t.name} props={props.map(([k]) => k)} />}

      <Section icon={TI.code} title="Parameters" actions={props.length ? <CopyButton text={json(schema)} label="Copy schema" /> : undefined}>
        {props.length ? (
          <div className="space-y-2">
            {props.map(([k, v]) => (
              <div key={k} className="rounded-lg bg-soft/60 px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-sm">{k}{schema.required?.includes(k) && <span className="text-bad"> *</span>}</span>
                  <span className="rounded-full border border-line px-2 text-[11px] text-sky-700 dark:text-sky-300">{Array.isArray(v.type) ? v.type.join(" | ") : v.type ?? "any"}</span>
                </div>
                {v.description && <div className="mt-0.5 text-xs text-muted">{v.description}</div>}
                {v.enum && <div className="mt-0.5 text-[11px] text-muted">one of: {v.enum.map(String).join(", ")}</div>}
              </div>
            ))}
          </div>
        ) : <p className="text-sm text-muted">{t.source === "mcp" && !t.available ? "The server isn't connected — its parameters can't be read." : "No parameters."}</p>}
      </Section>

      <Section icon={TI.shield} title="Policy · what the platform enforces">
        <div className="grid gap-3 sm:grid-cols-2">
          <Fact icon={TI.shield} label="Confirmation">{confirm}
            <span className="block text-[11px] text-muted">{CONFIRM_HELP[confirm] ?? ""}</span></Fact>
          <Fact icon={TI.shield} label="Success allows the claim">{p.backs ? `“${p.backs}”` : "—"}
            <span className="block text-[11px] text-muted">The agent may only say it after this succeeds</span></Fact>
          <Fact icon={TI.key} label="Verification role">{p.role ?? "—"}</Fact>
          <Fact icon={TI.hash} label="Line after success">{p.success_line ?? "—"}</Fact>
          <Fact icon={TI.gear} label="Checks (hooks)">{(p.hooks ?? []).length ? (p.hooks ?? []).join(", ") : "—"}</Fact>
          {p.idempotent != null && <Fact icon={TI.gear} label="Idempotent">{p.idempotent ? "yes" : "no"}</Fact>}
        </div>
      </Section>

      <Section icon={TI.clock} title="Metadata">
        <div className="grid gap-3 sm:grid-cols-2">
          {[["Tool name", t.name], ["Collection", t.group], ["Project ID", getProject() || "default"], ...(t.server ? [["MCP server", t.server]] : [])].map(([k, v]) => (
            <div key={k}><div className="text-xs text-muted">{k}</div>
              <div className="mt-0.5 flex items-center gap-1 rounded bg-soft px-2 py-1 font-mono text-xs"><span className="truncate">{v}</span><span className="ml-auto"><CopyButton text={v} /></span></div></div>
          ))}
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- move to collection

function MoveDialog({ t, collections, onClose, onMoved }: { t: LibraryTool; collections: string[]; onClose: () => void; onMoved: (g: string) => void }) {
  const [group, setGroup] = useState(t.group);
  const [fresh, setFresh] = useState("");
  const target = fresh ? fresh : group;
  const move = useMutation({ mutationFn: () => api.putTool(t.name, { group: target, policy: t.policy, note: `moved to ${target}` }),
                             onSuccess: () => onMoved(target) });
  return (
    <Dialog title="Move to Collection" sub={`${t.name} — agents that use it get a new release`} onClose={onClose}
      footer={<><span /><div className="flex gap-2"><Button onClick={onClose}>Cancel</Button>
        <Button kind="primary" disabled={!target || target === t.group || move.isPending} onClick={() => move.mutate()}>{move.isPending ? "Moving…" : "Move"}</Button></div></>}>
      <div className="space-y-1">
        {collections.map(g => (
          <label key={g} className={cx("flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-soft", !fresh && group === g && "bg-soft")}>
            <input type="radio" name="collection" checked={!fresh && group === g} onChange={() => { setGroup(g); setFresh(""); }} />
            <span className="text-muted">{TI.folder}</span><span className="font-mono">{g}</span>{g === t.group && <span className="text-xs text-muted">(current)</span>}
          </label>
        ))}
      </div>
      <label className="mt-3 block"><span className={label()}>Or a new collection</span>
        <input id="new-collection" className="w-full font-mono" placeholder="e.g. billing" value={fresh}
          onChange={e => setFresh(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, ""))} /></label>
      <div className="mt-3"><ErrorBox error={move.error} /></div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- the page

type Draft = { id: string; name: string; group: string; policy: ToolPolicy };
type Pane = { kind: "tool"; name: string; remove?: boolean }
  | { kind: "edit"; name: string; group: string; policy: ToolPolicy; isNew: boolean; draft?: string }
  | { kind: "servers"; add?: boolean } | null;

const store = {
  get<T>(k: string, d: T): T { try { const v = localStorage.getItem(k); return v ? (JSON.parse(v) as T) : d; } catch { return d; } },
  set(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const draftsKey = () => `tools.drafts.${getProject() || "default"}`;
const collectionsKey = () => `tools.collections.${getProject() || "default"}`;

export default function Tools() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["tool-library"], queryFn: api.toolLibrary });
  const secretsQ = useQuery({ queryKey: ["secrets"], queryFn: api.secrets });
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("");
  const [collection, setCollection] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "inactive">("all");
  const [browsing, setBrowsing] = useState(false);          // the collections list instead of the tools
  const [newCollection, setNewCollection] = useState<string | null>(null);
  const [extraCollections, setExtraCollections] = useState<string[]>(() => store.get(collectionsKey(), []));
  const [drafts, setDrafts] = useState<Draft[]>(() => store.get(draftsKey(), []));
  const [moving, setMoving] = useState<LibraryTool | null>(null);
  const [pane, setPane] = useState<Pane>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["tool-library"] });
  const saveDrafts = (d: Draft[]) => { setDrafts(d); store.set(draftsKey(), d.map(withoutSecretValues)); };
  const lib = q.data;
  const groups = useMemo(() => [...new Set([...(lib?.tools ?? []).map(t => t.group), ...extraCollections])].sort(), [lib, extraCollections]);
  const tools = useMemo(() => (lib?.tools ?? []).filter(t => (!collection || t.group === collection)
    && (status === "all" || (status === "inactive") === (t.policy.enabled === false))
    && (!filter || `${t.name} ${t.group} ${t.description}`.toLowerCase().includes(filter.toLowerCase())))
    .sort((a, b) => a.name.localeCompare(b.name)), [lib, filter, collection, status]);
  if (!lib) return <ErrorBox error={q.error} />;
  const selectedName = pane?.kind === "tool" ? pane.name : params.get("tool");
  const selected = lib.tools.find(t => t.name === selectedName) ?? (pane === null ? tools[0] : undefined);
  const show = (name: string, remove = false) => { setPane({ kind: "tool", name, remove }); setParams({ tool: name }, { replace: true }); };
  const discovered = lib.discovered.filter(d => !filter || `${d.name} ${d.description}`.toLowerCase().includes(filter.toLowerCase()));
  const secrets = secretsQ.data?.secrets ?? [];
  const newDraft = (source: "http" | "web" = "http") => {
    const d: Draft = { id: `draft-${Date.now()}`, name: "", group: collection || groups[0] || "home", policy: structuredClone(source === "web" ? NEW_WEB : NEW_HTTP) };
    saveDrafts([d, ...drafts]);
    setPane({ kind: "edit", name: "", group: d.group, policy: d.policy, isNew: true, draft: d.id });
  };
  const editing = pane?.kind === "edit" ? pane : null;
  const addCollection = (name: string) => {
    const n = name.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "");
    if (n && !groups.includes(n)) { const next = [...extraCollections, n]; setExtraCollections(next); store.set(collectionsKey(), next); }
    setNewCollection(null);
    if (n) { setCollection(n); setBrowsing(false); }
  };

  return (
    <div className="mx-auto max-w-7xl space-y-4 pb-10">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="mr-4 text-2xl font-bold tracking-tight">Integration Tools</h1>
        <div className="relative min-w-60 flex-1">
          <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted">{CI.search}</span>
          <input id="tool-filter" placeholder="Search tools…" value={filter} onChange={e => setFilter(e.target.value)} style={{ paddingLeft: "2rem" }} className="w-full !py-2 text-sm" />
        </div>
        <select id="tool-status" aria-label="Status" className="!py-2 text-sm" value={status} onChange={e => setStatus(e.target.value as typeof status)}>
          <option value="all">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select>
        <AddMenu onApi={() => newDraft()} onWeb={() => newDraft("web")} onMcp={() => setPane({ kind: "servers", add: true })} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <aside className="space-y-3 lg:sticky lg:top-4 lg:max-h-[calc(100vh-7rem)] lg:overflow-y-auto lg:pr-1">
          {browsing ? (
            <div className="rounded-xl border border-line bg-panel p-2">
              <div className="flex items-center justify-between px-2 py-1.5">
                <span className="text-xs font-semibold tracking-wide text-muted uppercase">Collections</span>
                <button aria-label="New collection" title="New collection" onClick={() => setNewCollection("")} className="rounded p-1 text-muted hover:bg-soft hover:text-ink">{TI.plus}</button>
              </div>
              {newCollection !== null && (
                <form className="flex gap-1.5 px-2 pb-2" onSubmit={e => { e.preventDefault(); addCollection(newCollection); }}>
                  <input autoFocus aria-label="Collection name" className="min-w-0 flex-1 font-mono text-xs" placeholder="collection_name" value={newCollection}
                    onChange={e => setNewCollection(e.target.value)} onKeyDown={e => e.key === "Escape" && setNewCollection(null)} />
                  <Button type="submit" kind="primary">Add</Button>
                </form>
              )}
              {["", ...groups].map(g => {
                const n = g ? lib.tools.filter(t => t.group === g).length : lib.tools.length;
                return (
                  <button key={g || "__all"} onClick={() => { setCollection(g); setBrowsing(false); }}
                    className={cx("flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-soft", collection === g && "bg-soft font-medium")}>
                    <span className="text-muted">{TI.folder}</span><span className={cx("min-w-0 flex-1 truncate", g && "font-mono text-xs")}>{g || "All Tools"}</span>
                    <span className="text-xs text-muted">{n}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-line bg-panel">
              <button id="tool-collections" onClick={() => setBrowsing(true)} className="flex w-full items-center gap-1.5 border-b border-line px-3 py-2.5 text-xs font-semibold tracking-wide text-muted uppercase hover:text-ink">
                {TI.back}Collections</button>
              <div className="flex items-center justify-between px-3 py-2.5 text-sm font-medium">
                <span className={cx(collection && "font-mono")}>{collection || "All Tools"}</span>
                <span className="text-xs font-normal text-muted">{tools.length}</span>
              </div>
            </div>
          )}

          {!browsing && <>
            {drafts.map(d => <DraftCard key={d.id} d={d} active={editing?.draft === d.id}
              onClick={() => setPane({ kind: "edit", name: d.name, group: d.group, policy: d.policy, isNew: true, draft: d.id })} />)}
            <button onClick={() => setPane({ kind: "servers" })}
              className={cx("flex w-full items-center gap-2.5 rounded-xl border border-dashed p-3 text-left",
                pane?.kind === "servers" ? "border-accent bg-accent/5" : "border-line hover:border-accent/60")}>
              <span className="grid h-9 w-9 place-items-center rounded-lg bg-soft text-muted">{TI.server}</span>
              <span className="min-w-0 flex-1"><span className="block text-sm font-medium">MCP servers</span>
                <span className="text-xs text-muted">{lib.servers.length} server{lib.servers.length === 1 ? "" : "s"} · {lib.servers.filter(s => s.status.connected).length} connected</span></span>
            </button>
            {tools.map(t => (
              <ToolCard key={t.name} t={t} active={pane?.kind !== "servers" && !editing && selected?.name === t.name} onClick={() => show(t.name)}
                menu={<RowMenu items={[
                  { label: "Edit", icon: TI.edit, onClick: () => setPane({ kind: "edit", name: t.name, group: t.group, policy: structuredClone(t.policy), isNew: false }) },
                  { label: "Move to Collection", icon: TI.folder, onClick: () => setMoving(t) },
                  { label: "Delete", icon: TI.trash, danger: true, onClick: () => show(t.name, true) },
                ]} />} />
            ))}
            {!tools.length && <Empty>{collection && !filter ? "No tools in this collection yet — use ⋮ → Move to Collection on a tool." : "No tools match."}</Empty>}
            {discovered.length > 0 && <>
              <div className="pt-2 text-xs font-semibold tracking-wide text-muted uppercase">Available from servers · {discovered.length}</div>
              {discovered.map(d => (
                <div key={d.name} className="rounded-xl border border-dashed border-line p-3">
                  <div className="truncate font-mono text-xs">{d.name} <span className="text-muted">· {d.server ?? "?"}</span></div>
                  <div className="line-clamp-2 text-xs text-muted">{d.description}</div>
                  <button onClick={() => setPane({ kind: "edit", name: d.name, group: collection || groups[0] || "home", isNew: true, policy: { kind: "read" } })}
                    className="mt-1.5 text-xs font-medium text-accent-text hover:underline">+ Add to the library</button>
                </div>
              ))}
            </>}
          </>}
        </aside>

        <main className="min-w-0">
          {editing ? (
            <ToolForm key={editing.draft ?? `${editing.name}-${editing.isNew}`} lib={lib} init={editing} secrets={secrets} groups={groups}
              serverDescription={lib.tools.find(t => t.name === editing.name)?.description ?? lib.discovered.find(d => d.name === editing.name)?.description ?? ""}
              serverSchema={(lib.tools.find(t => t.name === editing.name)?.input_schema ?? lib.discovered.find(d => d.name === editing.name)?.input_schema ?? null) as Schema | null}
              onDraft={editing.draft ? (name, group, policy) => saveDrafts(drafts.map(d => d.id === editing.draft ? { ...d, name, group, policy } : d)) : undefined}
              onDone={name => {
                if (editing.draft) saveDrafts(drafts.filter(d => d.id !== editing.draft));
                refresh(); qc.invalidateQueries({ queryKey: ["secrets"] }); show(name);
              }}
              onCancel={() => { if (editing.draft) saveDrafts(drafts.filter(d => d.id !== editing.draft)); setPane(null); }} />
          ) : pane?.kind === "servers" ? (
            <Servers lib={lib} startAdding={!!pane.add} onChanged={refresh} />
          ) : selected ? (
            <ToolDetail key={`${selected.name}-${pane?.kind === "tool" && pane.remove ? "rm" : ""}`} t={selected} lib={lib}
              startRemoving={pane?.kind === "tool" && !!pane.remove}
              onChanged={() => { refresh(); setPane(null); setParams({}, { replace: true }); }}
              onEdit={() => setPane({ kind: "edit", name: selected.name, group: selected.group, policy: structuredClone(selected.policy), isNew: false })} />
          ) : <Empty>Choose a tool.</Empty>}
        </main>
      </div>
      {moving && <MoveDialog t={moving} collections={groups} onClose={() => setMoving(null)}
        onMoved={g => { setMoving(null); setCollection(g); refresh(); show(moving.name); }} />}
    </div>
  );
}

// ---------------------------------------------------------------- add / edit form

type HeaderRow = { k: string; v: unknown };
const AUTH_DEFAULT: Record<string, ToolAuth> = {
  none: { type: "none" }, bearer: { type: "bearer", token: { newSecret: { name: "", value: "" } } },
  token: { type: "token", token: { newSecret: { name: "", value: "" } } },
  basic: { type: "basic", username: "", password: { newSecret: { name: "", value: "" } } },
  api_key: { type: "api_key", header: "X-API-Key", value: { newSecret: { name: "", value: "" } } },
};

function ToolForm({ lib, init, secrets, groups, serverDescription, serverSchema, onDraft, onDone, onCancel }: {
  lib: ToolLibrary; init: { name: string; group: string; policy: ToolPolicy; isNew: boolean }; secrets: SecretRow[]; groups: string[];
  serverDescription: string; serverSchema: Schema | null;
  onDraft?: (name: string, group: string, policy: ToolPolicy) => void; onDone: (name: string) => void; onCancel: () => void;
}) {
  const http = init.policy.source === "http";
  const web = init.policy.source === "web";
  const own = http || web;                        // defines its own description and parameter schema
  const [name, setName] = useState(init.name);
  const [group, setGroup] = useState(init.group);
  const [p, setP] = useState<ToolPolicy>(init.policy);
  const [headers, setHeaders] = useState<HeaderRow[]>(Object.entries(init.policy.http?.headers ?? {}).map(([k, v]) => ({ k, v })));
  const [auth, setAuth] = useState<ToolAuth>(init.policy.http?.auth ?? { type: "none" });
  const [schema, setSchema] = useState<Schema>((init.policy.input_schema as Schema) ?? { type: "object", properties: {}, required: [] });
  const [editingSchema, setEditingSchema] = useState(false);
  const [rawArgs, setRawArgs] = useState(json(init.policy.args));
  const [agents, setAgents] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const set = (k: keyof ToolPolicy, v: unknown) => setP({ ...p, [k]: v === "" ? undefined : v });
  const setHttp = (patch: Partial<NonNullable<ToolPolicy["http"]>>) => setP({ ...p, http: { ...p.http, ...patch } });

  const build = (): ToolPolicy => {
    const out: ToolPolicy = { ...p };
    if (own) out.input_schema = schema;
    if (http) {
      out.input_schema = schema;
      out.http = { ...p.http, headers: Object.fromEntries(headers.filter(h => h.k.trim()).map(h => [h.k.trim(), h.v])) };
      if (auth.type === "none") delete out.http.auth; else out.http.auth = auth;
    }
    return out;
  };
  useEffect(() => { onDraft?.(name, group, build()); },            // keep the draft card up to date
    [name, group, p, headers, auth, schema]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: async () => {
      let policy = build();
      try { policy = { ...policy, args: rawArgs.trim() ? JSON.parse(rawArgs) : undefined }; }
      catch (e) { throw new Error(`Role arguments: invalid JSON (${(e as Error).message})`); }
      if (policy.timeout_s != null && !(policy.timeout_s >= 1 && policy.timeout_s <= 60)) throw new Error("Timeout: 1 to 60 seconds");
      if (own && schemaProblem(policy.input_schema)) throw new Error(`Parameters: ${schemaProblem(policy.input_schema)}`);
      policy = await materialize(policy);
      await api.putTool(name, { group, policy, agents });
      return name;
    },
    onSuccess: onDone,
  });
  const c = lib.choices;
  const confirm = p.confirm ?? (p.kind === "read" ? "none" : "affirm");
  const count = propCount(own ? schema : serverSchema);
  const invalid = own ? schemaProblem(schema) : null;
  const title = init.isNew ? (web ? "Add New Web Tool" : http ? "Add New API Request" : `Add ${name} to the library`) : "Edit Tool";
  const canSave = !!name && !!group && (!own || !!p.description) && (!http || !!p.http?.url);
  return (
    <section className="space-y-6 rounded-xl border border-line bg-panel p-6">
      <h2 className="text-lg font-semibold">{title}</h2>

      <div className="space-y-4">
        <label className="block"><span className={label()}>Tool Name</span>
          <input id="tool-name" className="w-full font-mono" maxLength={NAME_MAX} value={name} disabled={!init.isNew || !own} placeholder={web ? "e.g. navigate_to_page" : "e.g. get_branch_hours"}
            onChange={e => setName(e.target.value.replace(/[^A-Za-z0-9_-]/g, "_"))} />
          <span className="mt-1 flex justify-between text-[11px] text-muted"><span>{init.isNew && own ? (web ? "Letters, digits, _ and - — the model calls the tool by this name, and your website registers a function with the same name" : "Letters, digits, _ and - — the model calls the tool by this name") : "The name can't change (agents call it by name)"}</span>{name.length}/{NAME_MAX}</span></label>
        <label className="block"><span className={label()}>Description</span>
          {own ? <>
            <textarea id="tool-desc" rows={4} maxLength={DESC_MAX} className="w-full" placeholder="What it does and when the agent should call it (the model reads this)"
              value={p.description ?? ""} onChange={e => set("description", e.target.value)} />
            <span className="mt-1 block text-right text-[11px] text-muted">{(p.description ?? "").length}/{DESC_MAX}</span>
          </> : <p className="rounded-lg bg-soft px-3 py-2 text-sm text-muted">{serverDescription || "—"} <span className="block text-[11px]">From the {init.policy.source === "local" ? "platform" : "MCP server"}.</span></p>}</label>
      </div>

      <div className="border-t border-line pt-5">
        <h3 className="text-base font-semibold">Tool Settings</h3>
        <p className="mb-4 text-sm text-muted">Configure the tool settings for your integration</p>
        <div className="space-y-4">
          {http && <label className="block"><span className={label()}>URL</span>
            <input id="tool-url" className="w-full font-mono text-xs" placeholder="https://api.example.com/endpoint/{id}" value={p.http?.url ?? ""} onChange={e => setHttp({ url: e.target.value })} />
            <span className="mt-1 block text-[11px] text-muted">{"{arg}"} placeholders are filled from the agent's arguments; the other arguments go in the query (GET / DELETE) or the JSON body.</span></label>}
          <div className="grid gap-4 sm:grid-cols-2">
            {http && <label className="block"><span className={label()}>Method</span>
              <select id="tool-method" className="w-full" value={p.http?.method ?? "GET"} onChange={e => setHttp({ method: e.target.value })}>{c.methods.map(m => <option key={m}>{m}</option>)}</select></label>}
            <label className="block"><span className={label()}>Timeout <span className="text-xs font-normal text-muted">(default: 10)</span></span>
              <div className="flex items-center rounded-lg border border-line pr-3">
                <input id="tool-timeout" type="number" min={1} max={60} className="min-w-0 flex-1 !border-0" value={p.timeout_s ?? ""}
                  onChange={e => set("timeout_s", e.target.value === "" ? undefined : Number(e.target.value))} /><span className="text-sm text-muted">seconds</span></div>
              <span className="mt-1 block text-[11px] text-muted">Request timeout in seconds (1-60).</span></label>
          </div>
          {http && <>
            <div>
              <label htmlFor="tool-auth" className={label()}>Authentication</label>
              <select id="tool-auth" className="w-full" value={auth.type} onChange={e => setAuth(structuredClone(AUTH_DEFAULT[e.target.value]))}>
                {Object.entries(AUTH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              <span className="mt-1 block text-[11px] text-muted">{AUTH_HELP[auth.type]}</span>
              {auth.type !== "none" && (
                <div className="mt-3 grid gap-3 rounded-lg bg-soft/50 p-3 sm:grid-cols-2">
                  {(auth.type === "bearer" || auth.type === "token") && <div className="sm:col-span-2"><span className={label()}>Token</span>
                    <SecretInput id="auth-token" preferSecret value={auth.token} secrets={secrets} onChange={v => setAuth({ ...auth, token: v })} /></div>}
                  {auth.type === "basic" && <>
                    <div><span className={label()}>Username</span><SecretInput id="auth-user" value={auth.username} secrets={secrets} onChange={v => setAuth({ ...auth, username: v })} /></div>
                    <div><span className={label()}>Password</span><SecretInput id="auth-pass" preferSecret value={auth.password} secrets={secrets} onChange={v => setAuth({ ...auth, password: v })} /></div>
                  </>}
                  {auth.type === "api_key" && <>
                    <label className="block"><span className={label()}>Header name</span>
                      <input id="auth-header" className="w-full" value={auth.header} onChange={e => setAuth({ ...auth, header: e.target.value })} /></label>
                    <div><span className={label()}>Key</span><SecretInput id="auth-key" preferSecret value={auth.value} secrets={secrets} onChange={v => setAuth({ ...auth, value: v })} /></div>
                  </>}
                </div>
              )}
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between"><span className="text-sm font-medium">Headers</span>
                <Button onClick={() => setHeaders([...headers, { k: "", v: "" }])}>+ Add Header</Button></div>
              <div className="space-y-2">
                {headers.map((h, i) => (
                  <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1.4fr_auto]">
                    <input aria-label={`Header ${i + 1} name`} placeholder="Header-Name" value={h.k} onChange={e => setHeaders(headers.map((x, j) => j === i ? { ...x, k: e.target.value } : x))} />
                    <SecretInput id={`header-${i}`} value={h.v} secrets={secrets} placeholder="value" onChange={v => setHeaders(headers.map((x, j) => j === i ? { ...x, v } : x))} />
                    <button aria-label={`Remove header ${i + 1}`} onClick={() => setHeaders(headers.filter((_, j) => j !== i))} className="rounded p-2 text-bad hover:bg-bad/10">{TI.trash}</button>
                  </div>
                ))}
                {!headers.length && <p className="text-xs text-muted">No headers.</p>}
              </div>
            </div>
          </>}
          <label className="flex items-start gap-2.5 rounded-lg border border-line p-3">
            <input id="tool-enabled" type="checkbox" className="mt-0.5" checked={p.enabled !== false}
              onChange={e => setP({ ...p, enabled: e.target.checked ? undefined : false })} />
            <span><span className="block text-sm font-medium">Active</span>
              <span className="text-[11px] text-muted">Inactive tools stay in the library but are not offered to agents, and refuse to run (a flow's tool node takes its failure path).</span></span>
          </label>
          {p.kind !== "read" && <label className="flex items-start gap-2.5 rounded-lg border border-line p-3">
            <input id="tool-async" type="checkbox" className="mt-0.5" checked={!!p.async}
              onChange={e => setP({ ...p, async: e.target.checked ? true : undefined })} />
            <span><span className="block text-sm font-medium">Async</span>
              <span className="text-[11px] text-muted">Fire and forget: the agent gets “queued” at once and carries on while the request finishes in the background. Its result is only logged — don't use it when the agent needs the answer.</span></span>
          </label>}
          <div>
            <span className="text-sm font-medium">Messages while the tool runs</span>
            <p className="mb-2 text-[11px] text-muted">Optional lines the agent says itself. “Start” replaces the generic “one moment”; “Done” is said when the call succeeds. {"{{args.name}}"} fills in an argument.</p>
            <div className="grid gap-3 sm:grid-cols-2">
              {(["say_start", "say_done"] as const).map(k => (
                <div key={k} className="rounded-lg bg-soft/50 p-3">
                  <span className={label()}>{k === "say_start" ? "Request start" : "Request complete"}</span>
                  {(["en", "ar"] as const).map(l => (
                    <input key={l} id={`tool-${k}-${l}`} aria-label={`${k === "say_start" ? "Start" : "Done"} message (${l})`} dir={l === "ar" ? "rtl" : undefined}
                      className="mb-1.5 w-full text-sm" placeholder={l === "en" ? "English" : "العربية"} value={p[k]?.[l] ?? ""}
                      onChange={e => {
                        const next = { ...(p[k] ?? {}), [l]: e.target.value };
                        if (!next[l]) delete next[l];
                        setP({ ...p, [k]: Object.keys(next).length ? next : undefined });
                      }} />
                  ))}
                </div>
              ))}
            </div>
          </div>
          {p.kind === "read" && !web && <label className="block sm:w-1/2"><span className={label()}>Cache <span className="text-xs font-normal text-muted">(seconds, read tools)</span></span>
            <input id="tool-cache" type="number" min={0} className="w-full" value={p.cache_ttl ?? ""} onChange={e => set("cache_ttl", e.target.value === "" ? undefined : Number(e.target.value))} /></label>}
        </div>
      </div>

      <div className="border-t border-line pt-5">
        <h3 className="mb-4 text-base font-semibold">Function Configuration</h3>
        <div className="rounded-xl border border-line bg-soft/40 p-4">
          <div className="flex items-start gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-accent/15 text-accent-text">{TI.gear}</span>
            <div><div className="font-medium">Parameters</div><div className="text-sm text-muted">{own ? "Define the JSON schema for function parameters" : "Defined by the server — the agent fills these in"}</div></div>
          </div>
          {own && <div className="mt-3 flex justify-center"><button id="configure-schema" onClick={() => setEditingSchema(true)}
            className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:opacity-90">{TI.spark}Configure Schema →</button></div>}
          <div className="mt-3 flex items-center justify-between text-xs">
            {own ? <span className={cx("flex items-center gap-1.5", invalid ? "text-bad" : "text-muted")}><span className={cx("h-2 w-2 rounded-full", invalid ? "bg-bad" : "bg-good")} />{invalid ? `Schema invalid: ${invalid}` : "Schema valid"}</span> : <span />}
            <span className="text-muted">{count} propert{count === 1 ? "y" : "ies"} configured</span>
          </div>
        </div>
      </div>
      {editingSchema && <SchemaDialog init={schema} toolName={name} description={p.description ?? ""} onClose={() => setEditingSchema(false)}
        onSave={s => { setSchema(s); setEditingSchema(false); }} />}

      <div className="border-t border-line pt-5">
        <h3 className="text-base font-semibold">Platform policy</h3>
        <p className="mb-4 text-sm text-muted">What the platform enforces around this tool — ours, Hamsa has no equivalent.</p>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <label className="block"><span className={label()}>Collection</span>
            <input id="tool-group" list="tool-groups" className="w-full font-mono" value={group}
              onChange={e => setGroup(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} />
            <datalist id="tool-groups">{groups.map(g => <option key={g} value={g} />)}</datalist>
            <span className="mt-1 block text-[11px] text-muted">Usually the skill that uses it; skills without a flow get their collection's tools.</span></label>
          <label className="block"><span className={label()}>Kind</span>
            <select id="tool-kind" className="w-full" value={p.kind} onChange={e => setP({ ...p, kind: e.target.value as ToolPolicy["kind"],
              confirm: e.target.value === "read" ? undefined : p.confirm, backs: e.target.value === "read" ? undefined : p.backs, async: e.target.value === "read" ? undefined : p.async })}>
              {c.kinds.map(k => <option key={k}>{k}</option>)}</select>
            <span className="mt-1 block text-[11px] text-muted">write / send tools change or send something.</span></label>
          <label className="block"><span className={label()}>Confirmation</span>
            <select id="tool-confirm" className="w-full" value={confirm} disabled={p.kind === "read"} onChange={e => set("confirm", e.target.value)}>
              {c.confirm.map(k => <option key={k}>{k}</option>)}</select>
            <span className="mt-1 block text-[11px] text-muted">{CONFIRM_HELP[confirm]}</span></label>
          <label className="block"><span className={label()}>Success allows the claim</span>
            <select id="tool-backs" className="w-full" value={p.backs ?? ""} disabled={p.kind === "read"} onChange={e => set("backs", e.target.value)}>
              <option value="">—</option>{c.claims.map(k => <option key={k}>{k}</option>)}</select>
            <span className="mt-1 block text-[11px] text-muted">The agent may only say “booked / sent …” after this succeeds.</span></label>
          <label className="block"><span className={label()}>Line after success</span>
            <select id="tool-line" className="w-full" value={p.success_line ?? ""} onChange={e => set("success_line", e.target.value)}>
              <option value="">—</option>{c.phrases.map(k => <option key={k}>{k}</option>)}</select></label>
          <label className="block"><span className={label()}>Verification role</span>
            <select id="tool-role" className="w-full" value={p.role ?? ""} onChange={e => set("role", e.target.value)}>
              <option value="">—</option>{c.roles.map(k => <option key={k}>{k}</option>)}</select>
            <span className="mt-1 block text-[11px] text-muted">Caller verification the platform drives itself.</span></label>
        </div>
        {p.role && <label className="mt-4 block"><span className={label()}>Role arguments (JSON)</span>
          <textarea id="tool-args" className="w-full font-mono text-xs" rows={2} value={rawArgs} onChange={e => setRawArgs(e.target.value)} />
          <span className="mt-1 block text-[11px] text-muted">e.g. {'{"mobile": "mobileNo"}'} — role parameter → this tool's argument</span></label>}
        <div className="mt-4">
          <div className="mb-1 text-sm font-medium">Checks and bookkeeping (named hooks)</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(c.hooks).map(([h, phases]) => (
              <label key={h} className="flex items-center gap-1.5 text-xs">
                <input type="checkbox" checked={(p.hooks ?? []).includes(h)} onChange={e => set("hooks",
                  e.target.checked ? [...(p.hooks ?? []), h] : (p.hooks ?? []).filter(x => x !== h))} />
                <span className="font-mono">{h}</span><span className="text-muted">({phases.join(", ")})</span>
              </label>
            ))}
          </div>
        </div>
        {init.isNew && lib.agents.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
            <span className="font-medium">Give it to:</span>
            {lib.agents.map(a => (
              <label key={a.id} className="flex items-center gap-1.5">
                <input type="checkbox" checked={agents.includes(a.id)}
                  onChange={e => setAgents(e.target.checked ? [...agents, a.id] : agents.filter(x => x !== a.id))} />{a.name}
              </label>
            ))}
            <span className="text-xs text-muted">(none ticked: the default agent)</span>
          </div>
        )}
      </div>

      {problem && <ErrorBox error={problem} />}
      <ErrorBox error={save.error} />
      <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line pt-4">
        <span className="mr-auto text-xs text-muted">Saving publishes a new release of the agents that have this tool.</span>
        <Button onClick={onCancel}>{init.isNew && own ? "Discard" : "Cancel"}</Button>
        <Button kind="primary" disabled={!canSave || save.isPending}
          onClick={() => { setProblem(canSave ? null : "Name, description and URL are required"); save.mutate(); }}>
          {save.isPending ? "Saving…" : init.isNew ? "Create" : "Update"}</Button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- MCP servers

function Servers({ lib, startAdding, onChanged }: { lib: ToolLibrary; startAdding: boolean; onChanged: () => void }) {
  const blank = { name: "", url: "https://", header: "Authorization", scheme: "Bearer", secret: "", token: "" };
  const [form, setForm] = useState<{ id?: string; name: string; url: string; header: string; scheme: string;
                                     secret: string; token: string } | null>(startAdding ? blank : null);
  const [preview, setPreview] = useState<{ ok: boolean; tools?: { name: string }[]; error?: string; for: string } | null>(null);
  const body = () => form && ({
    name: form.name, url: form.url, transport: "streamable_http",
    auth: { header: form.header || "Authorization", scheme: form.scheme, ...(form.secret ? { secret: form.secret } : {}),
            ...(form.token ? { token: form.token } : {}) },
  });
  const key = JSON.stringify(body());
  const discover = useMutation({ mutationFn: () => api.discoverServer(body()!), onSuccess: r => setPreview({ ...r, for: key }) });
  const save = useMutation({
    mutationFn: () => (form!.id ? api.updateServer(form!.id, body()!) : api.addServer(body()!)),
    onSuccess: () => { setForm(null); setPreview(null); onChanged(); },
  });
  const del = useMutation({ mutationFn: (id: string) => api.deleteServer(id), onSuccess: onChanged });
  const edit = (s: McpServer) => { setPreview(null); setForm({ id: s.id, name: s.name, url: s.url, header: s.auth?.header ?? "Authorization",
                                                             scheme: s.auth?.scheme ?? "Bearer", secret: s.auth?.secret ?? "", token: "" }); };
  const verified = !!preview?.ok && preview.for === key;
  return (
    <section className="space-y-4 rounded-xl border border-line bg-panel p-6">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">MCP servers</h2>
        {!form && <Button onClick={() => { setPreview(null); setForm(blank); }}>+ Add server</Button>}
      </div>
      <div className="divide-y divide-line">
        {lib.servers.map(s => (
          <div key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{s.name}</span>
                {s.status.connected ? <Badge tone="good">connected · {s.status.tools.length} tools</Badge> : <Badge tone="bad">not connected</Badge>}
              </div>
              <div className="truncate font-mono text-[11px] text-muted">{s.url}{s.auth?.secret ? ` · 🔒 ${s.auth.secret}` : ""}</div>
              {s.status.error && <div className="text-[11px] text-bad">{s.status.error}</div>}
            </div>
            <div className="flex gap-2">
              <Button onClick={() => edit(s)}>Edit</Button>
              <Button kind="ghost" onClick={() => del.mutate(s.id)}>Delete</Button>
            </div>
          </div>
        ))}
        {lib.servers.length === 0 && <Empty>No MCP server yet.</Empty>}
      </div>
      <ErrorBox error={del.error} />
      {form && (
        <div className="space-y-4 border-t border-line pt-4">
          <h3 className="text-base font-semibold">{form.id ? `Edit ${form.name}` : "Add New MCP Server"}</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block"><span className={label()}>Name</span>
              <input id="srv-name" className="w-full font-mono" value={form.name} disabled={!!form.id} placeholder="my_server"
                onChange={e => setForm({ ...form, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} /></label>
            <label className="block"><span className={label()}>URL <span className="text-xs font-normal text-muted">(streamable HTTP)</span></span>
              <input id="srv-url" className="w-full font-mono text-xs" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
            <label className="block"><span className={label()}>Auth header / scheme</span>
              <div className="flex gap-2"><input id="srv-header" className="min-w-0 flex-1" value={form.header} onChange={e => setForm({ ...form, header: e.target.value })} />
                <input id="srv-scheme" className="w-24" value={form.scheme} onChange={e => setForm({ ...form, scheme: e.target.value })} /></div></label>
            <label className="block"><span className={label()}>Token</span>
              <input id="srv-token" type="password" className="w-full" autoComplete="off"
                placeholder={form.secret ? `stored as ${form.secret} — paste to replace` : "paste a token (stored encrypted)"}
                value={form.token} onChange={e => setForm({ ...form, token: e.target.value })} /></label>
          </div>
          {preview && preview.for === key && (preview.ok
            ? <div className="rounded-lg bg-good/10 px-3 py-2 text-xs text-good">✓ {preview.tools?.length} tools: {preview.tools?.slice(0, 12).map(t => t.name).join(", ")}{(preview.tools?.length ?? 0) > 12 ? " …" : ""}</div>
            : <div className="rounded-lg bg-bad/10 px-3 py-2 text-xs text-bad">✗ {preview.error}</div>)}
          <ErrorBox error={discover.error ?? save.error} />
          <div className="flex flex-wrap items-center justify-end gap-3">
            {!verified && <span className="mr-auto text-xs text-muted">Run Discover tools to verify the server before saving</span>}
            <Button onClick={() => discover.mutate()} disabled={discover.isPending}>{discover.isPending ? "Connecting…" : "Discover tools"}</Button>
            <Button kind="ghost" onClick={() => { setForm(null); setPreview(null); }}>Cancel</Button>
            <Button kind="primary" onClick={() => save.mutate()} disabled={!verified || save.isPending}>{save.isPending ? "Saving…" : form.id ? "Update" : "Create"}</Button>
          </div>
        </div>
      )}
    </section>
  );
}
