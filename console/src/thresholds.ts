// Hamsa's dashboard guidance bands (docs.tryhamsa.com/agents/dashboard/*) as tone + short hint.
export type Tone = "good" | "warn" | "bad";
export type Band = { tone?: Tone; note?: string };

/** `limits` ascending: value below limits[i] gets labels[i]; at or above the last limit gets the last label. */
function lowerIsBetter(v: number | null | undefined, limits: [number, number, number], notes: [string, string, string, string]): Band {
  if (v == null) return {};
  const i = v < limits[0] ? 0 : v < limits[1] ? 1 : v < limits[2] ? 2 : 3;
  return { tone: ([ "good", undefined, "warn", "bad"] as (Tone | undefined)[])[i], note: notes[i] };
}

export const asrBand = (ms: number | null | undefined) =>
  lowerIsBetter(ms, [300, 500, 800], ["normal", "acceptable", "investigate", "investigate"]);
export const llmBand = (ms: number | null | undefined) =>
  lowerIsBetter(ms, [1000, 2000, 3000], ["fast", "normal", "acceptable", "slow — callers may think it is unresponsive"]);
export const ttsBand = (ms: number | null | undefined) =>
  lowerIsBetter(ms, [400, 600, 800], ["fast", "normal", "acceptable", "investigate"]);
export const latencyBand = (ms: number | null | undefined) =>
  lowerIsBetter(ms, [2000, 3000, 4000], ["excellent", "normal", "acceptable", "poor"]);
/** error rate in percent */
export const errorBand = (pct: number | null | undefined) =>
  lowerIsBetter(pct, [1, 3, 5], ["excellent", "normal", "acceptable", "problematic"]);
/** escalation / forwarded-to-human rate in percent */
export const escalationBand = (pct: number | null | undefined) =>
  lowerIsBetter(pct, [10, 20, 30], ["excellent", "normal", "acceptable", "high"]);
/** first-call resolution in percent (higher is better) */
export function fcrBand(pct: number | null | undefined): Band {
  if (pct == null) return {};
  return pct > 80 ? { tone: "good", note: "excellent" } : pct >= 70 ? { tone: "good", note: "good" }
    : pct >= 60 ? { tone: "warn", note: "acceptable" } : { tone: "bad", note: "investigate" };
}
/** average words per AI reply: 20-60 natural, <20 maybe too short, >80 may overwhelm */
export function wordsBand(w: number | null | undefined): Band {
  if (w == null || w <= 0) return {};
  return w < 20 ? { tone: "warn", note: "may be too short" } : w <= 60 ? { tone: "good", note: "natural range" }
    : w <= 80 ? { note: "getting long" } : { tone: "warn", note: "may overwhelm callers" };
}
/** share of calls under 30 s in percent: those often mean connection problems */
export const shortCallsBand = (pct: number | null | undefined): Band =>
  pct == null ? {} : pct > 30 ? { tone: "warn", note: "many calls under 30 s — check for connection problems" } : {};

/** CSAT in percent (higher is better): > 85 excellent, 75-85 good, 65-75 needs work, < 65 investigate */
export function csatBand(pct: number | null | undefined): Band {
  if (pct == null) return {};
  return pct > 85 ? { tone: "good", note: "excellent" } : pct >= 75 ? { tone: "good", note: "good" }
    : pct >= 65 ? { tone: "warn", note: "needs work" } : { tone: "bad", note: "investigate" };
}
/** NPS, -100..100: > 50 excellent, 30-50 good, 0-30 acceptable, < 0 investigate */
export function npsBand(n: number | null | undefined): Band {
  if (n == null) return {};
  return n > 50 ? { tone: "good", note: "excellent" } : n >= 30 ? { tone: "good", note: "good" }
    : n >= 0 ? { tone: "warn", note: "acceptable" } : { tone: "bad", note: "investigate" };
}
