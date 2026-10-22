// Tiny Chrome DevTools Protocol driver for the screenshot script (Node >= 22: global WebSocket + fetch). Dev tool only.
//   node tests/cdp.mjs <debug port> <steps.json>
// steps: [{goto}, {wait: ms}, {waitFor: css}, {click: css}, {type: [css, text]}, {eval: js}, {size: [w, h]}, {dark: true|false},
//         {shot: file.png, full: true}]
import { readFileSync, writeFileSync } from "node:fs";

const [port, stepsFile] = process.argv.slice(2);
const steps = JSON.parse(readFileSync(stepsFile, "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let targets = [];
for (let i = 0; i < 100; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (targets.some((t) => t.type === "page")) break; } catch (e) {}
  await sleep(200);
}
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0;
const waiting = new Map();
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } });
const send = (method, params = {}) => new Promise((res) => { const n = ++id; waiting.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async (js) => (await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true })).result?.result?.value;

await send("Page.enable");
await send("Runtime.enable");
let size = [1280, 900];
for (const s of steps) {
  if (s.size) { size = s.size; await send("Emulation.setDeviceMetricsOverride", { width: size[0], height: size[1], deviceScaleFactor: 1, mobile: size[0] < 600 }); }
  if (s.dark !== undefined) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: s.dark ? "dark" : "light" }] });
  if (s.goto) { await send("Page.navigate", { url: s.goto }); await sleep(1500); }
  if (s.wait) await sleep(s.wait);
  if (s.waitFor) {
    let ok = false;
    for (let i = 0; i < (s.timeout || 150); i++) { if (await evaluate(`!!document.querySelector(${JSON.stringify(s.waitFor)})`)) { ok = true; break; } await sleep(200); }
    if (!ok) console.error("not found: " + s.waitFor);
  }
  if (s.click) await evaluate(`(function(){var e=document.querySelector(${JSON.stringify(s.click)}); if(e) e.click(); return !!e;})()`);
  if (s.type) await evaluate(`(function(){var e=document.querySelector(${JSON.stringify(s.type[0])}); if(e){e.value=${JSON.stringify(s.type[1])};} return !!e;})()`);
  if (s.eval) console.log(JSON.stringify(await evaluate(s.eval)));
  if (s.shot) {
    let clip;
    if (s.full) {
      const hgt = await evaluate("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)");
      await send("Emulation.setDeviceMetricsOverride", { width: size[0], height: Math.min(hgt, 6000), deviceScaleFactor: 1, mobile: size[0] < 600 });
      await sleep(300);
    }
    const r = await send("Page.captureScreenshot", { format: "png", clip });
    writeFileSync(s.shot, Buffer.from(r.result.data, "base64"));
    console.log("shot " + s.shot);
    if (s.full) await send("Emulation.setDeviceMetricsOverride", { width: size[0], height: size[1], deviceScaleFactor: 1, mobile: size[0] < 600 });
  }
}
ws.close();
process.exit(0);
