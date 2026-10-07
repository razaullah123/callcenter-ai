// Runs runtime/server/public_page.py's EMBED_JS (the widget on a customer's site) against a fake browser and reports what it did.
// usage: node embed_harness.js <path to the widget script>
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const ORIGIN = "https://agent.example";

function el(tag) {
  const e = { tag, listeners: {}, children: [], style: {}, dataset: {}, classList: { add() {}, remove() {} }, hidden: false, textContent: "" };
  e.addEventListener = (t, f) => { (e.listeners[t] = e.listeners[t] || []).push(f); };
  e.appendChild = c => { e.children.push(c); return c; };
  e.remove = () => { e.removed = true; };
  e.querySelector = sel => (e.found = e.found || {})[sel] || ((e.found[sel]) = el(sel));
  e.attachShadow = () => { const r = el("shadow"); e.shadow = r; return r; };
  if (tag === "iframe") e.contentWindow = { posted: [], postMessage(m, o) { this.posted.push([m, o]); } };
  return e;
}
const windowListeners = {};
const doc = {
  currentScript: { dataset: { token: "tok" }, src: ORIGIN + "/embed.js" },
  body: el("body"), createElement: el, querySelectorAll: () => [], addEventListener() {},
};
const ctx = {
  document: doc, URL, Promise, console, location: { href: "https://shop.example/" }, Object, Array, String, Math, setTimeout,
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ embed: { launcher: "always", label: "Talk" } }) }),
};
ctx.window = ctx;
ctx.addEventListener = (t, f) => { (windowListeners[t] = windowListeners[t] || []).push(f); };
ctx.window.addEventListener = ctx.addEventListener;
vm.createContext(ctx);
vm.runInContext(src, ctx);

const out = { checks: {} };
const tick = () => new Promise(r => setTimeout(r, 5));

(async () => {
  await tick(); await tick();                                   // the config fetch resolves and the widget is built
  const root = doc.body.children[0].shadow;
  const btn = root.querySelector(".btn");
  btn.listeners.click[0]();                                     // open the overlay: creates the iframe
  const frame = root.querySelector(".box").children.find(c => c.tag === "iframe");
  out.checks.iframe_src = frame.src;
  const msg = (data, source = frame.contentWindow, origin = ORIGIN) => windowListeners.message.forEach(f => f({ data, source, origin }));
  const last = () => frame.contentWindow.posted[frame.contentWindow.posted.length - 1];

  // a site registers functions both ways
  ctx.VoiceAgent.registerTools({ navigate_to_page: async args => "went to " + args.path, boom: () => { throw new Error("nope"); }, notafunction: 5 });
  ctx.VoiceAgentTools = { from_global: args => ({ echo: args }) };

  msg({ type: "hmg-web-tools?" });
  out.checks.names = last()[0].names.sort(); out.checks.names_origin = last()[1];

  msg({ type: "hmg-web-tool", id: "1", name: "navigate_to_page", args: { path: "/pricing" } }); await tick();
  out.checks.string_result = last()[0];
  msg({ type: "hmg-web-tool", id: "2", name: "from_global", args: { a: 1 } }); await tick();
  out.checks.global_result = last()[0];
  msg({ type: "hmg-web-tool", id: "3", name: "boom", args: {} }); await tick();
  out.checks.throws = last()[0];
  msg({ type: "hmg-web-tool", id: "4", name: "missing", args: {} }); await tick();
  out.checks.missing = last()[0];

  // nobody else may talk to the registry: another window, another origin, a closed frame
  const before = frame.contentWindow.posted.length;
  msg({ type: "hmg-web-tool", id: "5", name: "navigate_to_page", args: { path: "/x" } }, { posted: [], postMessage() {} }); await tick();
  msg({ type: "hmg-web-tool", id: "6", name: "navigate_to_page", args: { path: "/x" } }, frame.contentWindow, "https://evil.example"); await tick();
  msg({ type: "hmg-web-tools?" }, frame.contentWindow, "https://evil.example");
  msg("not an object"); msg(null); msg({ type: 7 });
  out.checks.ignored_foreign = frame.contentWindow.posted.length === before;

  root.querySelector(".x").listeners.click[0]();                // close: the frame is gone, so nothing is answered any more
  msg({ type: "hmg-web-tools?" }); await tick();
  out.checks.frame_removed = frame.removed === true && frame.contentWindow.posted.length === before;
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ error: String(e && e.stack || e) })); process.exit(1); });
