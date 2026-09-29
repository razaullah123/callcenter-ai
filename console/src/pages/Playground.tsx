import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, cx } from "../ui";

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

type Line = { role: "user" | "agent" | "sys"; text: string };

export default function Playground() {
  const [state, setState] = useState<"idle" | "connecting" | "listening" | "speaking">("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [lat, setLat] = useState<number[]>([]);
  const [level, setLevel] = useState(0);
  const [callId, setCallId] = useState<string | null>(null);
  const refs = useRef<{ ws?: WebSocket; ctx?: AudioContext; mic?: MediaStream; t: number; srcs: AudioBufferSourceNode[] }>({ t: 0, srcs: [] });
  const logRef = useRef<HTMLDivElement>(null);
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [lines]);
  useEffect(() => () => hang(), []);  // eslint-disable-line react-hooks/exhaustive-deps

  const add = (l: Line) => setLines(p => [...p, l]);

  const play = (b64: string) => {
    const r = refs.current, ctx = r.ctx!;
    const bin = atob(b64), n = bin.length >> 1, f = new Float32Array(n);
    for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v >= 32768) v -= 65536; f[i] = v / 32768; }
    const buf = ctx.createBuffer(1, n, RATE); buf.copyToChannel(f, 0);
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(ctx.destination);
    r.t = Math.max(r.t, ctx.currentTime + 0.03); src.start(r.t); r.t += buf.duration;
    r.srcs.push(src);
    src.onended = () => { r.srcs = r.srcs.filter(s => s !== src); if (!r.srcs.length) setState(s => s === "speaking" ? "listening" : s); };
    setState("speaking");
  };
  const clear = () => { const r = refs.current; r.srcs.forEach(s => { try { s.stop(); } catch { /* ended */ } }); r.srcs = []; r.t = 0; };

  const start = async () => {
    setLines([]); setLat([]); setState("connecting");
    const r = refs.current;
    r.ctx = new AudioContext({ sampleRate: RATE });
    await r.ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" })));
    r.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    const node = new AudioWorkletNode(r.ctx, "capture");
    r.ctx.createMediaStreamSource(r.mic).connect(node);
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    r.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ event: "start", audio: { encoding: "pcm16", sample_rate: RATE } }));
    node.port.onmessage = e => {
      const f = e.data as Float32Array, i16 = new Int16Array(f.length); let peak = 0;
      for (let i = 0; i < f.length; i++) { const v = Math.max(-1, Math.min(1, f[i])); i16[i] = v * 32767; peak = Math.max(peak, Math.abs(v)); }
      setLevel(peak);
      if (ws.readyState === 1) {
        let s = ""; const u8 = new Uint8Array(i16.buffer);
        for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
        ws.send(JSON.stringify({ event: "media", payload: btoa(s) }));
      }
    };
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.event === "media") play(m.payload);
      else if (m.event === "clear") { clear(); setState("listening"); add({ role: "sys", text: "✂ interrupted" }); }
      else if (m.event === "ready") { setState("listening"); setCallId(m.call_id); }
      else if (m.event === "transcript") add({ role: m.role, text: m.text });
      else if (m.event === "metric") setLat(p => [...p, m.ms]);
      else if (m.event === "transfer") add({ role: "sys", text: `↪ transfer to human: ${m.reason}` });
      else if (m.event === "hangup") { add({ role: "sys", text: "☎ agent ended the call" }); hang(); }
    };
    ws.onclose = () => hang();
  };

  function hang() {
    const r = refs.current;
    try { if (r.ws?.readyState === 1) r.ws.send(JSON.stringify({ event: "stop" })); r.ws?.close(); } catch { /* closed */ }
    r.mic?.getTracks().forEach(t => t.stop());
    const ctx = r.ctx;
    setTimeout(() => { try { ctx?.close(); } catch { /* closed */ } }, 1500);
    r.ws = undefined; r.mic = undefined; r.ctx = undefined;
    setState("idle");
  }

  const avg = lat.length ? Math.round(lat.reduce((a, b) => a + b, 0) / lat.length) : null;
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Playground</h1>
        <p className="text-sm text-muted">Talk to the agent with your microphone (same pipeline as phone calls). Use headphones for clean interruptions. In hybrid test mode: mobile 0551234567, OTP 1234.</p>
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-3">
          {state === "idle" ? <Button kind="primary" onClick={start}>Start call</Button> : <Button kind="danger" onClick={hang}>Hang up</Button>}
          <Badge tone={state === "speaking" ? "warn" : state === "listening" ? "good" : "neutral"}>{state}</Badge>
          <div className="h-2 w-28 overflow-hidden rounded bg-soft"><div className="h-2 bg-accent transition-all" style={{ width: `${Math.min(100, level * 160)}%` }} /></div>
          {callId && <Link to={`/calls/${callId}`} className="font-mono text-xs text-accent">{callId}</Link>}
          <span className="ml-auto text-xs tabular-nums text-muted">
            {lat.length ? `last ${Math.round(lat[lat.length - 1])} ms · avg ${avg} ms` : "latency appears after the first reply"}
          </span>
        </div>
      </Card>
      <Card title="Conversation">
        <div ref={logRef} className="flex max-h-[55vh] min-h-48 flex-col gap-2 overflow-y-auto">
          {lines.map((l, i) => l.role === "sys"
            ? <div key={i} className="self-center text-xs text-muted">{l.text}</div>
            : <div key={i} dir="auto" className={cx("max-w-[85%] rounded-xl px-3 py-2 text-sm", l.role === "user" ? "self-end bg-accent/10" : "self-start bg-soft")}>{l.text}</div>)}
          {!lines.length && <div className="py-10 text-center text-sm text-muted">Press “Start call” and say hello.</div>}
        </div>
      </Card>
    </div>
  );
}
