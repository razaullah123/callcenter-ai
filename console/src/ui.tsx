import type { ReactNode } from "react";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

export function Card({ title, actions, children, className }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums",
        tone === "good" && "text-good", tone === "warn" && "text-warn", tone === "bad" && "text-bad")}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: {
  children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const tones = {
    neutral: "bg-soft text-muted", good: "bg-good/15 text-good", warn: "bg-warn/15 text-warn",
    bad: "bg-bad/15 text-bad", info: "bg-accent/15 text-accent",
  };
  return <span className={cx("inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium", tones[tone])}>{children}</span>;
}

export function Button({ children, onClick, kind = "default", disabled, type = "button", title }: {
  children: ReactNode; onClick?: () => void; kind?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean; type?: "button" | "submit"; title?: string;
}) {
  const kinds = {
    default: "border border-line bg-panel hover:bg-soft",
    primary: "bg-accent text-white hover:opacity-90",
    danger: "bg-bad text-white hover:opacity-90",
    ghost: "hover:bg-soft",
  };
  return (
    <button type={type} title={title} disabled={disabled} onClick={onClick}
      className={cx("rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50", kinds[kind])}>
      {children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-10 text-center text-sm text-muted">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : JSON.stringify(error);
  return <div className="rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">{msg}</div>;
}

export const fmtMs = (v: number | null | undefined) =>
  v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;
export const fmtPct = (n: number, d: number) => (d ? `${Math.round((100 * n) / d)}%` : "—");
export const fmtTime = (iso: string) => new Date(iso).toLocaleString();
export const fmtClock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function levelTone(level: string): "neutral" | "warn" | "bad" {
  return level === "error" ? "bad" : level === "warning" ? "warn" : "neutral";
}

export function outcome(c: { booked: boolean; handoff: string | null; verified: boolean; ended_at: string | null }) {
  if (c.booked) return <Badge tone="good">booked</Badge>;
  if (c.handoff) return <Badge tone="warn">handoff</Badge>;
  if (!c.ended_at) return <Badge tone="info">live</Badge>;
  if (!c.verified) return <Badge tone="neutral">unverified</Badge>;
  return <Badge>completed</Badge>;
}
