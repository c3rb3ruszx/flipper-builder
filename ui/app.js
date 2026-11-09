/* Flipper Builder UI. Talks to Python through window.pywebview.api (see gui.py). */
(() => {
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const W = 128, H = 64;

/* =====================================================================
   LCD: plays the firmware's dolphin animations on a 128x64 canvas,
   with Bayer-dither transitions and typed speech bubbles.
   ===================================================================== */
const BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]];
// where the bubble sits for each animation (top-left or top-right corner) so it avoids the dolphin
const BUBBLE_SIDE = {
  wake: "tr", waves: "tl", painting: "tr", procrastinating: "tr", sleep: "tr", books: "tr",
  laptop: "tr", soldering: "tr", radio: "tr", dj: "tl", cry: "tr",
};

const LCD = {
  ctx: null, sheets: {}, masks: [],
  key: null, anim: null, frames: [], i: 0, fps: 2, last: 0, loop: true, onEnd: null,
  old: null, tStart: 0, tDur: 280,
  bubble: null, bubbleShown: 0, bubbleStart: 0, bubbleUntil: 0,
  frameCanvas: null, tmp: null, textCache: new Map(),

  async init() {
    const c = $("#screen");
    this.ctx = c.getContext("2d");
    this.frameCanvas = this.mk(); this.tmp = this.mk(); this.old = this.mk();
    for (let L = 0; L <= 16; L++) {               // dither masks: pixel shown when bayer < L
      const m = this.mk(), mc = m.getContext("2d");
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++)
        if (BAYER[y % 4][x % 4] < L) mc.fillRect(x, y, 1, 1);
      this.masks.push(m);
    }
    await Promise.all(Object.entries(window.DOLPHIN).map(([k, a]) => new Promise((res) => {
      const img = new Image();
      img.onload = () => { this.sheets[k] = img; res(); };
      img.onerror = res;
      img.src = a.src;
    })));
    try { await document.fonts.load("8px Silkscreen"); } catch (_) {}
    requestAnimationFrame((t) => this.tick(t));
  },

  mk() { const c = document.createElement("canvas"); c.width = W; c.height = H; return c; },

  /* play(key, {fps, loop, active, onEnd}) */
  play(key, opt = {}) {
    const a = window.DOLPHIN[key];
    if (!a || !this.sheets[key]) return;
    if (this.key === key && !opt.restart && !opt.active) { this.fps = opt.fps || this.fps; return; }
    // snapshot what's on screen for the dither transition
    const oc = this.old.getContext("2d");
    oc.clearRect(0, 0, W, H);
    oc.drawImage(this.ctx.canvas, 0, 0);
    this.tStart = performance.now();
    this.key = key; this.anim = a;
    const passive = a.order.slice(0, a.passive);
    this.frames = opt.active ? a.order : (passive.length ? passive : a.order);
    this.i = 0; this.last = 0;
    this.fps = opt.fps || a.fps;
    this.loop = opt.loop !== false;
    this.onEnd = opt.onEnd || null;
  },

  /* poke: play the animation's "active" frames once (like pressing a button on the device) */
  poke() {
    const a = this.anim;
    if (!a || a.order.length <= a.passive) return;
    const active = a.order.slice(a.passive);
    const back = this.frames, loop = this.loop;
    this.frames = active; this.i = 0; this.loop = false;
    this.onEnd = () => { this.frames = back; this.i = 0; this.loop = loop; this.onEnd = null; };
  },

  say(text, ms = 0) {
    if (!text) { this.bubble = null; return; }
    if (this.bubble && this.bubble.text === text) { this.bubbleUntil = ms ? performance.now() + ms : 0; return; }
    const sameStart = this.bubble && text.startsWith(this.bubble.text.replace(/[\d%.]+$/, ""));
    this.bubble = { text };
    this.bubbleStart = performance.now() - (sameStart ? 1e5 : 0);   // live counters don't re-type
    this.bubbleUntil = ms ? performance.now() + ms : 0;
  },

  tick(t) {
    requestAnimationFrame((tt) => this.tick(tt));
    const a = this.anim;
    if (!a) return;
    if (!this.last) this.last = t;
    if (t - this.last >= 1000 / this.fps) {
      this.last = t;
      this.i++;
      if (this.i >= this.frames.length) {
        if (this.onEnd) { const f = this.onEnd; this.onEnd = null; this.i = this.frames.length - 1; f(); }
        else this.i = this.loop ? 0 : this.frames.length - 1;
      }
    }
    // current animation frame
    const fc = this.frameCanvas.getContext("2d");
    fc.clearRect(0, 0, W, H);
    const f = this.frames[Math.min(this.i, this.frames.length - 1)];
    fc.drawImage(this.sheets[this.key], f * a.w, 0, a.w, a.h, 0, H - a.h, a.w, a.h);
    this.drawBubble(fc, t);

    const ctx = this.ctx;
    ctx.clearRect(0, 0, W, H);
    // rAF timestamps can trail performance.now() slightly, so clamp both ends
    const p = Math.max(0, Math.min(1, (t - this.tStart) / this.tDur));
    if (p < 1) {
      const L = Math.min(16, Math.floor(p * 16));
      ctx.drawImage(this.old, 0, 0);
      ctx.globalCompositeOperation = "destination-out";
      ctx.drawImage(this.masks[L], 0, 0);
      const tc = this.tmp.getContext("2d");
      tc.clearRect(0, 0, W, H);
      tc.drawImage(this.frameCanvas, 0, 0);
      tc.globalCompositeOperation = "destination-in";
      tc.drawImage(this.masks[L], 0, 0);
      tc.globalCompositeOperation = "source-over";
      ctx.globalCompositeOperation = "source-over";
      ctx.drawImage(this.tmp, 0, 0);
    } else {
      ctx.drawImage(this.frameCanvas, 0, 0);
    }
  },

  /* 1-bit text: render with the pixel font, then threshold so every pixel is solid */
  text(str) {
    if (this.textCache.has(str)) return this.textCache.get(str);
    const c = document.createElement("canvas");
    const x = c.getContext("2d");
    x.font = "8px Silkscreen";
    const w = Math.max(1, Math.ceil(x.measureText(str).width));
    c.width = w; c.height = 9;
    x.font = "8px Silkscreen"; x.textBaseline = "top"; x.fillStyle = "#000";
    x.fillText(str, 0, 0);
    const d = x.getImageData(0, 0, w, 9);
    for (let i = 3; i < d.data.length; i += 4) d.data[i] = d.data[i] > 100 ? 255 : 0;
    x.putImageData(d, 0, 0);
    if (this.textCache.size > 300) this.textCache.clear();
    this.textCache.set(str, c);
    return c;
  },

  wrap(text, max) {
    const words = text.split(" "), lines = [];
    let line = "";
    const m = this.text.bind(this);
    for (const w of words) {
      const tryLine = line ? line + " " + w : w;
      if (m(tryLine).width > max && line) { lines.push(line); line = w; } else line = tryLine;
    }
    if (line) lines.push(line);
    return lines.slice(0, 3);
  },

  drawBubble(c, t) {
    const b = this.bubble;
    if (!b) return;
    if (this.bubbleUntil && t > this.bubbleUntil) { this.bubble = null; return; }
    const shown = Math.floor((t - this.bubbleStart) / 28);          // typewriter, ~35 chars/s
    const full = b.text;
    const lines = this.wrap(full, 66);
    let budget = shown;
    const vis = lines.map((l) => { const s = l.slice(0, Math.max(0, budget)); budget -= l.length + 1; return s; });
    const bw = Math.max(...lines.map((l) => this.text(l).width)) + 7;
    const bh = lines.length * 8 + 4;
    const side = BUBBLE_SIDE[this.key] || "tr";
    const x = side === "tl" ? 1 : W - bw - 2, y = 1;
    // clear the art behind the bubble so the backlight shows through, then outline it
    c.clearRect(x, y, bw, bh);
    c.fillStyle = "#000";
    c.fillRect(x + 1, y, bw - 2, 1); c.fillRect(x + 1, y + bh - 1, bw - 2, 1);
    c.fillRect(x, y + 1, 1, bh - 2); c.fillRect(x + bw - 1, y + 1, 1, bh - 2);
    // tail
    const tx = side === "tl" ? x + 8 : x + bw - 12;
    c.clearRect(tx, y + bh - 1, 4, 1);
    c.fillRect(tx, y + bh - 1, 1, 3); c.fillRect(tx + 1, y + bh + 1, 1, 1);
    c.fillRect(tx + 2, y + bh, 1, 1); c.fillRect(tx + 3, y + bh - 1, 1, 1);
    vis.forEach((l, i) => { if (l) c.drawImage(this.text(l), x + 4, y + 2 + i * 8); });
    // blinking cursor while typing
    if (shown < full.length && Math.floor(t / 250) % 2) {
      const li = vis.findIndex((l, i) => l.length < lines[i].length);
      const row = li < 0 ? vis.length - 1 : li;
      const cx = x + 4 + (vis[row] ? this.text(vis[row]).width : 0) + 1;
      c.fillRect(cx, y + 3 + row * 8, 1, 5);
    }
  },
};

/* =====================================================================
   API bridge (pywebview), with a mock for previewing in a plain browser
   ===================================================================== */
let api = null;
function mockApi() {
  const fws = [["official", "Official firmware (release)", "OFW", "1.5.1"], ["official-dev", "Official firmware (dev)", "OFW DEV", null],
    ["momentum", "Momentum", "MOMENTUM", "mntm-012"], ["momentum-dev", "Momentum (dev)", "MNTM DEV", "d3f89dfe"], ["unleashed", "Unleashed", "UNLEASHED", "unlshd-094"],
    ["unleashed-dev", "Unleashed (dev)", "UNLSHD DEV", null]];
  let timers = [];
  const emit = (batch) => window.fb.onEvents(batch);
  return {
    init: async () => ({ version: "1.1.0", repo: "https://github.com/c3rb3ruszx/flipper-builder",
      firmwares: fws.map(([id, label, short, sdk]) => ({ id, label, short, sdk })), fw: "momentum-dev", send: false,
      recent: [{ url: "https://github.com/jblanked/FlipWiFi", fw: "momentum-dev", name: "FlipWiFi", repo: "jblanked/FlipWiFi", ok: true, t: 0 },
               { url: "https://github.com/flipperdevices/flipperzero-good-faps/tree/dev/clock", fw: "official", name: "Clock", repo: "flipperdevices/flipperzero-good-faps", ok: true, t: 0 }],
      clipboard: "", out_dir: "C:\\output" }),
    clipboard: async () => "",
    set_prefs: async () => true,
    build: async (url, fw) => {
      const fail = /fail/.test(url);
      const steps = [
        [300, [{ type: "stage", stage: "fetch", detail: "owner/repo" }, { type: "log", line: "== Repo: owner/repo" }, { type: "log", line: "$ git clone --depth 1 ..." }]],
        [1600, [{ type: "stage", stage: "scan" }, { type: "log", line: "== Found 1 app(s): Demo App" }]],
        [2300, [{ type: "stage", stage: "sdk", detail: "MNTM DEV" }, { type: "log", line: "SDK d3f89dfe is up to date." }]],
        [3300, [{ type: "stage", stage: "build", detail: "1 app(s)" }, { type: "log", line: "== Building Demo App (/)" }]],
      ];
      for (let p = 1; p <= 10; p++) steps.push([3300 + p * 280, [{ type: "progress", value: p / 10 }, { type: "log", line: `\tCC\tsrc/file_${p}.c` }]]);
      steps.push([6500, fail
        ? [{ type: "log", line: "src/main.c:12:5: error: 'foo' undeclared" }, { type: "log", line: "!! Command failed" }, { type: "finished", ok: false, error: "Demo App failed to build." }]
        : [{ type: "result", result: { name: "Demo App", appid: "demo_app", category: "GPIO", ok: true, icon: null, dir: "x", fw,
             files: [{ name: "demo_app.fap", path: "C:\\output\\demo_app.fap", size: 24576 }] } },
           { type: "log", line: "OK Demo App: demo_app.fap" },
           { type: "finished", ok: true, built: 1, total: 1, out: "C:\\output", fw, sdk: "d3f89dfe",
             recent: [{ url, fw, name: "Demo App", repo: "owner/repo", ok: true, t: 0 }] }]]);
      timers = steps.map(([ms, b]) => setTimeout(() => emit(b), ms));
      return { ok: true };
    },
    update_sdk: async (fw) => { setTimeout(() => emit([{ type: "stage", stage: "sdk" }]), 100);
      setTimeout(() => emit([{ type: "finished", ok: true, sdk_only: true, fw, sdk: "new123" }]), 2500); return { ok: true }; },
    send: async () => { setTimeout(() => emit([{ type: "stage", stage: "send" }]), 100);
      setTimeout(() => emit([{ type: "finished", ok: true, send_only: true }]), 2500); return { ok: true }; },
    cancel: async () => { timers.forEach(clearTimeout); emit([{ type: "finished", ok: false, cancelled: true, error: "Cancelled." }]); return true; },
    clear_recent: async () => true, open_folder: async () => true, open_url: async () => true, copy: async () => true,
  };
}

/* =====================================================================
   App state + UI
   ===================================================================== */
const S = {
  fws: [], fw: "official", send: false, busy: null, stage: null, progress: 0,
  results: [], recent: [], repo: "", lastClip: "", idleTimer: null, sleepTimer: null, outDir: "",
};
const STAGES = ["fetch", "scan", "sdk", "build", "done"];
const STAGE_RANGE = { fetch: [0, .16], scan: [.16, .22], sdk: [.22, .38], build: [.38, .96], send: [.96, 1], done: [1, 1] };
const STAGE_ANIM = { fetch: "books", scan: "books", sdk: "laptop", build: "soldering", send: "radio" };
const IDLE_ANIMS = ["waves", "painting", "procrastinating"];
const TIPS = [
  "Paste a GitHub link!", "Folder links build one app", "Left / right picks firmware",
  "Poke me!", "Need a .fap?", "Enter builds, Esc cancels", "Drop a link on me",
];

function fwObj(id = S.fw) { return S.fws.find((f) => f.id === id) || S.fws[0]; }

/* ---------- LED ---------- */
function led(color, blink = false) {
  const l = $("#led");
  l.className = "led" + (color ? " " + color : "") + (blink ? " blink" : "");
}

/* ---------- idle behaviour ---------- */
function goIdle(sayText) {
  clearTimeout(S.idleTimer); clearTimeout(S.sleepTimer);
  const pick = IDLE_ANIMS[Math.floor(Math.random() * IDLE_ANIMS.length)];
  LCD.play(pick);
  if (sayText) LCD.say(sayText, 5000);
  S.idleTimer = setTimeout(() => { if (!S.busy) goIdle(Math.random() < .6 ? TIPS[Math.floor(Math.random() * TIPS.length)] : ""); }, 26000);
  S.sleepTimer = setTimeout(() => { if (!S.busy) { clearTimeout(S.idleTimer); LCD.play("sleep"); LCD.say("zzz..."); } }, 240000);
}
function wake() {
  if (!S.busy && LCD.key === "sleep") goIdle("Oh! Hi again");
  else if (!S.busy) { clearTimeout(S.sleepTimer); S.sleepTimer = setTimeout(() => { if (!S.busy) { LCD.play("sleep"); LCD.say("zzz..."); } }, 240000); }
}

/* ---------- firmware selector ---------- */
function renderFirmwares() {
  const row = $("#fwRow");
  $$(".fw", row).forEach((b) => b.remove());
  S.fws.forEach((f) => {
    const b = document.createElement("button");
    b.className = "fw"; b.dataset.fw = f.id; b.title = f.label;
    b.innerHTML = `<span class="fw-name">${f.short}</span><span class="fw-sdk">${f.sdk || "not downloaded"}</span>`;
    b.onclick = () => setFw(f.id, true);
    row.appendChild(b);
  });
  setFw(S.fw, false);
}
function setFw(id, user) {
  S.fw = id;
  const row = $("#fwRow"), btn = $(`.fw[data-fw="${id}"]`, row), ind = $("#fwIndicator");
  $$(".fw", row).forEach((b) => b.classList.toggle("on", b === btn));
  if (btn) {
    ind.style.width = btn.offsetWidth + "px"; ind.style.height = btn.offsetHeight + "px";
    ind.style.transform = `translate(${btn.offsetLeft}px, ${btn.offsetTop}px)`;
  }
  const f = fwObj();
  $("#deviceFw").textContent = f.short;
  $("#sdkPillText").textContent = `SDK ${f.sdk || "not downloaded"}`;
  if (user) {
    api.set_prefs(S.fw, S.send);
    if (!S.busy) { LCD.say(`${f.short}: ${f.sdk ? "SDK " + f.sdk : "SDK not downloaded yet"}`, 3500); wake(); }
  }
}
function cycleFw(d) {
  if (S.busy) return;
  const i = S.fws.findIndex((f) => f.id === S.fw);
  setFw(S.fws[(i + d + S.fws.length) % S.fws.length].id, true);
}

/* ---------- link preview ---------- */
function parseLink(text) {
  let u = (text || "").trim().replace(/^"|"$/g, "");
  if (!u) return null;
  let m = u.match(/^git@github\.com:([^/]+)\/(.+?)(?:\.git)?$/);
  if (m) return { owner: m[1], repo: m[2], rest: [] };
  u = u.replace(/^https?:\/\//, "").replace(/^(www\.)?github\.com\//, "").split(/[?#]/)[0].replace(/\/+$/, "");
  const parts = u.split("/").filter(Boolean);
  if (parts.length < 2 || parts[0].includes(".")) return { bad: true };
  let rest = parts.slice(2);
  if (rest[0] === "tree" || rest[0] === "blob") rest = rest.slice(1); else rest = [];
  return { owner: parts[0], repo: parts[1].replace(/\.git$/, ""), rest };
}
function renderPreview() {
  const p = parseLink($("#url").value), box = $("#preview");
  $("#inputWrap").classList.remove("bad");
  if (!p) { box.innerHTML = ""; return; }
  if (p.bad) { box.innerHTML = `<span class="chip bad">Not a GitHub link</span>`; return; }
  const chips = [`<span class="chip">${esc(p.owner)} / ${esc(p.repo)}</span>`];
  if (p.rest.length) chips.push(`<span class="chip muted">${esc(p.rest[0])}</span>`);
  if (p.rest.length > 1) chips.push(`<span class="chip muted">${esc(p.rest.slice(1).join("/"))}</span>`);
  else chips.push(`<span class="chip muted">${p.rest.length ? "whole branch" : "default branch"}</span>`);
  const key = p.owner + "/" + p.repo;
  if (box.dataset.key !== key + p.rest.join("/")) {
    box.innerHTML = chips.join("");
    box.dataset.key = key + p.rest.join("/");
    if (!S.busy && key !== S.repo) { S.repo = key; LCD.say(`Ooh, ${p.repo}!`, 3000); LCD.poke(); }
  }
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------- progress bar ---------- */
const BAR_N = 32;
let barScan = 0;
function initBar() { $("#bar").innerHTML = "<i></i>".repeat(BAR_N); }
function paintBar(value, mode) {
  const cells = $$("#bar i"), lit = Math.round(value * BAR_N);
  cells.forEach((c, i) => { c.className = i < lit ? (mode || "on") : ""; });
  if (S.busy && mode !== "ok" && mode !== "err" && lit < BAR_N) {   // a sweeping "working" cell ahead of the fill
    const [, end] = STAGE_RANGE[S.stage] || [0, 1];
    const span = Math.max(1, Math.round(end * BAR_N) - lit);
    const k = lit + (barScan % span);
    if (cells[k]) cells[k].className = "scan";
  }
}
setInterval(() => { if (S.busy) { barScan++; paintBar(S.progress); } }, 110);

function setStage(name) {
  S.stage = name;
  const idx = STAGES.indexOf(name === "send" ? "build" : name);
  $$(".stage").forEach((el, i) => {
    el.className = "stage" + (i < idx ? " done" : i === idx ? " active" : "");
  });
  S.progress = Math.max(S.progress, (STAGE_RANGE[name] || [0])[0]);
}
function resetStages() { $$(".stage").forEach((el) => (el.className = "stage")); S.progress = 0; paintBar(0); }

/* ---------- build button ---------- */
function setBuildButton(busy) {
  const b = $("#buildBtn"), l = $("#buildLabel");
  l.classList.add("swap");
  setTimeout(() => {
    b.classList.toggle("busy", busy);
    l.textContent = busy ? "Cancel" : "Build .fap";
    l.classList.remove("swap");
  }, 140);
}

/* ---------- shelf: results + recent ---------- */
function fmtSize(n) { return n > 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B"; }
function renderShelf() {
  const body = $("#shelfBody"), title = $("#shelfTitle"), actions = $("#shelfActions");
  body.innerHTML = ""; actions.innerHTML = "";
  if (S.busy === "build" && !S.results.length) {
    title.textContent = "Working";
    body.innerHTML = `<div class="skeleton"><span class="blk"></span>Your .fap will show up here</div>`;
  } else if (S.results.length) {
    title.textContent = "Built";
    actions.innerHTML = `<button class="mini" id="openOut"><i data-icon="folder"></i>Open folder</button>`;
    $("#openOut", actions).onclick = () => api.open_folder(S.outDir);
    S.results.forEach((r, i) => body.appendChild(resultCard(r, i)));
  } else if (S.recent.length) {
    title.textContent = "Recent";
    actions.innerHTML = `<button class="mini" id="clearRecent" title="Clear the list"><i data-icon="trash"></i>Clear</button>`;
    $("#clearRecent", actions).onclick = async () => { await api.clear_recent(); S.recent = []; renderShelf(); };
    S.recent.forEach((r, i) => {
      const b = document.createElement("button");
      b.className = "recent"; b.style.animationDelay = i * 45 + "ms";
      const f = S.fws.find((x) => x.id === r.fw);
      b.innerHTML = `<span class="st ${r.ok ? "" : "bad"}"></span>
        <span><div class="r-name">${esc(r.name || r.repo)}</div><div class="r-sub">${esc(r.url.replace(/^https?:\/\//, ""))}</div></span>
        <span class="r-fw">${esc(f ? f.short : r.fw)}</span>`;
      b.onclick = () => { $("#url").value = r.url; if (f) setFw(r.fw, false); renderPreview(); $("#url").focus(); toast("Loaded. Press Build"); };
      body.appendChild(b);
    });
  } else {
    title.textContent = "Getting started";
    body.innerHTML = `<div class="empty">Paste a link to any Flipper Zero app on GitHub: a repo, a branch, or an app's folder.<br>
      Try <a data-ex="https://github.com/flipperdevices/flipperzero-good-faps/tree/dev/clock">the official Clock app</a>
      or <a data-ex="https://github.com/flipperdevices/flipperzero-good-faps/tree/dev/music_player">Music Player</a>.</div>`;
    $$("a[data-ex]", body).forEach((a) => (a.onclick = () => { $("#url").value = a.dataset.ex; renderPreview(); }));
  }
  renderIcons(document);
}
function resultCard(r, i) {
  const el = document.createElement("div");
  el.className = "card" + (r.ok ? "" : " fail");
  el.style.animationDelay = i * 70 + "ms";
  const icon = r.ok
    ? (r.icon ? `<div class="app-icon"><img src="${r.icon}" alt=""></div>` : `<div class="app-icon none"><i data-icon="usb"></i></div>`)
    : `<div class="app-icon bad"><i data-icon="cross"></i></div>`;
  const file = r.files && r.files[0];
  const sub = r.ok
    ? `${esc(file.name)} · ${fmtSize(file.size)} · SD: <b>apps/${esc(r.category)}/</b>`
    : `failed to build · see the log`;
  el.innerHTML = `${icon}<div class="card-main"><div class="card-title">${esc(r.name)}${r.version ? ` <span style="color:var(--dim);font-size:12px">v${esc(r.version)}</span>` : ""}</div>
    <div class="card-sub">${sub}</div></div><div class="card-actions"></div>`;
  const acts = $(".card-actions", el);
  if (r.ok) {
    acts.append(
      iconBtn("folder", "Show in folder", () => api.open_folder(file.path)),
      iconBtn("copy", "Copy SD card path", async () => { await api.copy(`apps/${r.category}/${file.name}`); toast("SD path copied"); }),
      iconBtn("usb", "Send to Flipper over USB", () => startJob("send", () => api.send(r.dir, r.fw))),
    );
  } else {
    acts.append(iconBtn("terminal", "Show log", () => openLog(true)));
  }
  return el;
}
function iconBtn(icon, title, fn) {
  const b = document.createElement("button");
  b.className = "icon-btn"; b.title = title; b.innerHTML = `<i data-icon="${icon}"></i>`;
  b.onclick = fn;
  return b;
}

/* ---------- log ---------- */
let logCount = 0;
function addLog(line) {
  const body = $("#logBody");
  const div = document.createElement("div");
  if (line.startsWith("==")) div.className = "h";
  else if (line.startsWith("OK ")) div.className = "ok";
  else if (line.startsWith("!!") || /\berror\b/i.test(line)) div.className = "err";
  else if (line.startsWith("$ ")) div.className = "cmd";
  div.textContent = line;
  const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
  body.appendChild(div);
  if (++logCount > 4000) { body.firstChild.remove(); logCount--; }
  if (stick) body.scrollTop = body.scrollHeight;
  if (line.trim()) $("#logLast").textContent = line.trim();
}
function openLog(open) {
  const l = $("#log");
  l.classList.toggle("open", open === undefined ? !l.classList.contains("open") : open);
  if (l.classList.contains("open")) setTimeout(() => ($("#logBody").scrollTop = 1e9), 360);
}

/* ---------- toast / callout / confetti ---------- */
let toastT;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 1800);
}
function callout(msg, kind) {
  const c = $("#callout");
  if (!msg) { c.hidden = true; return; }
  c.className = "callout" + (kind ? " " + kind : "");
  c.textContent = msg; c.hidden = false;
}
function confetti() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const cv = $("#confetti"), ctx = cv.getContext("2d");
  cv.width = innerWidth; cv.height = innerHeight;
  const r = $("#buildBtn").getBoundingClientRect();
  const colors = ["#ff8200", "#ffa040", "#ffd2a0", "#ffffff", "#5fdd7a"];
  const ps = Array.from({ length: 90 }, () => ({
    x: r.left + r.width / 2 + (Math.random() - .5) * r.width * .6, y: r.top + 10,
    vx: (Math.random() - .5) * 9, vy: -Math.random() * 11 - 5, s: 3 + Math.floor(Math.random() * 3) * 2,
    c: colors[Math.floor(Math.random() * colors.length)], life: 0,
  }));
  let start = performance.now();
  (function frame(t) {
    const dt = Math.min(2, (t - start) / 16); start = t;
    ctx.clearRect(0, 0, cv.width, cv.height);
    let alive = 0;
    for (const p of ps) {
      p.life += dt; p.vy += .38 * dt; p.vx *= .99; p.x += p.vx * dt; p.y += p.vy * dt;
      if (p.y < cv.height + 20 && p.life < 140) { alive++; ctx.globalAlpha = Math.max(0, 1 - p.life / 140);
        ctx.fillStyle = p.c; ctx.fillRect(Math.round(p.x), Math.round(p.y), p.s, p.s); }
    }
    ctx.globalAlpha = 1;
    if (alive) requestAnimationFrame(frame); else ctx.clearRect(0, 0, cv.width, cv.height);
  })(start);
}

/* ---------- jobs ---------- */
async function startJob(kind, call) {
  if (S.busy) return;
  clearTimeout(S.idleTimer); clearTimeout(S.sleepTimer);
  S.busy = kind;
  callout(null);
  resetStages();
  setBuildButton(true);
  led("orange", true);
  $$(".stage")[STAGES.length - 1].className = "stage";
  if (kind === "send") { setStage("send"); LCD.play("radio", { fps: 4 }); LCD.say("Beaming it over..."); led("blue", true); }
  if (kind === "sdk") { LCD.play("laptop", { fps: 4 }); LCD.say(`Fetching the ${fwObj().short} SDK...`); }
  const res = await call();
  if (res && res.error) {
    S.busy = null; setBuildButton(false); led(null);
    callout(res.error);
    $("#inputWrap").classList.add("bad");
    LCD.play("cry"); LCD.say("Hmm, that link...", 4000);
    setTimeout(() => !S.busy && goIdle(), 6000);
  }
}

function build() {
  if (S.busy) { cancel(); return; }
  const url = $("#url").value.trim();
  if (!url) {
    $("#inputWrap").classList.add("bad"); $("#url").focus();
    LCD.say("Paste a link first!", 3000); LCD.poke();
    return;
  }
  S.results = [];
  S.send = $("#send").checked;
  logCount = 0; $("#logBody").innerHTML = "";
  openLog(false);
  startJob("build", () => api.build(url, S.fw, S.send));
  renderShelf();
}
function cancel() {
  if (!S.busy) return;
  LCD.say("Stopping...");
  api.cancel();
}

/* events pushed from Python */
function onEvents(batch) {
  for (const e of batch) {
    switch (e.type) {
      case "log": addLog(e.line); break;
      case "stage": {
        setStage(e.stage);
        const anim = STAGE_ANIM[e.stage];
        if (anim) LCD.play(anim, { fps: 4 });
        if (e.stage === "send") led("blue", true);
        const f = fwObj();
        const say = {
          fetch: `Fetching ${e.detail ? e.detail.split("/")[1] : "the repo"}...`, scan: "Looking for apps...",
          sdk: `Checking ${f.short} SDK...`, build: "Soldering bits...", send: "Beaming it over...",
        }[e.stage];
        if (say) LCD.say(say);
        break;
      }
      case "progress": {
        const [a, b] = STAGE_RANGE.build;
        S.progress = Math.max(S.progress, a + (b - a) * e.value);
        if (S.stage === "build") LCD.say(`Compiling ${Math.round(e.value * 100)}%`);
        break;
      }
      case "result":
        S.results.push(e.result);
        renderShelf();
        break;
      case "finished": finished(e); break;
    }
  }
  if (S.busy) paintBar(S.progress);
}

function finished(e) {
  const kind = S.busy;
  S.busy = null;
  setBuildButton(false);
  if (e.fw && e.sdk) { const f = fwObj(e.fw); if (f) f.sdk = e.sdk; renderFirmwares(); }
  if (e.recent) S.recent = e.recent;
  if (e.out) S.outDir = e.out;

  if (e.cancelled) {
    led(null); resetStages(); callout("Cancelled.", "info");
    LCD.play("sleep"); LCD.say("Okay, nap time", 3000);
    setTimeout(() => !S.busy && goIdle(), 5000);
    renderShelf();
    return;
  }
  if (!e.ok) {
    const cur = $$(".stage").find((s) => s.classList.contains("active"));
    if (cur) cur.className = "stage fail";
    paintBar(Math.max(S.progress, .05), "err");
    led("red");
    callout(e.error || "Something went wrong. The log has the details.");
    LCD.play("cry"); LCD.say("Oh no... it broke");
    openLog(true);
    renderShelf();
    setTimeout(() => { if (!S.busy) { led(null); goIdle(); } }, 14000);
    return;
  }
  // success
  callout(null);
  if (e.sdk_only || e.send_only) resetStages();
  else { $$(".stage").forEach((s) => (s.className = "stage done okall")); paintBar(1, "ok"); }
  led("green", true);
  $("#lcd").classList.add("flash"); setTimeout(() => $("#lcd").classList.remove("flash"), 600);
  if (e.sdk_only) {
    LCD.play("dj", { fps: 5 }); LCD.say(`SDK ${e.sdk} ready!`, 4000); toast("SDK updated");
  } else if (e.send_only) {
    LCD.play("dj", { fps: 5 }); LCD.say("Sent! Check your Flipper", 5000); toast("Sent to Flipper");
  } else {
    const all = e.built === e.total;
    LCD.play("dj", { fps: 5 });
    LCD.say(all ? `Done! ${e.built} app${e.built > 1 ? "s" : ""} built` : `Built ${e.built} of ${e.total}`, 7000);
    if (all) confetti(); else led("orange");
    renderShelf();
  }
  setTimeout(() => { if (!S.busy) { led(null); goIdle(); } }, 12000);
}

/* ---------- input: keyboard, d-pad, drag & drop, clipboard ---------- */
function press(name) {
  const el = $(`[data-dp="${name}"]`);
  if (el) { el.classList.add("pressed"); setTimeout(() => el.classList.remove("pressed"), 140); }
  wake();
  if (name === "left") cycleFw(-1);
  else if (name === "right") cycleFw(1);
  else if (name === "up" || name === "down") openLog(name === "up");
  else if (name === "ok") build();
  else if (name === "back") { if (S.busy) cancel(); else { LCD.poke(); LCD.say("Back? I'm right here.", 2500); } }
}

function bind() {
  $$("[data-dp]").forEach((b) => (b.onclick = () => press(b.dataset.dp)));
  $("#buildBtn").onclick = build;
  $("#lcd").onclick = () => { wake(); LCD.poke(); if (!S.busy) LCD.say(TIPS[Math.floor(Math.random() * TIPS.length)], 3000); };
  $("#logHead").onclick = () => openLog();
  $("#url").addEventListener("input", () => { renderPreview(); wake(); });
  $("#url").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); build(); } });
  $("#pasteBtn").onclick = async () => {
    const t = await api.clipboard();
    if (t) { $("#url").value = t; renderPreview(); toast("Pasted"); }
    else { toast("No GitHub link on the clipboard"); }
  };
  $("#send").onchange = () => { S.send = $("#send").checked; api.set_prefs(S.fw, S.send);
    if (S.send && !S.busy) LCD.say("I'll beam it over USB", 3000); };
  $("#sdkPill").onclick = () => startJob("sdk", () => api.update_sdk(S.fw));
  $("#repoBtn").onclick = () => api.open_url(S.repoUrl);

  document.addEventListener("keydown", (e) => {
    const typing = document.activeElement === $("#url");
    if (e.key === "Escape") { press("back"); return; }
    if (e.ctrlKey && e.key.toLowerCase() === "l") { e.preventDefault(); $("#url").focus(); $("#url").select(); return; }
    if (typing) return;
    const map = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", Enter: "ok" };
    if (map[e.key]) { e.preventDefault(); press(map[e.key]); }
  });

  // drag & drop a link anywhere
  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#drop").classList.add("on"); });
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop").classList.remove("on"); } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); dragDepth = 0; $("#drop").classList.remove("on");
    const t = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    if (t && t.includes("github.com")) { $("#url").value = t.split("\n")[0].trim(); renderPreview(); LCD.poke(); toast("Link dropped"); }
    else toast("That's not a GitHub link");
  });

  // grab a GitHub link from the clipboard when the window regains focus
  window.addEventListener("focus", async () => {
    wake();
    if (S.busy || $("#url").value.trim()) return;
    const t = await api.clipboard();
    if (t && t !== S.lastClip) { S.lastClip = t; $("#url").value = t; renderPreview(); toast("Link from clipboard"); }
  });
  window.addEventListener("resize", () => setFw(S.fw, false));
}

/* ---------- boot ---------- */
async function boot() {
  renderIcons();
  initBar();
  const lcdReady = LCD.init();
  const info = await api.init();
  S.fws = info.firmwares; S.fw = info.fw; S.send = info.send; S.recent = info.recent || [];
  S.repoUrl = info.repo; S.outDir = info.out_dir;
  $("#ver").textContent = "v" + info.version;
  $("#send").checked = S.send;
  renderFirmwares();
  renderShelf();
  bind();
  if (info.clipboard) { $("#url").value = info.clipboard; S.lastClip = info.clipboard; }
  await lcdReady;
  // power-on: backlight fades up, dolphin wakes up
  LCD.play("wake", { fps: 6, loop: false, onEnd: () => goIdle(info.clipboard ? "Got your link! Press OK" : "Hi! Paste a GitHub link") });
  await sleep(150);
  document.body.classList.remove("booting");
  if (info.clipboard) setTimeout(renderPreview, 1800);
}

window.fb = { onEvents, LCD, S };
let started = false;
function start(a) { if (started) return; started = true; api = a; boot(); }
window.addEventListener("pywebviewready", () => start(window.pywebview.api));
setTimeout(() => { if (!started) start(window.pywebview ? window.pywebview.api : mockApi()); }, 700);
})();
