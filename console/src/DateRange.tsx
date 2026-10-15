import { useEffect, useRef, useState, type ReactNode } from "react";
import { cx } from "./ui";

// ------------------------------------------------------------------ date range (Hamsa's presets)

export type Preset = "all" | "hour" | "today" | "yesterday" | "week" | "month" | "custom";
export const PRESETS: [Preset, string][] = [
  ["hour", "Last hour"], ["today", "Today"], ["yesterday", "Yesterday"], ["week", "This week (from Sunday)"],
  ["month", "This month"], ["custom", "Custom"],
];
export const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
export const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const parseYmd = (s: string | null) => {
  const m = s?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
};

/** [start, end) of a preset in the viewer's local time. */
export function rangeOf(preset: Preset, from: string | null, to: string | null): { start: Date; end: Date } {
  const now = new Date();
  const today = midnight(now);
  switch (preset) {
    case "all": return { start: new Date(2000, 0, 1), end: addDays(today, 1) };
    case "hour": return { start: new Date(now.getTime() - 3_600_000), end: new Date(now.getTime() + 60_000) };
    case "yesterday": return { start: addDays(today, -1), end: today };
    case "week": return { start: addDays(today, -today.getDay()), end: addDays(today, 7 - today.getDay()) };
    case "month": return { start: new Date(today.getFullYear(), today.getMonth(), 1),
                           end: new Date(today.getFullYear(), today.getMonth() + 1, 1) };
    case "custom": {
      const s = parseYmd(from) ?? today, e = parseYmd(to) ?? s;
      return s <= e ? { start: s, end: addDays(e, 1) } : { start: e, end: addDays(s, 1) };
    }
    default: return { start: today, end: addDays(today, 1) };
  }
}

export const presetLabel = (p: Preset, from: string | null, to: string | null) =>
  p === "custom" && from ? (to && to !== from ? `${from} → ${to}` : from) : p === "all" ? "All" : PRESETS.find(x => x[0] === p)?.[1] ?? "Today";

const ico = (d: ReactNode) => (
  <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round">{d}</svg>
);
const I = {
  calendar: ico(<><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>),
  chevron: ico(<path d="m6 9 6 6 6-6" />),
  check: ico(<path d="M20 6 9 17l-5-5" />),
};


/** Hamsa's date dropdown: presets (+ "All" where `withAll`) and a custom from / to. */
export function RangePicker({ preset, from, to, onChange, withAll }: {
  preset: Preset; from: string | null; to: string | null; withAll?: boolean;
  onChange: (p: Preset, from?: string, to?: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(preset === "custom");
  const [a, setA] = useState(from ?? ymd(new Date()));
  const [b, setB] = useState(to ?? from ?? ymd(new Date()));
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button id="range-picker" onClick={() => { setOpen(!open); setCustom(preset === "custom"); }}
        className="flex w-64 max-w-full items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">
        {I.calendar}<span className="flex-1 truncate text-left">{presetLabel(preset, from, to)}</span>{I.chevron}
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-1 w-64 rounded-lg border border-line bg-panel p-1 shadow-lg">
          {(withAll ? [["all", "All"] as [Preset, string], ...PRESETS] : PRESETS).map(([p, label]) => (
            <button key={p} onClick={() => { if (p === "custom") setCustom(true); else { onChange(p); setOpen(false); } }}
              className={cx("flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-soft",
                (p === "custom" ? custom : preset === p && !custom) && "font-medium")}>
              {label}{(p === "custom" ? custom : preset === p && !custom) && <span className="text-accent-text">{I.check}</span>}
            </button>
          ))}
          {custom && (
            <form className="space-y-2 border-t border-line p-2" onSubmit={e => { e.preventDefault(); onChange("custom", a, b); setOpen(false); }}>
              <label className="block text-xs text-muted">From<input type="date" value={a} max={b} onChange={e => setA(e.target.value)} className="mt-0.5 w-full" /></label>
              <label className="block text-xs text-muted">To<input type="date" value={b} min={a} onChange={e => setB(e.target.value)} className="mt-0.5 w-full" /></label>
              <button type="submit" className="w-full rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90">Apply</button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
