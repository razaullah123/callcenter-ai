import { useEffect, useRef, useState } from "react";
import { cx } from "../ui";
import type { VarGroup } from "./variables";

/** "(x) Insert variable": a searchable list (system · custom · collected before this step) that puts `{{ name }}` into the
 *  text field being edited, at the cursor. `fields` are the text fields it may write to (the last one focused is used). */
export type PickerField = { id: string; value: string; onChange: (v: string) => void };

export default function VariablePicker({ fields, groups, label = "Insert variable" }: { fields: PickerField[]; groups: VarGroup[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const last = useRef<string>(fields[0]?.id ?? "");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const track = (e: FocusEvent) => { const id = (e.target as HTMLElement | null)?.id; if (id && fields.some(f => f.id === id)) last.current = id; };
    document.addEventListener("focusin", track);
    return () => document.removeEventListener("focusin", track);
  }, [fields]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const insert = (name: string) => {
    const field = fields.find(f => f.id === last.current) ?? fields[0];
    if (!field) return;
    const el = document.getElementById(field.id) as HTMLInputElement | HTMLTextAreaElement | null;
    const at = el?.selectionStart ?? field.value.length, to = el?.selectionEnd ?? at;
    const text = `{{ ${name} }}`;
    field.onChange(field.value.slice(0, at) + text + field.value.slice(to));
    setOpen(false); setQ("");
    setTimeout(() => { el?.focus(); el?.setSelectionRange(at + text.length, at + text.length); }, 0);
  };
  const match = (s: string) => !q || s.toLowerCase().includes(q.toLowerCase());
  const shown = groups.map(g => ({ ...g, items: g.items.filter(i => match(i.name) || match(i.hint ?? "")) })).filter(g => g.items.length);
  return (
    <div ref={box} className="relative inline-block">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
        className="flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted hover:bg-soft hover:text-ink">
        <span className="font-semibold">(x)</span>{label}</button>
      {open && (
        <div className="absolute left-0 z-40 mt-1 w-72 rounded-xl border border-line bg-panel p-2 shadow-xl" role="listbox" aria-label="Variables">
          <input autoFocus className="mb-1.5 w-full text-xs" placeholder="Search variables…" value={q} onChange={e => setQ(e.target.value)} />
          <div className="max-h-64 overflow-y-auto">
            {shown.map(g => (
              <div key={g.label} className="mb-1.5">
                <div className="px-1 text-[10px] font-semibold uppercase tracking-wide text-muted">{g.label}</div>
                {g.items.map(i => (
                  <button key={i.name} type="button" role="option" onClick={() => insert(i.name)}
                    className={cx("flex w-full items-baseline justify-between gap-2 rounded-md px-1.5 py-1 text-left text-xs hover:bg-soft")}>
                    <span className="font-mono">{i.name}</span>{i.hint && <span className="truncate text-[10px] text-muted">{i.hint}</span>}
                  </button>))}
              </div>))}
            {!shown.length && <div className="px-1 py-3 text-center text-xs text-muted">No variable matches.</div>}
          </div>
        </div>
      )}
    </div>
  );
}
