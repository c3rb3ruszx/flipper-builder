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

  /* live mirror: 1024 bytes = 8 pages x 128 columns, each byte a vertical strip of 8 pixels (bit 0 on top) */
  live: null, liveOrient: 0, liveImg: null,
  showLive(bytes, orient) {
    if (!this.live) this.snapshotForTransition();
    this.live = bytes; this.liveOrient = orient || 0;
  },
  endLive() { if (this.live) { this.snapshotForTransition(); this.live = null; } },
  snapshotForTransition() {
    const oc = this.old.getContext("2d");
    oc.clearRect(0, 0, W, H); oc.drawImage(this.ctx.canvas, 0, 0);
    this.tStart = performance.now();
  },
  drawLive(c) {
    if (!this.liveImg) this.liveImg = c.createImageData(W, H);
    const px = this.liveImg.data, d = this.live, flip = this.liveOrient === 1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const on = (d[(y >> 3) * W + x] >> (y & 7)) & 1;
      const sx = flip ? W - 1 - x : x, sy = flip ? H - 1 - y : y;
      const i = (sy * W + sx) * 4;
      px[i] = px[i + 1] = px[i + 2] = 0; px[i + 3] = on ? 255 : 0;
    }
    c.putImageData(this.liveImg, 0, 0);
  },

  tick(t) {
    requestAnimationFrame((tt) => this.tick(tt));
    if (this.live) {
      const fc = this.frameCanvas.getContext("2d");
      this.drawLive(fc);
      this.compose(t);
      return;
    }
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
    this.compose(t);
  },

  /* draw frameCanvas onto the screen, dithering in from the previous picture right after a switch */
  compose(t) {
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
  const fws = [["official", "Official firmware (release)", "OFW", "1.5.1", "88.2"], ["official-dev", "Official firmware (dev)", "OFW DEV", null, null],
    ["momentum", "Momentum", "MOMENTUM", "mntm-012", "87.1"], ["momentum-dev", "Momentum (dev)", "MNTM DEV", "d3f89dfe", "87.1"],
    ["unleashed", "Unleashed", "UNLEASHED", "unlshd-094", "88.1"], ["unleashed-dev", "Unleashed (dev)", "UNLSHD DEV", "1fe23784", "88.25"]];
  let timers = [];
  const emit = (batch) => window.fb.onEvents(batch);
  const later = (ms, batch) => timers.push(setTimeout(() => emit(batch), ms));
  const lib = [
    { key: "a", source: "https://github.com/jblanked/FlipWiFi", repo: "jblanked/FlipWiFi", name: "FlipWiFi", appid: "flip_wifi", category: "GPIO", version: "1.6", fw: "momentum-dev", sdk: "d3f89dfe", api: "87.1", file: "C:\\x.fap", files: [{ path: "C:\\x.fap" }], size: 67000, built: Date.now() / 1000 - 3600 },
    { key: "b", source: "https://github.com/flipperdevices/flipperzero-good-faps/tree/dev/clock", repo: "flipperdevices/flipperzero-good-faps", name: "Clock", appid: "clock", category: "Tools", version: "1.0", fw: "momentum-dev", sdk: "aaaa", api: "86.0", file: "C:\\c.fap", files: [{ path: "C:\\c.fap" }], size: 3800, built: Date.now() / 1000 - 86400 * 3 },
    { key: "c", source: "https://github.com/x/dice", repo: "x/dice", name: "Dice D&D", appid: "dice", category: "Games", version: "1.2", fw: "official", sdk: "1.5.1", api: "88.2", file: "C:\\d.fap", files: [{ path: "C:\\d.fap" }], size: 9000, built: Date.now() / 1000 - 600 },
  ];
  return {
    init: async () => ({ version: "1.3.0", repo: "https://github.com/c3rb3ruszx/flipper-builder",
      firmwares: fws.map(([id, label, short, sdk, api]) => ({ id, label, short, sdk, api })), fw: "momentum-dev", after: "none", auto_fw: true,
      recent: [{ url: "https://github.com/jblanked/FlipWiFi", fw: "momentum-dev", name: "FlipWiFi", repo: "jblanked/FlipWiFi", ok: true, t: 0 }],
      clipboard: "", out_dir: "C:\\output", device: { connected: false }, library_count: lib.length }),
    start_background: async () => { later(2500, [{ type: "device", device: { connected: true, port: "COM7", name: "Ochenko", fw: "momentum-dev", fw_name: "Momentum dev", version: "mntm-dev", commit: "d3f89dfe", api: "87.1" } }]);
      later(4000, [{ type: "update", version: "1.4.0", url: "https://github.com/c3rb3ruszx/flipper-builder/releases" }]); return true; },
    clipboard: async () => "", set_prefs: async () => true, refresh_device: async () => ({}),
    build: async (url, fw) => {
      const fail = /fail/.test(url), multi = /multi/.test(url);
      later(300, [{ type: "stage", stage: "fetch", detail: "owner/repo" }, { type: "log", line: "== Repo: owner/repo" }]);
      later(1300, [{ type: "stage", stage: "scan" }, { type: "log", line: "== Found 3 app(s)" }]);
      if (multi) { later(1600, [{ type: "select", apps: ["Air Arkanoid:air_arkanoid:Games", "Clock:clock:Tools", "Music Player:music_player:Media", "Weather Station:weather_station:Sub-GHz", "NFC Magic:nfc_magic:NFC"].map((s) => { const [name, appid, category] = s.split(":"); return { name, appid, category, path: appid, icon: null, version: "1.0" }; }) }]); return { ok: true }; }
      return mockApi.rest(later, url, fw, fail);
    },
    answer_select: async (ids) => { mockApi.rest(later, "multi", "momentum-dev", false); return true; },
    rebuild: async (keys, fw) => { later(200, [{ type: "batch", index: 1, total: 2, source: "x" }]); return mockApi.rest(later, "lib", fw, false, true); },
    update_sdk: async (fw) => { later(100, [{ type: "stage", stage: "sdk" }]); later(2000, [{ type: "finished", ok: true, sdk_only: true, fw, sdk: "new123", api: "87.2" }]); return { ok: true }; },
    send: async () => { later(100, [{ type: "stage", stage: "send" }]); later(2000, [{ type: "finished", ok: true, send_only: true }]); return { ok: true }; },
    install: async () => { later(100, [{ type: "stage", stage: "send" }]); later(1800, [{ type: "finished", ok: true, send_only: true, installed: true }]); return { ok: true }; },
    cancel: async () => { timers.forEach(clearTimeout); emit([{ type: "finished", ok: false, cancelled: true, error: "Cancelled." }]); return true; },
    library: async () => ({ items: lib, sdk: Object.fromEntries(fws.map(([id, , , v, a]) => [id, { version: v, api: a }])) }),
    library_remove: async () => true,
    catalog: async () => { await sleep(600); const cats = ["Games", "GPIO", "Tools", "NFC", "Sub-GHz", "Media"];
      return { count: 60, apps: Array.from({ length: 60 }, (_, i) => ({ id: "app_" + i, name: ["Snake", "Dice", "Tetris", "Wifi Marauder", "Signal Gen", "Metronome", "Tuner", "Nfc Magic", "Doom Fire", "Bt Remote"][i % 10] + " " + (i + 1), category: cats[i % cats.length], repo: "someone/app" + i, url: "https://github.com/someone/app" + i })) }; },
    pick_folder: async () => "C:\\code\\my_app", set_watch: async (p) => ({ ok: true, path: p }),
    flipper_list: async () => { await sleep(500); return { sd: { total_kib: 7759872, free_kib: 5312000 }, apps: [
      ["Games", "tetris.fap", 7288, { name: "Tetris", version: "1.5", api: "87.1", icon: "/wP/A0kCSQL/A0gASABIAHgASABIAHgA" }],
      ["Games", "dice_app.fap", 22504, null], ["GPIO", "flip_wifi.fap", 67868, { name: "FlipWiFi", version: "1.6", api: "86.0", icon: null }],
      ["Tools", "clock.fap", 3812, null], ["Media", "music_player.fap", 18360, { name: "Music Player", version: "1.4", api: "87.1", icon: null }],
      ["NFC", "nfc_magic.fap", 40210, null], ["Sub-GHz", "weather_station.fap", 61002, null]].map(([category, file, size, meta]) =>
      ({ path: `/ext/apps/${category}/${file}`, category, file, size, meta })) }; },
    flipper_check: async (apps) => { apps.forEach((a, i) => later(400 * (i + 1), [{ type: "fmeta", path: a.path, done: i + 1, total: apps.length,
      meta: { name: prettyFile(a.path.split("/").pop()), version: "1." + i, api: i % 3 ? "87.1" : "85.0", icon: null } }]));
      later(400 * (apps.length + 1), [{ type: "fcheck_done", done: apps.length }]); return { ok: true }; },
    flipper_check_stop: async () => true,
    mirror_start: async () => {
      later(400, [{ type: "mirror", state: "live" }]);
      let f = 0;
      mockApi.mt = setInterval(() => {             // fake frames: a box bouncing across the screen
        const d = new Uint8Array(1024), bx = (f * 3) % 112, by = 8 + Math.round(Math.abs(Math.sin(f / 9)) * 40);
        for (let y = by; y < by + 14; y++) for (let x = bx; x < bx + 14; x++)
          if (y === by || y === by + 13 || x === bx || x === bx + 13) d[(y >> 3) * 128 + x] |= 1 << (y & 7);
        for (let x = 0; x < 128; x++) d[7 * 128 + x] |= 0x80;
        f++; let s = ""; d.forEach((b) => (s += String.fromCharCode(b)));
        window.fb.onFrame(btoa(s), 0);
      }, 60);
      return { ok: true }; },
    mirror_stop: async () => { clearInterval(mockApi.mt); return { ok: true }; },
    mirror_press: async () => ({ ok: true }),
    save_screenshot: async () => ({ path: "C:\\shot.png" }),
    files_list: async (path) => { await sleep(250); return { path, entries: path === "/ext"
      ? ["apps", "apps_data", "badusb", "infrared", "nfc", "subghz", "u2f"].map((n) => ({ name: n, dir: true, size: 0 })).concat([{ name: "readme.txt", dir: false, size: 812 }])
      : [{ name: "captures", dir: true, size: 0 }, { name: "garage.sub", dir: false, size: 2310 }, { name: "tv_off.ir", dir: false, size: 640 }] }; },
    files_mkdir: async () => ({ ok: true }), files_rename: async () => ({ ok: true }),
    files_delete: async () => { later(200, [{ type: "stage", stage: "send" }]); later(900, [{ type: "finished", ok: true, quiet: true, send_only: true, files_changed: true }]); return { ok: true }; },
    files_download: async () => { later(300, [{ type: "fprog", label: "Downloading", done: 4000, total: 9000 }]); later(1200, [{ type: "finished", ok: true, quiet: true, send_only: true }]); return { ok: true }; },
    files_upload: async () => { later(300, [{ type: "fprog", label: "Uploading", done: 2000, total: 9000 }]); later(1200, [{ type: "finished", ok: true, quiet: true, send_only: true, files_changed: true }]); return { ok: true }; },
    backup_folders: async () => { await sleep(600); return { folders: [["apps", 8900000], ["apps_data", 120000], ["badusb", 40000], ["infrared", 210000], ["nfc", 88000], ["subghz", 1500000], ["u2f", 2000], ["update", 0]].map(([name, size]) => ({ name, size, files: 3 })) }; },
    backups_list: async () => ({ dir: "C:\\backups", backups: [{ path: "C:\\b.zip", name: "C3rb3rus_2026-11-09_0930", files: 214, bytes: 1960000, created: Date.now() / 1000 - 7200, device: { fw_name: "Momentum dev" } }] }),
    backup_start: async () => { for (let i = 1; i <= 5; i++) later(300 * i, [{ type: "fprog", label: "Backing up", done: i * 400000, total: 2000000 }]);
      later(1900, [{ type: "finished", ok: true, quiet: true, send_only: true, backup_done: { files: 214 } }]); return { ok: true }; },
    backup_restore: async () => { later(300, [{ type: "fprog", label: "Restoring", done: 900000, total: 2000000 }]); later(1500, [{ type: "finished", ok: true, quiet: true, send_only: true, restored: { files: 214 } }]); return { ok: true }; },
    backup_delete: async () => ({ ok: true }),
    flipper_open: async () => { await sleep(300); return { ok: true }; },
    flipper_remove: async () => { await sleep(300); return { ok: true }; },
    flipper_update: async () => { later(100, [{ type: "stage", stage: "send" }]); later(1500, [{ type: "finished", ok: true, send_only: true, installed: true, flipper_changed: true }]); return { ok: true }; },
    clear_recent: async () => true, open_folder: async () => true, open_url: async () => true, copy: async () => true,
  };
}
mockApi.rest = (later, url, fw, fail, rebuild) => {
  later(2000, [{ type: "stage", stage: "sdk", detail: "MNTM DEV" }]);
  later(2800, [{ type: "stage", stage: "build", detail: "1 app(s)" }]);
  for (let p = 1; p <= 8; p++) later(2800 + p * 250, [{ type: "progress", value: p / 8 }, { type: "log", line: `\tCC\tsrc/file_${p}.c` }]);
  later(5200, fail
    ? [{ type: "log", line: "main.c:(.text+0x1a): undefined reference to `subghz_devices_foo'" },
       { type: "finished", ok: false, error: "Demo App failed to build.", hints: [{ title: "Your firmware's SDK is missing functions this app uses", detail: "It calls subghz_devices_foo, which this firmware doesn't provide. The app was probably written for a different firmware. Try building it for another one.", try: ["unleashed-dev", "official-dev"] }] }]
    : [{ type: "result", result: { name: "Demo App", appid: "demo_app", category: "GPIO", ok: true, icon: null, dir: "x", fw, api: "87.1",
         files: [{ name: "demo_app.fap", path: "C:\\output\\demo_app.fap", size: 24576 }] } },
       { type: "finished", ok: true, built: 1, total: 1, out: "C:\\output", fw, sdk: "d3f89dfe", api: "87.1", rebuild: !!rebuild,
         recent: [{ url, fw, name: "Demo App", repo: "owner/repo", ok: true, t: 0 }] }]);
  return { ok: true };
};

/* =====================================================================
   App state + UI
   ===================================================================== */
const S = {
  fws: [], fw: "official", after: "none", autoFw: true, busy: null, stage: null, progress: 0,
  results: [], recent: [], repo: "", lastClip: "", idleTimer: null, sleepTimer: null, outDir: "",
  device: { connected: false }, tab: "build", lib: null, libFilter: "all", catalog: null, catFilter: "All",
  watch: "", pickApps: [], pickOn: new Set(), batch: null,
};
const STAGES = ["fetch", "scan", "sdk", "build", "done"];
const STAGE_RANGE = { fetch: [0, .16], scan: [.16, .22], sdk: [.22, .38], build: [.38, .96], send: [.96, 1], done: [1, 1] };
const STAGE_ANIM = { fetch: "books", scan: "books", sdk: "laptop", build: "soldering", send: "radio" };
const IDLE_ANIMS = ["waves", "painting", "procrastinating"];
const TIPS = [
  "Paste a GitHub link!", "Folder links build one app", "Left / right picks firmware", "Poke me!",
  "Need a .fap?", "Enter builds, Esc cancels", "Drop a link on me", "Try the Discover tab", "Plug me in over USB!",
];

function fwObj(id = S.fw) { return S.fws.find((f) => f.id === id) || S.fws[0]; }
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function apiOk(appApi, devApi) {           // same rule as the firmware loader
  if (!appApi || !devApi) return null;
  const [am, an] = appApi.split(".").map(Number), [dm, dn] = devApi.split(".").map(Number);
  return am === dm && dn >= an;
}
function ago(t) {
  const s = Date.now() / 1000 - t;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}
const isLocalPath = (t) => /^[A-Za-z]:[\\/]|^\\\\/.test((t || "").trim());

/* ---------- LED ---------- */
function led(color, blink = false) { $("#led").className = "led" + (color ? " " + color : "") + (blink ? " blink" : ""); }

/* ---------- idle behaviour ---------- */
function goIdle(sayText) {
  clearTimeout(S.idleTimer); clearTimeout(S.sleepTimer);
  if (S.watch) { LCD.play("laptop"); LCD.say(sayText || "Watching your folder...", sayText ? 5000 : 0); return; }
  LCD.play(IDLE_ANIMS[Math.floor(Math.random() * IDLE_ANIMS.length)]);
  if (sayText) LCD.say(sayText, 5000);
  S.idleTimer = setTimeout(() => { if (!S.busy) goIdle(Math.random() < .6 ? TIPS[Math.floor(Math.random() * TIPS.length)] : ""); }, 26000);
  S.sleepTimer = setTimeout(() => { if (!S.busy && !S.watch) { clearTimeout(S.idleTimer); LCD.play("sleep"); LCD.say("zzz..."); } }, 240000);
}
function wake() {
  if (!S.busy && LCD.key === "sleep") goIdle("Oh! Hi again");
  else if (!S.busy && !S.watch) { clearTimeout(S.sleepTimer); S.sleepTimer = setTimeout(() => { if (!S.busy) { LCD.play("sleep"); LCD.say("zzz..."); } }, 240000); }
}

/* ---------- tabs ---------- */
const TAB_ORDER = ["build", "library", "discover", "flipper"];
function setTab(name) {
  const prev = S.tab; S.tab = name;
  $$(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === name));
  $$(".pane").forEach((p) => {
    const on = p.dataset.pane === name;
    p.classList.toggle("left", !on && TAB_ORDER.indexOf(p.dataset.pane) < TAB_ORDER.indexOf(name));
    p.classList.toggle("on", on);
  });
  moveInk($("#tabInk"), $(`.tab[data-tab="${name}"]`), true);
  if (name === "library") loadLibrary();
  if (name === "discover") loadCatalog();
  if (name === "flipper" && prev !== "flipper") { loadFlipper(); setTimeout(() => setSub(FM.sub), 30); }
  if (prev !== name) wake();
}
function moveInk(ink, btn, flat) {
  if (!btn) return;
  ink.style.width = btn.offsetWidth + "px";
  if (flat) ink.style.transform = `translateX(${btn.offsetLeft}px)`;
  else { ink.style.height = btn.offsetHeight + "px"; ink.style.transform = `translate(${btn.offsetLeft}px, ${btn.offsetTop}px)`; }
}

/* ---------- firmware selector ---------- */
function renderFirmwares() {
  const row = $("#fwRow");
  $$(".fw", row).forEach((b) => b.remove());
  S.fws.forEach((f) => {
    const b = document.createElement("button");
    b.className = "fw"; b.dataset.fw = f.id; b.title = f.label + (f.api ? ` (SDK API ${f.api})` : "");
    b.innerHTML = `<span class="fw-name">${f.short}</span><span class="fw-sdk">${f.sdk || "not downloaded"}</span>`;
    b.onclick = () => setFw(f.id, true);
    row.appendChild(b);
  });
  setFw(S.fw, false);
}
function setFw(id, user) {
  S.fw = id;
  const row = $("#fwRow"), btn = $(`.fw[data-fw="${id}"]`, row);
  $$(".fw", row).forEach((b) => b.classList.toggle("on", b === btn));
  moveInk($("#fwIndicator"), btn, false);
  const f = fwObj();
  $("#deviceFw").textContent = f.short;
  $("#sdkPillText").textContent = `SDK ${f.sdk || "not downloaded"}`;
  $("#rebuildLabel").textContent = `Rebuild all for ${f.short}`;
  renderCompat();
  if (user) {
    api.set_prefs(S.fw, S.after);
    if (!S.busy) { LCD.say(`${f.short}: ${f.sdk ? "SDK " + f.sdk : "SDK not downloaded yet"}`, 3500); wake(); }
    if (S.tab === "library") renderLibrary();
  }
}
function cycleFw(d) {
  if (S.busy) return;
  const i = S.fws.findIndex((f) => f.id === S.fw);
  setFw(S.fws[(i + d + S.fws.length) % S.fws.length].id, true);
}

/* ---------- after-build mode ---------- */
function setAfter(mode, user) {
  S.after = mode;
  $$("#afterSeg button").forEach((b) => b.classList.toggle("on", b.dataset.after === mode));
  moveInk($("#afterInk"), $(`#afterSeg button[data-after="${mode}"]`), false);
  if (user) {
    api.set_prefs(S.fw, S.after);
    if (!S.busy && mode !== "none") LCD.say(S.device.connected ? "I'll beam it over USB" : "Plug your Flipper in first!", 3000);
  }
}

/* ---------- device (Flipper over USB) ---------- */
function onDevice(d) {
  const was = S.device;
  S.device = d || { connected: false };
  const pill = $("#devicePill"), txt = $("#deviceText");
  pill.className = "pill device-pill";
  if (!S.device.connected) { txt.textContent = "No Flipper"; }
  else if (S.device.error) { pill.classList.add("err"); txt.textContent = (S.device.name || "Flipper") + " · busy"; pill.title = S.device.error; }
  else {
    pill.classList.add("on");
    txt.textContent = `${S.device.name} · ${S.device.fw_name || "?"}`;
    pill.title = `${S.device.version || ""} ${S.device.commit ? "(" + S.device.commit + ")" : ""} · API ${S.device.api || "?"} · ${S.device.port}`;
  }
  const newly = S.device.connected && !S.device.error && (!was.connected || was.error || was.port !== S.device.port);
  if (newly) {
    led("blue"); setTimeout(() => !S.busy && led(null), 1200);
    let msg = `Hi ${S.device.name}!`;
    if (S.device.fw && S.autoFw && S.device.fw !== S.fw && S.fws.some((f) => f.id === S.device.fw)) {
      setFw(S.device.fw, false); api.set_prefs(S.fw, S.after);
      toast(`Flipper on ${S.device.fw_name}: switched to ${fwObj().short}`);
      msg += ` You run ${S.device.fw_name}`;
    } else if (!S.device.fw) {
      toast(`${S.device.fw_name || "This firmware"} isn't one I know. Pick the closest one.`);
    }
    if (!S.busy) { LCD.say(msg, 4000); LCD.poke(); }
  } else if (was.connected && !S.device.connected && !S.busy) {
    LCD.say("Bye Flipper!", 2500);
  }
  renderCompat();
  if (S.tab === "library") renderLibrary();
  if (S.tab === "flipper" && (newly || (was.connected && !S.device.connected) || (!was.error && S.device.error))) {
    loadFlipper();
    if (FM.sub === "files") loadFiles("/ext");
  }
  if (!S.device.connected && MIR.state !== "off") stopMirror("Flipper unplugged");
}
function renderCompat() {
  const c = $("#compat"), d = S.device, f = fwObj();
  if (!f) return;
  c.className = "compat";
  if (!d.connected) { c.innerHTML = `<span>Plug in your Flipper and I'll match the firmware for you.</span>`; return; }
  if (d.error) { c.classList.add("warn"); c.innerHTML = `<span>${esc(d.error)}</span><button id="devRetry">Try again</button>`;
    $("#devRetry").onclick = () => api.refresh_device(); return; }
  if (d.fw && d.fw !== S.fw) {
    const want = fwObj(d.fw);
    c.classList.add("warn");
    c.innerHTML = `<span>Your Flipper runs <b>${esc(d.fw_name)}</b>, but ${esc(f.short)} is selected.</span>` +
      (want ? `<button id="useDevFw">Use ${esc(want.short)}</button>` : "");
    if (want) $("#useDevFw").onclick = () => setFw(d.fw, true);
    return;
  }
  const ok = apiOk(f.api, d.api);
  if (ok === false) {
    const sdkNewer = f.api && d.api && f.api.localeCompare(d.api, undefined, { numeric: true }) > 0;
    c.classList.add("bad");
    c.innerHTML = `<span>SDK API ${esc(f.api)} won't run on your Flipper (API ${esc(d.api)}). ` +
      (sdkNewer ? "Update your Flipper's firmware." : "Update the SDK.") + `</span>` +
      (sdkNewer ? "" : `<button id="compatSdk">Update SDK</button>`);
    if (!sdkNewer) $("#compatSdk").onclick = () => startJob("sdk", () => api.update_sdk(S.fw));
  } else {
    c.classList.add("ok");
    c.innerHTML = `<span>&#10003; Matches your Flipper: <b>${esc(d.fw_name || "?")}</b> · API ${esc(d.api || "?")}` +
      (f.api ? ` · SDK API ${esc(f.api)}` : " · SDK downloads on first build") + `</span>`;
  }
}

/* ---------- link preview ---------- */
function parseLink(text) {
  let u = (text || "").trim().replace(/^"|"$/g, "");
  if (!u) return null;
  if (isLocalPath(u)) return { local: true, path: u, name: u.split(/[\\/]/).filter(Boolean).pop() };
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
  const val = $("#url").value, p = parseLink(val), box = $("#preview");
  $("#inputWrap").classList.remove("bad");
  $("#inIcon").innerHTML = pixelIcon(p && p.local ? "folder" : "github");
  if (S.watch && (!p || !p.local || p.path !== S.watch)) setWatch("");
  if (!p) { box.innerHTML = ""; box.dataset.key = ""; return; }
  if (p.bad) { box.innerHTML = `<span class="chip bad">Not a GitHub link or folder</span>`; box.dataset.key = ""; return; }
  let chips, key;
  if (p.local) {
    key = "local:" + p.path;
    chips = [`<span class="chip">${esc(p.name)}</span>`, `<span class="chip muted">local folder</span>`,
      `<span class="chip watch ${S.watch ? "on" : ""}" id="watchChip" title="Rebuild automatically whenever you save a file">${S.watch ? "&#9679; watching" : "&#9675; watch &amp; rebuild"}</span>`];
  } else {
    key = p.owner + "/" + p.repo + p.rest.join("/");
    chips = [`<span class="chip">${esc(p.owner)} / ${esc(p.repo)}</span>`];
    if (p.rest.length) chips.push(`<span class="chip muted">${esc(p.rest[0].length === 40 ? p.rest[0].slice(0, 8) : p.rest[0])}</span>`);
    if (p.rest.length > 1) chips.push(`<span class="chip muted">${esc(p.rest.slice(1).join("/"))}</span>`);
    else chips.push(`<span class="chip muted">${p.rest.length ? "whole branch" : "default branch"}</span>`);
  }
  if (box.dataset.key !== key + S.watch) {
    box.innerHTML = chips.join("");
    box.dataset.key = key + S.watch;
    const w = $("#watchChip");
    if (w) w.onclick = () => setWatch(S.watch ? "" : p.path);
    const short = p.local ? p.name : p.repo;
    if (!S.busy && short !== S.repo) { S.repo = short; LCD.say(`Ooh, ${short}!`, 3000); LCD.poke(); }
  }
}

/* ---------- watch mode (local folders) ---------- */
async function setWatch(path) {
  const r = await api.set_watch(path);
  if (r && r.error) { toast(r.error); return; }
  S.watch = path;
  $("#preview").dataset.key = "";
  renderPreview();
  if (path) { toast("Watching: saves rebuild automatically"); if (!S.busy) goIdle("Watching your folder..."); }
  else if (!S.busy) goIdle();
}

/* ---------- progress bar ---------- */
const BAR_N = 32;
let barScan = 0;
function initBar() { $("#bar").innerHTML = "<i></i>".repeat(BAR_N); }
function paintBar(value, mode) {
  const cells = $$("#bar i"), lit = Math.round(value * BAR_N);
  cells.forEach((c, i) => { c.className = i < lit ? (mode || "on") : ""; });
  if (S.busy && mode !== "ok" && mode !== "err" && lit < BAR_N) {
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
  $$(".stage").forEach((el, i) => { el.className = "stage" + (i < idx ? " done" : i === idx ? " active" : ""); });
  S.progress = Math.max(S.progress, (STAGE_RANGE[name] || [0])[0]);
}
function resetStages() { $$(".stage").forEach((el) => (el.className = "stage")); S.progress = 0; paintBar(0); }

/* ---------- build button ---------- */
function setBuildButton(busy) {
  const b = $("#buildBtn"), l = $("#buildLabel");
  l.classList.add("swap");
  setTimeout(() => { b.classList.toggle("busy", busy); l.textContent = busy ? "Cancel" : "Build .fap"; l.classList.remove("swap"); }, 140);
}

/* ---------- shared bits ---------- */
function fmtSize(n) {
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  return n > 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B";
}
function appIcon(r, cls = "") {
  if (r.icon) return `<div class="app-icon ${cls}"><img src="${r.icon}" alt=""></div>`;
  return `<div class="app-icon none ${cls}"><i data-icon="usb"></i></div>`;
}
function iconBtn(icon, title, fn) {
  const b = document.createElement("button");
  b.className = "icon-btn"; b.title = title; b.innerHTML = `<i data-icon="${icon}"></i>`;
  b.onclick = (e) => { e.stopPropagation(); fn(); };
  return b;
}
function needDevice() {
  if (S.device.connected && !S.device.error) return true;
  toast(S.device.error ? "Flipper is busy: close qFlipper" : "Plug in your Flipper first");
  LCD.say("Plug me in over USB!", 3000); LCD.poke();
  return false;
}

/* ---------- build tab shelf: results + recent ---------- */
function renderShelf() {
  const body = $("#shelfBody"), title = $("#shelfTitle"), actions = $("#shelfActions");
  body.innerHTML = ""; actions.innerHTML = "";
  if (S.busy && !S.results.length && S.busy !== "sdk") {
    title.textContent = S.batch ? `Rebuilding ${S.batch.index} of ${S.batch.total}` : "Working";
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
    body.innerHTML = `<div class="empty">Paste a link to any Flipper Zero app on GitHub (a repo, a branch, or an app's folder), pick a folder on this PC,
      or browse the <a id="goDiscover">Discover</a> tab. Try <a data-ex="https://github.com/flipperdevices/flipperzero-good-faps/tree/dev/clock">the official Clock app</a>.</div>`;
    $$("a[data-ex]", body).forEach((a) => (a.onclick = () => { $("#url").value = a.dataset.ex; renderPreview(); }));
    $("#goDiscover", body).onclick = () => setTab("discover");
  }
  renderIcons(document);
}
function resultCard(r, i) {
  const el = document.createElement("div");
  el.className = "card" + (r.ok ? "" : " fail");
  el.style.animationDelay = i * 70 + "ms";
  const icon = r.ok ? appIcon(r) : `<div class="app-icon bad"><i data-icon="cross"></i></div>`;
  const file = r.files && r.files[0];
  const flag = r.installed || r.sent ? ` · <b style="color:var(--ok)">on your Flipper</b>` : "";
  const sub = r.ok ? `${esc(file.name)} · ${fmtSize(file.size)} · SD: <b>apps/${esc(r.category)}/</b>${flag}` : `failed to build · see the hints above`;
  el.innerHTML = `${icon}<div class="card-main"><div class="card-title">${esc(r.name)}${r.version ? ` <span style="color:var(--dim);font-size:12px">v${esc(r.version)}</span>` : ""}</div>
    <div class="card-sub">${sub}</div></div><div class="card-actions"></div>`;
  const acts = $(".card-actions", el);
  if (r.ok) {
    acts.append(
      iconBtn("folder", "Show in folder", () => api.open_folder(file.path)),
      iconBtn("copy", "Copy SD card path", async () => { await api.copy(`apps/${r.category}/${file.name}`); toast("SD path copied"); }),
      iconBtn("download", "Install to the Flipper's SD card", () => needDevice() && startJob("install", () => api.install(r.files.map((f) => [f.path, r.category])))),
      iconBtn("usb", "Install & open on the Flipper", () => needDevice() && startJob("send", () => api.send(r.dir, r.fw))),
    );
  } else acts.append(iconBtn("terminal", "Show log", () => openLog(true)));
  return el;
}

/* ---------- library tab ---------- */
async function loadLibrary() {
  const data = await api.library();
  S.lib = data;
  $("#libCount").textContent = data.items.length || "";
  renderLibrary();
}
function libStatus(item) {
  const d = S.device;
  if (d.connected && !d.error && d.fw === item.fw && d.api) {
    const ok = apiOk(item.api, d.api);
    if (ok === false) return ["bad", "won't run on your Flipper"];
    if (ok) return ["ok", "runs on your Flipper"];
  }
  const cur = S.lib.sdk[item.fw] && S.lib.sdk[item.fw].api;
  if (cur && item.api && cur !== item.api) return ["old", `outdated (SDK now ${cur})`];
  return ["", ""];
}
function renderLibrary() {
  if (!S.lib) return;
  const items = S.lib.items, list = $("#libList"), filt = $("#libFilter");
  const counts = {};
  items.forEach((i) => (counts[i.fw] = (counts[i.fw] || 0) + 1));
  if (S.libFilter !== "all" && !counts[S.libFilter]) S.libFilter = "all";
  filt.innerHTML = `<button class="fchip ${S.libFilter === "all" ? "on" : ""}" data-f="all">All<small>${items.length}</small></button>` +
    Object.keys(counts).map((fw) => `<button class="fchip ${S.libFilter === fw ? "on" : ""}" data-f="${fw}">${esc((fwObj(fw) || { short: fw }).short)}<small>${counts[fw]}</small></button>`).join("");
  $$(".fchip", filt).forEach((b) => (b.onclick = () => { S.libFilter = b.dataset.f; renderLibrary(); }));
  const shown = items.filter((i) => S.libFilter === "all" || i.fw === S.libFilter);
  const stale = shown.filter((i) => libStatus(i)[0] === "old" || libStatus(i)[0] === "bad").length;
  $("#libNote").innerHTML = items.length
    ? `Every app you've built. After a firmware update, <b>Rebuild all</b> builds them again for the firmware selected on the Build tab` +
      (S.after !== "none" ? " and installs them to your Flipper." : ".") + (stale ? ` <span style="color:var(--orange-2)">${stale} need a rebuild.</span>` : "")
    : "";
  $("#rebuildAll").disabled = !shown.length;
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = `<div class="empty-big"><b>Nothing here yet</b>Apps you build show up here, so you can reinstall them<br>or rebuild them all after a firmware update.</div>`;
    return;
  }
  shown.forEach((it, idx) => {
    const [cls, label] = libStatus(it);
    const row = document.createElement("div");
    row.className = "row"; row.style.animationDelay = Math.min(idx, 12) * 30 + "ms";
    row.innerHTML = `${appIcon(it)}<div style="min-width:0"><div class="row-title">${esc(it.name)}${it.version ? ` <span style="color:var(--dim);font-size:12px">v${esc(it.version)}</span>` : ""}</div>
      <div class="row-sub"><span class="badge fw">${esc((fwObj(it.fw) || { short: it.fw }).short)}</span>${cls ? `<span class="badge ${cls}">${esc(label)}</span>` : ""}
      <span title="${fmtSize(it.size || 0)}">API ${esc(it.api || "?")} · ${ago(it.built)}</span></div></div><div class="row-actions"></div>`;
    $(".row-actions", row).append(
      iconBtn("download", "Install to the Flipper's SD card", () => needDevice() && startJob("install", () => api.install((it.files || [{ path: it.file }]).map((f) => [f.path, it.category])))),
      iconBtn("refresh", `Rebuild for ${fwObj().short}`, () => startRebuild([it.key])),
      iconBtn("folder", "Show the file", () => api.open_folder(it.file)),
      iconBtn("trash", "Remove from library (deletes the .fap)", async () => { await api.library_remove(it.key, true); loadLibrary(); toast("Removed"); }),
    );
    row.onclick = () => { $("#url").value = it.source; setTab("build"); renderPreview(); };
    row.style.cursor = "pointer"; row.title = `${it.source}\nClick to load it on the Build tab`;
    list.appendChild(row);
  });
  renderIcons(list);
}
function startRebuild(keys) {
  if (S.busy) return;
  const shownKeys = keys || S.lib.items.filter((i) => S.libFilter === "all" || i.fw === S.libFilter).map((i) => i.key);
  if (S.after !== "none" && !needDevice()) return;
  S.results = []; logCount = 0; $("#logBody").innerHTML = ""; S.batch = null;
  setTab("build");
  startJob("rebuild", () => api.rebuild(shownKeys, S.fw, S.after));
  renderShelf();
}

/* ---------- discover tab ---------- */
async function loadCatalog(force) {
  if (S.catalog && !force) { renderCatalog(); return; }
  $("#catList").innerHTML = `<div class="skeleton"><span class="blk"></span>Loading the Flipper Apps Catalog...</div>`;
  if (!S.busy) { LCD.play("books", { fps: 4 }); LCD.say("Reading the catalog...", 4000); }
  const data = await api.catalog(!!force);
  if (data.error) { $("#catList").innerHTML = `<div class="empty-big"><b>Couldn't load it</b>${esc(data.error)}</div>`; return; }
  S.catalog = data;
  if (!S.busy) goIdle(`${data.count} apps to try!`);
  renderCatalog();
}
function renderCatalog() {
  const data = S.catalog; if (!data) return;
  const q = $("#search").value.trim().toLowerCase();
  const cats = ["All", ...new Set(data.apps.map((a) => a.category))];
  $("#catFilter").innerHTML = cats.map((c) => `<button class="fchip ${S.catFilter === c ? "on" : ""}" data-c="${esc(c)}">${esc(c)}</button>`).join("");
  $$("#catFilter .fchip").forEach((b) => (b.onclick = () => { S.catFilter = b.dataset.c; renderCatalog(); }));
  const hits = data.apps.filter((a) => (S.catFilter === "All" || a.category === S.catFilter) &&
    (!q || (a.name + " " + a.id + " " + a.repo + " " + a.category).toLowerCase().includes(q)));
  $("#catNote").textContent = `${hits.length} of ${data.count} apps from Flipper's official catalog, built from the exact version Flipper reviewed.`;
  const list = $("#catList");
  list.innerHTML = "";
  hits.slice(0, 150).forEach((a, idx) => {
    const row = document.createElement("div");
    row.className = "row"; row.style.animationDelay = Math.min(idx, 12) * 25 + "ms";
    row.innerHTML = `<div class="app-icon cat">${esc(a.category.slice(0, 2).toUpperCase())}</div>
      <div style="min-width:0"><div class="row-title">${esc(a.name)}</div><div class="row-sub"><span class="badge fw">${esc(a.category)}</span><span>${esc(a.repo)}</span></div></div>
      <div class="row-actions"></div>`;
    const go = document.createElement("button");
    go.className = "mini primary"; go.textContent = "Build";
    go.onclick = (e) => { e.stopPropagation(); $("#url").value = a.url; setTab("build"); renderPreview(); build(); };
    $(".row-actions", row).append(iconBtn("github", "Open its source on GitHub", () => api.open_url(a.url)), go);
    row.onclick = () => { $("#url").value = a.url; setTab("build"); renderPreview(); toast("Loaded. Press Build"); };
    row.style.cursor = "pointer";
    list.appendChild(row);
  });
  if (hits.length > 150) list.insertAdjacentHTML("beforeend", `<div class="lib-note" style="text-align:center;padding:8px">Showing 150. Search to narrow it down.</div>`);
  if (!hits.length) list.innerHTML = `<div class="empty-big"><b>No matches</b>Try another word or category.</div>`;
  renderIcons(list);
}

/* ---------- on-flipper tab: what's installed on the device ---------- */
const FL = { apps: null, sd: null, filter: "All", checking: false, error: "" };
function xbmIcon(b64) {           // 10x10 1-bit icon from the .fap manifest -> PNG data URI
  try {
    const raw = atob(b64), c = document.createElement("canvas");
    c.width = c.height = 10;
    const x = c.getContext("2d");
    x.fillStyle = "#000";
    for (let y = 0; y < 10; y++) for (let i = 0; i < 10; i++)
      if ((raw.charCodeAt(y * 2 + (i >> 3)) >> (i & 7)) & 1) x.fillRect(i, y, 1, 1);
    return c.toDataURL();
  } catch (_) { return null; }
}
function prettyFile(f) { return f.replace(/\.fap$/i, "").split(/[_\-]+/).map((w) => w ? w[0].toUpperCase() + w.slice(1) : w).join(" "); }
function libMatch(app) {          // a build in the library for this file, preferring the Flipper's firmware
  if (!S.lib) return null;
  const same = S.lib.items.filter((i) => (i.files || [{ path: i.file }]).some((f) => (f.path || "").split(/[\\/]/).pop().toLowerCase() === app.file.toLowerCase()));
  return same.find((i) => i.fw === S.device.fw) || null;
}
function flUpdate(app) {          // library build that should replace what's installed
  const lib = libMatch(app);
  if (!lib || !app.meta) return null;
  const ok = apiOk(app.meta.api, S.device.api);
  const libOk = apiOk(lib.api, S.device.api);
  if (libOk && (ok === false || lib.api !== app.meta.api || (lib.version && app.meta.version && lib.version !== app.meta.version))) return lib;
  return null;
}
async function loadFlipper() {
  if (!S.device.connected || S.device.error) { FL.apps = null; FL.error = ""; renderFlipper(); return; }
  if (!S.lib) await loadLibrary();
  $("#flList").innerHTML = `<div class="skeleton"><span class="blk"></span>Reading your Flipper's SD card...</div>`;
  const r = await api.flipper_list();
  if (r.error) { FL.apps = null; FL.error = r.error; renderFlipper(); return; }
  FL.apps = r.apps; FL.sd = r.sd; FL.error = ""; FL.animated = false;
  $("#flCount").textContent = r.apps.length || "";
  renderFlipper();
  const todo = r.apps.filter((a) => !a.meta);
  if (todo.length && !FL.checking) {
    FL.checking = true;
    showFlProgress(0, todo.length);
    api.flipper_check(todo.map((a) => ({ path: a.path, size: a.size })));
  }
}
function showFlProgress(done, total) {
  const p = $("#flProg");
  if (done == null || (FM.sub !== "apps" && !S.busy)) { p.hidden = true; return; }
  p.hidden = false;
  // USB reads run at ~110 KB/s; estimate from the apps still missing their info
  const left = (FL.apps || []).filter((a) => !a.meta).reduce((s, a) => s + a.size, 0);
  const secs = Math.round(left / 110000);
  const eta = secs > 90 ? `about ${Math.round(secs / 60)} min left` : secs > 5 ? `about ${secs}s left` : "almost done";
  $("#flProgText").textContent = `Reading app info from the Flipper: ${done} of ${total}, ${eta}. Only the first time; it's cached after.`;
}
function renderFlipper() {
  const d = S.device, list = $("#flList");
  $("#flName").textContent = d.connected && !d.error ? d.name : d.connected ? "Flipper busy" : "No Flipper";
  $("#flSub").textContent = d.connected && !d.error
    ? `${d.fw_name || "Unknown firmware"} · ${d.version || ""}${d.commit ? " (" + d.commit + ")" : ""} · API ${d.api || "?"} · ${d.port}`
    : d.connected ? d.error : "Plug it in over USB, unlock it, and close qFlipper.";
  $("#flRefresh").disabled = !(d.connected && !d.error);
  const sd = FL.sd;
  if (FL.apps && sd && sd.total_kib) {
    const used = 1 - sd.free_kib / sd.total_kib, n = 40, lit = used > 0 ? Math.max(1, Math.round(used * n)) : 0;
    $("#flSdBar").innerHTML = Array.from({ length: n }, (_, i) => `<i class="${i < lit ? (used > .9 ? "hi" : "on") : ""}"></i>`).join("");
    $("#flSdText").textContent = `SD card: ${(sd.free_kib / 1048576).toFixed(2)} GB free of ${(sd.total_kib / 1048576).toFixed(2)} GB`;
    $("#flSd").hidden = false;
  } else $("#flSd").hidden = true;
  if (!FL.apps) {
    $("#flFilter").innerHTML = ""; $("#flUpdateAll").hidden = true;
    list.innerHTML = FL.error
      ? `<div class="empty-big"><b>Couldn't read it</b>${esc(FL.error)}</div>`
      : `<div class="empty-big"><b>${d.connected ? "Flipper busy" : "No Flipper"}</b>${d.connected ? esc(d.error || "") : "Plug in your Flipper to see the apps on its SD card,<br>open them, remove them, or update them with your newer builds."}</div>`;
    return;
  }
  const counts = {};
  FL.apps.forEach((a) => (counts[a.category] = (counts[a.category] || 0) + 1));
  if (FL.filter !== "All" && !counts[FL.filter]) FL.filter = "All";
  $("#flFilter").innerHTML = `<button class="fchip ${FL.filter === "All" ? "on" : ""}" data-c="All">All<small>${FL.apps.length}</small></button>` +
    Object.keys(counts).sort().map((c) => `<button class="fchip ${FL.filter === c ? "on" : ""}" data-c="${esc(c)}">${esc(c)}<small>${counts[c]}</small></button>`).join("");
  $$("#flFilter .fchip").forEach((b) => (b.onclick = () => { FL.filter = b.dataset.c; renderFlipper(); }));
  const updates = FL.apps.map((a) => [a, flUpdate(a)]).filter(([, l]) => l);
  $("#flUpdateAll").hidden = !updates.length;
  $("#flUpdateLabel").textContent = `Update ${updates.length}`;
  $("#flUpdateAll").onclick = () => flDoUpdate(updates);
  const q = $("#flSearch").value.trim().toLowerCase();
  const shown = FL.apps.filter((a) => (FL.filter === "All" || a.category === FL.filter) &&
    (!q || (a.file + " " + a.category + " " + ((a.meta && a.meta.name) || "")).toLowerCase().includes(q)))
    .sort((a, b) => a.category.localeCompare(b.category) || ((a.meta && a.meta.name) || a.file).localeCompare((b.meta && b.meta.name) || b.file));
  const bad = FL.apps.filter((a) => a.meta && a.meta.api && apiOk(a.meta.api, d.api) === false).length;
  list.innerHTML = "";
  if (bad) list.insertAdjacentHTML("beforeend", `<div class="lib-note" style="color:var(--orange-2)">${bad} app${bad > 1 ? "s" : ""} won't open on this firmware (built for another API). Rebuild them here, or remove them.</div>`);
  shown.forEach((a, idx) => {
    const m = a.meta || {}, lib = libMatch(a), upd = flUpdate(a);
    const icon = m.icon ? xbmIcon(m.icon) : lib && lib.icon;
    const ok = m.api ? apiOk(m.api, d.api) : null;
    const row = document.createElement("div");
    row.className = "row";
    // slide in only on the first draw; live updates (header reads) must not replay the animation
    if (FL.animated) row.style.animation = "none"; else row.style.animationDelay = Math.min(idx, 14) * 22 + "ms";
    row.dataset.path = a.path;
    row.innerHTML = `${icon ? `<div class="app-icon"><img src="${icon}" alt=""></div>` : `<div class="app-icon cat">${esc(a.category.slice(0, 2).toUpperCase())}</div>`}
      <div style="min-width:0"><div class="row-title">${esc(m.name || prettyFile(a.file))}${m.version ? ` <span style="color:var(--dim);font-size:12px">v${esc(m.version)}</span>` : ""}</div>
      <div class="row-sub"><span class="badge fw">${esc(a.category)}</span>` +
      (ok === false ? `<span class="badge bad" title="Built for API ${esc(m.api)}, your Flipper is ${esc(d.api)}">won't open (API ${esc(m.api)})</span>` : ok ? `<span class="badge ok">runs</span>` : "") +
      (upd ? `<span class="badge old">update in library</span>` : lib ? `<span class="badge fw" title="You built this one">yours</span>` : "") +
      `<span>${fmtSize(a.size)} · ${esc(a.file)}</span></div></div><div class="row-actions"></div>`;
    const acts = $(".row-actions", row);
    const openBtn = iconBtn("play", "Open it on the Flipper", async () => {
      LCD.say("Opening it on your Flipper", 2500); LCD.poke();
      const r = await api.flipper_open(a.path);
      toast(r.error ? r.error : "Opened on your Flipper");
    });
    openBtn.classList.add("go");
    acts.append(openBtn);
    if (upd) acts.append(iconBtn("download", `Install your build (API ${upd.api}) over it`, () => flDoUpdate([[a, upd]])));
    const rm = iconBtn("trash", "Remove it from the Flipper", () => flRemove(a));
    rm.classList.add("danger");
    acts.append(rm);
    list.appendChild(row);
  });
  if (!shown.length) list.insertAdjacentHTML("beforeend", `<div class="empty-big"><b>No apps</b>${FL.apps.length ? "Nothing matches that filter." : "There are no .fap apps in /ext/apps on this SD card."}</div>`);
  renderIcons(list);
  FL.animated = true;
}
async function flRemove(a) {
  const name = (a.meta && a.meta.name) || prettyFile(a.file);
  const yes = await confirmDialog(`Remove ${name}?`,
    `This deletes <code>${esc(a.path)}</code> from your Flipper's SD card. It can't be undone` +
    (libMatch(a) ? ", but the build stays in your Library so you can reinstall it." : "."), "Remove");
  if (!yes) return;
  const r = await api.flipper_remove(a.path);
  if (r.error) { toast(r.error); return; }
  toast(`${name} removed`);
  LCD.say("Poof! Gone", 2500); LCD.poke();
  FL.apps = FL.apps.filter((x) => x.path !== a.path);
  $("#flCount").textContent = FL.apps.length || "";
  renderFlipper();
}
async function flDoUpdate(pairs) {
  if (S.busy) return;
  const names = pairs.map(([a]) => (a.meta && a.meta.name) || prettyFile(a.file));
  const yes = await confirmDialog(`Update ${pairs.length === 1 ? names[0] : pairs.length + " apps"}?`,
    `Replaces the installed ${pairs.length === 1 ? "file" : "files"} with your newer build${pairs.length > 1 ? "s" : ""} from the Library, at the same place on the SD card` +
    (pairs.length > 1 ? `: ${esc(names.join(", "))}.` : "."), "Update");
  if (!yes) return;
  startJob("install", () => api.flipper_update(pairs.map(([a, lib]) => [a.path, lib.file])));
}
function confirmDialog(title, html, okLabel) {
  return new Promise((resolve) => {
    $("#cfTitle").textContent = title;
    $("#cfBody").innerHTML = html;
    $("#cfYes").textContent = okLabel || "OK";
    $("#cfYes").classList.toggle("danger", /delete|remove/i.test(okLabel || ""));
    $("#confirm").hidden = false;
    const done = (v) => { $("#confirm").hidden = true; $("#cfYes").onclick = $("#cfNo").onclick = null; resolve(v); };
    $("#cfYes").onclick = () => done(true);
    $("#cfNo").onclick = () => done(false);
    confirmDialog.cancel = () => done(false);
    setTimeout(() => $("#cfNo").focus(), 30);
  });
}

/* ---------- screen mirror + remote ---------- */
const MIR = { state: "off", held: {}, frame: null };
function b64bytes(b64) { const s = atob(b64), a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; }
function onFrame(b64, orient) {
  if (MIR.state === "off") return;
  MIR.frame = b64bytes(b64);
  LCD.showLive(MIR.frame, orient);
}
async function toggleMirror() {
  if (MIR.state !== "off") { stopMirror(); return; }
  if (!S.device.connected || S.device.error) { needDevice(); return; }
  MIR.state = "starting";
  $("#mirrorLabel").textContent = "Connecting...";
  const r = await api.mirror_start();
  if (r.error) { MIR.state = "off"; $("#mirrorLabel").textContent = "Mirror my Flipper"; toast(r.error); }
}
function stopMirror(msg) {
  MIR.state = "off";
  api.mirror_stop();
  Object.values(MIR.held).forEach((h) => { clearTimeout(h.t1); clearInterval(h.t2); });
  MIR.held = {};
  document.body.classList.remove("mirroring");
  $("#mirrorLabel").textContent = "Mirror my Flipper";
  $("#shotBtn").hidden = true;
  $("#deviceFw").textContent = fwObj().short;
  $("#legend").innerHTML = `<span><b>&#9664; &#9654;</b> firmware</span><span><b>OK</b> build</span><span><b>&#8617;</b> cancel</span>`;
  LCD.endLive();
  goIdle(msg || "Back to building!");
}
function onMirrorState(e) {
  if (e.state === "live") {
    MIR.state = "live";
    document.body.classList.add("mirroring");
    $("#mirrorLabel").textContent = "Stop mirroring";
    $("#shotBtn").hidden = false;
    $("#deviceFw").textContent = "LIVE";
    $("#legend").innerHTML = `<span><b>d-pad / arrows</b> navigate</span><span><b>OK / Enter</b> select</span><span><b>&#8617; / Backspace</b> back</span><span><b>hold</b> long press</span>`;
    clearTimeout(S.idleTimer); clearTimeout(S.sleepTimer);
  } else if (e.state === "paused") {
    if (MIR.state !== "off") $("#mirrorLabel").textContent = "Paused for USB job...";
  } else if (e.state === "error") {
    if (MIR.state !== "off") { toast(e.detail || "Mirror stopped"); stopMirror("Lost the Flipper's screen"); }
  }
}
/* Flipper input: PRESS, then SHORT + RELEASE on a quick tap, or LONG after 500 ms and REPEAT while held */
function mirrorDown(key) {
  if (MIR.state !== "live" || MIR.held[key]) return;
  api.mirror_press(key, "press");
  const h = MIR.held[key] = { long: false };
  h.t1 = setTimeout(() => {
    h.long = true; api.mirror_press(key, "long");
    h.t2 = setInterval(() => api.mirror_press(key, "repeat"), 180);
  }, 500);
}
function mirrorUp(key) {
  const h = MIR.held[key];
  if (!h) return;
  clearTimeout(h.t1); clearInterval(h.t2);
  delete MIR.held[key];
  if (!h.long) api.mirror_press(key, "short");
  api.mirror_press(key, "release");
}
function screenshot() {
  if (!MIR.frame) return;
  const scale = 4, c = document.createElement("canvas");
  c.width = W * scale + 32; c.height = H * scale + 32;
  const x = c.getContext("2d");
  x.fillStyle = "#ff8c29"; x.fillRect(0, 0, c.width, c.height);
  x.fillStyle = "#000";
  for (let y = 0; y < H; y++) for (let i = 0; i < W; i++)
    if ((MIR.frame[(y >> 3) * W + i] >> (y & 7)) & 1) x.fillRect(16 + i * scale, 16 + y * scale, scale, scale);
  api.save_screenshot(c.toDataURL("image/png"));
  toast("Screenshot saved");
  $("#lcd").classList.add("flash"); setTimeout(() => $("#lcd").classList.remove("flash"), 500);
}

/* ---------- on-flipper sub sections: apps / files / backups ---------- */
const FM = { path: "/ext", entries: null, sub: "apps", bk: null, pick: null };
function setSub(name) {
  FM.sub = name;
  $$("#flSeg button").forEach((b) => b.classList.toggle("on", b.dataset.sub === name));
  moveInk($("#flSegInk"), $(`#flSeg button[data-sub="${name}"]`), false);
  $("#subApps").hidden = name !== "apps"; $("#subFiles").hidden = name !== "files"; $("#subBackups").hidden = name !== "backups";
  if (name === "files") loadFiles(FM.path);
  if (name === "backups") loadBackups();
}
function devReady() { return S.device.connected && !S.device.error; }
async function loadFiles(path) {
  FM.path = path;
  renderCrumbs();
  if (!devReady()) { $("#fmList").innerHTML = `<div class="empty-big"><b>No Flipper</b>Plug it in to browse its SD card.</div>`; return; }
  $("#fmList").innerHTML = `<div class="skeleton"><span class="blk"></span>Reading ${esc(path)}...</div>`;
  const r = await api.files_list(path);
  if (r.error) { $("#fmList").innerHTML = `<div class="empty-big"><b>Couldn't open it</b>${esc(r.error)}</div>`; return; }
  FM.entries = r.entries;
  renderFiles();
}
function renderCrumbs() {
  const parts = FM.path.split("/").filter(Boolean);
  $("#fmCrumbs").innerHTML = parts.map((p, i) => `<button data-p="/${parts.slice(0, i + 1).join("/")}">${esc(i === 0 ? "SD card" : p)}</button>`).join(`<span class="sep">/</span>`);
  $$("#fmCrumbs button").forEach((b) => (b.onclick = () => loadFiles(b.dataset.p)));
  $("#fmUp").disabled = FM.path === "/ext";
}
function renderFiles() {
  const list = $("#fmList");
  list.innerHTML = "";
  if (!FM.entries.length) { list.innerHTML = `<div class="empty-big"><b>Empty folder</b>Upload files here, or make a folder.</div>`; return; }
  FM.entries.forEach((e, idx) => {
    const p = `${FM.path}/${e.name}`;
    const row = document.createElement("div");
    row.className = "row fm"; row.style.animationDelay = Math.min(idx, 14) * 18 + "ms";
    row.innerHTML = `<div class="app-icon ${e.dir ? "folder" : "file"}"><i data-icon="${e.dir ? "folder" : "file"}"></i></div>
      <div style="min-width:0"><div class="row-title">${esc(e.name)}</div><div class="row-sub">${e.dir ? "folder" : fmtSize(e.size)}</div></div>
      <div class="row-actions"></div>`;
    const acts = $(".row-actions", row);
    acts.append(
      iconBtn("download", e.dir ? "Copy this folder to your PC" : "Copy this file to your PC", () => startJob("files", () => api.files_download(p))),
      iconBtn("edit", "Rename", () => fmRename(e, p)),
    );
    const rm = iconBtn("trash", "Delete", () => fmDelete(e, p)); rm.classList.add("danger"); acts.append(rm);
    if (e.dir) { row.style.cursor = "pointer"; row.onclick = () => loadFiles(p); }
    list.appendChild(row);
  });
  renderIcons(list);
}
async function fmRename(e, p) {
  const name = await promptDialog(`Rename ${e.dir ? "folder" : "file"}`, e.name, "Rename");
  if (!name || name === e.name) return;
  if (/[\\/]/.test(name)) { toast("Names can't contain slashes"); return; }
  const r = await api.files_rename(p, `${FM.path}/${name}`);
  if (r.error) toast(r.error); else { toast("Renamed"); loadFiles(FM.path); }
}
async function fmDelete(e, p) {
  const yes = await confirmDialog(`Delete ${e.name}?`,
    `This deletes <code>${esc(p)}</code>${e.dir ? " and <b>everything inside it</b>" : ""} from your Flipper's SD card. It can't be undone. Make a backup first if you're not sure.`, "Delete");
  if (yes) startJob("files", () => api.files_delete(p));
}
async function fmNewFolder() {
  const name = await promptDialog("New folder", "", "Create");
  if (!name) return;
  if (/[\\/]/.test(name)) { toast("Names can't contain slashes"); return; }
  const r = await api.files_mkdir(`${FM.path}/${name}`);
  if (r.error) toast(r.error); else loadFiles(FM.path);
}

const BK_DEFAULT = ["subghz", "nfc", "lfrfid", "infrared", "badusb", "ibutton", "apps_data", "music_player", "wav_player", "subghz_remote"];
async function loadBackups() {
  const r = await api.backups_list();
  FM.bk = r;
  const list = $("#bkList");
  list.innerHTML = "";
  if (!r.backups.length) { list.innerHTML = `<div class="empty-big"><b>No backups yet</b>Make one before a firmware update or an SD card swap.</div>`; return; }
  r.backups.forEach((b, idx) => {
    const dev = b.device || {}, row = document.createElement("div");
    row.className = "row"; row.style.animationDelay = Math.min(idx, 10) * 25 + "ms";
    row.innerHTML = `<div class="app-icon"><i data-icon="folder"></i></div>
      <div style="min-width:0"><div class="row-title">${esc(b.name)}</div>
      <div class="row-sub"><span>${b.files} files · ${fmtSize(b.bytes)} · ${ago(b.created || b.mtime)}</span>${dev.fw_name ? `<span class="badge fw">${esc(dev.fw_name)}</span>` : ""}</div></div>
      <div class="row-actions"></div>`;
    const acts = $(".row-actions", row);
    acts.append(iconBtn("upload", "Restore: copy these files back to the Flipper", () => bkRestore(b)),
      iconBtn("folder", "Show the zip", () => api.open_folder(b.path)));
    const rm = iconBtn("trash", "Delete this backup from your PC", async () => {
      if (await confirmDialog("Delete this backup?", `Deletes <code>${esc(b.name)}.zip</code> from this PC. Your Flipper isn't touched.`, "Delete")) {
        await api.backup_delete(b.path); loadBackups();
      }
    });
    rm.classList.add("danger"); acts.append(rm);
    list.appendChild(row);
  });
  renderIcons(list);
}
async function bkRestore(b) {
  if (!needDevice()) return;
  const yes = await confirmDialog(`Restore ${b.name}?`,
    `Copies ${b.files} files (${fmtSize(b.bytes)}) back to your Flipper's SD card, replacing files with the same name. Nothing else on the SD card is deleted. About ${Math.max(1, Math.round(b.bytes / 110000 / 60))} min.`, "Restore");
  if (yes) startJob("restore", () => api.backup_restore(b.path));
}
async function bkNew() {
  if (!needDevice()) return;
  const box = $("#bkPick");
  box.hidden = false;
  box.innerHTML = `<div class="skeleton"><span class="blk"></span>Measuring the folders on your SD card...</div>`;
  const r = await api.backup_folders();
  if (r.error) { box.innerHTML = `<div class="lib-note">${esc(r.error)}</div>`; return; }
  // never pre-pick system/rebuildable folders: Windows' own folder, dot-folders, firmware updates, apps, security keys
  const SKIP = ["System Volume Information", "update", "u2f", "apps", "apps_manifests"];
  r.folders = r.folders.filter((f) => f.name !== "System Volume Information");
  FM.pick = new Set(r.folders.filter((f) => !f.file && !f.name.startsWith(".") && !SKIP.includes(f.name) &&
    (BK_DEFAULT.includes(f.name) || f.size < 1048576)).map((f) => f.name));
  const render = () => {
    const chosen = r.folders.filter((f) => FM.pick.has(f.name));
    const bytes = chosen.reduce((s, f) => s + f.size, 0);
    box.innerHTML = `<div class="lib-note">Pick what to back up. Apps are left out by default: you can rebuild them from your Library.${r.folders.some((f) => f.name === "u2f") ? " <b>u2f</b> holds your security keys; if you include it, keep the zip private." : ""}</div>
      <div class="bk-grid">${r.folders.map((f) => `<button class="bk-item ${FM.pick.has(f.name) ? "on" : ""}" data-n="${esc(f.name)}"><span class="box">${FM.pick.has(f.name) ? pixelIcon("check") : ""}</span><span>${esc(f.name)}</span><small>${fmtSize(f.size)}</small></button>`).join("")}</div>
      <div class="bk-foot"><span>${chosen.length} picked · ${fmtSize(bytes)} · about ${Math.max(1, Math.round(bytes / 110000 / 60))} min</span>
      <button class="mini" id="bkCancel">Cancel</button><button class="mini primary" id="bkGo" ${chosen.length ? "" : "disabled"}>Start backup</button></div>`;
    $$(".bk-item", box).forEach((b) => (b.onclick = () => { FM.pick.has(b.dataset.n) ? FM.pick.delete(b.dataset.n) : FM.pick.add(b.dataset.n); render(); }));
    $("#bkCancel").onclick = () => { box.hidden = true; };
    $("#bkGo").onclick = () => { box.hidden = true; startJob("backup", () => api.backup_start([...FM.pick])); };
    renderIcons(box);
  };
  render();
}
function onFprog(e) {
  const p = $("#flProg");
  p.hidden = false;
  const pct = Math.min(100, Math.round(e.done / Math.max(1, e.total) * 100));
  const left = Math.max(0, e.total - e.done) / 110000;
  $("#flProgText").textContent = `${e.label}: ${pct}% · ${fmtSize(e.done)} of ${fmtSize(e.total)}` + (left > 5 ? ` · about ${left > 90 ? Math.round(left / 60) + " min" : Math.round(left) + "s"} left` : "");
  S.progress = Math.max(S.progress, STAGE_RANGE.send[0] + (e.done / Math.max(1, e.total)) * (1 - STAGE_RANGE.send[0]));
  if (S.busy) LCD.say(`${e.label} ${pct}%`);
}
function promptDialog(title, value, okLabel) {
  $("#cfInputWrap").hidden = false;
  $("#cfInput").value = value || "";
  const p = confirmDialog(title, "", okLabel).then((ok) => { $("#cfInputWrap").hidden = true; return ok ? $("#cfInput").value.trim() : null; });
  setTimeout(() => { $("#cfInput").focus(); $("#cfInput").select(); }, 40);
  return p;
}

/* ---------- app picker (repos with several apps) ---------- */
function openPicker(apps) {
  S.pickApps = apps;
  S.pickOn = new Set(apps.length <= 6 ? apps.map((a) => a.appid) : []);
  $("#pickSub").textContent = `This repo has ${apps.length} apps. Pick the ones you want.`;
  $("#pickSearch").value = "";
  renderPicker();
  $("#picker").hidden = false;
  LCD.say(`${apps.length} apps! Which ones?`); LCD.poke();
  setTimeout(() => $("#pickSearch").focus(), 50);
}
function renderPicker() {
  const q = $("#pickSearch").value.trim().toLowerCase(), list = $("#pickList");
  list.innerHTML = "";
  S.pickApps.filter((a) => !q || (a.name + a.appid + a.category + a.path).toLowerCase().includes(q)).forEach((a) => {
    const b = document.createElement("button");
    b.className = "pick" + (S.pickOn.has(a.appid) ? " on" : "");
    b.innerHTML = `<span class="box">${S.pickOn.has(a.appid) ? pixelIcon("check") : ""}</span>${appIcon(a)}
      <span style="min-width:0"><div class="pick-name">${esc(a.name)}</div><div class="pick-sub">${esc(a.category)} · ${esc(a.path)}</div></span>`;
    b.onclick = () => { S.pickOn.has(a.appid) ? S.pickOn.delete(a.appid) : S.pickOn.add(a.appid); renderPicker(); };
    list.appendChild(b);
  });
  const n = S.pickOn.size;
  $("#pickCount").textContent = `${n} selected`;
  $("#pickGo").textContent = n ? `Build ${n} app${n > 1 ? "s" : ""}` : "Build";
  $("#pickGo").disabled = !n;
  renderIcons(list);
}
function closePicker(answer) {
  $("#picker").hidden = true;
  api.answer_select(answer);
  if (answer && answer.length) LCD.say(`Building ${answer.length}...`);
}

/* ---------- log ---------- */
let logCount = 0;
function addLog(line) {
  const body = $("#logBody");
  const div = document.createElement("div");
  if (line.startsWith("==") || line.startsWith("####")) div.className = "h";
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
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 2200);
}
function callout(msg, kind, hintList) {
  const c = $("#callout");
  if (!msg) { c.hidden = true; return; }
  c.className = "callout" + (kind ? " " + kind : "");
  c.innerHTML = `<div>${esc(msg)}</div>` + (hintList || []).map((h, i) => `<div class="hint"><b>${esc(h.title)}</b>${esc(h.detail)}` +
    (h.try && h.try.length ? `<div class="try">${h.try.filter((id) => fwObj(id)).map((id) => `<button data-try="${id}">Try ${esc(fwObj(id).short)}</button>`).join("")}</div>` : "") + `</div>`).join("");
  $$("button[data-try]", c).forEach((b) => (b.onclick = () => { setFw(b.dataset.try, true); build(); }));
  c.hidden = false;
  if (hintList && hintList.length) setTimeout(() => c.scrollIntoView({ block: "nearest", behavior: "smooth" }), 80);
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
  if (["send", "install", "files", "backup", "restore"].includes(kind)) {
    setStage("send");
    if (MIR.state === "off") { LCD.play("radio", { fps: 4 }); LCD.say({ backup: "Backing up...", restore: "Restoring...", files: "Working on files..." }[kind] || "Beaming it over..."); }
    led("blue", true);
  }
  if (kind === "sdk") { LCD.play("laptop", { fps: 4 }); LCD.say(`Fetching the ${fwObj().short} SDK...`); }
  const res = await call();
  if (res && res.error) {
    S.busy = null; setBuildButton(false); led(null);
    callout(res.error);
    if (kind === "build") $("#inputWrap").classList.add("bad");
    LCD.play("cry"); LCD.say("Hmm, that didn't work", 4000);
    setTimeout(() => !S.busy && goIdle(), 6000);
    renderShelf();
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
  if (S.after !== "none" && !S.device.connected) toast("No Flipper plugged in: it'll build, then the install step will fail");
  S.results = []; S.batch = null;
  logCount = 0; $("#logBody").innerHTML = "";
  openLog(false);
  startJob("build", () => api.build(url, S.fw, S.after));
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
          fetch: `Fetching ${e.detail ? e.detail.split("/").pop() : "the repo"}...`, scan: "Looking for apps...",
          sdk: `Checking ${f.short} SDK...`, build: "Soldering bits...", send: "Beaming it over...",
        }[e.stage];
        if (say) LCD.say(S.batch && e.stage === "fetch" ? `App ${S.batch.index} of ${S.batch.total}...` : say);
        break;
      }
      case "progress": {
        const [a, b] = STAGE_RANGE.build;
        S.progress = Math.max(S.progress, a + (b - a) * e.value);
        if (S.stage === "build") LCD.say(`Compiling ${Math.round(e.value * 100)}%`);
        break;
      }
      case "result": S.results.push(e.result); renderShelf(); break;
      case "select": openPicker(e.apps); break;
      case "batch": S.batch = e; S.progress = 0; renderShelf(); break;
      case "device": onDevice(e.device); break;
      case "fmeta": {
        const a = FL.apps && FL.apps.find((x) => x.path === e.path);
        if (a) a.meta = e.meta;
        showFlProgress(e.done, e.total);
        // redraw at most ~3x a second while 200+ headers stream in
        if (S.tab === "flipper" && !FL.t) FL.t = setTimeout(() => { FL.t = null; renderFlipper(); }, 350);
        break;
      }
      case "mirror": onMirrorState(e); break;
      case "fprog": onFprog(e); break;
      case "fcheck_done":
        FL.checking = false; showFlProgress(null);
        if (e.error) toast(e.error);
        if (S.tab === "flipper") renderFlipper();
        break;
      case "update":
        $("#updatePill").hidden = false; $("#updateText").textContent = `Update ${e.version}`;
        $("#updatePill").onclick = () => api.open_url(e.url);
        break;
      case "watch":
        if (!S.busy && S.watch) { toast(`Changed: ${(e.changed || []).join(", ") || "files"}`); LCD.say("You saved! Rebuilding"); build(); }
        break;
      case "finished": finished(e); break;
    }
  }
  if (S.busy) paintBar(S.progress);
}

function finished(e) {
  S.busy = null; S.batch = null;
  setBuildButton(false);
  $("#picker").hidden = true;
  if (!FL.checking) $("#flProg").hidden = true;
  if (e.files_changed && FM.sub === "files" && S.tab === "flipper") loadFiles(FM.path);
  if (e.backup_done) { loadBackups(); toast(`Backup saved: ${e.backup_done.files} files`); }
  if (e.restored) toast(`Restored ${e.restored.files} files`);
  if (e.saved) toast("Saved. Opening the folder"), api.open_folder(e.saved);
  if (!e.ok && !e.cancelled && S.tab !== "build" && e.error) toast(e.error);
  if (e.quiet && e.ok) {     // file jobs: small celebration, no confetti or stage reset theatre
    resetStages(); callout(null); led("green", true);
    if (MIR.state === "off") { LCD.play("dj", { fps: 5 }); LCD.say(e.backup_done ? "Backup done!" : e.restored ? "All restored!" : "Done!", 4000); }
    setTimeout(() => { if (!S.busy) { led(null); if (MIR.state === "off") goIdle(); } }, 6000);
    return;
  }
  if (e.fw && (e.sdk || e.api)) { const f = fwObj(e.fw); if (f) { if (e.sdk) f.sdk = e.sdk; if (e.api) f.api = e.api; } renderFirmwares(); }
  if (e.recent) S.recent = e.recent;
  if (e.out) S.outDir = e.out;
  // the install step runs after the result cards were drawn: mark them now
  if (e.installed && !e.install_error) S.results.forEach((r) => { if (r.ok) r.installed = true; });
  if (S.tab === "library" || e.rebuild || e.built) loadLibrary();
  if (e.flipper_changed && S.tab === "flipper") setTimeout(loadFlipper, 400);

  if (e.cancelled) {
    led(null); resetStages(); callout(e.error && e.error !== "Cancelled." ? e.error : "Cancelled.", "info");
    LCD.play("sleep"); LCD.say("Okay, nap time", 3000);
    setTimeout(() => !S.busy && goIdle(), 5000);
    renderShelf();
    return;
  }
  if (!e.ok || (e.total && !e.built)) {
    const cur = $$(".stage").find((s) => s.classList.contains("active"));
    if (cur) cur.className = "stage fail";
    paintBar(Math.max(S.progress, .05), "err");
    led("red");
    callout(e.error || (e.total > 1 ? "None of the apps built." : "The app failed to build."), null, e.hints);
    LCD.play("cry"); LCD.say("Oh no... it broke");
    if (!(e.hints && e.hints.length)) openLog(true);
    renderShelf();
    setTimeout(() => { if (!S.busy) { led(null); goIdle(); } }, 14000);
    return;
  }
  // success (maybe partial)
  callout(null);
  if (e.sdk_only || e.send_only) resetStages();
  else { $$(".stage").forEach((s) => (s.className = "stage done okall")); paintBar(1, "ok"); }
  led("green", true);
  $("#lcd").classList.add("flash"); setTimeout(() => $("#lcd").classList.remove("flash"), 600);
  if (e.sdk_only) { LCD.play("dj", { fps: 5 }); LCD.say(`SDK ${e.sdk} ready!`, 4000); toast("SDK updated"); renderCompat(); }
  else if (e.send_only) { LCD.play("dj", { fps: 5 }); LCD.say(e.installed ? "Installed! Check apps on your Flipper" : "Sent! Check your Flipper", 5000); toast(e.installed ? "Installed on your Flipper" : "Sent to Flipper"); }
  else {
    const all = e.built === e.total;
    LCD.play("dj", { fps: 5 });
    let msg = all ? `Done! ${e.built} app${e.built > 1 ? "s" : ""} built` : `Built ${e.built} of ${e.total}`;
    if (e.installed && !e.install_error) msg += " + installed";
    LCD.say(msg, 7000);
    if (e.install_error) callout("Built, but installing to the Flipper failed: " + e.install_error, null,
      [{ title: "Couldn't talk to the Flipper", detail: "Plug it in, unlock it, leave any open app, and close qFlipper (only one program can use the USB port).", try: [] }]);
    else if (!all && e.hints) callout(`${e.total - e.built} app(s) failed.`, null, e.hints);
    if (all && !e.install_error) confetti(); else led("orange");
    renderShelf();
  }
  setTimeout(() => { if (!S.busy) { led(null); goIdle(); } }, 12000);
}

/* ---------- input: keyboard, d-pad, drag & drop, clipboard ---------- */
function press(name) {
  if (MIR.state === "live") return;   // while mirroring, the buttons drive the real Flipper (pointer handlers)
  const el = $(`[data-dp="${name}"]`);
  if (el) { el.classList.add("pressed"); setTimeout(() => el.classList.remove("pressed"), 140); }
  wake();
  if (name === "left") cycleFw(-1);
  else if (name === "right") cycleFw(1);
  else if (name === "up" || name === "down") openLog(name === "up");
  else if (name === "ok") { if (S.tab !== "build") setTab("build"); build(); }
  else if (name === "back") {
    if (!$("#confirm").hidden) confirmDialog.cancel();
    else if (!$("#picker").hidden) closePicker([]);
    else if (S.busy) cancel();
    else { LCD.poke(); LCD.say("Back? I'm right here.", 2500); }
  }
}

function bind() {
  $$("[data-dp]").forEach((b) => {
    b.onclick = () => press(b.dataset.dp);
    // mirror mode: real press / hold / release on the Flipper
    b.addEventListener("pointerdown", (ev) => { if (MIR.state === "live") { ev.preventDefault(); b.classList.add("pressed"); mirrorDown(b.dataset.dp); } });
    const up = () => { if (MIR.held[b.dataset.dp]) { b.classList.remove("pressed"); mirrorUp(b.dataset.dp); } };
    b.addEventListener("pointerup", up); b.addEventListener("pointerleave", up);
  });
  $("#mirrorBtn").onclick = toggleMirror;
  $("#shotBtn").onclick = screenshot;
  $$("#flSeg button").forEach((b) => (b.onclick = () => setSub(b.dataset.sub)));
  $("#fmUp").onclick = () => loadFiles(FM.path.split("/").slice(0, -1).join("/") || "/ext");
  $("#fmRefresh").onclick = () => loadFiles(FM.path);
  $("#fmNew").onclick = fmNewFolder;
  $("#fmUpload").onclick = () => { if (needDevice()) startJob("files", () => api.files_upload(FM.path)); };
  $("#bkNew").onclick = bkNew;
  $$(".tab").forEach((t) => (t.onclick = () => setTab(t.dataset.tab)));
  $$("#afterSeg button").forEach((b) => (b.onclick = () => setAfter(b.dataset.after, true)));
  $("#buildBtn").onclick = build;
  $("#lcd").onclick = () => { wake(); LCD.poke(); if (!S.busy) LCD.say(TIPS[Math.floor(Math.random() * TIPS.length)], 3000); };
  $("#logHead").onclick = () => openLog();
  $("#url").addEventListener("input", () => { renderPreview(); wake(); });
  $("#url").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); build(); } });
  $("#pasteBtn").onclick = async () => {
    const t = await api.clipboard();
    if (t) { $("#url").value = t; renderPreview(); toast("Pasted"); } else toast("No GitHub link on the clipboard");
  };
  $("#folderBtn").onclick = async () => {
    const p = await api.pick_folder();
    if (p) { $("#url").value = p; renderPreview(); toast("Folder picked. Press Build"); }
  };
  $("#sdkPill").onclick = () => startJob("sdk", () => api.update_sdk(S.fw));
  $("#devicePill").onclick = () => { api.refresh_device(); toast("Looking for your Flipper..."); };
  $("#repoBtn").onclick = () => api.open_url(S.repoUrl);
  $("#rebuildAll").onclick = () => startRebuild(null);
  $("#search").addEventListener("input", () => renderCatalog());
  $("#catRefresh").onclick = () => { S.catalog = null; loadCatalog(true); };
  $("#pickSearch").addEventListener("input", renderPicker);
  $("#flRefresh").onclick = () => { FL.apps = null; loadFlipper(); };
  $("#flStop").onclick = () => {
    if (S.busy) cancel(); else { api.flipper_check_stop(); toast("Stopping after this app"); }
  };
  $("#flSearch").addEventListener("input", () => renderFlipper());
  $("#pickAll").onclick = () => { S.pickApps.forEach((a) => S.pickOn.add(a.appid)); renderPicker(); };
  $("#pickNone").onclick = () => { S.pickOn.clear(); renderPicker(); };
  $("#pickCancel").onclick = () => closePicker([]);
  $("#pickGo").onclick = () => closePicker([...S.pickOn]);

  const MIR_KEYS = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "ok", Backspace: "back", Escape: "back" };
  document.addEventListener("keyup", (e) => { if (MIR.state === "live" && MIR_KEYS[e.key]) { e.preventDefault(); mirrorUp(MIR_KEYS[e.key]); } });
  document.addEventListener("keydown", (e) => {
    const typing = ["url", "search", "pickSearch", "flSearch", "cfInput"].includes(document.activeElement && document.activeElement.id);
    if (MIR.state === "live" && !typing && $("#confirm").hidden && MIR_KEYS[e.key]) {
      e.preventDefault();
      if (!e.repeat) mirrorDown(MIR_KEYS[e.key]);
      return;
    }
    if (e.key === "Enter" && !$("#confirm").hidden && document.activeElement && document.activeElement.id === "cfInput") { e.preventDefault(); $("#cfYes").click(); return; }
    if (e.key === "Escape") { press("back"); return; }
    if (!$("#confirm").hidden) return;   // the dialog's own buttons handle it
    if (!$("#picker").hidden) { if (e.key === "Enter" && S.pickOn.size) closePicker([...S.pickOn]); return; }
    if (e.ctrlKey && e.key.toLowerCase() === "l") { e.preventDefault(); setTab("build"); $("#url").focus(); $("#url").select(); return; }
    if (e.ctrlKey && e.key.toLowerCase() === "f") { e.preventDefault(); setTab("discover"); setTimeout(() => $("#search").focus(), 50); return; }
    if (typing) return;
    const map = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down", Enter: "ok" };
    if (map[e.key]) { e.preventDefault(); press(map[e.key]); }
  });

  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; $("#drop").classList.add("on"); });
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop").classList.remove("on"); } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); dragDepth = 0; $("#drop").classList.remove("on");
    const t = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    if (t && t.includes("github.com")) { setTab("build"); $("#url").value = t.split("\n")[0].trim(); renderPreview(); LCD.poke(); toast("Link dropped"); }
    else toast("That's not a GitHub link");
  });

  window.addEventListener("focus", async () => {
    wake();
    if (S.busy || $("#url").value.trim()) return;
    const t = await api.clipboard();
    if (t && t !== S.lastClip) { S.lastClip = t; $("#url").value = t; renderPreview(); toast("Link from clipboard"); }
  });
  window.addEventListener("resize", () => {
    setFw(S.fw, false); setAfter(S.after, false); moveInk($("#tabInk"), $(".tab.on"), true);
    moveInk($("#flSegInk"), $(`#flSeg button[data-sub="${FM.sub}"]`), false);
  });
}

/* ---------- boot ---------- */
async function boot() {
  renderIcons();
  initBar();
  const lcdReady = LCD.init();
  const info = await api.init();
  S.fws = info.firmwares; S.fw = info.fw; S.after = info.after || "none"; S.autoFw = info.auto_fw !== false;
  S.recent = info.recent || []; S.repoUrl = info.repo; S.outDir = info.out_dir;
  $("#ver").textContent = "v" + info.version;
  $("#libCount").textContent = info.library_count || "";
  renderFirmwares();
  setAfter(S.after, false);
  moveInk($("#tabInk"), $(".tab.on"), true);
  renderShelf();
  bind();
  if (info.device) onDevice(info.device);
  if (info.clipboard) { $("#url").value = info.clipboard; S.lastClip = info.clipboard; }
  await lcdReady;
  LCD.play("wake", { fps: 6, loop: false, onEnd: () => goIdle(info.clipboard ? "Got your link! Press OK" : "Hi! Paste a GitHub link") });
  await sleep(150);
  document.body.classList.remove("booting");
  if (info.clipboard) setTimeout(renderPreview, 1800);
  // fonts settle after first paint; re-measure the sliding indicators
  setTimeout(() => { setFw(S.fw, false); setAfter(S.after, false); moveInk($("#tabInk"), $(".tab.on"), true); }, 400);
  api.start_background();
}

window.fb = { onEvents, onFrame, LCD, S, MIR, FM };
let started = false;
function start(a) { if (started) return; started = true; api = a; boot(); }
window.addEventListener("pywebviewready", () => start(window.pywebview.api));
setTimeout(() => { if (!started) start(window.pywebview ? window.pywebview.api : mockApi()); }, 700);
})();
