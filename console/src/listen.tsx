import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl } from "./api";
import { cx } from "./ui";

// Listen in on a running call (Hamsa: "Start Live Monitoring"). Listen-only: the server sends the caller's and the agent's
// audio as binary frames — 1 byte source (0 caller, 1 agent), 2 bytes sample rate, PCM16 mono — which are scheduled here
// back to back with a small buffer. Each side can be muted.

type State = "idle" | "connecting" | "listening" | "ended" | "error";
const BUFFER_S = 0.15;

export function ListenCard({ callId }: { callId: string }) {
  const [state, setState] = useState<State>("idle");
  const [message, setMessage] = useState("");
  const [muted, setMuted] = useState<[boolean, boolean]>([false, false]);       // caller, agent
  const [talking, setTalking] = useState<[boolean, boolean]>([false, false]);
  const ctx = useRef<AudioContext | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const gains = useRef<GainNode[]>([]);
  const next = useRef<[number, number]>([0, 0]);
  const quiet = useRef<[number, number]>([0, 0]);                                // when each side last made sound (ms)
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  const stop = useCallback((to: State = "idle", why = "") => {
    ws.current?.close();
    ws.current = null;
    ctx.current?.close().catch(() => undefined);
    ctx.current = null;
    setTalking([false, false]);
    setState(to);
    setMessage(why);
  }, []);
  useEffect(() => () => stop(), [stop]);                                         // leaving the page stops it
  useEffect(() => { gains.current.forEach((g, i) => { g.gain.value = muted[i] ? 0 : 1; }); }, [muted]);
  useEffect(() => {                                                              // the "speaking" dots fade after a moment of silence
    if (state !== "listening") return;
    const t = setInterval(() => {
      const now = performance.now();
      setTalking(prev => { const v: [boolean, boolean] = [now - quiet.current[0] < 400, now - quiet.current[1] < 400]; return v[0] === prev[0] && v[1] === prev[1] ? prev : v; });
    }, 150);
    return () => clearInterval(t);
  }, [state]);

  const play = (buf: ArrayBuffer) => {
    const c = ctx.current;
    if (!c || buf.byteLength < 5) return;
    const dv = new DataView(buf);
    const source = dv.getUint8(0) === 1 ? 1 : 0;
    const rate = dv.getUint16(1);
    const raw = buf.slice(3);
    const pcm = new Int16Array(raw, 0, raw.byteLength >> 1);
    if (!pcm.length || rate < 8000 || rate > 48000) return;
    const audio = c.createBuffer(1, pcm.length, rate);
    const out = audio.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < pcm.length; i++) { out[i] = pcm[i] / 32768; sum += out[i] * out[i]; }
    if (Math.sqrt(sum / pcm.length) > 0.01) quiet.current[source] = performance.now();
    const node = c.createBufferSource();
    node.buffer = audio;
    node.connect(gains.current[source]);
    const at = Math.max(next.current[source], c.currentTime + BUFFER_S);          // after an underrun, rebuffer
    node.start(at);
    next.current[source] = at + audio.duration;
  };

  const start = async () => {
    setState("connecting");
    setMessage("");
    try {
      const c = new AudioContext();
      await c.resume();
      ctx.current = c;
      gains.current = [c.createGain(), c.createGain()];
      gains.current.forEach((g, i) => { g.gain.value = mutedRef.current[i] ? 0 : 1; g.connect(c.destination); });
      next.current = [0, 0];
      const sock = new WebSocket(wsUrl("/api/live/listen", { call_id: callId }));
      sock.binaryType = "arraybuffer";
      ws.current = sock;
      sock.onopen = () => setState("listening");
      sock.onmessage = e => {
        if (typeof e.data !== "string") return play(e.data as ArrayBuffer);
        try {
          const m = JSON.parse(e.data) as { type: string; message?: string };
          if (m.type === "ended") stop("ended", "The call has ended.");
          else if (m.type === "error") stop("error", m.message ?? "Could not listen to this call.");
        } catch { /* ignore */ }
      };
      sock.onclose = ev => { if (ws.current === sock) stop(ev.code === 1008 ? "error" : "idle", ev.code === 1008 ? "You may not listen to this call." : ""); };
      sock.onerror = () => { if (ws.current === sock) stop("error", "Could not connect."); };
    } catch (e) {
      stop("error", e instanceof Error ? e.message : String(e));
    }
  };

  const on = state === "listening" || state === "connecting";
  const toggle = (i: 0 | 1) => setMuted(m => (i === 0 ? [!m[0], m[1]] : [m[0], !m[1]]));
  const side = (i: 0 | 1, label: string) => (
    <button type="button" aria-pressed={!muted[i]} aria-label={`${muted[i] ? "Unmute" : "Mute"} ${label}`} onClick={() => toggle(i)}
      className={cx("flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs", muted[i] ? "border-line text-muted line-through" : "border-line hover:bg-soft")}>
      <span className={cx("h-2 w-2 rounded-full", talking[i] && !muted[i] ? "animate-pulse bg-good" : "bg-soft ring-1 ring-line")} />{label}</button>
  );
  return (
    <section className="rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 font-semibold">
          <span className={cx("h-2 w-2 rounded-full", state === "listening" ? "animate-pulse bg-good" : "bg-soft ring-1 ring-line")} />Listen in</h3>
        {!on ? <button id="listen-start" onClick={start} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90">
            {state === "ended" || state === "error" ? "Listen again" : "Start listening"}</button>
          : <div className="flex items-center gap-2">{side(0, "Caller")}{side(1, "Agent")}
            <button id="listen-stop" onClick={() => stop()} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-soft">{state === "connecting" ? "Connecting…" : "Stop"}</button></div>}
      </div>
      {message && <p role="status" className={cx("mt-2 text-xs", state === "error" ? "text-bad" : "text-muted")}>{message}</p>}
      <p className="mt-2 text-[11px] text-muted">Listen-only — you can't be heard, and the caller isn't told someone may be listening. Each listener is written to the agent's audit
        log. Up to 3 people at once. Use the instruction box to steer the agent.</p>
    </section>
  );
}
