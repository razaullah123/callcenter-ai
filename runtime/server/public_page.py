"""The public page and the embed script (see public.py): plain HTML / CSS / JavaScript, no build step, served as strings."""

NOT_FOUND_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Link unavailable</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0a0a;color:#eee;font:16px system-ui,sans-serif;text-align:center;padding:24px}
p{color:#999;margin-top:8px}</style></head><body><main><h1 style="margin:0;font-size:22px">This link is no longer available</h1>
<p>The agent was unpublished or the address is wrong.</p></main></body></html>"""

PAGE_HTML = r'''<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>Talk to the agent</title>
<style>
:root{--bg:#0a0a0a;--fg:#f5f5f5;--muted:#a1a1a1;--panel:rgba(255,255,255,.08);--line:rgba(255,255,255,.18);--g1:#cadcfc;--g2:#a0b9d1}
html[data-theme=light]{--fg:#141414;--muted:#6b6b6b;--panel:rgba(0,0,0,.05);--line:rgba(0,0,0,.14)}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;transition:background .25s,color .25s}
main{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:max(28px,env(safe-area-inset-top)) 20px max(20px,env(safe-area-inset-bottom));position:relative}
header{text-align:center;max-width:520px;min-height:76px}
h1{margin:0;font-size:22px;font-weight:600;letter-spacing:-.01em}
.desc{margin:4px 0 0;color:var(--muted);font-size:14px}
.tag{margin:10px 0 0;color:var(--muted);font-size:14px}
.stage{display:flex;flex-direction:column;align-items:center;gap:18px;flex:1;justify-content:center;width:100%}
canvas{display:block;max-width:100%}
#tx{width:min(520px,100%);min-height:64px;max-height:132px;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end;gap:6px;text-align:center;font-size:15px;mask-image:linear-gradient(transparent,#000 40%)}
#tx p{margin:0;color:var(--fg);opacity:.9}
#tx p.old{opacity:.45}
#tx[hidden]{display:none}
.call{display:flex;flex-direction:column;align-items:center;gap:8px}
button{font:inherit;color:inherit}
#call{width:64px;height:64px;border-radius:50%;border:1px solid var(--line);background:var(--panel);display:grid;place-items:center;cursor:pointer;transition:transform .15s,background .15s}
#call:hover{transform:scale(1.05)}
#call:focus-visible,#theme:focus-visible{outline:2px solid var(--g2);outline-offset:3px}
#call.live{background:#dc2626;border-color:#dc2626;color:#fff}
#call svg{width:26px;height:26px}
#status{font-size:13px;color:var(--muted);min-height:20px;text-align:center}
#status.err{color:#f87171}
#theme{position:absolute;top:max(14px,env(safe-area-inset-top));right:14px;width:38px;height:38px;border-radius:50%;border:1px solid var(--line);background:var(--panel);cursor:pointer;display:grid;place-items:center}
#theme[hidden]{display:none}
footer{font-size:11px;color:var(--muted);opacity:.8;height:20px}
body.embed footer{display:none}
@media (prefers-reduced-motion:reduce){canvas{animation:none}}
</style>
</head>
<body>
<main>
  <button id="theme" type="button" aria-label="Switch light / dark" hidden>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
  </button>
  <header><h1 id="title"></h1><p class="desc" id="desc"></p><p class="tag" id="tag"></p></header>
  <section class="stage" aria-label="Voice call">
    <canvas id="viz" width="320" height="320" aria-hidden="true"></canvas>
    <div id="tx" aria-live="polite" hidden></div>
    <div class="call">
      <button id="call" type="button" aria-label="Start call"></button>
      <div id="status" role="status">Ready to talk</div>
    </div>
  </section>
  <footer>Voice AI by Cloud Solutions</footer>
</main>
<script>
(() => {
  const TOKEN = "__TOKEN__";
  const Q = new URLSearchParams(location.search);
  const PREVIEW = TOKEN === "preview", EMBED = Q.get("embed") === "1";
  const RATE = 16000, FRAME = 320;
  const WORKLET = "class Capture extends AudioWorkletProcessor{constructor(){super();this.b=[]}process(i){const c=i[0][0];if(!c)return true;for(const s of c)this.b.push(s);while(this.b.length>=" + FRAME + ")this.port.postMessage(new Float32Array(this.b.splice(0," + FRAME + ")));return true}}registerProcessor('capture',Capture);";
  const $ = id => document.getElementById(id);
  const ICON_CALL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/></svg>';
  const ICON_END = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.7 13.3a16 16 0 0 0 3.4 2.6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2v3a2 2 0 0 1-2.2 2A19.8 19.8 0 0 1 11.2 19a19.4 19.4 0 0 1-3.3-2.7m-2.7-3.3A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9"/><path d="M22 2 2 22"/></svg>';
  const LABEL = {ready: "Ready to talk", connecting: "Connecting…", listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…", ended: "Call ended — tap to call again"};
  if (EMBED) document.body.classList.add("embed");

  let cfg = {theme: "dark", visualizer: "orb", gradient: ["#CADCFC", "#A0B9D1"], bg_dark: "#0A0A0A", bg_light: "#FAFAFA",
             show_transcript: true, theme_switcher: false, name: "", description: "", tagline: "", allowed_params: []};
  let state = "ready", themeNow = "dark";
  let ws = null, ctx = null, mic = null, analyser = null, playAt = 0, sources = [], micLevel = 0, lvl = 0, outBuf = null, awaitingAudio = false;
  const lines = [];

  // ---------------------------------------------------------------- appearance
  function hexRgb(h) { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  const rgba = (h, a) => { const c = hexRgb(h); return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")"; };
  function applyConfig(c) {
    cfg = Object.assign(cfg, c || {});
    let saved = null; try { saved = localStorage.getItem("pub-theme"); } catch (e) { /* private mode */ }
    themeNow = cfg.theme_switcher && (saved === "light" || saved === "dark") ? saved : cfg.theme;
    document.documentElement.dataset.theme = themeNow;
    const bg = themeNow === "dark" ? cfg.bg_dark : cfg.bg_light;
    document.documentElement.style.setProperty("--bg", bg);
    document.documentElement.style.setProperty("--g1", cfg.gradient[0]);
    document.documentElement.style.setProperty("--g2", cfg.gradient[1]);
    $("title").textContent = cfg.name || ""; $("title").hidden = !cfg.name;
    $("desc").textContent = cfg.description || ""; $("desc").hidden = !cfg.description;
    $("tag").textContent = cfg.tagline || ""; $("tag").hidden = !cfg.tagline;
    $("theme").hidden = !cfg.theme_switcher;
    $("tx").hidden = !cfg.show_transcript;
    document.title = cfg.name ? "Talk to " + cfg.name : "Talk to the agent";
  }
  $("theme").onclick = () => {
    const next = themeNow === "dark" ? "light" : "dark";
    try { localStorage.setItem("pub-theme", next); } catch (e) { /* private mode */ }
    cfg.theme_switcher = true; themeNow = next; document.documentElement.dataset.theme = next;
    document.documentElement.style.setProperty("--bg", next === "dark" ? cfg.bg_dark : cfg.bg_light);
  };

  function setState(s, text) {
    state = s;
    const st = $("status"); st.textContent = text || LABEL[s] || ""; st.className = s === "error" ? "err" : "";
    const live = s === "connecting" || s === "listening" || s === "thinking" || s === "speaking";
    $("call").innerHTML = live ? ICON_END : ICON_CALL; $("call").classList.toggle("live", live);
    $("call").setAttribute("aria-label", live ? "End call" : "Start call");
  }
  function addLine(text) {
    lines.push(text); while (lines.length > 4) lines.shift();
    const box = $("tx"); box.innerHTML = "";
    lines.forEach((t, i) => { const p = document.createElement("p"); p.dir = "auto"; p.textContent = t; if (i < lines.length - 1) p.className = "old"; box.appendChild(p); });
  }

  // ---------------------------------------------------------------- visualizer
  const cv = $("viz"), g = cv.getContext("2d");
  function resize() {
    const d = window.devicePixelRatio || 1, s = Math.max(180, Math.min(340, Math.floor(Math.min(innerWidth * 0.8, innerHeight * 0.4))));
    cv.style.width = s + "px"; cv.style.height = s + "px"; cv.width = Math.round(s * d); cv.height = Math.round(s * d);
  }
  addEventListener("resize", resize); resize();
  function simulated(t) {
    switch (state) {
      case "connecting": return 0.1 + 0.1 * Math.abs(Math.sin(t * 3));
      case "listening": return 0.1 + 0.12 * Math.abs(Math.sin(t * 2.3) * Math.sin(t * 0.7));
      case "thinking": return 0.1 + 0.06 * Math.sin(t * 4);
      case "speaking": return 0.35 + 0.35 * Math.abs(Math.sin(t * 7) * Math.sin(t * 2.1));
      default: return 0.05 + 0.03 * Math.sin(t * 1.5);
    }
  }
  function measured(t) {
    if (state === "speaking" && analyser) {
      if (!outBuf) outBuf = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(outBuf);
      let sum = 0; for (let i = 0; i < outBuf.length; i++) { const v = (outBuf[i] - 128) / 128; sum += v * v; }
      return Math.min(1, Math.sqrt(sum / outBuf.length) * 3.2);
    }
    if (state === "listening") return Math.min(1, micLevel * 2.2);
    return simulated(t);
  }
  function draw(t, v) {
    const W = cv.width, c = W / 2, dark = themeNow === "dark", g1 = cfg.gradient[0], g2 = cfg.gradient[1];
    g.clearRect(0, 0, W, W);
    if (cfg.visualizer === "wave") {
      g.lineWidth = Math.max(2, W * 0.012); g.lineCap = "round";
      for (let k = 0; k < 3; k++) {
        g.beginPath();
        for (let x = 0; x <= W; x += 4) {
          const env = Math.sin(Math.PI * x / W), amp = W * (0.02 + 0.2 * v) * env * (1 - k * 0.22);
          const y = c + Math.sin(x * 0.025 * (k + 1) + t * (2 + k * 0.8)) * amp;
          x === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
        }
        g.strokeStyle = rgba(k === 1 ? g1 : g2, 0.95 - k * 0.28); g.stroke();
      }
    } else if (cfg.visualizer === "aura") {
      g.globalCompositeOperation = dark ? "lighter" : "source-over";
      for (let i = 0; i < 6; i++) {
        const a = t * (0.25 + 0.07 * i) + i * 1.3, r = W * (0.2 + 0.12 * v + 0.03 * i);
        const x = c + Math.cos(a) * W * 0.17, y = c + Math.sin(a * 1.2) * W * 0.17;
        const rg = g.createRadialGradient(x, y, 0, x, y, r * 1.5);
        rg.addColorStop(0, rgba(i % 2 ? g1 : g2, dark ? 0.42 : 0.5)); rg.addColorStop(1, rgba(i % 2 ? g1 : g2, 0));
        g.fillStyle = rg; g.beginPath(); g.arc(x, y, r * 1.5, 0, Math.PI * 2); g.fill();
      }
      g.globalCompositeOperation = "source-over";
    } else {
      const R = W * 0.3 * (1 + 0.1 * v);
      const glow = g.createRadialGradient(c, c, R * 0.6, c, c, R * 1.7);
      glow.addColorStop(0, rgba(g2, 0.35)); glow.addColorStop(1, rgba(g2, 0));
      g.fillStyle = glow; g.beginPath(); g.arc(c, c, R * 1.7, 0, Math.PI * 2); g.fill();
      const body = g.createRadialGradient(c - R * 0.3, c - R * 0.35, R * 0.1, c, c, R);
      body.addColorStop(0, g1); body.addColorStop(1, g2);
      g.save(); g.beginPath(); g.arc(c, c, R, 0, Math.PI * 2); g.clip(); g.fillStyle = body; g.fillRect(0, 0, W, W);
      for (let i = 0; i < 4; i++) {
        const a = t * (0.35 + 0.12 * i) + i * 1.7, x = c + Math.cos(a) * R * 0.55, y = c + Math.sin(a * 1.1) * R * 0.55;
        const rg = g.createRadialGradient(x, y, 0, x, y, R * (0.7 + 0.4 * v));
        rg.addColorStop(0, i % 2 ? "rgba(255,255,255,0.35)" : rgba(g2, 0.55)); rg.addColorStop(1, "rgba(255,255,255,0)");
        g.fillStyle = rg; g.fillRect(0, 0, W, W);
      }
      g.restore();
    }
  }
  const t0 = performance.now();
  function frame(now) {
    const t = (now - t0) / 1000, want = PREVIEW ? simulated(t) : measured(t);
    lvl += (want - lvl) * 0.18;
    draw(t, lvl);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  // ---------------------------------------------------------------- the call
  function play(b64) {
    const bin = atob(b64), n = bin.length >> 1, f = new Float32Array(n);
    for (let i = 0; i < n; i++) { let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8); if (v >= 32768) v -= 65536; f[i] = v / 32768; }
    const buf = ctx.createBuffer(1, n, RATE); buf.copyToChannel(f, 0);
    const src = ctx.createBufferSource(); src.buffer = buf; src.connect(analyser);
    playAt = Math.max(playAt, ctx.currentTime + 0.03); src.start(playAt); playAt += buf.duration;
    sources.push(src);
    src.onended = () => { sources = sources.filter(s => s !== src); if (!sources.length && state === "speaking") setState("listening"); };
    awaitingAudio = false; if (state !== "speaking") setState("speaking");
  }
  function clearAudio() { sources.forEach(s => { try { s.stop(); } catch (e) { /* already ended */ } }); sources = []; playAt = 0; }
  function finish(text, error) {
    try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({event: "stop"})); if (ws) ws.close(); } catch (e) { /* closed */ }
    if (mic) mic.getTracks().forEach(t => t.stop());
    const c = ctx; setTimeout(() => { try { if (c) c.close(); } catch (e) { /* closed */ } }, 1200);
    ws = null; mic = null; ctx = null; analyser = null; sources = []; micLevel = 0;
    setState(error ? "error" : "ended", text);
  }
  // ---------------------------------------------------------------- web tools: functions the embedding site registered
  // The site (embed.js) answers which tools it can run and runs them; this page only relays between it and the server.
  const pendingTools = {};
  function hostTools() {
    return new Promise(resolve => {
      if (parent === window) return resolve([]);
      const done = names => { removeEventListener("message", on); clearTimeout(timer); resolve(names); };
      const on = e => {
        if (e.source === parent && e.data && e.data.type === "hmg-web-tools")
          done(Array.isArray(e.data.names) ? e.data.names.filter(n => typeof n === "string").slice(0, 50) : []);
      };
      const timer = setTimeout(() => done([]), 500);
      addEventListener("message", on);
      parent.postMessage({type: "hmg-web-tools?"}, "*");
    });
  }
  addEventListener("message", e => {
    if (e.source !== parent || !e.data || e.data.type !== "hmg-web-tool-result") return;
    const reply = pendingTools[e.data.id];
    if (!reply) return;
    delete pendingTools[e.data.id];
    reply("error" in e.data ? {error: String(e.data.error)} : {result: e.data.result});
  });
  function runWebTool(m, sock) {
    const reply = r => {
      if (sock.readyState !== 1) return;
      try { sock.send(JSON.stringify(Object.assign({event: "web_tool_result", id: m.id}, r))); }
      catch (err) { sock.send(JSON.stringify({event: "web_tool_result", id: m.id, error: "the result is not plain data"})); }
    };
    if (parent === window) return reply({error: "this page is not embedded in a site"});
    pendingTools[m.id] = reply;
    parent.postMessage({type: "hmg-web-tool", id: m.id, name: m.name, args: m.args || {}}, "*");
  }
  async function start() {
    if (PREVIEW || state === "connecting" || state === "listening" || state === "thinking" || state === "speaking") return;
    setState("connecting"); lines.length = 0; $("tx").innerHTML = "";
    try {
      mic = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}});
    } catch (e) { setState("error", "Microphone access is needed to talk to the agent."); return; }
    ctx = new (window.AudioContext || window.webkitAudioContext)({sampleRate: RATE});
    await ctx.resume();
    analyser = ctx.createAnalyser(); analyser.fftSize = 512; analyser.connect(ctx.destination);
    await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], {type: "application/javascript"})));
    const node = new AudioWorkletNode(ctx, "capture");
    ctx.createMediaStreamSource(mic).connect(node);
    const params = {}; (cfg.allowed_params || []).forEach(k => { if (Q.has(k)) params[k] = Q.get(k); });
    const webTools = await hostTools();
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/public/" + encodeURIComponent(TOKEN));
    const mine = ws;
    ws.onopen = () => mine.send(JSON.stringify({event: "start", audio: {encoding: "pcm16", sample_rate: RATE}, params, web_tools: webTools}));
    node.port.onmessage = ev => {
      const f = ev.data, i16 = new Int16Array(f.length); let peak = 0;
      for (let i = 0; i < f.length; i++) { const v = Math.max(-1, Math.min(1, f[i])); i16[i] = v * 32767; peak = Math.max(peak, Math.abs(v)); }
      micLevel += (peak - micLevel) * 0.5;
      if (mine.readyState === 1) {
        let s = ""; const u8 = new Uint8Array(i16.buffer);
        for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
        mine.send(JSON.stringify({event: "media", payload: btoa(s)}));
      }
    };
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.event === "media") play(m.payload);
      else if (m.event === "clear") { clearAudio(); setState("listening"); }
      else if (m.event === "ready") setState("listening");
      else if (m.event === "web_tool") runWebTool(m, mine);
      else if (m.event === "transcript") {
        if (m.role === "user") { awaitingAudio = true; if (state === "listening") setState("thinking"); }
        else if (m.role === "agent") addLine(m.text);
      }
      else if (m.event === "hangup" || m.event === "transfer") finish(m.event === "transfer" ? "Transferring you to a person…" : null, false);
      else if (m.event === "error") finish(m.message, true);
    };
    ws.onclose = () => { if (ws === mine && state !== "ended" && state !== "error") finish(null, false); };
  }
  $("call").onclick = () => {
    if (PREVIEW) return;
    if (state === "ready" || state === "ended" || state === "error") start(); else finish(null, false);
  };

  // ---------------------------------------------------------------- start
  setState("ready"); applyConfig(cfg);
  if (PREVIEW) {
    addEventListener("message", e => {            // the console's live preview: settings and a state to show
      if (e.origin !== location.origin || !e.data || e.data.type !== "share-preview") return;
      applyConfig(e.data.config); if (e.data.state) setState(e.data.state);
      if (e.data.config && e.data.config.name === undefined) { $("title").hidden = true; }
      if (e.data.sample) { lines.length = 0; (e.data.sample || []).forEach(addLine); }
    });
    parent.postMessage({type: "share-preview-ready"}, location.origin);
  } else {
    fetch("/public/" + encodeURIComponent(TOKEN) + "/config").then(r => r.ok ? r.json() : Promise.reject()).then(c => {
      applyConfig(c); if (Q.get("autostart") === "1") start();
    }).catch(() => { setState("error", "This link is no longer available."); $("call").disabled = true; });
  }
})();
</script>
</body>
</html>
'''

EMBED_JS = r'''(function () {
  var script = document.currentScript;
  if (!script) return;
  // Web tools: the site registers functions the agent may call (the agent's dashboard defines which tools exist and what they take).
  //   VoiceAgent.registerTools({ navigate_to_page: async function (args) { location.href = args.path; return "done"; } })
  // or, before this script loads: window.VoiceAgentTools = { navigate_to_page: function (args) { ... } }
  var local = {};
  window.VoiceAgent = window.VoiceAgent || {};
  window.VoiceAgent.registerTools = function (map) {
    Object.keys(map || {}).forEach(function (k) { if (typeof map[k] === "function") local[k] = map[k]; });
  };
  function registered() {
    var o = {}, g = window.VoiceAgentTools || {};
    Object.keys(g).forEach(function (k) { if (typeof g[k] === "function") o[k] = g[k]; });
    Object.keys(local).forEach(function (k) { o[k] = local[k]; });
    return o;
  }
  var d = script.dataset, origin = new URL(script.src, location.href).origin, token = d.token;
  if (!token) { console.error("[agent widget] data-token is missing"); return; }
  function fromAttributes() {
    var o = {};
    if (d.position) o.position = d.position;
    if (d.size) o.size = d.size;
    if (d.color) o.color = d.color;
    if (d.label !== undefined) o.label = d.label;
    if (d.autoStart !== undefined) o.auto_start = d.autoStart !== "false";
    if (d.launcher) o.launcher = d.launcher;
    return o;
  }
  fetch(origin + "/public/" + encodeURIComponent(token) + "/config").then(function (r) {
    if (!r.ok) throw new Error("unavailable");
    return r.json();
  }).then(function (cfg) { init(Object.assign({}, cfg.embed, fromAttributes())); }).catch(function () { /* unpublished: no widget */ });

  function init(o) {
    var SIZE = {sm: 48, md: 60, lg: 72}, size = SIZE[o.size] || 60;
    var host = document.createElement("div");
    host.style.cssText = "all:initial";
    document.body.appendChild(host);
    var root = host.attachShadow({mode: "open"});
    var pos = o.position === "bottom-left" ? "left:20px" : o.position === "bottom-center" ? "left:50%;transform:translateX(-50%)" : "right:20px";
    var color = /^#[0-9a-fA-F]{6}$/.test(o.color) ? o.color : "#6366f1";
    root.innerHTML = '<style>' +
      '.btn{position:fixed;bottom:20px;' + pos + ';z-index:2147483000;display:flex;align-items:center;justify-content:center;gap:8px;min-width:' + size + 'px;height:' + size + 'px;padding:0 ' + (o.label ? 20 : 0) + 'px;border:0;border-radius:' + size + 'px;background:' + color + ';color:#fff;font:600 15px system-ui,sans-serif;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.25)}' +
      '.btn:focus-visible{outline:3px solid #fff;outline-offset:2px}.btn svg{width:' + Math.round(size * 0.42) + 'px;height:' + Math.round(size * 0.42) + 'px}' +
      '.ov{position:fixed;inset:0;z-index:2147483001;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center}' +
      '.ov.open{display:flex}.box{position:relative;width:min(440px,100%);height:min(680px,100%);background:#0a0a0a;border-radius:16px;overflow:hidden}' +
      '@media (max-width:520px){.box{width:100%;height:100%;border-radius:0}}iframe{width:100%;height:100%;border:0;display:block}' +
      '.x{position:absolute;top:10px;left:10px;z-index:1;width:34px;height:34px;border-radius:50%;border:0;background:rgba(255,255,255,.14);color:#fff;font-size:20px;line-height:1;cursor:pointer}' +
      '</style><button class="btn" part="button" aria-label="Talk to us"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/></svg><span class="lbl"></span></button>' +
      '<div class="ov" role="dialog" aria-modal="true" aria-label="Talk to us"><div class="box"><button class="x" aria-label="Close">×</button></div></div>';
    var btn = root.querySelector(".btn"), ov = root.querySelector(".ov"), box = root.querySelector(".box");
    var label = root.querySelector(".lbl");
    if (o.label) label.textContent = o.label; else label.remove();
    var triggers = document.querySelectorAll("[data-agent-trigger]");
    btn.hidden = o.launcher === "never" || (o.launcher !== "always" && triggers.length > 0);
    var frame = null;
    window.addEventListener("message", function (e) {          // the agent page asks which tools exist and runs them here
      var d = e.data;
      if (!frame || e.source !== frame.contentWindow || e.origin !== origin || !d || typeof d.type !== "string") return;
      function post(msg) { if (frame) frame.contentWindow.postMessage(msg, origin); }
      if (d.type === "hmg-web-tools?") return post({type: "hmg-web-tools", names: Object.keys(registered())});
      if (d.type !== "hmg-web-tool") return;
      var fn = registered()[d.name];
      if (!fn) return post({type: "hmg-web-tool-result", id: d.id, error: "the website has not registered " + d.name});
      Promise.resolve().then(function () { return fn(d.args || {}); }).then(
        function (r) { post({type: "hmg-web-tool-result", id: d.id, result: r === undefined ? null : r}); },
        function (err) { post({type: "hmg-web-tool-result", id: d.id, error: String((err && err.message) || err)}); });
    });
    function open() {
      if (frame) return;
      frame = document.createElement("iframe");
      frame.allow = "microphone; autoplay";
      frame.title = "Talk to us";
      frame.src = origin + "/p/" + encodeURIComponent(token) + "?embed=1" + (o.auto_start ? "&autostart=1" : "");
      box.appendChild(frame); ov.classList.add("open");
    }
    function close() { if (frame) { frame.remove(); frame = null; } ov.classList.remove("open"); }
    btn.addEventListener("click", open);
    root.querySelector(".x").addEventListener("click", close);
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    Array.prototype.forEach.call(triggers, function (el) { el.addEventListener("click", function (e) { e.preventDefault(); open(); }); });
  }
})();
'''
