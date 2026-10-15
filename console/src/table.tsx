/** Table toolbar pieces in Hamsa's style, shared by Call History and the Agents list. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "./ui";

const ico = (d: ReactNode, cls = "h-4 w-4") => (
  <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
    strokeLinejoin="round">{d}</svg>
);
export const CI = {
  plus: ico(<path d="M12 5v14M5 12h14" />),
  trash: ico(<><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /></>),
  pencil: ico(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></>),
  duplicate: ico(<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  search: ico(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>),
  globe: ico(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></>),
  phone: ico(<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z" />),
  chat: ico(<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />),
  copy: ico(<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>, "h-3.5 w-3.5"),
  check: ico(<path d="M20 6 9 17l-5-5" />, "h-3.5 w-3.5"),
  dots: ico(<><circle cx="12" cy="5" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="12" cy="19" r="1" /></>),
  eye: ico(<><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>),
  chevron: ico(<path d="m6 9 6 6 6-6" />),
  sort: ico(<path d="m7 15 5 5 5-5M7 9l5-5 5 5" />, "h-3.5 w-3.5"),
  down: ico(<path d="M12 5v14M19 12l-7 7-7-7" />, "h-3.5 w-3.5"),
  up: ico(<path d="M12 19V5M5 12l7-7 7 7" />, "h-3.5 w-3.5"),
  columns: ico(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M9 3v18M15 3v18" /></>),
  reset: ico(<><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /></>),
};

/** Copy to the clipboard (✓ for a moment). */
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" title={done ? "Copied" : label} aria-label={label}
      onClick={e => { e.stopPropagation(); navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1200); }).catch(() => {}); }}
      className="rounded p-1 text-muted hover:bg-soft hover:text-ink">{done ? CI.check : CI.copy}</button>
  );
}

export function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc, true); };
  }, [open]);
  return { open, setOpen, ref };
}

/** Hamsa's dashed filter button with a checkbox list. */
export function MultiSelect({ id, label, options, value, onChange }: {
  id: string; label: string; options: [string, string, ReactNode?][]; value: string[]; onChange: (v: string[]) => void;
}) {
  const { open, setOpen, ref } = usePopover();
  const toggle = (k: string) => onChange(value.includes(k) ? value.filter(x => x !== k) : [...value, k]);
  return (
    <div ref={ref} className="relative">
      <button id={id} onClick={() => setOpen(!open)}
        className={cx("flex items-center gap-2 rounded-lg border border-dashed px-3 py-1.5 text-sm hover:bg-soft",
          value.length ? "border-line bg-panel" : "border-line")}>
        <span>{label}</span>
        {value.length > 0 && value.length < options.length && (
          <span className="rounded bg-soft px-1.5 text-xs">{value.length > 2 ? `${value.length} selected` : value.map(v => options.find(o => o[0] === v)?.[1]).join(", ")}</span>)}
        {CI.chevron}
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-1 w-56 rounded-lg border border-line bg-panel p-1 shadow-lg">
          {options.map(([k, name, icon]) => (
            <label key={k} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-soft">
              <input type="checkbox" checked={value.includes(k)} onChange={() => toggle(k)} className="accent-[var(--color-accent)]" />
              {icon && <span className="text-muted">{icon}</span>}{name}
            </label>
          ))}
          {value.length > 0 && <button onClick={() => onChange([])}
            className="mt-1 w-full border-t border-line px-2 pt-1.5 pb-1 text-center text-xs text-muted hover:text-ink">Clear filters</button>}
        </div>
      )}
    </div>
  );
}

/** Columns a viewer chose to show, remembered per browser. */
export function loadColumns(key: string, defaults: string[]): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(key) ?? "null");
    if (Array.isArray(v)) return v.filter((k: unknown) => typeof k === "string");
  } catch { /* storage unavailable */ }
  return defaults;
}

export function saveColumns(key: string, v: string[]): void {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* ignore */ }
}

/** Hamsa's "View" column chooser. */
export function ViewMenu({ id, columns, shown, onChange }: {
  id: string; columns: { key: string; label: string; fixed?: boolean }[]; shown: string[]; onChange: (v: string[]) => void;
}) {
  const { open, setOpen, ref } = usePopover();
  const [find, setFind] = useState("");
  return (
    <div ref={ref} className="relative">
      <button id={id} onClick={() => setOpen(!open)} className="flex items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">
        {CI.columns}View</button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 w-56 rounded-lg border border-line bg-panel p-1 shadow-lg">
          <input autoFocus placeholder="Search columns…" value={find} onChange={e => setFind(e.target.value)} className="mb-1 w-full !py-1 text-sm" />
          <div className="px-2 py-1 text-[11px] font-medium text-muted">Toggle columns</div>
          {columns.filter(c => !c.fixed && c.label.toLowerCase().includes(find.toLowerCase())).map(c => (
            <label key={c.key} className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-soft">
              <input type="checkbox" checked={shown.includes(c.key)} className="accent-[var(--color-accent)]"
                onChange={() => onChange(shown.includes(c.key) ? shown.filter(k => k !== c.key) : [...shown, c.key])} />{c.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** The ⋮ menu of a table row. */
export function RowMenu({ items }: { items: { label: string; icon?: ReactNode; danger?: boolean; onClick: () => void }[] }) {
  // drawn in a layer on <body>, at the button: inside the table's scroll box it would be clipped
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!pos) return;
    const close = () => setPos(null);
    const outside = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node) && !button.current?.contains(e.target as Node)) close();
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc, true);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", esc, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [pos]);
  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (pos) { setPos(null); return; }
    const r = button.current!.getBoundingClientRect();
    const height = items.length * 34 + 10;
    const below = r.bottom + 4 + height <= window.innerHeight;          // else open upwards
    setPos({ top: below ? r.bottom + 4 : Math.max(8, r.top - 4 - height), right: Math.max(8, window.innerWidth - r.right) });
  };
  return (
    <>
      <button ref={button} aria-label="Row actions" aria-haspopup="menu" aria-expanded={!!pos} onClick={toggle}
        className={cx("rounded-md p-1 text-muted hover:bg-soft hover:text-ink", pos && "bg-soft text-ink")}>{CI.dots}</button>
      {pos && createPortal(
        <div ref={menu} role="menu" style={{ position: "fixed", top: pos.top, right: pos.right }}
          className="z-50 w-48 rounded-lg border border-line bg-panel p-1 text-left shadow-lg"
          onClick={e => e.stopPropagation()}>
          {items.map((it, i) => (
            <div key={it.label}>
              {it.danger && i > 0 && <div className="my-1 h-px bg-line" />}
              <button role="menuitem" onClick={() => { setPos(null); it.onClick(); }}
                className={cx("flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                  it.danger ? "text-bad hover:bg-bad/10" : "hover:bg-soft")}>
                <span className={it.danger ? "" : "text-muted"}>{it.icon}</span>{it.label}</button>
            </div>
          ))}
        </div>, document.body)}
    </>
  );
}

/** Numbered pages: ‹ Previous 1 2 … 9 Next ›. */
export function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  const nums: (number | "…")[] = [];
  for (let i = 0; i < pages; i++) {
    if (i === 0 || i === pages - 1 || Math.abs(i - page) <= 1) nums.push(i);
    else if (nums[nums.length - 1] !== "…") nums.push("…");
  }
  const btn = "grid h-8 min-w-8 place-items-center rounded-md px-2 text-sm";
  return (
    <nav className="flex items-center gap-1" aria-label="Pages">
      <button disabled={page === 0} onClick={() => onPage(page - 1)} className={cx(btn, "text-muted hover:bg-soft disabled:opacity-40")}>‹ Previous</button>
      {nums.map((n, i) => n === "…" ? <span key={`e${i}`} className="px-1 text-muted">…</span> : (
        <button key={n} onClick={() => onPage(n)} aria-current={n === page ? "page" : undefined}
          className={cx(btn, n === page ? "border border-line bg-panel font-medium" : "text-muted hover:bg-soft")}>{n + 1}</button>
      ))}
      <button disabled={page + 1 >= pages} onClick={() => onPage(page + 1)} className={cx(btn, "text-muted hover:bg-soft disabled:opacity-40")}>Next ›</button>
    </nav>
  );
}
