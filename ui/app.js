/* Flipper Workbench UI. Talks to Python through window.pywebview.api (see gui.py). */
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
    init: async () => ({ version: "1.3.0", repo: "https://github.com/c3rb3ruszx/flipper-workbench",
      firmwares: fws.map(([id, label, short, sdk, api]) => ({ id, label, short, sdk, api })), fw: "momentum-dev", after: "none", auto_fw: true,
      recent: [{ url: "https://github.com/jblanked/FlipWiFi", fw: "momentum-dev", name: "FlipWiFi", repo: "jblanked/FlipWiFi", ok: true, t: 0 }],
      clipboard: "", out_dir: "C:\\output", device: { connected: false }, library_count: lib.length }),
    start_background: async () => { later(2500, [{ type: "device", device: { connected: true, port: "COM7", name: "Ochenko", fw: "momentum-dev", fw_name: "Momentum dev", version: "mntm-dev", commit: "d3f89dfe", api: "87.1" } }]);
      later(4000, [{ type: "update", version: "1.4.0", url: "https://github.com/c3rb3ruszx/flipper-workbench/releases" }]); return true; },
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
    set_fullscreen: async () => ({ ok: true }),
    files_view: async (path) => {
      if (/\.ir$/.test(path)) return { kind: "ir", title: "Infrared remote", size: 812, fields: [["Buttons", "2"], ["File type", "IR signals file"]],
        buttons: [{ name: "Power", detail: "NEC · address 0x7 · command 0x2", tx: "ir tx NEC 0x7 0x2" }, { name: "Vol_up", detail: "raw · 38 kHz · 4 samples", tx: "ir tx RAW F:38000 DC:33 9000 4500 560 560" }],
        note: "Send fires the button from your Flipper's IR LED, so point it at the device.", text: "Filetype: IR signals file\nVersion: 1\n#\nname: Power\ntype: parsed\n" };
      if (/\.sub$/.test(path)) return { kind: "subghz", title: "Sub-GHz recording", size: 20480, fields: [["Frequency", "433.92 MHz"], ["Modulation", "AM650 (OOK, 650 kHz)"], ["Protocol", "RAW"], ["Samples", "1,200"], ["Length", "1.84 s"]],
        wave: Array.from({ length: 300 }, (_, i) => (i % 2 ? -1 : 1) * (200 + ((i * 97) % 900))), text: "Filetype: Flipper SubGhz RAW File\n" };
      return { kind: "js", title: "JavaScript app", size: 120, fields: [["Lines", "3"]], text: '// demo\nlet x = 42;\nprint("Hello " + x);\n' };
    },
    files_save: async (p, t) => ({ ok: true, size: t.length }),
    ir_send: async () => ({ ok: true }),
    record_start: async () => ({ ok: true, max: 60 }),
    set_framed: async () => ({ ok: true }), save_screenshot_framed: async () => ({ path: "x.png" }),
    auto_backup_due: async () => ({ due: false }),
    auto_backup_plan: async () => ({ due: false }),
    cleanup_scan: async () => { await sleep(500); return { temp: [{ path: "/ext/apps/Tools/clock.fap.fwtmp", size: 4200 }], updates: [{ path: "/ext/update/f7-update-mntm-011", size: 2400000, files: 9 }] }; },
    cleanup_delete: async () => ({ ok: true }),
    devkit_templates: async () => ({ templates: [["hello", "Hello screen", "Draws text"], ["menu", "Menu app", "A menu"], ["js", "JavaScript app", "A .js app"]], categories: ["Tools", "Games"], default_dir: "D:\\projects", vscode: true }),
    devkit_create: async (n) => ({ folder: "D:\\projects\\my_app", appid: "my_app" }),
    flipper_ports: async () => ({ ports: [{ port: "COM7", name: "Ochenko" }] }), flipper_select: async () => ({ ok: true }),
    devboard_info: async () => ({ official: [{ channel: "release", version: "0.1.1" }], ports: [] }), devboard_ports: async () => ({ ports: [] }),
    devboard_use_official: async () => ({ source: "Black Magic 0.1.1 (release)", parts: [["0x1000", "bootloader.bin", 13248], ["0x8000", "partition-table.bin", 3072], ["0x10000", "blackmagic.bin", 904288]] }),
    ble_last: async () => ({}), set_auto_mirror: async () => ({ ok: true }), ble_mirror_start: async () => ({ ok: true }),
    ble_scan: async () => ({ error: "Bluetooth is off on this PC. Turn it on in Windows settings, then scan again." }),
    remote_start: async () => ({ url: "http://192.168.1.20:8780/?t=abc", qr: "", clients: [] }), remote_stop: async () => ({ ok: true }), remote_status: async () => ({ on: true, clients: [] }),
    mf_dict_load: async () => ({ keys: ["A0A1A2A3A4A5", "FFFFFFFFFFFF"], exists: true }),
    mf_dict_save: async (k) => ({ ok: true, count: k.length }),
    sd_map: async () => { await sleep(300); const f = (name, size, files, children = []) => ({ name, path: "/ext/" + name, size, files, children });
      return { free_kib: 15115776, types: [["gz", 23053548, 2], ["fap", 13122964, 271], ["ir", 8811452, 1968]],
        tree: { name: "SD card", path: "/ext", size: 97000000, files: 4472, children: [f("update", 24680000, 14), f("badusb", 21732000, 300), f("infrared", 19756000, 1968, [f("TVs", 9000000, 800), f("ACs", 5000000, 400)]), f("apps", 12850000, 271), f("apps_data", 10019000, 500), f("subghz", 5144000, 90)] } }; },
    fw_info: async () => { await sleep(400); return { power: { "charge.level": "100", "charge.state": "charged" }, targets: {
      official: { id: "official", family: "Official", channel: "release", version: "1.5.1", age: "25 days ago", changelog: "- Lots of fixes" },
      "momentum-dev": { id: "momentum-dev", family: "Momentum", channel: "dev", version: "d3f89dfe", age: "82 days ago", changelog: "" },
      momentum: { id: "momentum", family: "Momentum", channel: "release", version: "mntm-012", age: "312 days ago", changelog: "" } } }; },
    fw_prepare: async () => { later(1200, [{ type: "finished", ok: true, quiet: true, send_only: true, fw_ready: { remote: "/ext/update/x", family: "Official", version: "1.5.1" } }]); return { ok: true }; },
    fw_discard: async () => ({ ok: true }),
    fw_bundled: async () => ({ apps: ["game_2048.fap"] }),
    irdb_index: async () => ({ count: 3, items: [["TVs/Samsung/Samsung_TV.ir", 2400], ["ACs/LG/LG_AC.ir", 1800], ["_Converted_/Fans/Dyson.ir", 900]] }),
    irdb_get: async (p) => mockApi().files_view("x.ir"),
    irdb_install: async () => ({ ok: true, path: "/ext/infrared/Samsung_TV.ir" }),
    ir_files: async () => ({ files: ["/ext/infrared/LG.ir"] }),
    music_convert: async () => ({ title: "Nokia", name: "Nokia", bpm: 180, notes: 13, fmf: "Filetype: Flipper Music Format", preview: [[1318.5, 167], [1174.7, 167], [740, 333]] }),
    music_pick: async () => ({ cancelled: true }), music_save: async () => ({ ok: true, path: "/ext/music_player/Nokia.fmf" }),
    nfc_make: async (items, tag) => { await sleep(80); const cap = { NTAG213: 144, NTAG215: 496, NTAG216: 872 }[tag];
      const bytes = items.reduce((s, it) => s + 12 + JSON.stringify(it).length, 0);
      if (bytes > cap) return { error: `That needs ${bytes} bytes but an ${tag} holds ${cap}.` };
      return { nfc: "Filetype: Flipper NFC device", tag, uid: "04 A1 B2 C3 D4 E5 86", bytes, capacity: cap,
        does: items.map((it) => ({ url: `Opens ${it.url}`, wifi: `Offers to join the Wi-Fi network "${it.ssid}"`, text: "Shows the text" }[it.kind] || `Does a ${it.kind} thing`)) }; },
    nfc_save: async () => ({ ok: true, path: "/ext/nfc/Tag.nfc" }), nfc_export: async () => ({ ok: true }),
    anim_list: async () => ({ made: [] }), anim_pick: async () => ({ cancelled: true }),
    packs_list: async () => ({ packs: [{ id: "a", name: "Akira", author: "Kuronons", anims: 1, icons: 3, previews: [] }] }),
    sync_pairs: async () => ({ pairs: [{ local: "D:\\badusb", remote: "/ext/badusb", mode: "both", delete: false }] }),
    sync_save_pairs: async () => ({ ok: true }),
    sync_plan: async () => ({ same: 4, steps: [["upload", "new.txt", 120], ["download", "fromflipper.txt", 300], ["conflict", "both.txt", 90]] }),
    set_auto_backup: async () => ({ ok: true }),
    record_stop: async () => ({ path: "rec.gif", frames: 40, seconds: 3.2 }),
    cli_stop: async () => { mockApi.cstop = true; return { ok: true }; },
    cli_run: async (cmd) => {
      mockApi.cstop = false;
      if (cmd === "log") {
        later(300, [{ type: "cli", text: "Current log level: none\nPress CTRL+C to stop...\n" }]);
        const t = setInterval(() => { if (mockApi.cstop) { clearInterval(t); emit([{ type: "cli", done: true, ended: "stopped" }]); } }, 200);
        return { ok: true };
      }
      const text = cmd === "help" ? "Available commands:\nbt            loader        crypto\nlog           device_info   storage\n"
        : cmd === "uptime" ? "Uptime: 11h23m49s\n" : `could not find command \`${cmd}\`, try \`help\`\n`;
      later(400, [{ type: "cli", text }, { type: "cli", done: true, ended: "done" }]);
      return { ok: true };
    },
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
const isLocalPath = (t) => /^[A-Za-z]:[\\/]|^\\\\|^(\/(Users|home|Volumes)\/|~\/)/.test((t || "").trim());

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
    setTimeout(autoBackup, 2500);   // after the hello, and after any SD reads the tabs start
    MIR.userOff = false;
    setTimeout(autoMirror, 3500);
  } else if (was.connected && !S.device.connected && !S.busy) {
    LCD.say("Bye Flipper!", 2500);
  }
  renderCompat();
  btButton();
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
  if (n >= 1073741824) return (n / 1073741824).toFixed(2) + " GB";
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
function setDisc(mode) {
  S.disc = mode;
  $$("#discSeg button").forEach((b) => b.classList.toggle("on", b.dataset.disc === mode));
  moveInk($("#discInk"), $(`#discSeg button[data-disc="${mode}"]`), false);
  $("#search").placeholder = mode === "ir" ? "Search 8,900 IR remotes: brand, model or device" : "Search the official Flipper Apps Catalog";
  S.catFilter = "All";
  if (mode === "ir") loadIrdb(); else loadCatalog();
}
async function loadIrdb(force) {
  if (S.irdb && !force) { renderIrdb(); return; }
  $("#catFilter").innerHTML = "";
  $("#catList").innerHTML = `<div class="skeleton"><span class="blk"></span>Loading the IR remote library...</div>`;
  const data = await api.irdb_index(!!force);
  if (data.error) { $("#catList").innerHTML = `<div class="empty-big"><b>Couldn't load it</b>${esc(data.error)}</div>`; return; }
  S.irdb = data;
  if (S.disc === "ir") renderIrdb();
}
function irLabel(cat) { return cat === "_Converted_" ? "Converted" : cat.replace(/_/g, " "); }
function renderIrdb() {
  const data = S.irdb; if (!data) return;
  const q = $("#search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const counts = {};
  data.items.forEach(([p]) => { const c = p.split("/")[0]; counts[c] = (counts[c] || 0) + 1; });
  const cats = ["All", ...Object.keys(counts).sort((a, b) => (a === "_Converted_") - (b === "_Converted_") || a.localeCompare(b))];
  $("#catFilter").innerHTML = cats.map((c) => `<button class="fchip ${S.catFilter === c ? "on" : ""}" data-c="${esc(c)}">${esc(c === "All" ? "All" : irLabel(c))}${c !== "All" ? `<small>${counts[c]}</small>` : ""}</button>`).join("");
  $$("#catFilter .fchip").forEach((b) => (b.onclick = () => { S.catFilter = b.dataset.c; renderIrdb(); }));
  const hits = data.items.filter(([p]) => (S.catFilter === "All" || p.startsWith(S.catFilter + "/")) &&
    (!q.length || q.every((w) => p.toLowerCase().replace(/_/g, " ").includes(w))));
  $("#catNote").innerHTML = `${hits.length.toLocaleString()} of ${data.count.toLocaleString()} remotes from the community <b>Flipper-IRDB</b>. Open one to see its buttons and try them before installing.`;
  const list = $("#catList");
  list.innerHTML = "";
  hits.slice(0, 150).forEach(([p, size], idx) => {
    const parts = p.split("/"), file = parts.pop(), brand = parts.slice(1).join(" / ");
    const row = document.createElement("div");
    row.className = "row"; row.style.animationDelay = Math.min(idx, 12) * 20 + "ms"; row.style.cursor = "pointer";
    row.innerHTML = `<div class="app-icon cat">IR</div>
      <div style="min-width:0"><div class="row-title">${esc(file.replace(/\.ir$/i, "").replace(/_/g, " "))}</div>
      <div class="row-sub"><span class="badge fw">${esc(irLabel(parts[0]))}</span><span>${esc(brand)} · ${fmtSize(size)}</span></div></div><div class="row-actions"></div>`;
    const inst = document.createElement("button"); inst.className = "mini primary"; inst.textContent = "Install";
    inst.onclick = (e) => { e.stopPropagation(); irdbInstall(p); };
    $(".row-actions", row).append(inst);
    row.onclick = () => openViewer(p, size, "irdb");
    list.appendChild(row);
  });
  if (hits.length > 150) list.insertAdjacentHTML("beforeend", `<div class="lib-note" style="text-align:center;padding:8px">Showing 150. Add a brand or model to narrow it down.</div>`);
  if (!hits.length) list.innerHTML = `<div class="empty-big"><b>No matches</b>Try just the brand, or another category.</div>`;
}
async function irdbInstall(p) {
  if (!needDevice()) return;
  toast("Installing...");
  const r = await api.irdb_install(p);
  toast(r.error ? r.error : `Installed: ${r.path}`);
  if (!r.error && FM.sub === "files" && FM.path === "/ext/infrared") loadFiles(FM.path);
}
async function loadCatalog(force) {
  if (S.disc === "ir") { loadIrdb(force); return; }
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
  if (S.disc === "ir") { renderIrdb(); return; }
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
function vNewer(a, b) {            // is version a newer than b? ("1.10" > "1.9")
  const pa = String(a).match(/\d+/g) || [], pb = String(b).match(/\d+/g) || [];
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = +(pa[i] || 0), y = +(pb[i] || 0);
    if (x !== y) return x > y;
  }
  return false;
}
function catUpdate(app) {          // newer release of this app in Flipper's official catalog
  if (!S.catalog || !S.catalog.apps || !app.meta || !app.meta.version) return null;
  // apps that ship with the firmware are updated with it; on custom firmware, if we can't tell, say nothing
  if (S.bundled === undefined) return null;
  if (S.bundled ? S.bundled.has(app.file.toLowerCase()) : !/^official/.test(S.device.fw || "")) return null;
  const id = app.file.replace(/\.fap$/i, "");
  const c = S.catalog.apps.find((x) => x.id === id);
  return c && c.version && vNewer(c.version, app.meta.version) ? c : null;
}
async function catDoUpdate(app, c) {
  const m = app.meta || {};
  if (!(await confirmDialog(`Update ${m.name || app.file}?`,
    `Flipper's catalog has <b>v${esc(c.version)}</b> (you have v${esc(m.version)}). I'll build it for your firmware and install it over the one on your Flipper.`, "Update"))) return;
  setTab("build");
  $("#url").value = c.url; renderPreview();
  setAfter("install", true);
  build();
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
  if (!S.catalog) api.catalog(false).then((d) => { if (d && !d.error && !S.catalog) { S.catalog = d; if (FL.apps) renderFlipper(); } });
  const bkey = `${S.device.fw}|${S.device.version}|${S.device.commit}`;
  if (S.bundledKey !== bkey) {
    S.bundledKey = bkey; S.bundled = undefined;
    api.fw_bundled().then((r) => { if (S.bundledKey === bkey) { S.bundled = r && r.apps ? new Set(r.apps) : null; if (FL.apps) renderFlipper(); } });
  }
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
    const m = a.meta || {}, lib = libMatch(a), upd = flUpdate(a), cup = upd ? null : catUpdate(a);
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
      (cup ? `<span class="badge old" title="Flipper's official catalog has a newer version">v${esc(cup.version)} in catalog</span>` : "") +
      `<span>${fmtSize(a.size)} · ${esc(a.file)}</span></div></div><div class="row-actions"></div>`;
    const acts = $(".row-actions", row);
    const openBtn = iconBtn("play", "Open it on the Flipper", async () => {
      LCD.say("Opening it on your Flipper", 2500); LCD.poke();
      const r = await api.flipper_open(a.path);
      toast(r.error ? r.error : "Opened on your Flipper");
    });
    openBtn.classList.add("go");
    acts.append(openBtn);
    if (cup) acts.append(iconBtn("download", `Update to v${cup.version} from Flipper's catalog`, () => catDoUpdate(a, cup)));
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
async function toggleMirror(auto) {
  if (MIR.state !== "off") { MIR.userOff = true; stopMirror(); return; }
  if (auto !== true) MIR.userOff = false;
  if (!S.device.connected || S.device.error) { needDevice(); return; }
  MIR.state = "starting"; MIR.ble = false; btButton();
  $("#mirrorLabel").textContent = "Connecting...";
  const r = await api.mirror_start();
  if (r.error) { MIR.state = "off"; $("#mirrorLabel").textContent = ""; toast(r.error); btButton(); }
}
function stopMirror(msg) {
  setLcdFull(false);
  MIR.ble = false; setTimeout(btButton, 0);
  if (!S.device.connected) { $("#deviceText").textContent = "No Flipper"; $("#devicePill").classList.remove("on"); }
  MIR.state = "off";
  api.mirror_stop();
  Object.values(MIR.held).forEach((h) => { clearTimeout(h.t1); clearInterval(h.t2); });
  MIR.held = {};
  document.body.classList.remove("mirroring");
  $("#mirrorLabel").textContent = "";
  $("#shotBtn").hidden = true;
  recStop(); $("#recBtn").hidden = true; $("#frameBtn").hidden = true;
  $("#deviceFw").textContent = fwObj().short;
  $("#legend").innerHTML = `<span><b>&#9664; &#9654;</b> firmware</span><span><b>OK</b> build</span><span><b>&#8617;</b> cancel</span>`;
  LCD.endLive();
  goIdle(msg || "Back to building!");
}
function onMirrorState(e) {
  if (e.state === "live") {
    MIR.state = "live";
    if (e.detail === "Bluetooth") { MIR.ble = true; if (!S.device.connected) { $("#deviceText").textContent = "Bluetooth · mirror only"; $("#devicePill").classList.add("on"); } }
    setTimeout(btButton, 0);
    document.body.classList.add("mirroring");
    $("#mirrorLabel").textContent = "";
    $("#shotBtn").hidden = false; $("#recBtn").hidden = false; $("#frameBtn").hidden = false;
    $("#deviceFw").textContent = "LIVE";
    $("#legend").innerHTML = `<span><b>d-pad / arrows</b> navigate</span><span><b>OK / Enter</b> select</span><span><b>&#8617; / Backspace</b> back</span><span><b>hold</b> long press</span>`;
    clearTimeout(S.idleTimer); clearTimeout(S.sleepTimer);
  } else if (e.state === "connecting") {
    if (MIR.state !== "off") $("#mirrorLabel").textContent = e.detail || "Connecting...";
    btButton();
  } else if (e.state === "paused") {
    if (MIR.state !== "off") $("#mirrorLabel").textContent = "Screen paused while Workbench uses USB...";
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
/* GIF recording: Python keeps the frames, the button shows the time */
const REC = { on: false, t0: 0, timer: null, max: 60 };
function recToggle() { if (REC.on) recStop(); else recStart(); }
async function recStart() {
  const r = await api.record_start();
  if (r.error) { toast(r.error); return; }
  REC.on = true; REC.t0 = Date.now(); REC.max = r.max || 60;
  $("#recBtn").classList.add("on"); $("#recBtn").title = "Stop recording and save the GIF";
  const tick = () => {
    const s = Math.floor((Date.now() - REC.t0) / 1000);
    $("#recTime").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    if (s >= REC.max) recStop();
  };
  tick(); REC.timer = setInterval(tick, 250);
}
async function recStop() {
  if (!REC.on) return;
  REC.on = false; clearInterval(REC.timer);
  $("#recBtn").classList.remove("on"); $("#recBtn").title = "Record the Flipper's screen as a GIF (up to 60 s)";
  toast("Saving GIF...");
  const r = await api.record_stop(true);
  if (r.error) toast(r.error);
  else if (r.path) toast(`GIF saved (${r.seconds}s)`);
}
function screenshot() {
  if (!MIR.frame) return;
  if (S.framed) {
    api.save_screenshot_framed().then((r) => toast(r.error || "Framed screenshot saved"));
    $("#lcd").classList.add("flash"); setTimeout(() => $("#lcd").classList.remove("flash"), 500);
    return;
  }
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
  $("#subCli").hidden = name !== "cli";
  $("#subTools").hidden = name !== "tools";
  if (name === "tools") toolsShow();
  if (name === "files") loadFiles(FM.path);
  if (name === "backups") loadBackups();
  if (name === "cli") cliShow();
}

/* ---------- Flipper CLI: one command at a time over USB, output streamed back ---------- */
const CLI = { running: false, hist: [], hi: 0, greeted: false };
const CLI_QUICK = ["help", "neofetch", "device_info", "uptime", "free", "storage info /ext", "loader list", "log"];
// commands that change or delete things, or transmit, get a confirm first
const CLI_RISKY = /^(storage\s+(remove|format|write|write_chunk|copy|rename|mkdir|extract)|update|power\s|factory_reset|crypto\s|subghz\s+tx|ir\s+tx|nfc\s+(emulate|write)|rfid\s+(emulate|write)|ikey\s+(emulate|write)|date\s+\S)/i;
function cliPrint(text, cls) {
  const out = $("#cliOut"), atEnd = out.scrollHeight - out.scrollTop - out.clientHeight < 40;
  const span = document.createElement("span");
  if (cls) span.className = cls;
  span.textContent = text;
  out.appendChild(span);
  while (out.childNodes.length > 4000) out.removeChild(out.firstChild);   // keep long `log` sessions light
  if (atEnd) out.scrollTop = out.scrollHeight;
}
function cliShow() {
  if (!CLI.greeted) {
    CLI.greeted = true;
    cliPrint("Your Flipper's own command line. Type a command and press Enter; try help.\n" +
             "Up/Down recall earlier commands. Stop (or Ctrl+C) ends commands that keep running, like log.\n\n", "note");
    $("#cliChips").innerHTML = CLI_QUICK.map((c) => `<button class="fchip" data-c="${esc(c)}">${esc(c)}</button>`).join("");
    $$("#cliChips button").forEach((b) => (b.onclick = () => cliRun(b.dataset.c)));
  }
  if (!CLI.running) setTimeout(() => $("#cliInput").focus(), 50);
}
function cliBusy(on) {
  CLI.running = on;
  $("#cliInput").disabled = on;
  $("#cliStop").hidden = !on;
  $$("#cliChips button").forEach((b) => (b.disabled = on));
  if (!on) $("#cliInput").focus();
}
async function cliRun(cmd) {
  cmd = (cmd || "").trim();
  if (!cmd || CLI.running) return;
  if (!CLI.hist.length || CLI.hist[CLI.hist.length - 1] !== cmd) CLI.hist.push(cmd);
  CLI.hi = CLI.hist.length;
  $("#cliInput").value = "";
  if (/^(clear|cls)$/i.test(cmd)) { $("#cliOut").innerHTML = ""; return; }
  if (!devReady()) { cliPrint(`>: ${cmd}\n`, "cmd"); cliPrint("No Flipper. Plug it in over USB and close qFlipper.\n\n", "err"); return; }
  if (CLI_RISKY.test(cmd) && !(await confirmDialog("Run this on your Flipper?",
      `<code>${esc(cmd)}</code> can change or delete things on your Flipper, or transmit. Run it?`, "Run it"))) return;
  cliPrint(`>: ${cmd}\n`, "cmd");
  cliBusy(true);
  const r = await api.cli_run(cmd);
  if (r && r.error) { cliPrint(r.error + "\n\n", "err"); cliBusy(false); }
}
function onCli(e) {
  if (e.text && e.log) {                 // live log: whole lines only, filtered, coloured by level
    CLI.partial = (CLI.partial || "") + e.text;
    const lines = CLI.partial.split("\n"); CLI.partial = lines.pop();
    const f = ($("#logFilter").value || "").trim().toLowerCase();
    for (const ln of lines) {
      if (f && !ln.toLowerCase().includes(f)) continue;
      const m = /\[([EWIDT])\]/.exec(ln);
      cliPrint(ln + "\n", m ? { E: "lvl-e", W: "lvl-w", D: "lvl-d", T: "lvl-d" }[m[1]] : undefined);
    }
  } else if (e.text) cliPrint(e.text);
  if (e.done && e.log) {
    CLI.follow = false; $("#logFollow").classList.remove("on"); $("#logFollow").lastChild.textContent = "Follow log";
    if (e.error) cliPrint(e.error + "\n", "err"); else cliPrint("^C\n\n", "note");
    return;
  }
  if (e.done) {
    if (e.error) cliPrint(e.error + "\n", "err");
    else if (e.ended === "stopped") cliPrint("^C\n", "note");
    else if (e.ended === "timeout") cliPrint("Stopped after 5 minutes.\n", "note");
    cliPrint("\n");
    cliBusy(false);
  }
}
function cliKey(e) {
  if (e.key === "Enter") { e.preventDefault(); cliRun($("#cliInput").value); }
  else if (e.key === "ArrowUp" && CLI.hist.length) { e.preventDefault(); CLI.hi = Math.max(0, CLI.hi - 1); $("#cliInput").value = CLI.hist[CLI.hi]; }
  else if (e.key === "ArrowDown" && CLI.hist.length) {
    e.preventDefault(); CLI.hi = Math.min(CLI.hist.length, CLI.hi + 1); $("#cliInput").value = CLI.hist[CLI.hi] || "";
  }
}

/* ---------- full screen mirror (click the live screen, like qFlipper) ---------- */
function setLcdFull(on) {
  on = !!on && MIR.state === "live";
  if (on === document.body.classList.contains("lcd-full")) return;
  document.body.classList.toggle("lcd-full", on);
  api.set_fullscreen(on);
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
    row.style.cursor = "pointer";
    row.onclick = (ev) => { if (ev.target.closest(".row-actions")) return; if (e.dir) loadFiles(p); else openViewer(p, e.size); };
    list.appendChild(row);
  });
  renderIcons(list);
}
/* ---------- file viewer (captures in plain language) + script editor ---------- */
const VW = { path: null, data: null, dirty: false, isNew: false };
const ED_JS = new Set("const let var function return if else for while do break continue new typeof instanceof in of true false null undefined this class extends import export from require try catch finally throw switch case default async await".split(" "));
const ED_DUCKY = /^(REM|REM_BLOCK|END_REM|STRING|STRINGLN|DELAY|DEFAULT_DELAY|DEFAULTDELAY|DEFAULT_STRING_DELAY|STRING_DELAY|REPEAT|GUI|WINDOWS|COMMAND|CTRL|CONTROL|ALT|OPTION|SHIFT|ENTER|ESC|ESCAPE|TAB|SPACE|BACKSPACE|DELETE|UP|DOWN|LEFT|RIGHT|UPARROW|DOWNARROW|LEFTARROW|RIGHTARROW|HOME|END|PAGEUP|PAGEDOWN|INSERT|CAPSLOCK|NUMLOCK|SCROLLLOCK|PRINTSCREEN|MENU|APP|F\d{1,2}|ID|HOLD|RELEASE|ALTCHAR|ALTSTRING|ALTCODE|SYSRQ|WAIT_FOR_BUTTON_PRESS|MEDIA|BLE_ID|BT_ID|CTRL-ALT|CTRL-SHIFT|ALT-SHIFT|ALT-GUI|GUI-SHIFT|CTRL-GUI)\b/;
function edKind(path) { return /\.js$/i.test(path) ? "js" : (/\/badusb\//i.test(path) || /\.(ducky|bad)$/i.test(path)) ? "ducky" : "text"; }
function edHighlight(text, kind) {
  if (kind === "ducky") {
    return text.split("\n").map((ln) => {
      const m = ln.match(/^(\s*)(\S+)(.*)$/);
      if (!m) return esc(ln);
      if (/^REM\b/.test(m[2])) return `<span class="c">${esc(ln)}</span>`;
      if (ED_DUCKY.test(m[2])) {
        const rest = /^STRING/.test(m[2]) ? `<span class="s">${esc(m[3])}</span>` : esc(m[3]).replace(/\b(\d+)\b/g, '<span class="n">$1</span>');
        return `${esc(m[1])}<span class="k">${esc(m[2])}</span>${rest}`;
      }
      return esc(ln);
    }).join("\n");
  }
  if (kind === "js") {
    const re = /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|\b(\d+(?:\.\d+)?)\b|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$]*)/g;
    let out = "", last = 0, m;
    while ((m = re.exec(text))) {
      out += esc(text.slice(last, m.index)); last = re.lastIndex;
      if (m[1]) out += `<span class="c">${esc(m[1])}</span>`;
      else if (m[2]) out += `<span class="s">${esc(m[2])}</span>`;
      else if (m[3]) out += `<span class="n">${m[3]}</span>`;
      else if (m[4]) out += ED_JS.has(m[4]) ? `<span class="k">${m[4]}</span>` : `<span class="f">${m[4]}</span>`;
      else out += ED_JS.has(m[5]) ? `<span class="k">${m[5]}</span>` : m[5];
    }
    return out + esc(text.slice(last));
  }
  return esc(text);
}
function edRender() {
  const t = $("#edText").value;
  $("#edHl").innerHTML = edHighlight(t, edKind(VW.path)) + "\n";
  $("#edHl").scrollTop = $("#edText").scrollTop; $("#edHl").scrollLeft = $("#edText").scrollLeft;
}
function vwFields(fields) {
  return `<dl class="vw-fields">${fields.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
}
function vwWave(wave) {
  const c = document.createElement("canvas"); c.className = "vw-wave";
  requestAnimationFrame(() => {
    const w = c.clientWidth, h = c.clientHeight; c.width = w * devicePixelRatio; c.height = h * devicePixelRatio;
    const x = c.getContext("2d"); x.scale(devicePixelRatio, devicePixelRatio);
    const total = wave.reduce((a, b) => a + Math.abs(b), 0) || 1;
    x.strokeStyle = "#ff8200"; x.lineWidth = 1.2; x.beginPath();
    let t = 0, y0 = h * 0.72, y1 = h * 0.22; x.moveTo(0, y0);
    for (const v of wave) {
      const y = v > 0 ? y1 : y0, nx = (t + Math.abs(v)) / total * w;
      x.lineTo(t / total * w, y); x.lineTo(nx, y); t += Math.abs(v);
    }
    x.stroke();
  });
  return c;
}
async function openViewer(path, size, src) {
  VW.path = path; VW.data = null; VW.isNew = false; VW.src = src || "sd";
  $("#vwInstall").hidden = true; $("#vwEditIr").hidden = true;
  $("#vwTitle").textContent = path.split("/").pop();
  $("#vwSub").textContent = path;
  $("#vwEdit").hidden = true; $("#vwDownload").hidden = false;
  $("#vwEditor").hidden = true; $("#vwBody").hidden = false;
  $("#vwBody").innerHTML = `<div class="skeleton"><span class="blk"></span>Reading ${esc(path.split("/").pop())}...</div>`;
  $("#viewer").hidden = false;
  const r = VW.src === "irdb" ? await api.irdb_get(path) : await api.files_view(path, size || 0);
  if (VW.path !== path) return;
  if (VW.src === "irdb") { $("#vwDownload").hidden = true; $("#vwInstall").hidden = !!r.error; $("#vwInstall").onclick = () => irdbInstall(path); }
  VW.data = r;
  const body = $("#vwBody");
  if (r.error) { body.innerHTML = `<div class="vw-empty"><b>Couldn't open it</b><br>${esc(r.error)}</div>`; return; }
  if (r.too_big) { body.innerHTML = `<div class="vw-empty">${fmtSize(r.size)} is too big to show here. Download it instead.</div>`; return; }
  if (r.binary) { body.innerHTML = `<div class="vw-empty">This is a binary file (${fmtSize(r.size)}), not text. Download it to open it on your PC.</div>`; return; }
  $("#vwTitle").textContent = r.title || path.split("/").pop();
  $("#vwSub").textContent = VW.src === "irdb" ? `Flipper-IRDB · ${path}` : `${path} · ${fmtSize(r.size)}`;
  $("#vwEdit").hidden = VW.src === "irdb";
  $("#vwEditIr").hidden = !(r.kind === "ir" && VW.src !== "irdb");
  body.innerHTML = vwFields(r.fields || []);
  if (r.note) body.insertAdjacentHTML("beforeend", `<div class="vw-note">${esc(r.note)}</div>`);
  if (r.wave && r.wave.length) { body.insertAdjacentHTML("beforeend", `<div class="vw-sec">Signal</div>`); body.appendChild(vwWave(r.wave)); }
  if (r.buttons) {
    body.insertAdjacentHTML("beforeend", `<div class="vw-sec">Buttons</div><div class="vw-btns" id="vwBtns"></div>`);
    r.buttons.forEach((b) => {
      const row = document.createElement("div"); row.className = "vw-btn";
      row.innerHTML = `<b>${esc(b.name)}</b><span>${esc(b.detail || b.type)}</span>`;
      if (b.tx) {
        const go = document.createElement("button"); go.className = "mini"; go.innerHTML = `<i data-icon="play"></i>Send`;
        go.title = "Fire this button from the Flipper's IR LED";
        go.onclick = async () => {
          if (!devReady()) { needDevice(); return; }
          go.disabled = true;
          const res = await api.ir_send(b.tx);
          go.disabled = false;
          toast(res.error ? `IR: ${res.error}` : `Sent ${b.name}`);
        };
        row.appendChild(go);
      }
      $("#vwBtns").appendChild(row);
    });
  }
  if (r.kind === "nfc" && VW.src !== "irdb") {
    const cmp = document.createElement("button"); cmp.className = "mini"; cmp.textContent = "Compare with another dump";
    cmp.onclick = () => nfcCompare(path, r);
    body.appendChild(cmp);
  }
  if (r.blocks) {
    body.insertAdjacentHTML("beforeend", `<details class="vw-details"><summary>Show all ${r.blocks.length} ${esc(r.blocks[0][0].split(" ")[0].toLowerCase())}s</summary><pre class="vw-raw">${r.blocks.map(([k, v]) => `${esc(k.padEnd(10))} ${esc(v)}`).join("\n")}</pre></details>`);
  }
  if (r.kind === "ir" && VW.src === "irdb") body.insertAdjacentHTML("afterbegin", `<div class="vw-note">From the community IR library. Try a button with Send (your Flipper must be plugged in), then install it to keep it.</div>`);
  const textKind = ["js", "badusb", "text"].includes(r.kind);
  body.insertAdjacentHTML("beforeend", textKind
    ? `<pre class="vw-raw">${edHighlight(r.text, edKind(path))}</pre>`
    : `<details class="vw-details"><summary>Show the file as text</summary><pre class="vw-raw">${esc(r.text)}</pre></details>`);
  renderIcons(body);
  if (textKind) openEditor();
}
/* IR remote editing: rename, reorder, delete, merge buttons from another remote */
function irSerialize(signals, text) {
  const ver = (/^Version:\s*(\S+)/m.exec(text || "") || [])[1] || "1";
  return `Filetype: IR signals file\nVersion: ${ver}\n` + signals.map((s) => "#\n" + s.map(([k, v]) => `${k}: ${v}`).join("\n")).join("\n") + "\n";
}
function irName(s) { return (s.find(([k]) => k === "name") || [, ""])[1]; }
async function openIrEditor() {
  const r = VW.data; if (!r || !r.signals) return;
  const sig = r.signals.map((s) => s.map((kv) => [...kv]));
  $("#vwEditIr").hidden = true; $("#vwEdit").hidden = true;
  const others = devReady() ? await api.ir_files() : { files: [] };
  const body = $("#vwBody");
  const render = () => {
    body.innerHTML = `<div class="vw-note">Rename, reorder or remove buttons, or add the buttons of another remote. Nothing changes on the Flipper until you save.</div>
      <div class="vw-btns" id="irRows"></div>
      <div class="ir-merge"><select id="irMergeSel"><option value="">Add the buttons of another remote...</option>${(others.files || []).filter((f) => f !== VW.path).map((f) => `<option>${esc(f)}</option>`).join("")}</select><button class="mini" id="irMergeGo">Add</button></div>
      <div class="sheet-foot ed-foot"><span class="ed-status" id="irStatus">${sig.length} buttons</span><button class="mini" id="irCancel">Cancel</button><button class="mini primary" id="irSave">Save to Flipper</button></div>`;
    sig.forEach((s, i) => {
      const row = document.createElement("div"); row.className = "ir-edit-row";
      const d = Object.fromEntries(s);
      row.innerHTML = `<div style="min-width:0"><input value="${esc(d.name || "")}" maxlength="32" spellcheck="false"><small>${esc(d.type === "raw" ? `raw · ${d.frequency || "?"} Hz` : `${d.protocol || "?"} · ${d.address || ""} · ${d.command || ""}`)}</small></div><div class="acts"></div>`;
      $("input", row).oninput = (e) => { s.find(([k]) => k === "name")[1] = e.target.value.replace(/[\r\n]/g, " ").trim() || "Button"; };
      const acts = $(".acts", row);
      acts.append(iconBtn("tri-up", "Move up", () => { if (i) { [sig[i - 1], sig[i]] = [sig[i], sig[i - 1]]; render(); } }),
        iconBtn("tri-down", "Move down", () => { if (i < sig.length - 1) { [sig[i + 1], sig[i]] = [sig[i], sig[i + 1]]; render(); } }));
      const del = iconBtn("trash", "Remove this button", () => { sig.splice(i, 1); render(); }); del.classList.add("danger"); acts.append(del);
      $("#irRows").appendChild(row);
    });
    renderIcons(body);
    $("#irCancel").onclick = () => openViewer(VW.path, r.size);
    $("#irMergeGo").onclick = async () => {
      const f = $("#irMergeSel").value; if (!f) return;
      const o = await api.files_view(f, 0);
      if (o.error || !o.signals) { toast(o.error || "That isn't an IR remote"); return; }
      const names = new Set(sig.map(irName));
      o.signals.forEach((s) => {
        const c = s.map((kv) => [...kv]), nm = c.find(([k]) => k === "name");
        let base = nm[1], n = 2; while (names.has(nm[1])) nm[1] = `${base}_${n++}`;
        names.add(nm[1]); sig.push(c);
      });
      toast(`Added ${o.signals.length} buttons from ${f.split("/").pop()}`); render();
    };
    $("#irSave").onclick = async () => {
      if (!sig.length && !(await confirmDialog("Save an empty remote?", "Every button was removed.", "Save"))) return;
      $("#irStatus").textContent = "Saving...";
      const res = await api.files_save(VW.path, irSerialize(sig, r.text));
      if (res.error) { $("#irStatus").textContent = res.error; return; }
      toast("Remote saved to the Flipper"); openViewer(VW.path, res.size);
    };
  };
  render();
}
function openEditor(initial) {
  const text = initial !== undefined ? initial : (VW.data && VW.data.text) || "";
  $("#vwBody").hidden = true; $("#vwEditor").hidden = false; $("#vwEdit").hidden = true;
  $("#edText").value = text; VW.dirty = false;
  $("#edStatus").textContent = edKind(VW.path) === "ducky" ? "BadUSB script: it types on whatever computer the Flipper is plugged into when you run it from the Flipper."
    : edKind(VW.path) === "js" ? "JavaScript app for the Flipper. Save & run shows its output in the CLI tab." : "";
  $("#edStatus").classList.remove("err");
  $("#edRun").hidden = edKind(VW.path) !== "js";
  edRender();
  setTimeout(() => $("#edText").focus(), 30);
}
async function closeViewer() {
  if (!$("#vwEditor").hidden && VW.dirty && !(await confirmDialog("Discard your changes?", "The edits haven't been saved to the Flipper.", "Discard"))) return;
  $("#viewer").hidden = true; VW.path = null;
}
async function edSave(andRun) {
  if (!devReady()) { needDevice(); return false; }
  $("#edSave").disabled = $("#edRun").disabled = true;
  $("#edStatus").classList.remove("err"); $("#edStatus").textContent = "Saving...";
  const r = await api.files_save(VW.path, $("#edText").value);
  $("#edSave").disabled = $("#edRun").disabled = false;
  if (r.error) { $("#edStatus").classList.add("err"); $("#edStatus").textContent = r.error; return false; }
  VW.dirty = false; VW.isNew = false;
  $("#edStatus").textContent = `Saved to the Flipper (${fmtSize(r.size)}).`;
  if (FM.sub === "files") loadFiles(FM.path);
  if (andRun) {
    $("#viewer").hidden = true;
    setTab("flipper"); setSub("cli");
    cliRun(`js ${VW.path}`);
  }
  return true;
}
/* ---------- SD card clean up: suggestions only; the user ticks, then confirms ---------- */
async function openCleanup() {
  if (!needDevice()) return;
  VW.path = null; VW.data = null;
  $("#vwTitle").textContent = "Clean up"; $("#vwSub").textContent = "Things on the SD card you probably don't need";
  $("#vwEdit").hidden = true; $("#vwDownload").hidden = true; $("#vwEditor").hidden = true; $("#vwBody").hidden = false; $("#vwEditIr").hidden = true; $("#vwInstall").hidden = true;
  $("#vwBody").innerHTML = `<div class="skeleton"><span class="blk"></span>Looking through the SD card...</div>`;
  $("#viewer").hidden = false;
  if (!FL.apps) await loadFlipper();
  const scan = await api.cleanup_scan();
  if ($("#viewer").hidden) return;
  if (scan.error) { $("#vwBody").innerHTML = `<div class="vw-empty"><b>Couldn't scan it</b><br>${esc(scan.error)}</div>`; return; }
  const groups = [];
  const apps = FL.apps || [], d = S.device;
  const bad = apps.filter((a) => a.meta && a.meta.api && apiOk(a.meta.api, d.api) === false);
  if (bad.length) groups.push({ title: "Apps that won't open on this firmware", why: "Built for another API. Rebuild the ones you want from the Library, or remove them.",
    items: bad.map((a) => ({ path: a.path, label: (a.meta && a.meta.name) || prettyFile(a.file), sub: `API ${a.meta.api} · ${a.path}`, size: a.size })) });
  const byFile = {};
  apps.forEach((a) => (byFile[a.file.toLowerCase()] = byFile[a.file.toLowerCase()] || []).push(a));
  const dups = Object.values(byFile).filter((l) => l.length > 1);
  if (dups.length) groups.push({ title: "The same app in more than one folder", why: "Only one copy is needed. Tick the copies to remove; the newest version is listed first.",
    items: dups.flatMap((l) => l.sort((x, y) => (vNewer((x.meta || {}).version || 0, (y.meta || {}).version || 0) ? -1 : 1))
      .map((a, i) => ({ path: a.path, label: `${(a.meta && a.meta.name) || prettyFile(a.file)}${a.meta && a.meta.version ? " v" + a.meta.version : ""}`, sub: a.path + (i === 0 ? " · newest" : ""), size: a.size }))) });
  if (scan.updates.length) groups.push({ title: "Firmware update packages", why: "Left over from earlier firmware updates. Safe to remove once an update has finished; they get downloaded again when needed.",
    items: scan.updates.map((u) => ({ path: u.path, label: u.path.split("/").pop(), sub: `${u.files} file${u.files === 1 ? "" : "s"}`, size: u.size })) });
  if (scan.temp.length) groups.push({ title: "Leftover temp files", why: "Half-finished copies from interrupted transfers.", pre: true,
    items: scan.temp.map((t) => ({ path: t.path, label: t.path.split("/").pop(), sub: t.path, size: t.size })) });
  const body = $("#vwBody");
  if (!groups.length) { body.innerHTML = `<div class="vw-empty"><b>All tidy</b><br>No duplicates, no apps that won't open, no update packages or temp files.</div>`; return; }
  const pick = new Set(groups.filter((g) => g.pre).flatMap((g) => g.items.map((i) => i.path)));
  const render = () => {
    const bytes = groups.flatMap((g) => g.items).filter((i) => pick.has(i.path)).reduce((s, i) => s + (i.size || 0), 0);
    body.innerHTML = groups.map((g) => `<div class="vw-sec">${esc(g.title)}</div><div class="vw-note">${esc(g.why)}</div>
      <div class="vw-btns">${g.items.map((it) => `<button class="pick cl-item ${pick.has(it.path) ? "on" : ""}" data-p="${esc(it.path)}"><span class="box">${pick.has(it.path) ? pixelIcon("check") : ""}</span>
        <span style="min-width:0;flex:1;text-align:left"><div class="pick-name">${esc(it.label)}</div><div class="pick-sub">${esc(it.sub)}</div></span><small>${fmtSize(it.size || 0)}</small></button>`).join("")}</div>`).join("") +
      `<div class="sheet-foot ed-foot"><span class="ed-status">${pick.size} ticked · ${fmtSize(bytes)}</span>
       <button class="mini" id="clCancel">Close</button><button class="mini primary danger" id="clGo" ${pick.size ? "" : "disabled"}>Delete ${pick.size || ""}</button></div>`;
    $$(".cl-item", body).forEach((b) => (b.onclick = () => { pick.has(b.dataset.p) ? pick.delete(b.dataset.p) : pick.add(b.dataset.p); render(); }));
    $("#clCancel").onclick = () => { $("#viewer").hidden = true; };
    $("#clGo").onclick = async () => {
      const list = [...pick];
      if (!(await confirmDialog(`Delete ${list.length} item${list.length > 1 ? "s" : ""} from your Flipper?`,
        `${list.slice(0, 8).map((x) => `<code>${esc(x)}</code>`).join("<br>")}${list.length > 8 ? `<br>and ${list.length - 8} more` : ""}<br><br>This can't be undone.`, "Delete"))) return;
      $("#viewer").hidden = true;
      startJob("files", () => api.cleanup_delete(list));
    };
  };
  render();
}
/* ---------- firmware update: prepare (backup, download, verify, copy, verify) then install on a second yes ---------- */
const FWU = { info: null, pick: null };
function fwCurrent(t, d) {          // is this package what the Flipper already runs?
  if (!d || !d.connected || d.fw !== t.id) return false;
  const v = String(t.version);
  return v === d.version || v === d.commit || (d.commit && (v.startsWith(d.commit) || d.commit.startsWith(v)));
}
async function openFirmware() {
  if (!needDevice()) return;
  VW.path = null; VW.data = null;
  $("#vwTitle").textContent = "Firmware"; $("#vwSub").textContent = "Update your Flipper with the official packages of each firmware";
  $("#vwEdit").hidden = true; $("#vwDownload").hidden = true; $("#vwEditor").hidden = true; $("#vwBody").hidden = false; $("#vwEditIr").hidden = true; $("#vwInstall").hidden = true;
  $("#vwBody").innerHTML = `<div class="skeleton"><span class="blk"></span>Checking the firmware servers...</div>`;
  $("#viewer").hidden = false;
  const r = await api.fw_info();
  if ($("#viewer").hidden) return;
  FWU.info = r;
  FWU.pick = FWU.pick && r.targets[FWU.pick] ? FWU.pick : (S.device.fw && r.targets[S.device.fw] ? S.device.fw : Object.keys(r.targets)[0]);
  fwRender();
}
function fwRender() {
  const r = FWU.info, d = S.device, body = $("#vwBody");
  const order = ["official", "official-dev", "momentum", "momentum-dev", "unleashed", "unleashed-dev"].filter((k) => r.targets[k]);
  const pw = r.power || {}, level = parseInt(pw["charge.level"] || "0", 10), charging = ["charging", "charged"].includes(pw["charge.state"]);
  const t = r.targets[FWU.pick];
  const cur = fwCurrent(t, d), sameFamily = (d.fw || "").split("-")[0] === t.id.split("-")[0];
  const warn = [];
  if (!sameFamily) warn.push(`You're switching from <b>${esc(d.fw_name || "your firmware")}</b> to <b>${esc(t.family)}</b>. Your settings and files stay, but apps built for ${esc(d.fw_name || "the old firmware")} won't open until you rebuild them (Library > Rebuild all, one click).`);
  if (cur) warn.push("Your Flipper already runs this exact build, so updating would just reinstall it.");
  if (pw["charge.level"] && !(level >= 50 || (charging && level >= 25))) warn.push(`Battery is at ${level}%. Charge it to at least 50% first: an update must not run out of power.`);
  body.innerHTML = `
    <dl class="vw-fields"><dt>Your Flipper runs</dt><dd>${esc(d.fw_name || "?")} · ${esc(d.version || "")}${d.commit ? " (" + esc(d.commit) + ")" : ""} · API ${esc(d.api || "?")}</dd>
      ${pw["charge.level"] ? `<dt>Battery</dt><dd>${level}% · ${esc(pw["charge.state"] || "")}</dd>` : ""}</dl>
    <div class="vw-sec">Pick a firmware</div>
    <div class="fw-grid">${order.map((k) => { const x = r.targets[k]; return `<button class="fw-card ${k === FWU.pick ? "on" : ""}" data-k="${k}">
      <b>${esc(x.family)} ${x.channel === "dev" ? "dev" : ""}</b><span>${esc(x.version)}</span><small>${esc(x.age)}${fwCurrent(x, d) ? " · installed" : ""}</small></button>`; }).join("")}</div>
    ${warn.map((w) => `<div class="vw-note fw-warn">${w}</div>`).join("")}
    ${t.changelog ? `<details class="vw-details"><summary>What's new in ${esc(t.version)}</summary><pre class="vw-raw">${esc(t.changelog)}</pre></details>` : ""}
    <div class="vw-note">How it works: first I back up your SD card, download the official package and check its checksum, copy it to the Flipper and check every file there. Nothing is installed until you confirm a second time. The Flipper then verifies the package itself, restarts and updates (2-3 minutes). If anything ever goes wrong, its recovery mode (hold Left + Back, then qFlipper's Repair) can always restore it.</div>
    <label class="toggle"><input type="checkbox" id="fwBackup" checked><span class="switch"><span class="knob"></span></span><span class="toggle-text">Back up my SD card first</span></label>
    <div class="sheet-foot ed-foot"><span class="ed-status"></span><button class="mini" id="fwCancel">Close</button>
      <button class="mini primary" id="fwGo" ${pw["charge.level"] && !(level >= 50 || (charging && level >= 25)) ? "disabled" : ""}>Prepare update to ${esc(t.family)} ${esc(t.version)}</button></div>`;
  $$(".fw-card", body).forEach((b) => (b.onclick = () => { FWU.pick = b.dataset.k; fwRender(); }));
  $("#fwCancel").onclick = () => { $("#viewer").hidden = true; };
  $("#fwGo").onclick = () => {
    const backup = $("#fwBackup").checked;
    $("#viewer").hidden = true;
    startJob("firmware", () => api.fw_prepare(FWU.pick, backup));
  };
}
async function fwReady(info) {
  const yes = await confirmDialog(`Install ${info.family} ${info.version}?`,
    `The update is on your Flipper and every file checked out.<br><br>Your Flipper will restart and update itself for 2-3 minutes. <b>Keep it plugged in and don't press its buttons</b> until it's back.`, "Install now");
  if (!yes) {
    const r = await api.fw_discard(info.remote, !!info.existed);
    toast(r.error ? `Couldn't remove the package: ${r.error}` : r.kept ? "Not installed. The package stays in /ext/update, as it was before." : "Not installed. The package was removed from the SD card.");
    return;
  }
  LCD.say("Updating! Don't unplug me", 8000);
  startJob("firmware", () => api.fw_install(info.remote, info.version));
}
/* ---------- tools: music, animation maker, asset packs, folder sync, companion ---------- */
const TL = { music: null, audio: null, anim: null, animTimer: null };
function toolsShow() {
  renderIcons($("#toolsBody"));
  animListMade(); syRender();
  if ($('.tool[data-tool="packs"]').open) packsLoad();
}
/* ---------- NFC maker ---------- */
const NF_KINDS = {
  url: { label: "Link", fields: [["url", "Link", "https://example.com", "full"]] },
  text: { label: "Text", fields: [["text", "Text", "Anything you want a phone to show", "full area"], ["lang", "Language code", "en"]] },
  wifi: { label: "Wi-Fi", fields: [["ssid", "Network name", "MyNetwork"], ["password", "Password", ""],
    ["auth", "Security", "", "select", [["wpa2-personal", "WPA2 (most networks)"], ["wpa-personal", "WPA"], ["open", "Open (no password)"], ["shared", "WEP / shared"]]],
    ["enc", "Encryption", "", "select", [["aes", "AES"], ["tkip", "TKIP"], ["wep", "WEP"], ["none", "None"]]]] },
  contact: { label: "Contact", fields: [["first", "First name", ""], ["last", "Last name", ""], ["phone", "Phone", "+1 555 0100"],
    ["email", "Email", "name@example.com"], ["org", "Company", ""], ["url", "Website", "https://"]] },
  phone: { label: "Call", fields: [["number", "Phone number", "+1 555 0100", "full"]] },
  sms: { label: "Text message", fields: [["number", "Phone number", "+1 555 0100"], ["body", "Message", ""]] },
  email: { label: "Email", fields: [["to", "To", "name@example.com"], ["subject", "Subject", ""], ["body", "Body", "", "full area"]] },
  geo: { label: "Location", fields: [["lat", "Latitude", "48.8584"], ["lon", "Longitude", "2.2945"]] },
  app: { label: "Android app", fields: [["package", "Package name", "com.spotify.music", "full"]] },
  bluetooth: { label: "Bluetooth pair", fields: [["mac", "Device address", "00:11:22:AA:BB:CC", "full"]] },
};
const NF = { kind: "url", recs: [], last: null, t: null };
function nfStatus(t, err) { $("#nfStatus").textContent = t || ""; $("#nfStatus").classList.toggle("err", !!err); }
function nfRenderKinds() {
  $("#nfKinds").innerHTML = Object.entries(NF_KINDS).map(([k, v]) => `<button class="fchip ${k === NF.kind ? "on" : ""}" data-k="${k}">${esc(v.label)}</button>`).join("");
  $$("#nfKinds .fchip").forEach((b) => (b.onclick = () => { NF.kind = b.dataset.k; nfRenderKinds(); nfRenderFields(); nfUpdate(); }));
}
function nfRenderFields() {
  $("#nfFields").innerHTML = NF_KINDS[NF.kind].fields.map(([id, label, ph, cls, opts]) => {
    const full = (cls || "").includes("full") ? "full" : "";
    let input;
    if (cls === "select") input = `<select data-f="${id}">${opts.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select>`;
    else if ((cls || "").includes("area")) input = `<textarea data-f="${id}" spellcheck="false" placeholder="${esc(ph)}"></textarea>`;
    else input = `<input type="text" data-f="${id}" spellcheck="false" placeholder="${esc(ph)}"${id === "lang" ? ' value="en"' : ""}>`;
    return `<label class="${full}">${esc(label)}${input}</label>`;
  }).join("");
  $$("#nfFields [data-f]").forEach((el) => el.addEventListener("input", nfUpdate));
  const first = $("#nfFields [data-f]"); if (first) setTimeout(() => first.focus(), 30);
}
function nfCurrent() {
  const it = { kind: NF.kind };
  $$("#nfFields [data-f]").forEach((el) => (it[el.dataset.f] = el.value));
  return it;
}
function nfFilled(it) { return Object.entries(it).some(([k, v]) => k !== "kind" && k !== "lang" && k !== "auth" && k !== "enc" && String(v).trim()); }
function nfSummary(it) {
  const v = { url: it.url, text: it.text, wifi: it.ssid, contact: `${it.first || ""} ${it.last || ""}`.trim(), phone: it.number, sms: it.number,
    email: it.to, geo: `${it.lat}, ${it.lon}`, app: it.package, bluetooth: it.mac }[it.kind];
  return v || "";
}
function nfRenderRecs() {
  $("#nfRecs").innerHTML = NF.recs.map((r, i) => `<div class="nfc-rec"><b>${esc(NF_KINDS[r.kind].label)}</b><span>${esc(nfSummary(r))}</span><button class="icon-btn danger" data-i="${i}" title="Remove this record"><i data-icon="trash"></i></button></div>`).join("");
  $$("#nfRecs button").forEach((b) => (b.onclick = () => { NF.recs.splice(+b.dataset.i, 1); nfRenderRecs(); nfUpdate(); }));
  renderIcons($("#nfRecs"));
}
function nfUpdate() {
  clearTimeout(NF.t);
  NF.t = setTimeout(async () => {
    const cur = nfCurrent();
    const items = NF.recs.concat(nfFilled(cur) ? [cur] : []);
    if (!items.length) { $("#nfPreview").hidden = true; nfStatus(""); NF.last = null; return; }
    const r = await api.nfc_make(items, $("#nfTag").value);
    if (r.error) { nfStatus(r.error, true); NF.last = null; $("#nfPreview").hidden = NF.recs.length === 0; return; }
    nfStatus(""); NF.last = r;
    $("#nfPreview").hidden = false;
    $("#nfDoes").innerHTML = r.does.map((d) => `<li>${esc(d)}</li>`).join("");
    const pct = Math.min(100, Math.round(r.bytes / r.capacity * 100));
    $("#nfCapFill").style.width = pct + "%"; $("#nfCapFill").classList.toggle("full", pct > 90);
    $("#nfCapText").textContent = `${r.bytes} of ${r.capacity} bytes`;
    if (!$("#nfName").value.trim() || $("#nfName").dataset.auto) {
      const it = items[0];
      let base = nfSummary(it);
      if (it.kind === "url") {
        try { const u = new URL(/^\w+:/.test(it.url) ? it.url : "https://" + it.url);
          const site = u.hostname.replace(/^www\./, "").split(".").slice(-2, -1)[0] || u.hostname;
          const last = u.pathname.split("/").filter(Boolean).pop();
          base = site + (last ? " " + last : ""); } catch (_) { /* keep the summary */ }
      } else if (it.kind === "wifi") base = "WiFi " + (it.ssid || "");
      else base = (it.kind === "contact" ? "" : NF_KINDS[it.kind].label + " ") + base;
      base = base.replace(/[^\w\- ]+/g, " ").trim().replace(/\s+/g, "_").slice(0, 30) || NF_KINDS[it.kind].label;
      $("#nfName").value = base; $("#nfName").dataset.auto = "1";
    }
  }, 220);
}
async function nfSave(emulate) {
  if (!needDevice()) return;
  if (!NF.last) { nfStatus("Fill in the record first", true); return; }
  const name = $("#nfName").value;
  let r = await api.nfc_save(name, NF.last.nfc, emulate, false);
  if (r.exists) {
    const yes = await confirmDialog("Replace the tag?", `Your Flipper already has <code>${esc(r.path)}</code>. Replace it with this one?`, "Replace");
    if (!yes) return;
    r = await api.nfc_save(name, NF.last.nfc, emulate, true);
  }
  if (r.error) { toast(r.error); return; }
  toast(emulate ? "Emulating on your Flipper: tap it with a phone" : `Saved: ${r.path}`);
  if (MIR.state === "off") { LCD.say(emulate ? "Emulating! Tap me" : "Tag saved!", 4000); LCD.poke(); }
}

function muStatus(t, err) { $("#muStatus").textContent = t || ""; $("#muStatus").classList.toggle("err", !!err); }
function muShow(r) {
  if (r.error) { muStatus(r.error, true); $("#muResult").hidden = true; return; }
  TL.music = r;
  muStatus(`${r.title}: ${r.notes} notes at ${r.bpm} BPM${r.truncated ? " (cut to the first 1,500 notes)" : ""}`);
  $("#muName").value = r.name || r.title; $("#muResult").hidden = false;
}
function muPlay() {
  if (TL.audio) { TL.audio.close(); TL.audio = null; $("#muPlay").innerHTML = `<i data-icon="play"></i>Play`; renderIcons($("#muPlay").parentNode); return; }
  if (!TL.music) return;
  const ctx = new AudioContext(); TL.audio = ctx;
  let t = ctx.currentTime + 0.05;
  for (const [f, ms] of TL.music.preview) {
    if (f > 0) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "square"; o.frequency.value = f; g.gain.value = 0.06;
      o.connect(g).connect(ctx.destination); o.start(t); o.stop(t + ms / 1000 * 0.92);
    }
    t += ms / 1000;
  }
  $("#muPlay").textContent = "Stop";
  setTimeout(() => { if (TL.audio === ctx) muPlay(); }, (t - ctx.currentTime) * 1000 + 200);
}
async function animRebuild() {
  if (!TL.anim) return;
  $("#anStatus").textContent = "Converting...";
  const r = await api.anim_build({ fit: $("#anFit").value, dither: $("#anDither").value, threshold: +$("#anThr").value,
    contrast: +$("#anCon").value / 10, invert: $("#anInv").checked, fps: $("#anFps").value || null });
  if (r.error) { $("#anStatus").textContent = r.error; return; }
  TL.anim.frames = r.frames.map((b) => b64bytes(b)); TL.anim.fps = r.fps; TL.anim.i = 0;
  $("#anStatus").textContent = `${r.count} frame${r.count > 1 ? "s" : ""} at ${r.fps} fps`;
  $("#anWrap").hidden = false; $("#anSaveRow").hidden = false;
  clearInterval(TL.animTimer);
  const draw = () => {
    const a = TL.anim; if (!a || !a.frames.length) return;
    const c = $("#anCanvas").getContext("2d"), img = c.createImageData(128, 64), d = a.frames[a.i % a.frames.length];
    for (let y = 0; y < 64; y++) for (let x = 0; x < 128; x++) {
      const on = (d[y * 16 + (x >> 3)] >> (x & 7)) & 1, k = (y * 128 + x) * 4;
      img.data[k + 3] = on ? 255 : 0;
    }
    c.putImageData(img, 0, 0); a.i++;
  };
  draw(); TL.animTimer = setInterval(draw, 1000 / r.fps);
}
async function animListMade() {
  const r = await api.anim_list(), box = $("#anMade");
  box.innerHTML = (r.made || []).length ? `<div class="vw-sec">Made with Workbench</div>` : "";
  (r.made || []).forEach((m) => {
    const row = document.createElement("div"); row.className = "vw-btn";
    row.innerHTML = `<b>${esc(m.name)}</b><span>${esc(m.base)}/${esc(m.name)}</span>`;
    const rm = document.createElement("button"); rm.className = "mini"; rm.textContent = "Remove";
    rm.onclick = async () => {
      if (!needDevice() || !(await confirmDialog(`Remove ${m.name}?`, "Its folder and its manifest entry are deleted from the Flipper.", "Remove"))) return;
      const x = await api.anim_remove(m.name); toast(x.error || "Removed"); animListMade();
    };
    row.appendChild(rm); box.appendChild(row);
  });
}
async function packsLoad(force) {
  const grid = $("#pkGrid");
  if (!/^momentum/.test(S.device.fw || "")) { $("#pkNote").textContent = "Asset packs are a Momentum feature. Plug in a Flipper running Momentum to install them."; grid.innerHTML = ""; return; }
  if (TL.packs && !force) return;
  $("#pkNote").textContent = "Loading Momentum's pack library...";
  const r = await api.packs_list(!!force);
  if (r.error) { $("#pkNote").textContent = r.error; return; }
  TL.packs = r.packs;
  $("#pkNote").innerHTML = `${r.packs.length} packs. After installing, pick one on your Flipper: <b>Momentum Settings &gt; Interface &gt; Graphics &gt; Asset pack</b>.`;
  grid.innerHTML = r.packs.map((p) => `<div class="pack">${p.previews[0] ? `<img loading="lazy" src="${esc(p.previews[0])}" alt="">` : ""}
    <b>${esc(p.name)}</b><small>by ${esc(p.author || "?")} · ${p.anims} anim${p.anims === 1 ? "" : "s"}${p.icons ? ` · ${p.icons} icons` : ""}</small>
    <button class="mini primary" data-id="${esc(p.id)}">Install</button></div>`).join("");
  $$(".pack button", grid).forEach((b) => (b.onclick = () => { if (needDevice()) startJob("files", () => api.pack_install(b.dataset.id)); }));
}
/* folder sync */
async function syRender() {
  const r = await api.sync_pairs(); TL.pairs = r.pairs || [];
  const box = $("#syPairs"); box.innerHTML = TL.pairs.length ? "" : `<div class="vw-note">No folder pairs yet. Add one, for example your BadUSB scripts folder on the PC and /ext/badusb.</div>`;
  const MODES = { to_flipper: "PC → Flipper", to_pc: "Flipper → PC", both: "Both ways" };
  TL.pairs.forEach((p, i) => {
    const row = document.createElement("div"); row.className = "sy-pair";
    row.innerHTML = `<div class="paths">${esc(p.local)}<small>⇄ ${esc(p.remote)} · ${MODES[p.mode]}${p.delete ? " · deletes extras" : ""}</small></div><div class="acts"></div>`;
    const cmp = document.createElement("button"); cmp.className = "mini primary"; cmp.textContent = "Compare";
    cmp.onclick = () => syCompare(p);
    const rm = iconBtn("trash", "Forget this pair (no files are touched)", async () => { TL.pairs.splice(i, 1); await api.sync_save_pairs(TL.pairs); syRender(); });
    $(".acts", row).append(cmp, rm); box.appendChild(row);
  });
  renderIcons(box);
}
async function syAdd() {
  const pick = await api.sync_pick_local(); if (!pick.path) return;
  const remote = await promptDialog("Folder on the Flipper's SD card", "/ext/badusb", "Next");
  if (!remote) return;
  if (!/^\/ext\/[^.]/.test(remote) || remote.replace(/\/+$/, "") === "/ext") { toast("Pick a folder inside /ext, not the whole SD card"); return; }
  const mode = await promptDialog("Which way? Type 1, 2 or 3", "3", "Add") ;
  const m = { 1: "to_flipper", 2: "to_pc", 3: "both" }[(mode || "3").trim()] || "both";
  TL.pairs.push({ local: pick.path, remote: remote.replace(/\/+$/, ""), mode: m, delete: false });
  await api.sync_save_pairs(TL.pairs); syRender();
}
async function syCompare(p) {
  if (!needDevice()) return;
  const box = $("#syPlan");
  box.innerHTML = `<div class="skeleton"><span class="blk"></span>Comparing (the Flipper checks each matching file)...</div>`;
  const r = await api.sync_plan(p.local, p.remote, p.mode, p.delete);
  if (r.error) { box.innerHTML = `<div class="vw-note" style="color:var(--err)">${esc(r.error)}</div>`; return; }
  if (!r.steps.length) { box.innerHTML = `<div class="vw-note">Already in step: ${r.same} identical file${r.same === 1 ? "" : "s"}.</div>`; return; }
  const LBL = { upload: "PC → Flipper", download: "Flipper → PC", delete_remote: "delete on Flipper", delete_local: "delete on PC", conflict: "changed on both" };
  const steps = r.steps.map((s) => [...s]);
  const render = () => {
    box.innerHTML = `<div class="vw-sec">${steps.length} change${steps.length > 1 ? "s" : ""} · ${r.same} already the same</div><div class="sy-steps">` +
      steps.map(([a, rel, size], i) => `<div class="sy-step"><span class="act ${a}">${LBL[a]}</span><span>${esc(rel)}</span><span>${fmtSize(size)}</span>` +
        (a === "conflict" || a === "upload" && r.steps[i][0] === "conflict" || a === "download" && r.steps[i][0] === "conflict"
          ? `<select data-i="${i}"><option value="conflict" ${a === "conflict" ? "selected" : ""}>Skip</option><option value="upload" ${a === "upload" ? "selected" : ""}>Keep PC's</option><option value="download" ${a === "download" ? "selected" : ""}>Keep Flipper's</option></select>` : "<span></span>") + `</div>`).join("") +
      `</div><div class="sheet-foot ed-foot"><span class="ed-status">${steps.some((s) => s[0].startsWith("delete")) ? "Deleted PC files go to .workbench-trash in that folder." : ""}</span>
       <button class="mini" id="syCancel">Close</button><button class="mini primary" id="syGo">Sync ${steps.filter((s) => s[0] !== "conflict").length}</button></div>`;
    $$("select", box).forEach((sel) => (sel.onchange = () => { steps[+sel.dataset.i][0] = sel.value; render(); }));
    $("#syCancel").onclick = () => { box.innerHTML = ""; };
    $("#syGo").onclick = async () => {
      const run = steps.filter((s) => s[0] !== "conflict");
      if (!run.length) return;
      const dels = run.filter((s) => s[0].startsWith("delete")).length;
      if (dels && !(await confirmDialog(`Delete ${dels} file${dels > 1 ? "s" : ""}?`, "Files on the Flipper are deleted for good; files on the PC go to .workbench-trash.", "Sync and delete"))) return;
      box.innerHTML = "";
      startJob("files", () => api.sync_run(p.local, p.remote, run));
    };
  };
  render();
}
/* ---------- SD card map: treemap of folders by size, drill down, open in Files ---------- */
const MAP = { data: null, stack: [] };
function squarify(items, x, y, w, h) {          // items: [{size}], sorted big -> small; returns rects
  const out = [], total = items.reduce((s, i) => s + i.size, 0) || 1;
  let rest = items.map((i) => ({ ...i, area: i.size / total * w * h }));
  while (rest.length) {
    const horiz = w >= h, side = horiz ? h : w;
    let row = [rest[0]], best = Infinity;
    const worst = (r) => { const s = r.reduce((a, i) => a + i.area, 0), mx = Math.max(...r.map((i) => i.area)), mn = Math.min(...r.map((i) => i.area));
      return Math.max(side * side * mx / (s * s), (s * s) / (side * side * mn)); };
    best = worst(row);
    for (let k = 1; k < rest.length; k++) { const t = worst([...row, rest[k]]); if (t > best) break; row.push(rest[k]); best = t; }
    const s = row.reduce((a, i) => a + i.area, 0), thick = s / side;
    let off = 0;
    for (const it of row) {
      const len = it.area / thick;
      out.push(horiz ? { ...it, x, y: y + off, w: thick, h: len } : { ...it, x: x + off, y, w: len, h: thick });
      off += len;
    }
    if (horiz) { x += thick; w -= thick; } else { y += thick; h -= thick; }
    rest = rest.slice(row.length);
  }
  return out;
}
function mapRender() {
  const node = MAP.stack[MAP.stack.length - 1], body = $("#vwBody");
  const kids = node.children.filter((c) => c.size > 0);
  const loose = node.size - kids.reduce((s, c) => s + c.size, 0);
  const items = [...kids, ...(loose > 0 ? [{ name: "files here", size: loose, files: 0, leaf: true, children: [] }] : [])];
  const fk = MAP.data.free_kib;
  body.innerHTML = `<div class="map-crumbs">${MAP.stack.map((n, i) => `<button data-i="${i}">${esc(n.name)}</button>`).join("<span>/</span>")}</div>
    <div class="vw-note">${fmtSize(node.size)} in ${node.files.toLocaleString()} files${fk != null && MAP.stack.length === 1 ? ` · ${fmtSize(fk * 1024)} free` : ""}. Click a block to look inside; the folder name above opens it in Files.</div>
    <div class="treemap" id="tmBox"></div>
    ${MAP.stack.length === 1 ? `<div class="vw-sec">By file type</div><div class="types">${MAP.data.types.slice(0, 8).map(([ext, size, n]) =>
      `<div class="type-row"><span>.${esc(ext)}</span><div class="bar" style="width:${Math.max(1, size / MAP.data.types[0][1] * 100)}%"></div><span>${fmtSize(size)} · ${n}</span></div>`).join("")}</div>` : ""}`;
  $$(".map-crumbs button", body).forEach((b) => (b.onclick = () => {
    const i = +b.dataset.i;
    if (i === MAP.stack.length - 1 && i > 0) { $("#viewer").hidden = true; setSub("files"); loadFiles(node.path); return; }
    MAP.stack = MAP.stack.slice(0, i + 1); mapRender();
  }));
  const box = $("#tmBox"), W = box.clientWidth, H = box.clientHeight;
  const rects = squarify(items.sort((a, b) => b.size - a.size), 0, 0, W, H);
  const max = Math.max(...items.map((i) => i.size)) || 1;
  rects.forEach((r) => {
    const el = document.createElement("div");
    el.className = "tm" + (r.leaf || !r.children.length ? " leaf" : "");
    const t = Math.sqrt(r.size / max);
    el.style.cssText = `left:${r.x}px;top:${r.y}px;width:${r.w}px;height:${r.h}px;background:hsl(${28 + (1 - t) * 10},${85 - (1 - t) * 30}%,${42 + t * 14}%)`;
    el.title = `${r.name}: ${fmtSize(r.size)}${r.files ? `, ${r.files} files` : ""}`;
    if (r.w > 50 && r.h > 28) el.innerHTML = `<b>${esc(r.name)}</b><small>${fmtSize(r.size)}</small>`;
    if (!r.leaf && r.children.length) el.onclick = () => { MAP.stack.push(r); mapRender(); };
    else if (!r.leaf) el.onclick = () => { $("#viewer").hidden = true; setSub("files"); loadFiles(r.path); };
    box.appendChild(el);
  });
}
async function openSdMap() {
  if (!needDevice()) return;
  VW.path = null; VW.data = null;
  $("#vwTitle").textContent = "SD card map"; $("#vwSub").textContent = "What fills your Flipper's SD card";
  $("#vwEdit").hidden = true; $("#vwDownload").hidden = true; $("#vwEditor").hidden = true; $("#vwBody").hidden = false; $("#vwEditIr").hidden = true; $("#vwInstall").hidden = true;
  $("#vwEditIr").hidden = true; $("#vwInstall").hidden = true;
  $("#vwBody").innerHTML = `<div class="skeleton"><span class="blk"></span>Measuring every folder (about 20 seconds)...</div>`;
  $("#viewer").hidden = false;
  const r = await api.sd_map();
  if ($("#viewer").hidden) return;
  if (r.error) { $("#vwBody").innerHTML = `<div class="vw-empty"><b>Couldn't read it</b><br>${esc(r.error)}</div>`; return; }
  MAP.data = r; MAP.stack = [r.tree]; mapRender();
}
/* ---------- app dev kit ---------- */
const DK = { tpl: "hello", dir: null, info: null };
async function dkShow() {
  if (!DK.info) {
    DK.info = await api.devkit_templates();
    $("#dkCat").innerHTML = DK.info.categories.map((c) => `<option>${esc(c)}</option>`).join("");
    DK.dir = DK.info.default_dir;
  }
  $("#dkTemplates").innerHTML = DK.info.templates.map(([k, t, d]) => `<button class="dk-t ${k === DK.tpl ? "on" : ""}" data-k="${k}"><span class="box"></span><span><b>${esc(t)}</b><br><small>${esc(d)}</small></span></button>`).join("");
  $$("#dkTemplates .dk-t").forEach((b) => (b.onclick = () => { DK.tpl = b.dataset.k; dkShow(); }));
  $("#dkCat").disabled = DK.tpl === "js";
  $("#dkDirText").textContent = DK.dir;
}
async function dkCreate() {
  const name = $("#dkName").value.trim();
  if (!name) { $("#dkResult").textContent = "Give your app a name first."; return; }
  const r = await api.devkit_create(name, DK.tpl, $("#dkCat").value, DK.dir);
  if (r.error) { $("#dkResult").textContent = r.error; return; }
  const box = $("#dkResult");
  if (r.js) {
    box.innerHTML = `Created <code>${esc(r.js)}</code>. <button class="mini primary" id="dkSendJs">Send to Flipper &amp; edit</button> <button class="mini" id="dkOpen">Open folder</button>`;
    $("#dkSendJs").onclick = async () => { if (!needDevice()) return; const s = await api.devkit_send_js(r.js); if (s.error) { toast(s.error); return; } toast(`Sent to ${s.path}`); openViewer(s.path, 0); };
  } else {
    box.innerHTML = `Created <code>${esc(r.folder)}</code> with your app's source. <button class="mini primary" id="dkBuild">Build &amp; install, rebuild on save</button> <button class="mini" id="dkOpen">Open folder</button>${DK.info.vscode ? ` <button class="mini" id="dkCode">Open in VS Code</button>` : ""}
      <br>Its log lines carry the tag <code>${esc(r.appid)}</code>: use <b>CLI &gt; Follow log</b> with that filter while you test.`;
    $("#dkBuild").onclick = () => {
      setTab("build"); $("#url").value = r.folder; renderPreview(); setAfter("open", true);
      setWatch(r.folder);
      build();
    };
    if ($("#dkCode")) $("#dkCode").onclick = () => api.devkit_open_code(r.folder);
  }
  $("#dkOpen").onclick = () => api.open_folder(r.folder);
  renderIcons(box);
}

/* ---------- Wi-Fi dev board ---------- */
const BD = { plan: null };
function bdShowPlan(r) {
  if (r.error) { $("#bdPlan").innerHTML = `<span style="color:var(--err)">${esc(r.error)}</span>`; BD.plan = null; bdButtons(); return; }
  if (!r.parts) return;
  BD.plan = r;
  $("#bdPlan").innerHTML = `<b>${esc(r.source)}</b>: ${r.parts.map(([a, l, n]) => `${esc(l)} at ${a} (${fmtSize(n)})`).join(", ")}. Checked: ESP32-S2 images.`;
  bdButtons();
}
function bdOpen() { const t = $('.tool[data-tool="board"]'); return t && t.open && S.tab === "flipper" && FM.sub === "tools"; }
function bdDropFile(f) {
  if (f.size > 16 * 1024 * 1024) { bdShowPlan({ error: "That file is too big for the board." }); return; }
  const rd = new FileReader();
  rd.onload = async () => { $("#bdPlan").textContent = "Checking..."; bdShowPlan(await api.devboard_use_file(f.name, String(rd.result).split(",")[1])); };
  rd.readAsDataURL(f);
}
function bdButtons() { $("#bdFlash").disabled = !(BD.plan && $("#bdPort").value); }
async function bdPorts() {
  const r = await api.devboard_ports();
  $("#bdPort").innerHTML = r.ports.length ? r.ports.map((p) => `<option value="${esc(p.port)}">${esc(p.port)} · ${esc(p.desc || "ESP32-S2")}</option>`).join("")
    : `<option value="">No board found (hold BOOT while plugging in)</option>`;
  bdButtons();
}
async function bdShow() {
  if (BD.loaded) return; BD.loaded = true;
  const r = await api.devboard_info();
  $("#bdOfficial").innerHTML = (r.official || []).map((o) => `<option value="${esc(o.channel)}" ${o.channel === "release" ? "selected" : ""}>Black Magic ${esc(o.version)} (${esc(o.channel)})</option>`).join("") || `<option value="">Official firmware unavailable</option>`;
  bdPorts();
}

/* ---------- auto mirror: the screen shows the Flipper as soon as it's plugged in ---------- */
function autoMirror() {
  if (!S.autoMirror || MIR.userOff || MIR.state !== "off" || !devReady()) return;
  toggleMirror(true);
}

/* ---------- Bluetooth (experimental) ---------- */
function btButton() {             // the Bluetooth button under the screen follows the mirror
  const b = $("#btBtn");
  b.classList.toggle("live", MIR.ble && MIR.state === "live");
  b.classList.toggle("busy", MIR.ble && MIR.state === "starting");
  b.hidden = MIR.state !== "off" && !MIR.ble;          // hidden while mirroring over USB
  $("#btLabel").textContent = MIR.ble && MIR.state === "live" ? "Disconnect" : MIR.ble && MIR.state === "starting" ? "Connecting" : "Bluetooth";
  $("#mirrorBtn").hidden = (MIR.ble && MIR.state !== "off") || (MIR.state === "off" && !(S.device.connected && !S.device.error));
  $("#mirrorBtn").classList.toggle("busy", !MIR.ble && MIR.state === "starting");
  $("#mirrorBtn").title = MIR.state === "off" ? "Show your Flipper's screen here" : "Pause the screen and use the d-pad for Workbench";
  // like a real Flipper: its LED shows blue for Bluetooth (blinking while connecting, steady when connected)
  if (MIR.ble && MIR.state === "live") led("blue");
  else if (MIR.ble && MIR.state === "starting") led("blue", true);
  else if (!S.busy && /blue/.test($("#led").className)) led(null);
  b.title = MIR.ble && MIR.state !== "off" ? "Disconnect Bluetooth" : "Mirror over Bluetooth: no cable needed";
}
async function btConnect(address, name) {
  if (MIR.state !== "off") stopMirror();
  MIR.ble = true; MIR.state = "starting"; $("#mirrorLabel").textContent = "Connecting (Bluetooth)..."; btButton();
  const x = await api.ble_mirror_start(address, name || null);
  if (x.error) { MIR.state = "off"; MIR.ble = false; btButton(); toast(x.error); }
}
async function btQuick() {
  if (MIR.ble && MIR.state !== "off") { stopMirror(); return; }
  if (MIR.state !== "off") return;
  const last = await api.ble_last();
  if (last.address) { toast(`Connecting to ${last.name || "your Flipper"} over Bluetooth...`); btConnect(last.address, last.name); return; }
  MIR.ble = true; MIR.state = "starting"; $("#mirrorLabel").textContent = "Looking for your Flipper..."; btButton();
  const r = await api.ble_scan();
  MIR.state = "off"; MIR.ble = false; btButton(); $("#mirrorLabel").textContent = "";
  if (r.error) { toast(r.error); return; }
  if (!r.flippers.length) { toast("No Flipper heard. Turn on Bluetooth in its Settings (and close the phone app)."); return; }
  let pick = r.flippers[0];
  if (r.flippers.length > 1) {
    const asking = promptDialog("Which Flipper? Type its number:", "1", "Connect");
    $("#cfBody").innerHTML = r.flippers.map((f, i) => `${i + 1}. ${esc(f.name)} (${esc(f.address)})`).join("<br>");
    pick = r.flippers[parseInt((await asking) || "", 10) - 1];
    if (!pick) return;
  }
  toast(`Connecting to ${pick.name}. First time: type the PIN your Flipper shows.`);
  btConnect(pick.address, pick.name);
}
async function btScan() {
  $("#btStatus").textContent = "Listening for your Flipper (up to 15 seconds)..."; $("#btList").innerHTML = "";
  const r = await api.ble_scan();
  if (r.error) { $("#btStatus").textContent = r.error; return; }
  $("#btStatus").textContent = r.flippers.length ? `${r.flippers.length} found` : "No Flipper found. Is Bluetooth on in the Flipper's settings?";
  r.flippers.forEach((f) => {
    const row = document.createElement("div"); row.className = "vw-btn";
    row.innerHTML = `<b>${esc(f.name)}</b><span>${esc(f.address)} · signal ${f.rssi ?? "?"} dBm</span>`;
    const go = document.createElement("button"); go.className = "mini primary"; go.textContent = "Mirror over Bluetooth";
    go.onclick = () => { toast("Connecting. First time: type the PIN your Flipper shows."); btConnect(f.address, f.name); };
    row.appendChild(go); $("#btList").appendChild(row);
  });
}

/* ---------- phone remote ---------- */
const RM = { on: false, timer: null };
async function rmToggle() {
  if (RM.on) {
    await api.remote_stop(); RM.on = false; clearInterval(RM.timer);
    $("#rmBox").hidden = true; $("#rmToggle").textContent = "Turn on"; $("#rmStatus").textContent = "Off"; return;
  }
  const r = await api.remote_start();
  if (r.error) { $("#rmStatus").textContent = r.error; return; }
  RM.on = true; $("#rmToggle").textContent = "Turn off"; $("#rmBox").hidden = false;
  $("#rmUrl").textContent = r.url; $("#rmQr").src = r.qr || ""; $("#rmQr").hidden = !r.qr;
  const upd = async () => { const s = await api.remote_status(); $("#rmStatus").textContent = s.on ? (s.clients.length ? `On · ${s.clients.length} device${s.clients.length > 1 ? "s" : ""} connected` : "On · waiting for your phone") : "Off"; };
  upd(); RM.timer = setInterval(upd, 3000);
}

/* ---------- MIFARE user keys ---------- */
const MK = { keys: [], dirty: false };
function mkRender() {
  $("#mkList").innerHTML = MK.keys.map((k, i) => `<span class="key">${k}<button data-i="${i}" title="Remove">×</button></span>`).join("") ||
    `<span class="tool-status">No keys of your own yet.</span>`;
  $$("#mkList button").forEach((b) => (b.onclick = () => { MK.keys.splice(+b.dataset.i, 1); MK.dirty = true; mkRender(); mkStatus(); }));
}
function mkStatus(t) { $("#mkStatus").textContent = t || `${MK.keys.length} key${MK.keys.length === 1 ? "" : "s"}${MK.dirty ? " · not saved yet" : ""}`; }
async function mkLoad() {
  if (!devReady()) { mkStatus("Plug in your Flipper to load its keys"); return; }
  const r = await api.mf_dict_load();
  if (r.error) { mkStatus(r.error); return; }
  MK.keys = r.keys; MK.dirty = false; mkRender(); mkStatus(r.exists ? "" : "No user key file yet; saving creates it");
}
/* ---------- NFC: compare two dumps ---------- */
async function nfcCompare(pathA, a) {
  const files = await api.files_list(pathA.slice(0, pathA.lastIndexOf("/")));
  const others = (files.entries || []).filter((e) => !e.dir && /\.nfc$/i.test(e.name) && `${pathA.slice(0, pathA.lastIndexOf("/"))}/${e.name}` !== pathA);
  const pick = await promptDialog(`Compare with which dump? (${others.map((e) => e.name).slice(0, 6).join(", ")}${others.length > 6 ? "..." : ""})`, others[0] ? others[0].name : "", "Compare");
  if (!pick) return;
  const pathB = pick.startsWith("/") ? pick : `${pathA.slice(0, pathA.lastIndexOf("/"))}/${pick}`;
  const b = await api.files_view(pathB, 0);
  if (b.error || b.kind !== "nfc") { toast(b.error || "That isn't an NFC dump"); return; }
  const fa = Object.fromEntries(a.fields), fb2 = Object.fromEntries(b.fields);
  const keys = [...new Set([...Object.keys(fa), ...Object.keys(fb2)])];
  const ba = Object.fromEntries(a.blocks || []), bb = Object.fromEntries(b.blocks || []);
  const bkeys = [...new Set([...Object.keys(ba), ...Object.keys(bb)])].sort((x, y) => (+x.split(" ")[1]) - (+y.split(" ")[1]));
  const cell = (v, o) => !v ? `<span class="x">missing</span>` : !o ? esc(v) : v.split(" ").map((byte, i) => byte !== (o.split(" ")[i] || "") ? `<span class="x">${esc(byte)}</span>` : esc(byte)).join(" ");
  const nch = bkeys.filter((k) => ba[k] !== bb[k]).length;
  const body = $("#vwBody");
  body.innerHTML = `<div class="vw-note">${esc(pathA.split("/").pop())} vs ${esc(pathB.split("/").pop())}: ${nch} of ${bkeys.length} blocks/pages differ. Differing bytes are red.</div>
    <div class="nfc-diff"><div class="dr head"><span></span><span>${esc(pathA.split("/").pop())}</span><span>${esc(pathB.split("/").pop())}</span></div>
    ${keys.map((k) => `<div class="dr ${fa[k] !== fb2[k] ? "ch" : ""}"><span>${esc(k)}</span><span>${esc(fa[k] || "-")}</span><span>${esc(fb2[k] || "-")}</span></div>`).join("")}
    ${bkeys.map((k) => `<div class="dr ${ba[k] !== bb[k] ? "ch" : ""}"><span>${esc(k)}</span><span>${cell(ba[k], bb[k])}</span><span>${cell(bb[k], ba[k])}</span></div>`).join("")}</div>`;
  $("#vwTitle").textContent = "Compare NFC dumps";
}
async function fmNewFile() {
  const name = await promptDialog("New file", "script.js", "Create");
  if (!name) return;
  if (/[\\/:*?"<>|]/.test(name)) { toast("That name has characters the SD card can't use"); return; }
  VW.path = `${FM.path}/${name}`; VW.data = { text: "" }; VW.isNew = true;
  $("#vwTitle").textContent = name; $("#vwSub").textContent = `${VW.path} · new`;
  $("#vwDownload").hidden = true; $("#vwEditIr").hidden = true; $("#vwInstall").hidden = true;
  $("#viewer").hidden = false;
  openEditor(edKind(VW.path) === "js" ? 'print("Hello from Flipper Workbench!");\n' : edKind(VW.path) === "ducky" ? "REM My script\nDELAY 500\nSTRING Hello\n" : "");
  VW.dirty = true;
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
/* the same default pick as the New backup sheet: captures and app data, no apps or security keys */
const BK_SKIP = ["System Volume Information", "update", "u2f", "apps", "apps_manifests"];
function bkDefaultPick(folders) {
  return folders.filter((f) => !f.file && !f.name.startsWith(".") && !BK_SKIP.includes(f.name) &&
    (BK_DEFAULT.includes(f.name) || f.size < 1048576)).map((f) => f.name);
}
async function autoBackup() {
  if (S.busy || !devReady() || !S.autoBackup) return;
  const due = await api.auto_backup_due();
  if (!due.due || S.busy) return;
  const plan = await api.auto_backup_plan();     // skips when nothing changed since the last one
  if (plan.unchanged) { toast("Auto backup: nothing changed since the last one"); return; }
  if (!plan.due || !plan.names || S.busy || !devReady()) return;
  toast("Daily auto backup started: Stop in the progress bar if you need the Flipper");
  startJob("backup", () => api.backup_start(plan.names, true));
}
async function bkNew() {
  if (!needDevice()) return;
  const box = $("#bkPick");
  box.hidden = false;
  box.innerHTML = `<div class="skeleton"><span class="blk"></span>Measuring the folders on your SD card...</div>`;
  const r = await api.backup_folders();
  if (r.error) { box.innerHTML = `<div class="lib-note">${esc(r.error)}</div>`; return; }
  // never pre-pick system/rebuildable folders: Windows' own folder, dot-folders, firmware updates, apps, security keys
  r.folders = r.folders.filter((f) => f.name !== "System Volume Information");
  FM.pick = new Set(bkDefaultPick(r.folders));
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
  if (["send", "install", "files", "backup", "restore", "firmware"].includes(kind)) {
    setStage("send");
    if (MIR.state === "off") { LCD.play("radio", { fps: 4 }); LCD.say({ backup: "Backing up...", restore: "Restoring...", files: "Working on files...", firmware: "Firmware time..." }[kind] || "Beaming it over..."); }
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
      case "cli": onCli(e); break;
      case "ble_pin": (async () => {
        const pin = await promptDialog("Type the PIN shown on your Flipper", "", "Pair");
        api.ble_pin(pin || "");
      })(); break;
      case "fcheck_done":
        FL.checking = false; showFlProgress(null);
        if (e.error) toast(e.error);
        if (S.tab === "flipper") renderFlipper();
        break;
      case "update":
        $("#updatePill").hidden = false; $("#updateText").textContent = `Update ${e.version}`;
        $("#updatePill").onclick = async () => {
          if (!e.can_install) { api.open_url(e.url); return; }
          if (!(await confirmDialog(`Update to ${e.version}?`, `I'll download it, check it against GitHub's checksum, then restart into the new version. Your SDKs, built apps, library, settings and backups stay.`, "Update now"))) return;
          toast("Downloading the update...");
          const r = await api.self_update();
          if (r.error) toast(r.error); else { toast(`Restarting into ${r.version}...`); LCD.say("Brb, updating!", 5000); }
        };
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
  if (e.fw_ready) setTimeout(() => fwReady(e.fw_ready), 300);
  if (e.fw_done) {
    toast(`Updated: ${e.fw_done.fw_name} ${e.fw_done.version}`);
    setTimeout(async () => {
      if (S.lib && S.lib.length && await confirmDialog("Rebuild your apps?", "Your Flipper has new firmware. Rebuild your Library's apps for it now, so they open?", "Rebuild all")) {
        setTab("library"); setTimeout(() => $("#rebuildAll").click(), 300);
      }
    }, 1500);
  }
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
  $("#recBtn").onclick = recToggle;
  $("#frameBtn").onclick = () => {
    S.framed = !S.framed; $("#frameBtn").classList.toggle("on", S.framed); api.set_framed(S.framed);
    toast(S.framed ? "Screenshots and GIFs now come inside a drawn Flipper" : "Plain screenshots and GIFs");
  };
  $$("#flSeg button").forEach((b) => (b.onclick = () => setSub(b.dataset.sub)));
  $("#fmUp").onclick = () => loadFiles(FM.path.split("/").slice(0, -1).join("/") || "/ext");
  $("#fmRefresh").onclick = () => loadFiles(FM.path);
  $("#fmNew").onclick = fmNewFolder;
  $("#fmUpload").onclick = () => { if (needDevice()) startJob("files", () => api.files_upload(FM.path)); };
  $("#bkNew").onclick = bkNew;
  $$(".tab").forEach((t) => (t.onclick = () => setTab(t.dataset.tab)));
  $$("#afterSeg button").forEach((b) => (b.onclick = () => setAfter(b.dataset.after, true)));
  $("#buildBtn").onclick = build;
  $("#lcd").onclick = () => {
    if (MIR.state === "live") { setLcdFull(!document.body.classList.contains("lcd-full")); return; }
    wake(); LCD.poke(); if (!S.busy) LCD.say(TIPS[Math.floor(Math.random() * TIPS.length)], 3000);
  };
  $("#cliInput").addEventListener("keydown", cliKey);
  $("#bkAuto").onchange = () => { S.autoBackup = $("#bkAuto").checked; api.set_auto_backup(S.autoBackup); toast(S.autoBackup ? "Auto backup on: once a day when you plug in, last 5 kept" : "Auto backup off"); };
  $("#vwClose").onclick = closeViewer;
  $("#viewer").addEventListener("mousedown", (e) => { if (e.target.id === "viewer") closeViewer(); });
  $("#vwEdit").onclick = () => openEditor();
  $("#vwEditIr").onclick = () => openIrEditor();
  $("#muPick").onclick = async () => muShow(await api.music_pick());
  $("#muConvert").onclick = async () => { const t = $("#muText").value.trim(); if (!t) { muStatus("Paste a ringtone or pick a file", true); return; } muShow(await api.music_convert("ringtone.txt", t)); };
  $("#muPlay").onclick = muPlay;
  $("#muSave").onclick = async () => { if (!needDevice() || !TL.music) return; const r = await api.music_save($("#muName").value, TL.music.fmf); toast(r.error || `Saved: ${r.path}. Open it in the Music Player app.`); };
  // NFC maker
  nfRenderKinds(); nfRenderFields();
  $("#nfTag").onchange = nfUpdate;
  $("#nfName").addEventListener("input", () => delete $("#nfName").dataset.auto);
  $("#nfAdd").onclick = () => {
    const cur = nfCurrent();
    if (!nfFilled(cur)) { nfStatus("Fill in this record first", true); return; }
    if (!NF.last) { nfStatus("Fix this record first", true); return; }
    NF.recs.push(cur); nfRenderRecs(); nfRenderFields(); nfUpdate();
    nfStatus("Added. Fill in the next one, or save.");
  };
  $("#nfSave").onclick = () => nfSave(false);
  $("#nfEmu").onclick = () => nfSave(true);
  $("#nfExport").onclick = async () => {
    if (!NF.last) { nfStatus("Fill in the record first", true); return; }
    const r = await api.nfc_export($("#nfName").value, NF.last.nfc);
    if (r.error) toast(r.error); else if (r.ok) toast("Saved on this PC");
  };
  $("#anPick").onclick = async () => { const r = await api.anim_pick(); if (r.error) { toast(r.error); return; } if (!r.ok) return; TL.anim = { frames: [], i: 0 }; $("#anName").value = r.name.replace(/[^A-Za-z0-9_]+/g, "_").slice(0, 40); animRebuild(); };
  ["#anFit", "#anDither", "#anFps", "#anInv"].forEach((s) => ($(s).onchange = animRebuild));
  ["#anThr", "#anCon"].forEach((s) => ($(s).onchange = animRebuild));
  $("#anInstall").onclick = async () => {
    if (!needDevice()) return;
    $("#anInstall").disabled = true; toast("Installing the animation...");
    const r = await api.anim_install($("#anName").value, +$("#anWeight").value);
    $("#anInstall").disabled = false;
    if (r.error) { toast(r.error); return; }
    $("#anNote").innerHTML = r.momentum ? `Installed to the <b>Workbench</b> asset pack. On your Flipper, pick it in <b>Momentum Settings &gt; Interface &gt; Graphics &gt; Asset pack</b> to see it.` : `Installed to /ext/dolphin. It plays among your desktop animations after the Flipper restarts.`;
    animListMade();
  };
  $('.tool[data-tool="packs"]').addEventListener("toggle", (e) => { if (e.target.open) packsLoad(); });
  $("#syAdd").onclick = syAdd;
  $("#coInstall").onclick = async () => { if (!needDevice()) return; startJob("install", () => api.companion_install()); };
  $$("#discSeg button").forEach((b) => (b.onclick = () => setDisc(b.dataset.disc)));
  $("#vwDownload").onclick = () => { const p = VW.path; $("#viewer").hidden = true; startJob("files", () => api.files_download(p)); };
  $("#edCancel").onclick = async () => {
    if (VW.isNew) { closeViewer(); return; }
    if (VW.dirty && !(await confirmDialog("Discard your changes?", "The edits haven't been saved to the Flipper.", "Discard"))) return;
    VW.dirty = false; openViewer(VW.path, VW.data && VW.data.size);
  };
  $("#edSave").onclick = () => edSave(false);
  $("#edRun").onclick = () => edSave(true);
  $("#edText").addEventListener("input", () => { VW.dirty = true; edRender(); });
  $("#edText").addEventListener("scroll", () => { $("#edHl").scrollTop = $("#edText").scrollTop; $("#edHl").scrollLeft = $("#edText").scrollLeft; });
  $("#edText").addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); document.execCommand("insertText", false, "    "); }
    else if (e.ctrlKey && e.key.toLowerCase() === "s") { e.preventDefault(); edSave(false); }
  });
  $("#flClean").onclick = openCleanup;
  $("#flMap").onclick = openSdMap;
  $("#rmToggle").onclick = rmToggle;
  $("#btScan").onclick = btScan;
  $("#btBtn").onclick = btQuick;
  $("#amToggle").onchange = () => { S.autoMirror = $("#amToggle").checked; api.set_auto_mirror(S.autoMirror); if (S.autoMirror) autoMirror(); };
  $('.tool[data-tool="board"]').addEventListener("toggle", (e) => { if (e.target.open) bdShow(); });
  $("#bdUseOfficial").onclick = async () => { const c = $("#bdOfficial").value; if (!c) return; $("#bdPlan").textContent = "Downloading and checking..."; bdShowPlan(await api.devboard_use_official(c)); };
  $("#bdPick").onclick = async () => bdShowPlan(await api.devboard_pick());
  $("#bdLook").onclick = bdPorts;
  $("#bdPort").onchange = bdButtons;
  $("#bdFlash").onclick = async () => {
    const port = $("#bdPort").value; if (!BD.plan || !port) return;
    if (!(await confirmDialog("Flash the Wi-Fi dev board?", `<b>${esc(BD.plan.source)}</b> goes onto the board on <code>${esc(port)}</code>, replacing its firmware. Your Flipper isn't touched.`, "Flash"))) return;
    startJob("files", () => api.devboard_flash(port));
  };
  const bd = $("#bdBody");
  bd.addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) { e.preventDefault(); e.stopPropagation(); bd.classList.add("drop"); } });
  bd.addEventListener("dragleave", () => bd.classList.remove("drop"));
  bd.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    bd.classList.remove("drop");
    if (!f) return;
    e.preventDefault(); e.stopPropagation();
    bdDropFile(f);
  });
  $("#rmCopy").onclick = () => { api.copy($("#rmUrl").textContent); toast("Link copied"); };
  $('.tool[data-tool="devkit"]').addEventListener("toggle", (e) => { if (e.target.open) dkShow(); });
  $("#dkDir").onclick = async () => { const r = await api.devkit_pick_dir(); if (r.path) { DK.dir = r.path; dkShow(); } };
  $("#dkCreate").onclick = dkCreate;
  $("#logFollow").onclick = async () => {
    if (CLI.follow) { api.log_follow_stop(); return; }          // ends with a done event
    if (!needDevice()) return;
    CLI.follow = true; CLI.partial = ""; $("#logFollow").classList.add("on");
    $("#logFollow").lastChild.textContent = "Stop following";
    cliPrint(`>: log ${$("#logLevel").value}${$("#logFilter").value ? `   (showing lines with "${$("#logFilter").value}")` : ""}   (other USB actions keep working)\n`, "cmd");
    const r = await api.log_follow_start($("#logLevel").value);
    if (r && r.error) { cliPrint(r.error + "\n", "err"); CLI.follow = false; $("#logFollow").classList.remove("on"); $("#logFollow").lastChild.textContent = "Follow log"; }
  };
  $('.tool[data-tool="mfkeys"]').addEventListener("toggle", (e) => { if (e.target.open && !MK.loaded) { MK.loaded = true; mkLoad(); } });
  $("#mkLoad").onclick = mkLoad;
  $("#mkAddBtn").onclick = () => {
    const found = ($("#mkAdd").value.toUpperCase().match(/[0-9A-F]{12}/g) || []).filter((k) => !MK.keys.includes(k));
    MK.keys.push(...new Set(found)); MK.dirty = MK.dirty || found.length > 0; $("#mkAdd").value = ""; mkRender();
    mkStatus(found.length ? `Added ${found.length}` : "No new 12-character keys found");
  };
  $("#mkSave").onclick = async () => {
    if (!needDevice()) return;
    const r = await api.mf_dict_save(MK.keys);
    if (r.error) { mkStatus(r.error); return; }
    MK.dirty = false; mkStatus(`Saved ${r.count} keys to the Flipper`);
  };
  $("#flFirmware").onclick = openFirmware;
  $("#fmNewFile").onclick = () => (devReady() ? fmNewFile() : needDevice());
  $("#cliStop").onclick = () => api.cli_stop();
  $("#cliClear").onclick = () => { $("#cliOut").innerHTML = ""; $("#cliInput").focus(); };
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
  $("#devicePill").onclick = async () => {
    const r = await api.flipper_ports();
    if ((r.ports || []).length > 1) {          // several Flippers: pick one
      const list = r.ports.map((p, i) => `${i + 1}. ${p.port}${p.name ? " (" + p.name + ")" : ""}${p.port === r.selected ? " - in use" : ""}`).join("<br>");
      const asking = promptDialog(`Which Flipper? Type its number:`, "1", "Use it");
      $("#cfBody").innerHTML = list;
      const ans = await asking;
      const pick = r.ports[parseInt(ans || "", 10) - 1];
      if (!pick) { if (ans !== null) toast(`Pick 1 to ${r.ports.length}: ${r.ports.map((p) => p.port).join(", ")}`); return; }
      const x = await api.flipper_select(pick.port);
      toast(x.error || `Using the Flipper on ${pick.port}`);
      return;
    }
    api.refresh_device(); toast("Looking for your Flipper...");
  };
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
    const typing = ["url", "search", "pickSearch", "flSearch", "cfInput", "cliInput"].includes(document.activeElement && document.activeElement.id);
    if (!$("#viewer").hidden) { if (e.key === "Escape" && $("#confirm").hidden) { e.preventDefault(); closeViewer(); } return; }
    if (CLI.running && e.ctrlKey && e.key.toLowerCase() === "c" && !window.getSelection().toString()) { e.preventDefault(); api.cli_stop(); return; }
    if (e.key === "F11" && MIR.state === "live") { e.preventDefault(); setLcdFull(!document.body.classList.contains("lcd-full")); return; }
    if (e.key === "Escape" && document.body.classList.contains("lcd-full")) { e.preventDefault(); setLcdFull(false); return; }
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
  window.addEventListener("dragenter", (e) => {
    e.preventDefault();
    if (bdOpen() && [...e.dataTransfer.types].includes("Files")) { $("#bdBody").classList.add("drop"); return; }   // firmware for the dev board
    dragDepth++; $("#drop").classList.add("on");
  });
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#drop").classList.remove("on"); } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); dragDepth = 0; $("#drop").classList.remove("on");
    if (bdOpen() && e.dataTransfer.files && e.dataTransfer.files.length) { $("#bdBody").classList.remove("drop"); bdDropFile(e.dataTransfer.files[0]); return; }
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
  S.autoMirror = info.auto_mirror !== false; $("#amToggle").checked = S.autoMirror;
  S.framed = !!info.frame_shots; $("#frameBtn").classList.toggle("on", S.framed);
  S.autoBackup = info.auto_backup !== false; $("#bkAuto").checked = S.autoBackup;
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

window.fb = { onEvents, onFrame, LCD, S, MIR, FM, openViewer };
let started = false;
function start(a) { if (started) return; started = true; api = a; boot(); }
window.addEventListener("pywebviewready", () => start(window.pywebview.api));
setTimeout(() => { if (!started) start(window.pywebview ? window.pywebview.api : mockApi()); }, 700);
})();
