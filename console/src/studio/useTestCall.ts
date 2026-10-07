import { useCallback, useEffect, useRef, useState } from "react";

// A test session with an agent: a browser voice call (/ws, microphone + speaker) or a typed chat (/ws/chat).
// Both report the agent's lines and the call id; the call's events come from the live log (/api/live).

const RATE = 16000, FRAME = 320;   // 20 ms @ 16 kHz
const WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = []; }
  process(inputs) {
    const ch = inputs[0][0]; if (!ch) return true;
    for (const s of ch) this.buf.push(s);
    while (this.buf.length >= ${FRAME}) this.port.postMessage(new Float32Array(this.buf.splice(0, ${FRAME})));
    return true;
  }
}
registerProcessor("capture", Capture);`;

export type TestMode = "voice" | "chat";
export type Line = { role: "user" | "agent" | "sys"; text: string };
export type CallState = "idle" | "connecting" | "listening" | "speaking" | "ended";

const wsBase = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;

export function useTestCall() {
  const [state, setState] = useState<CallState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [callId, setCallId] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [latency, setLatency] = useState<number[]>([]);
  const [mode, setMode] = useState<TestMode | null>(null);
  const r = useRef<{ ws?: WebSocket; ctx?: AudioContext; mic?: MediaStream; t: number; srcs: AudioBufferSourceNode[] }>({ t: 0, srcs: [] });
  const add = (l: Line) => setLines(p => [...p, l]);

  const play = (b64: string) => {
    const ctx = r.current.ctx!;
    const bin = atob(b64), n = bin.length >> 1, f = new Float32Array(n);
    for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v >= 32768) v -= 65536; f[i] = v / 32768; }
    const buf = ctx.createBuffer(1, n, RATE); buf.copyToChannel(f, 0);
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination);
    r.current.t = Math.max(r.current.t, ctx.currentTime + 0.03); src.start(r.current.t); r.current.t += buf.duration;
    r.current.srcs.push(src);
    src.onended = () => { r.current.srcs = r.current.srcs.filter(s => s !== src); if (!r.current.srcs.length) setState(s => s === "speaking" ? "listening" : s); };
    setState("speaking");
  };
  const clearAudio = () => { r.current.srcs.forEach(s => { try { s.stop(); } catch { /* ended */ } }); r.current.srcs = []; r.current.t = 0; };

  const stop = useCallback(() => {
    const c = r.current;
    try { if (c.ws?.readyState === 1) c.ws.send(JSON.stringify({ event: "stop" })); c.ws?.close(); } catch { /* closed */ }
    c.mic?.getTracks().forEach(t => t.stop());
    const ctx = c.ctx;
    setTimeout(() => { try { ctx?.close(); } catch { /* closed */ } }, 1500);
    c.ws = undefined; c.mic = undefined; c.ctx = undefined;
    setState(s => (s === "idle" ? "idle" : "ended"));
  }, []);
  useEffect(() => () => stop(), [stop]);

  const onMessage = (e: MessageEvent) => {
    const m = JSON.parse(e.data);
    if (m.event === "media") play(m.payload);
    else if (m.event === "clear") { clearAudio(); setState("listening"); add({ role: "sys", text: "✂ interrupted" }); }
    else if (m.event === "ready") { setState("listening"); setCallId(m.call_id); }
    else if (m.event === "transcript") add({ role: m.role, text: m.text });
    else if (m.event === "metric") setLatency(p => [...p, m.ms]);
    else if (m.event === "transfer") add({ role: "sys", text: `↪ transferred to a person: ${m.reason ?? ""}` });
    else if (m.event === "agent_transfer") add({ role: "sys", text: `↪ now speaking with ${m.name ?? m.agent_id}` });
    else if (m.event === "hangup") { add({ role: "sys", text: "☎ the agent ended the call" }); stop(); }
    else if (m.event === "error") add({ role: "sys", text: `⚠ ${m.message}` });
  };

  const start = useCallback(async (m: TestMode, opts: { agent: string; draft: boolean; language?: string }) => {
    stop();
    setLines([]); setLatency([]); setCallId(null); setMode(m); setState("connecting");
    const c = r.current;
    if (m === "chat") {
      const ws = new WebSocket(`${wsBase()}/ws/chat`);
      c.ws = ws;
      ws.onopen = () => ws.send(JSON.stringify({ event: "start", agent: opts.agent, draft: opts.draft, language: opts.language }));
      ws.onmessage = onMessage;
      ws.onclose = () => setState(s => (s === "idle" ? "idle" : "ended"));
      return;
    }
    c.ctx = new AudioContext({ sampleRate: RATE });
    await c.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
    c.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    const node = new AudioWorkletNode(c.ctx, "capture");
    c.ctx.createMediaStreamSource(c.mic).connect(node);
    const ws = new WebSocket(`${wsBase()}/ws`);
    c.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ event: "start", audio: { encoding: "pcm16", sample_rate: RATE }, agent: opts.agent, draft: opts.draft }));
    node.port.onmessage = ev => {
      const f = ev.data as Float32Array, i16 = new Int16Array(f.length); let peak = 0;
      for (let i = 0; i < f.length; i++) { const v = Math.max(-1, Math.min(1, f[i])); i16[i] = v * 32767; peak = Math.max(peak, Math.abs(v)); }
      setLevel(peak);
      if (ws.readyState === 1) {
        let s = ""; const u8 = new Uint8Array(i16.buffer);
        for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
        ws.send(JSON.stringify({ event: "media", payload: btoa(s) }));
      }
    };
    ws.onmessage = onMessage;
    ws.onclose = () => stop();
  }, [stop]);  // eslint-disable-line react-hooks/exhaustive-deps

  const send = useCallback((text: string) => {
    const ws = r.current.ws;
    if (!text.trim() || !ws || ws.readyState !== 1) return;
    add({ role: "user", text });
    ws.send(JSON.stringify({ event: "text", text }));
  }, []);

  const running = state === "connecting" || state === "listening" || state === "speaking";
  return { state, running, mode, lines, callId, level, latency, start, stop, send };
}
