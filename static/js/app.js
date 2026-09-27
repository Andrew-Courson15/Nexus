/* NEXUS front end — vanilla JS, hash-routed single page. */
"use strict";

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const label = (s) => String(s ?? "").replace(/_/g, " ");
const localISODate = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const ordinal = (n) => n + ([, "st", "nd", "rd"][(n % 100 >> 3 ^ 1) && n % 10] || "th");
const fmtDate = (iso) =>
  new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const fmtDateTime = (iso) =>
  new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
// Picture formats NEXUS accepts. Anything the browser can't draw is shown through a server-made preview.
const IMAGE_EXT = /\.(jpe?g|jfif|png|gif|webp|bmp|dib|tiff?|tga|psd|psb|heic|heif|avif|svg|wmf|emf|ico|jp2|pcx|ppm|dds|ai|procreate|kra|ora)$/i;
const isImageFile = (f) => f.type.startsWith("image/") || IMAGE_EXT.test(f.name);
// The entry's picture (an image upload, or a document with a preview such as a PDF).
const picOf = (e) => e.attachments.find((a) => a.kind === "image" && a.preview_url) || e.attachments.find((a) => a.preview_url);
// A preview that can't be drawn (e.g. WMF on a Mac) becomes a small note instead of a broken image.
document.addEventListener("error", (ev) => {
  const img = ev.target;
  if (img.tagName !== "IMG" || img.dataset.fallback === "done") return;
  if (img.dataset.thumb && img.getAttribute("src") !== img.dataset.thumb) {  // e.g. offline: the small copy is saved
    img.src = img.dataset.thumb;
    return;
  }
  img.dataset.fallback = "done";
  if (img.dataset.local !== undefined) { img.remove(); return; }
  const note = document.createElement("span");
  note.className = "no-preview small muted";
  note.textContent = "No preview for this format";
  img.replaceWith(note);
}, true);

function store(key, value, session = false) {
  try {
    const st = session ? sessionStorage : localStorage;
    if (value === undefined) return st.getItem(key);
    if (value === null) st.removeItem(key);
    else st.setItem(key, value);
  } catch { /* storage unavailable: ignore */ }
  return null;
}

let toastTimer;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), isError ? 5000 : 2600);
}

/**
 * Call the NEXUS API. On the phone, when NEXUS can't be reached, read requests are
 * answered from the offline copy saved on this device (see "offline copy" below).
 */
async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const useCopy = HAS_OFFLINE_COPY && !opts.noCopy && (method === "GET" || path === "/api/search");
  // Known to be off (the background check notices when it's back): answer instantly instead of waiting again.
  if (useCopy && (recentlyUnreachable() || !$("#offline-bar").hidden)) {
    const saved = await offlineAnswer(path, opts);
    if (saved !== undefined) return saved;
  }
  // With a copy to fall back on, don't wait long on a PC that may be off (over Tailscale it hangs).
  let netOpts = opts;
  if (useCopy && method === "GET" && opts.timeout === undefined && path !== "/api/settings" && (await offlineCopy.snapshot())) {
    netOpts = { ...opts, timeout: 4000 };
  }
  try {
    const data = await apiNetwork(path, netOpts);
    if (useCopy) {
      showOfflineBar(false);
      if (method === "GET" && COPY_LISTS.includes(path)) offlineCopy.put("url:" + path, data).catch(() => {});
    }
    return data;
  } catch (err) {
    if (!err.offline || !useCopy) throw err;
    const saved = await offlineAnswer(path, opts);
    if (saved === undefined) throw err;
    return saved;
  }
}

async function apiNetwork(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  delete init.noCopy;
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
    delete init.json;
  }
  // When the PC is off, requests over Tailscale hang instead of failing, so every request
  // gets a time limit (longer for file uploads on a slow connection).
  const ctrl = new AbortController();
  const limit = opts.timeout ?? (init.body instanceof FormData ? 120000 : 10000);
  const timer = setTimeout(() => ctrl.abort(), limit);
  init.signal = ctrl.signal;
  delete init.timeout;
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    markUnreachable();
    throw unreachable();
  } finally {
    clearTimeout(timer);
  }
  // 502-504: the private link is up but NEXUS on the PC isn't answering.
  if ([502, 503, 504].includes(res.status)) { markUnreachable(); throw unreachable(); }
  lastUnreachable = 0;
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (res.status === 401 && data?.locked) {
    showUnlock();
    const err = new Error("NEXUS is locked on this device. Enter the passcode.");
    err.locked = true;
    throw err;
  }
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg);
  }
  return data;
}

let lastUnreachable = 0;
function markUnreachable() { lastUnreachable = Date.now(); }
/** True if NEXUS failed to answer in the last 30 s: don't make the user wait again. */
const recentlyUnreachable = () => Date.now() - lastUnreachable < 30000;

const APP_VERSION = document.querySelector('meta[name="nexus-version"]')?.content || "";

/**
 * The PC was updated but this device still runs the old app (phones keep home-screen apps in
 * memory for days): reload once into the new version. Drafts are kept, so nothing is lost.
 */
function reloadIfUpdated(serverVersion) {
  if (!serverVersion || !APP_VERSION || serverVersion === APP_VERSION) return;
  try {
    if (sessionStorage.getItem("nexus.reloadedFor") === serverVersion) return;  // never loop
    sessionStorage.setItem("nexus.reloadedFor", serverVersion);
  } catch { return; }
  navigator.serviceWorker?.getRegistration().then((r) => r?.update()).catch(() => {});
  toast("NEXUS was updated. Loading the new version…");
  setTimeout(() => location.reload(), 900);
}

/** Quick check (3 s at most) whether NEXUS answers right now. */
async function reachable() {
  try {
    const r = await api("/api/ping", { timeout: 3000 });
    reloadIfUpdated(r?.version);
    return true;
  } catch (err) {
    if (err.offline) return false;
    throw err;
  }
}

function unreachable() {
  const err = new Error("Can't reach NEXUS right now.");
  err.offline = true;
  return err;
}

/* ------------------------------------------------------------ offline copy
 * On the phone, a read-only copy of the archive is kept on the device (IndexedDB),
 * refreshed while NEXUS is reachable. When the PC is off, entries, projects,
 * relationships, art analyses and word search still work from it, with small
 * thumbnails kept in the Cache API. On the PC itself (localhost) none of this runs. */
const HAS_OFFLINE_COPY = !["localhost", "127.0.0.1", "[::1]"].includes(location.hostname) && "indexedDB" in window;
const SYNC_EVERY_MS = 5 * 60 * 1000;
const THUMB_CACHE = "media-thumbs";
const MAX_OFFLINE_THUMBS = 2000;  // newest first; about 100 KB each
// Lists the offline copy keeps exactly as NEXUS sent them (refreshed whenever they're fetched).
const COPY_LISTS = ["/api/settings", "/api/tags", "/api/insights/patterns", "/api/art/gallery",
  "/api/art/preferences", "/api/art/knowledge", "/api/art/topics"];

const offlineCopy = {
  _db: null,
  _snap: null,
  open() {
    if (this._db) return this._db;
    this._db = new Promise((resolve, reject) => {
      const req = indexedDB.open("nexus-offline", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("kv");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this._db;
  },
  async _tx(mode, fn) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("kv", mode);
      const req = fn(tx.objectStore("kv"));
      tx.oncomplete = () => resolve(req?.result);
      tx.onerror = () => reject(tx.error);
    });
  },
  get(key) { return this._tx("readonly", (st) => st.get(key)); },
  put(key, value) { return this._tx("readwrite", (st) => st.put(value, key)); },
  async snapshot() {
    // Read the stored copy once; a copy saved meanwhile (first sync) must never be replaced by
    // this older read finishing later.
    this._loading ??= this.get("snapshot").catch(() => null).then((stored) => {
      if (!this._snap) this._snap = stored || null;
    });
    await this._loading;
    return this._snap;
  },
  async save(snap) {
    delete snap._byId;
    this._snap = snap;
    await this.put("snapshot", snap);
  },
};

let lastSync = 0, syncing = false, lastSyncError = "";
/** Refresh the offline copy (at most every few minutes unless forced). Never throws. */
async function syncOfflineCopy({ force = false } = {}) {
  if (!HAS_OFFLINE_COPY || syncing || (!force && Date.now() - lastSync < SYNC_EVERY_MS)) return;
  syncing = true;
  try {
    const snap = await api("/api/offline/snapshot", { timeout: 60000, noCopy: true });
    await offlineCopy.save(snap);
    lastSync = Date.now();
    navigator.storage?.persist?.().catch(() => {});  // ask the phone not to clear it
    await Promise.allSettled(COPY_LISTS.map((u) => api(u, { timeout: 20000 })));
    lastSyncError = "";
    await saveThumbnails(snap);
  } catch (err) {  // unreachable, locked, or storage full: keep the previous copy, and say why in Settings
    lastSyncError = err.offline ? "" : err.locked ? "This device is locked (enter the passcode)." : err.message || String(err);
  } finally {
    syncing = false;
    window.dispatchEvent(new Event("nexus:synced"));
  }
}

async function saveThumbnails(snap) {
  if (!("caches" in window)) return;
  const cache = await caches.open(THUMB_CACHE);
  const wanted = [];
  for (const e of snap.entries) {
    const a = picOf(e);
    if (a?.thumb_url) wanted.push(a.thumb_url);
    if (wanted.length >= MAX_OFFLINE_THUMBS) break;
  }
  const keep = new Set(wanted.map((u) => new URL(u, location.href).href));
  for (const req of await cache.keys()) if (!keep.has(req.url)) await cache.delete(req);
  const queue = [...wanted];
  const worker = async () => {
    while (queue.length) {
      const url = queue.shift();
      if (await cache.match(url)) continue;
      try { await cache.add(url); } catch { /* skip this one */ }
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
}

function showOfflineBar(on, savedAt) {
  const bar = $("#offline-bar");
  if (!bar || bar.hidden === !on) return;
  bar.hidden = !on;
  if (on) {
    bar.textContent = `Your PC is off or NEXUS isn't running: you're seeing the copy saved on this phone` +
      (savedAt ? ` (from ${fmtDateTime(savedAt)})` : "") + `. It's read-only; new captures are kept and sent later.`;
  }
}

const STOP_WORDS = new Set(["the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "about", "my", "me",
  "is", "are", "was", "it", "that", "this", "ideas", "idea", "things", "stuff", "any", "all", "some"]);
const stem = (w) => w.replace(/(ings|ing|ies|es|ed|s)$/, "") || w;

const AI_LINK_LABELS = { suggested: "AI suggested relationship", accepted: "Confirmed by you (AI suggested)",
  ignored: "AI suggestion — ignored", rejected: "AI suggestion — rejected" };

/** An entry's links from the offline copy, shaped like /api/entries/{id}/relationships. */
function relsFromCopy(snap, eid) {
  if (!snap.edges) return snap.relationships?.[eid] || [];  // a copy saved by an older NEXUS
  const byId = snap._byId ??= new Map(snap.entries.map((e) => [e.id, e]));
  return snap.edges.filter((x) => x[1] === eid || x[2] === eid).map(([id, source, target, type, origin, status, confidence, rationale]) => {
    const other = byId.get(source === eid ? target : source);
    return other && { id, relationship_type: type, confidence, origin, status, rationale,
      direction: source === eid ? "outgoing" : "incoming",
      other: { id: other.id, title: other.title, entry_type: other.entry_type, status: other.status },
      label: origin === "ai" ? AI_LINK_LABELS[status] : "Created by you" };
  }).filter(Boolean);
}

/** Word search over the offline copy (search by meaning needs NEXUS). */
function searchCopy(snap, query) {
  const terms = [...new Set((query.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])
    .filter((t) => t.length > 1 && !STOP_WORDS.has(t)))];
  const stems = terms.map(stem);
  const results = [];
  for (const e of snap.entries) {
    const fields = [e.title, e.original_text, e.user_notes, e.transcript, e.ai_summary, e.ai_interpretation,
      e.tags.map((t) => t.name).join(" "), (e.ai_metadata?.concepts || []).join(" "),
      e.attachments.map((a) => a.original_filename.replace(/\.[^.]+$/, "").replace(/[\W_]+/g, " ")).join(" ")].filter(Boolean);
    const text = fields.join("\n").toLowerCase();
    const title = (e.title || "").toLowerCase();
    let score = 0;
    stems.forEach((st) => { if (text.includes(st)) score += title.includes(st) ? 3 : 1; });
    if (!score) continue;
    const source = fields.find((f) => stems.some((st) => f.toLowerCase().includes(st))) || "";
    const at = Math.max(0, Math.min(...stems.map((st) => source.toLowerCase().indexOf(st)).filter((i) => i >= 0)) - 60);
    let snippet = (at ? "…" : "") + source.slice(at, at + 180) + (source.length > at + 180 ? "…" : "");
    for (const st of stems) snippet = snippet.replace(new RegExp(`(${st.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\p{L}*)`, "giu"), "\u0001$1\u0002");
    results.push({ entry: e, score, snippet, matched_by: ["words"], similarity: null });
  }
  results.sort((a, b) => b.score - a.score);
  return { query, mode: "offline", semantic_enabled: false, relaxed: false,
    interpreted: { terms, entry_types: [], statuses: [], source_types: [] },
    note: "Searching the copy saved on this phone (matching words only). Search by meaning works when NEXUS is on.",
    results: terms.length ? results.slice(0, 50) : [] };
}

function listFromCopy(snap, q) {
  const many = (k) => q.getAll(k).join(",").split(",").filter(Boolean);
  const types = many("entry_type"), statuses = many("status"), sources = many("source_type");
  const pid = q.get("project_id"), tag = (q.get("tag") || "").trim().toLowerCase();
  const from = q.get("date_from"), to = q.get("date_to");
  const items = snap.entries.filter((e) =>
    (!types.length || types.includes(e.entry_type)) && (!statuses.length || statuses.includes(e.status))
    && (!sources.length || sources.includes(e.source_type)) && (!pid || e.projects.some((p) => String(p.id) === pid))
    && (!tag || e.tags.some((t) => t.name === tag))
    && (!from || e.created_at.slice(0, 10) >= from) && (!to || e.created_at.slice(0, 10) <= to));
  const by = {
    newest: (a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id,
    oldest: (a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id,
    modified: (a, b) => b.updated_at.localeCompare(a.updated_at) || b.id - a.id,
    alpha: (a, b) => (a.title || "").toLowerCase().localeCompare((b.title || "").toLowerCase()) || a.id - b.id,
  }[q.get("sort") || "newest"] || ((a, b) => b.created_at.localeCompare(a.created_at));
  items.sort(by);
  const offset = +q.get("offset") || 0, limit = +q.get("limit") || 60;
  return { items: items.slice(offset, offset + limit), total: items.length };
}

/** Answer a read request from the offline copy. `undefined` means the copy can't answer it. */
/** /api/characters from the offline copy (same rules as the server: links, then whole-word name mentions). */
const KIND_OF = { character: ["character", ["character"]], world: ["worldbuilding", ["world", "place", "location", "setting", "worldbuilding"]] };
function charactersFromCopy(snap, kind = "character") {
  const [kindType, kindTags] = KIND_OF[kind] || KIND_OF.character;
  const MAX = 24, entries = snap.entries, byId = snap._byId ??= new Map(entries.map((e) => [e.id, e]));
  const ref = (e) => { const pic = picOf(e);
    return { id: e.id, title: e.title, entry_type: e.entry_type, created_at: e.created_at, thumb_url: pic?.thumb_url || null, excerpt: e.excerpt || e.ai_summary }; };
  const edges = snap.edges || [];
  return entries.filter((e) => e.entry_type === kindType || e.tags.some((t) => kindTags.includes(t.name))).map((c) => {
    const seen = new Set([c.id]), links = [];
    for (const [, source, target, type, origin, status] of edges) {
      const other = source === c.id ? target : target === c.id ? source : null;
      if (other === null || seen.has(other) || !byId.has(other)) continue;
      seen.add(other);
      links.push({ entry: ref(byId.get(other)), relationship_type: type, direction: source === c.id ? "outgoing" : "incoming", origin, status });
    }
    const t = (c.title || "").trim();
    const name = t.length >= 3 && t.toLowerCase() !== "untitled" && t.length <= 40 && t.split(/\s+/).length <= 4 ? t : null;
    let mentions = [];
    if (name) {
      const re = new RegExp(`(?<![\\p{L}\\p{N}_])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\p{L}\\p{N}_])`, "iu");
      mentions = entries.filter((e) => !seen.has(e.id) && re.test([e.title, e.original_text, e.transcript, e.user_notes].filter(Boolean).join(" ")));
    }
    return { character: c, name_searched: name, link_count: links.length, mention_count: mentions.length,
      links: links.slice(0, MAX), mentions: mentions.slice(0, MAX).map(ref) };
  }).sort((a, b) => (b.link_count + b.mention_count) - (a.link_count + a.mention_count));
}

/** /api/insights/rediscover from the offline copy (same rules; the phone's own daily pick). */
function rediscoverFromCopy(snap, today) {
  const [y, mo, d] = today.split("-").map(Number), monthStart = new Date(y, mo - 1, 1), monthAgo = Date.now() - 30 * 864e5;
  const onThisDay = snap.entries.filter((e) => { const c = new Date(e.created_at); return c.getDate() === d && c < monthStart; }).slice(0, 12);
  const seen = new Set(onThisDay.map((e) => e.id));
  const stale = snap.entries.filter((e) => !seen.has(e.id) && ["raw", "exploring", "developing", "active"].includes(e.status)
    && new Date(e.updated_at) < monthAgo).sort((a, b) => a.id - b.id);
  let seed = y * 372 + mo * 31 + d;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  for (let i = stale.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [stale[i], stale[j]] = [stale[j], stale[i]]; }
  return { today, on_this_day: onThisDay, resurfaced: stale.slice(0, 3) };
}

async function offlineAnswer(path, opts = {}) {
  const snap = await offlineCopy.snapshot();
  const [p, qs] = path.split("?");
  const q = new URLSearchParams(qs || "");
  const entry = (id) => snap?.entries.find((e) => e.id === +id);
  let m, out;
  if (snap) {
    if (p === "/api/entries") out = listFromCopy(snap, q);
    else if ((m = p.match(/^\/api\/entries\/(\d+)$/))) out = entry(m[1]);
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/relationships$/))) out = entry(m[1]) && relsFromCopy(snap, +m[1]);
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/revisions$/))) out = entry(m[1]) && [];
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/art$/))) out = entry(m[1]) && (snap.art[m[1]] || []);
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/evolution$/))) out = entry(m[1]) && [entry(m[1])];
    else if ((m = p.match(/^\/api\/art\/analyses\/(\d+)$/))) out = Object.values(snap.art).flat().find((a) => a.id === +m[1]);
    else if (p === "/api/projects") out = q.get("trashed") === "true" ? [] : snap.projects;
    else if ((m = p.match(/^\/api\/projects\/(\d+)$/))) {
      const proj = snap.projects.find((x) => x.id === +m[1]);
      out = proj && { ...proj, entries: snap.entries.filter((e) => e.projects.some((x) => x.id === proj.id)) };
    } else if ((m = p.match(/^\/api\/projects\/(\d+)\/bible$/))) {
      const proj = snap.projects.find((x) => x.id === +m[1]);
      out = proj && { project: proj, entries: snap.entries.filter((e) => e.projects.some((x) => x.id === proj.id))
        .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id) };
    } else if (p === "/api/trash") out = { items: [], total: 0 };
    else if (p === "/api/search") out = searchCopy(snap, opts.json?.query || "");
    else if (p === "/api/graph") out = graphFromCopy(snap, q);
    else if (p === "/api/characters") out = charactersFromCopy(snap, q.get("kind") || "character");
    else if (p === "/api/insights/rediscover") out = rediscoverFromCopy(snap, q.get("today") || localISODate());
  }
  if (out === undefined && COPY_LISTS.includes(path)) out = await offlineCopy.get("url:" + path).catch(() => undefined);
  if (out === undefined || out === null || out === false) return undefined;
  showOfflineBar(true, snap?.generated_at);
  return out;
}

/* ------------------------------------------------------------ passcode lock */
/** Full-screen "enter the passcode" when NEXUS is locked for this device. */
function showUnlock() {
  if ($("#unlock")) return;
  document.body.insertAdjacentHTML("beforeend", `<div class="unlock" id="unlock" role="dialog" aria-modal="true" aria-labelledby="unlock-title">
    <form class="unlock-card"><span class="brand-mark" aria-hidden="true"></span>
      <h2 id="unlock-title">NEXUS is locked</h2>
      <p class="small muted">Enter the passcode set on the computer NEXUS runs on. This device stays unlocked afterwards.</p>
      <input type="password" name="passcode" autocomplete="current-password" aria-label="Passcode" required>
      <button class="btn primary" type="submit">UNLOCK</button>
      <p class="small" id="unlock-msg" role="alert"></p>
      <p class="small muted">You can still <a href="#/" data-close>capture ideas</a>: they wait on this device until it's unlocked.</p>
    </form></div>`);
  const box = $("#unlock"), form = $("form", box);
  $("input", form).focus();
  $("[data-close]", box).onclick = () => box.remove();
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await apiNetwork("/api/lock/unlock", { method: "POST", json: { passcode: form.passcode.value } });
      box.remove();
      toast("Unlocked.");
      flushOutbox();
      syncOfflineCopy({ force: true });
      render();
    } catch (err) {
      $("#unlock-msg", box).textContent = err.locked ? "That passcode isn't right." : err.message;
      form.passcode.select();
    }
  };
}

/** Settings panel (on the computer): the phone's private address as a QR code to scan. */
async function mountPhone(box) {
  let d;
  try { d = await api("/api/phone-link"); } catch { box.remove(); return; }
  box.innerHTML = d.url
    ? `<h3>Your phone</h3>
      <div class="phone-link">${d.qr_svg ? `<div class="qr" aria-hidden="true">${d.qr_svg}</div>` : ""}
        <ol class="small">
          <li>Install <strong>Tailscale</strong> on the phone and sign in with the same account as this computer.</li>
          <li>Point the phone's camera at this code (or type <code>${esc(d.url)}</code> into Safari).</li>
          <li>Tap <strong>Share → Add to Home Screen</strong>.</li>
        </ol></div>`
    : `<h3>Your phone</h3><p class="small muted">To use NEXUS on your phone from anywhere, privately: install Tailscale on this
      computer, then run this once in the nexus folder:</p><p><code>${esc(d.setup_command)}</code></p>
      <p class="small muted">Afterwards a QR code to scan with your phone appears here.</p>`;
}

/** Settings panel: the passcode other devices need (set on the PC itself). */
async function mountLock(box) {
  let st;
  try { st = await api("/api/lock", { noCopy: true }); } catch { box.remove(); return; }
  if (!st.this_computer) {
    box.innerHTML = `<h3>Passcode</h3><p class="small muted">${st.enabled
      ? "This device is unlocked. The passcode is managed on the computer NEXUS runs on."
      : "No passcode is set. You can set one on the computer NEXUS runs on."}</p>
      ${st.enabled ? `<button class="btn small ghost" data-act="lock-me">Lock this device</button>` : ""}`;
    $("[data-act=lock-me]", box)?.addEventListener("click", async () => {
      await api("/api/lock/sign-out", { method: "POST" });
      showUnlock();
    });
    return;
  }
  box.innerHTML = `<h3>Passcode for other devices</h3>
    <p class="health ${st.enabled ? "ok" : "bad"}"><span class="dot"></span>${st.enabled ? "On: phones and other devices must unlock once" : "Off"}</p>
    <p class="small muted">Only you can reach NEXUS through Tailscale, but a passcode also protects it if someone picks up your
      unlocked phone. This computer never needs it.</p>
    <form class="row" id="lock-form"><input type="password" name="passcode" minlength="4" autocomplete="new-password"
      placeholder="${st.enabled ? "New passcode" : "Choose a passcode (4+ characters)"}" aria-label="Passcode" style="flex:1">
      <button class="btn small primary" type="submit">${st.enabled ? "Change" : "Set passcode"}</button></form>
    ${st.enabled ? `<div class="row" style="margin-top:8px"><button class="btn small ghost" data-act="sign-out-all">Sign out all devices</button>
      <button class="btn small ghost danger" data-act="remove-lock">Remove passcode</button></div>` : ""}`;
  $("#lock-form", box).onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await api("/api/lock/passcode", { method: "PUT", json: { passcode: ev.target.passcode.value } });
      toast(st.enabled ? "Passcode changed." : "Passcode set. Other devices will ask for it once.");
      mountLock(box);
    } catch (err) { toast(err.message, true); }
  };
  $("[data-act=sign-out-all]", box)?.addEventListener("click", async () => {
    if (!confirm("Sign out every phone and other device? They'll need the passcode again.")) return;
    await api("/api/lock/sign-out-all", { method: "POST" });
    toast("All other devices are signed out.");
  });
  $("[data-act=remove-lock]", box)?.addEventListener("click", async () => {
    if (!confirm("Remove the passcode? Devices on your Tailscale network can then open NEXUS without it.")) return;
    await api("/api/lock/passcode", { method: "DELETE" });
    toast("Passcode removed.");
    mountLock(box);
  });
}

/* ------------------------------------------------------------ offline outbox
 * When NEXUS can't be reached (phone away from the PC, PC asleep), captures
 * are kept in this device's IndexedDB and sent later. Each capture carries a
 * device-made id, so a capture that is sent twice is still saved once. */
const outbox = {
  _db: null,
  open() {
    if (this._db) return this._db;
    this._db = new Promise((resolve, reject) => {
      const req = indexedDB.open("nexus-outbox", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("items", { keyPath: "client_id" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return this._db;
  },
  async _tx(mode, fn) {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("items", mode);
      const out = fn(tx.objectStore("items"));
      tx.oncomplete = () => resolve(out?.result ?? out);
      tx.onerror = () => reject(tx.error);
    });
  },
  add(item) { return this._tx("readwrite", (s) => s.put(item)); },
  remove(id) { return this._tx("readwrite", (s) => s.delete(id)); },
  all() { return this._tx("readonly", (s) => s.getAll()); },
};

function newClientId() {
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Send one capture. item: {client_id, captured_at, text?, file?, filename?, note?, project_id?} */
function sendCapture(item, timeout) {
  if (item.file) {
    const fd = new FormData();
    fd.append("file", item.file, item.filename || item.file.name || "capture");
    if (item.note) fd.append("note", item.note);
    if (item.project_id) fd.append("project_id", item.project_id);
    fd.append("client_id", item.client_id);
    fd.append("captured_at", item.captured_at);
    return api("/api/uploads", { method: "POST", body: fd, timeout });
  }
  return api("/api/entries", {
    method: "POST",
    timeout,
    json: { original_text: item.text, project_ids: item.project_id ? [item.project_id] : [],
            client_id: item.client_id, captured_at: item.captured_at },
  });
}

/** Save now if NEXUS is reachable, otherwise keep it on this device. Returns {entry} or {queued: true}. */
async function captureOrQueue(fields) {
  const item = { client_id: newClientId(), captured_at: new Date().toISOString(), ...fields };
  try {
    if (recentlyUnreachable() || !$("#offline-bar").hidden) throw unreachable();  // known offline: save instantly
    // Files can take a while to upload, so check first; text just gets a short time limit.
    // (If a timed-out capture did arrive after all, its id stops the re-send duplicating it.)
    if (item.file && !(await reachable())) throw unreachable();
    return { entry: await sendCapture(item, item.file ? undefined : 5000) };
  } catch (err) {
    if (!err.offline && !err.locked) throw err;  // locked: keep it here until this device is unlocked
    try {
      await outbox.add(item);
    } catch {
      throw new Error("NEXUS can't be reached and this browser can't store ideas offline.");
    }
    refreshOutbox();
    return { queued: true };
  }
}

let flushing = false;
async function flushOutbox({ quiet = true } = {}) {
  if (flushing) return;
  flushing = true;
  let sent = 0;
  try {
    const items = (await outbox.all()).sort((a, b) => a.captured_at.localeCompare(b.captured_at));
    for (const item of items) {
      try {
        await sendCapture(item);
        await outbox.remove(item.client_id);
        sent++;
      } catch (err) {
        if (err.offline || err.locked) break;  // unreachable or locked: try again later
        // Rejected by the server (e.g. file too large): keep it, but say why.
        item.error = err.message;
        await outbox.add(item);
      }
    }
  } catch { /* storage unavailable */ } finally {
    flushing = false;
  }
  await refreshOutbox();
  if (sent) {
    toast(`Sent ${sent} idea${sent === 1 ? "" : "s"} saved on this device.`);
    syncOfflineCopy({ force: true });
    if (["", "#/", "#/archive", "#/today", "#/ideas"].includes(location.hash)) render();
  } else if (!quiet) {
    toast("Still can't reach NEXUS. Your ideas are safe on this device.", true);
  }
}

async function refreshOutbox() {
  const btn = $("#outbox");
  let items = [];
  try { items = await outbox.all(); } catch { /* no storage */ }
  btn.hidden = !items.length;
  const failed = items.filter((i) => i.error).length;
  btn.textContent = `${items.length} to sync`;
  btn.title = failed ? `Couldn't send ${failed}: ${items.find((i) => i.error).error}` : "Saved on this device. Tap to send now.";
  btn.classList.toggle("error", failed > 0);
}

let settingsCache = null;
async function getSettings(force = false) {
  if (!settingsCache || force) settingsCache = await api("/api/settings");
  return settingsCache;
}

function options(values, selected, blank) {
  return (blank !== undefined ? `<option value="">${esc(blank)}</option>` : "") +
    values.map((v) => `<option value="${esc(v)}"${v === selected ? " selected" : ""}>${esc(label(v))}</option>`).join("");
}

/* ------------------------------------------------------------- processing */
const ACTIVE = ["queued", "transcribing", "analyzing"];
const isBusy = (e) => ACTIVE.includes(e.processing_status);
const busyLabel = (st) => ({ queued: "waiting to be organized…", transcribing: "transcribing…", analyzing: "organizing…" })[st] || "";

/**
 * Poll an entry until its background work finishes, then call onDone(entry).
 * Stops by itself when `node` leaves the page (the user navigated away).
 */
function watchEntry(id, node, onDone, onProgress) {
  let last = null;
  const tick = async () => {
    if (!node.isConnected) return;
    let e;
    try { e = await api(`/api/entries/${id}`); } catch { return setTimeout(tick, 4000); }
    if (!node.isConnected) return;
    if (isBusy(e)) {
      if (e.processing_status !== last) { last = e.processing_status; onProgress?.(e); }
      setTimeout(tick, 2000);
    } else onDone(e);
  };
  setTimeout(tick, 1500);
}

/* ------------------------------------------------------------- components */
function tagsHTML(tags, removable = false) {
  return tags
    .map((t) => `<span class="tag ${t.source}" title="${t.source === "ai" ? "AI-generated tag" : "Your tag"}">${esc(t.name)}${
      removable ? `<button type="button" data-remove-tag="${esc(t.name)}" aria-label="Remove tag ${esc(t.name)}">×</button>` : ""}</span>`)
    .join("");
}

function cardHTML(e, extra = "") {
  const img = picOf(e);
  const audio = e.attachments.find((a) => a.kind === "audio");
  return `<a class="card" href="#/entry/${e.id}">
    ${img ? `<img class="thumb" src="${esc(img.thumb_url || img.preview_url)}" alt="" loading="lazy">` : ""}
    <div class="meta">
      <span class="pill type">${esc(label(e.entry_type))}</span>
      <span class="pill status-${esc(e.status)}">${esc(e.status)}</span>
      ${audio ? `<span class="pill">voice</span>` : ""}
      ${e.shared_from ? `<span class="pill shared" title="Imported from ${esc(e.shared_from.by)}'s NEXUS">from ${esc(e.shared_from.by)}</span>` : ""}
      ${isBusy(e) ? `<span class="pill busy">${esc(busyLabel(e.processing_status))}</span>` : ""}
      <span class="spacer"></span><span>${fmtDate(e.created_at)}</span>
    </div>
    <h3>${esc(e.title)}</h3>
    ${e.ai_summary ? `<p class="summary" title="AI summary">${esc(e.ai_summary)}</p>`
      : e.excerpt ? `<p class="summary">${esc(e.excerpt)}</p>` : ""}
    ${extra}
    ${e.tags.length ? `<div class="meta">${tagsHTML(e.tags.slice(0, 6))}</div>` : ""}
    ${e.projects.length ? `<div class="meta">◆ ${e.projects.map((p) => esc(p.name)).join(", ")}</div>` : ""}
  </a>`;
}

function gridHTML(entries, emptyMsg) {
  return entries.length ? `<div class="grid">${entries.map((e) => cardHTML(e)).join("")}</div>`
    : `<p class="empty">${esc(emptyMsg)}</p>`;
}

const LEGEND = `<div class="legend" aria-label="Legend">
  <span class="l-user">User said</span><span class="l-ai">AI interpretation</span>
  <span class="l-infer">AI inference</span><span class="l-unknown">Unknown</span></div>`;

function prov(kind, title, bodyHTML, why = "") {
  return `<section class="prov ${kind}"><div class="prov-label"><span>${esc(title)}</span>${why ? `<span class="why">${esc(why)}</span>` : ""}</div>${bodyHTML}</section>`;
}

/* ---------------------------------------------------------------- capture */
/**
 * Mounts the capture box. `opts.projectId` files captures into a project.
 * `opts.onCaptured(entry)` is called after each successful capture.
 */
function mountCapture(root, opts = {}) {
  const draftKey = "nexus.draft" + (opts.projectId ? `.p${opts.projectId}` : "");
  root.innerHTML = `
    <div class="capture">
      <textarea id="cap-text" placeholder="What are you thinking about?" aria-label="What are you thinking about?"></textarea>
      <div class="capture-bar">
        <button class="btn primary" data-act="capture">CAPTURE</button>
        <button class="btn rec" data-act="record"><span class="dot"></span><span>Record Idea</span></button>
        <button class="btn" data-act="upload">Upload image / file</button>
        <input type="file" hidden multiple accept="image/*,audio/*,.pdf,.txt,.md,.doc,.docx,.tif,.tiff,.tga,.psd,.psb,.heic,.heif,.svg,.wmf,.emf,.ai,.procreate,.kra,.ora">
        <span class="spacer"></span>
        <span class="hint">Ctrl/⌘ + Enter to capture · drop files here</span>
      </div>
      <div class="pending" hidden></div>
    </div>`;
  const box = $(".capture", root);
  const ta = $("textarea", root);
  const fileInput = $("input[type=file]", root);
  const pending = $(".pending", root);
  const recBtn = $("[data-act=record]", root);
  ta.value = store(draftKey) || "";
  ta.addEventListener("input", () => store(draftKey, ta.value || null));

  async function capture() {
    const text = ta.value;
    if (!text.trim()) { ta.focus(); return; }
    const btn = $("[data-act=capture]", root);
    btn.disabled = true;
    btn.textContent = "SAVING…";
    try {
      const r = await captureOrQueue({ text, project_id: opts.projectId || null });
      ta.value = "";
      store(draftKey, null);
      if (r.queued) {
        toast("Saved on this device. It will be sent to NEXUS when it's reachable.");
      } else {
        toast("Captured. Original saved exactly as written.");
        opts.onCaptured?.(r.entry);
      }
    } catch (err) {
      toast("Could not capture: " + err.message + " — your text is still here.", true);
    } finally {
      btn.disabled = false;
      btn.textContent = "CAPTURE";
    }
  }

  function showPending(file, previewURL, kindLabel) {
    pending.hidden = false;
    pending.innerHTML = `<div class="recorder">
      <div class="row"><strong>${esc(kindLabel)}</strong><span class="muted small">${esc(file.name)} · ${fmtSize(file.size)}</span></div>
      ${file.type.startsWith("audio/") ? `<audio controls src="${previewURL}"></audio>` : ""}
      ${isImageFile(file) ? `<img src="${previewURL}" alt="" data-local style="max-height:220px;border-radius:8px">` : ""}
      <input type="text" placeholder="Add a few words about it (optional — stored as your words)" aria-label="Note">
      <div class="row"><button class="btn primary small" data-act="save">SAVE</button><button class="btn ghost small" data-act="discard">Discard</button></div>
    </div>`;
    const note = $("input", pending);
    if (ta.value.trim()) note.value = ta.value.trim();
    $("[data-act=discard]", pending).onclick = () => { pending.hidden = true; pending.innerHTML = ""; URL.revokeObjectURL(previewURL); };
    $("[data-act=save]", pending).onclick = async (ev) => {
      ev.target.disabled = true;
      ev.target.textContent = "SAVING…";
      try {
        const r = await captureOrQueue({
          file, filename: file.name, note: note.value.trim() ? note.value : null, project_id: opts.projectId || null,
        });
        if (note.value.trim() && note.value.trim() === ta.value.trim()) { ta.value = ""; store(draftKey, null); }
        pending.hidden = true;
        pending.innerHTML = "";
        URL.revokeObjectURL(previewURL);
        if (r.queued) {
          toast("Saved on this device. It will be sent to NEXUS when it's reachable.");
        } else {
          toast("Saved. The original file is stored untouched.");
          opts.onCaptured?.(r.entry);
        }
      } catch (err) {
        ev.target.disabled = false;
        ev.target.textContent = "SAVE";
        toast("Upload failed: " + err.message, true);
      }
    };
  }

  async function takeFiles(files) {
    files = [...files];
    if (files.length <= 1) return takeFile(files[0]);
    // Several files (e.g. panels of a page): each becomes its own entry, in order.
    pending.hidden = false;
    const shown = files.slice(0, 8).map((f) => esc(f.name)).join(", ") + (files.length > 8 ? ` and ${files.length - 8} more` : "");
    pending.innerHTML = `<div class="recorder"><strong>${files.length} files ready</strong>
      <span class="muted small">${shown}</span>
      <input type="text" placeholder="A few words for all of them (optional)" aria-label="Note">
      <label class="check small"><input type="checkbox" data-own-dates> Date each one by the file's own date (for older work, so it lands in the right month on the Timeline)</label>
      <div class="row"><button class="btn primary small" data-act="saveall">SAVE ALL</button><button class="btn ghost small" data-act="discard">Discard</button>
        <span class="small muted" data-progress role="status"></span></div></div>`;
    $("[data-act=discard]", pending).onclick = () => { pending.hidden = true; pending.innerHTML = ""; };
    $("[data-act=saveall]", pending).onclick = async (ev) => {
      ev.target.disabled = true;
      const note = $("input[type=text]", pending).value.trim() || null;
      const ownDates = $("[data-own-dates]", pending).checked, progress = $("[data-progress]", pending);
      $("[data-act=discard]", pending).disabled = true;
      let queued = 0, saved = 0, last = null;
      const failed = [];
      for (const [i, file] of files.entries()) {
        progress.textContent = `Saving ${i + 1} of ${files.length}…`;
        try {
          const r = await captureOrQueue({ file, filename: file.name, note, project_id: opts.projectId || null,
            ...(ownDates && file.lastModified ? { captured_at: new Date(file.lastModified).toISOString() } : {}) });
          if (r.queued) queued++; else { saved++; last = r.entry; }
        } catch (err) { failed.push(`${file.name}: ${err.message}`); }
      }
      pending.hidden = true;
      pending.innerHTML = "";
      const parts = [];
      if (saved) parts.push(`Saved ${saved} file${saved === 1 ? "" : "s"}`);
      if (queued) parts.push(`${queued} kept on this device to sync later`);
      if (failed.length) toast(`${parts.join(", ")}${parts.length ? ". " : ""}Couldn't save ${failed.length}: ${failed.join("; ")}`, true);
      else toast(`${parts.join(", ")}.`);
      if (last) opts.onCaptured?.(last);
    };
  }

  function takeFile(file) {
    if (!file) return;
    const kind = isImageFile(file) ? "Image" : file.type.startsWith("audio/") ? "Audio" : "File";
    showPending(file, URL.createObjectURL(file), kind + " ready");
  }

  // Voice capture with MediaRecorder
  let recorder = null, chunks = [], timer = null, started = 0;
  async function toggleRecord() {
    if (recorder && recorder.state === "recording") { recorder.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast("Recording isn't supported in this browser. You can upload an audio file instead.", true);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      toast("Microphone unavailable: " + err.message, true);
      return;
    }
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];
    const mimeType = types.find((t) => MediaRecorder.isTypeSupported?.(t)) || "";
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(timer);
      recBtn.classList.remove("live");
      $("span:last-child", recBtn).textContent = "Record Idea";
      const type = (recorder.mimeType || "audio/webm").split(";")[0];
      const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
      const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const file = new File(chunks, `recording-${stamp}.${ext}`, { type: recorder.mimeType || type });
      showPending(file, URL.createObjectURL(file), "Recording ready");
    };
    recorder.start();
    started = Date.now();
    recBtn.classList.add("live");
    const tick = () => {
      const s = Math.floor((Date.now() - started) / 1000);
      $("span:last-child", recBtn).textContent = `Stop · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    };
    tick();
    timer = setInterval(tick, 500);
  }

  $("[data-act=capture]", root).onclick = capture;
  recBtn.onclick = toggleRecord;
  $("[data-act=upload]", root).onclick = () => fileInput.click();
  fileInput.onchange = () => { takeFiles(fileInput.files); fileInput.value = ""; };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); capture(); } });
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag"); });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", (e) => {
    box.classList.remove("drag");
    if (e.dataTransfer.files.length) { e.preventDefault(); takeFiles(e.dataTransfer.files); }
  });
  return { focus: () => ta.focus() };
}

/* ------------------------------------------------------------------ views */
async function viewHome(el) {
  el.innerHTML = `<h1 class="sr-only">Capture</h1><div id="cap"></div>
    <div id="welcome"></div>
    <div id="just"></div>
    <h2>Recently captured</h2><div id="recent"><p class="loading">Loading…</p></div>`;
  const loadRecent = async () => {
    try {
      const data = await api("/api/entries?limit=9");
      $("#recent", el).innerHTML = gridHTML(data.items, "Nothing captured yet. Your first idea goes in the box above.");
      if (!data.total && !store("nexus.welcomeDone")) mountWelcome($("#welcome", el));
      else $("#welcome", el).innerHTML = "";
    } catch (err) {
      if (!err.offline) throw err;
      $("#recent", el).innerHTML = `<p class="empty">NEXUS isn't reachable right now, but you can still capture.
        Ideas are kept on this device and sent automatically when NEXUS is back.${HAS_OFFLINE_COPY
          ? `<br><br>This device hasn't saved a copy of your archive yet. Open NEXUS once while your PC is on
             and it saves one, so next time you can browse your ideas and pictures even when the PC is off.
             You can check it in <a href="#/settings">Settings → This device</a>.` : ""}</p>`;
    }
  };
  const cap = mountCapture($("#cap", el), {
    onCaptured: (entry) => {
      const just = $("#just", el);
      const dup = entry.ai_metadata?.duplicate_of;
      const show = (e) => {
        just.innerHTML = `<h2>Just captured</h2>${dup ? `<p class="small notice">You saved this same file before:
            <a href="#/entry/${dup.id}">${esc(dup.title)}</a> (${fmtDate(dup.created_at)}). Both are kept; trash one if you like.</p>` : ""}
          <div class="grid">${cardHTML(e)}</div>`;
      };
      show(entry);
      loadRecent();
      if (isBusy(entry)) watchEntry(entry.id, just, (e) => { show(e); loadRecent(); }, show);
    },
  });
  cap.focus();
  await loadRecent();
}

/** First run: what NEXUS is, and whether this computer is ready (AI, voice, search). */
async function mountWelcome(box) {
  if (box.dataset.shown) return;
  box.dataset.shown = "1";
  box.innerHTML = `<section class="panel welcome">
    <div class="row"><h2 style="margin:0">Welcome to NEXUS</h2><span class="spacer"></span>
      <button class="btn ghost small" data-act="welcome-done">Got it</button></div>
    <ul class="welcome-points">
      <li><strong>Capture anything</strong>: type, record a voice note, or drop in sketches and photos. It's saved instantly.</li>
      <li><strong>A local AI organizes it</strong> in the background: title, type, tags, summary and links to related ideas. Nothing leaves this computer.</li>
      <li><strong>Your words are never changed.</strong> ${["user", "ai", "infer", "unknown"].map((k) =>
        `<span class="legend-dot ${k}"></span>${{ user: "your words", ai: "AI interpretation", infer: "AI inference", unknown: "unknown" }[k]}`).join(" ")}</li>
    </ul>
    <h3>Is this computer ready?</h3><div id="welcome-check"><p class="loading">Checking…</p></div>
  </section>`;
  $("[data-act=welcome-done]", box).onclick = () => { store("nexus.welcomeDone", "1"); box.innerHTML = ""; };
  try {
    const st = await getSettings(true);
    const rows = [
      ["AI model", st.ai_provider.health, "Organizes your ideas and reads your sketches."],
      ["Voice notes", st.transcription_provider.health, "Turns recordings into text, on this computer."],
      ["Search by meaning", st.search.health, "Finds ideas by what they're about."],
    ];
    $("#welcome-check", box).innerHTML = `<ul class="setup-check">${rows.map(([name, h, what]) => `<li>
      <span class="health ${h?.ok ? "ok" : "bad"}"><span class="dot"></span><strong>${name}</strong></span>
      <span class="small muted">${esc(h?.ok ? what : h?.detail || "Not ready.")}</span></li>`).join("")}</ul>
      ${rows.every(([, h]) => h?.ok) ? `<p class="small">All set. Capture your first idea above.</p>`
        : `<p class="small muted">You can capture right away: anything not ready yet is done later (use <em>Re-run AI organizer</em> on an entry).
           See the README's setup section, or <a href="#/settings">Settings</a>.</p>`}
      <p class="small muted">Next: put NEXUS on your phone (README → "NEXUS on your phone"), and set a passcode in Settings.</p>`;
  } catch {
    $("#welcome-check", box).innerHTML = `<p class="small muted">Couldn't check right now.</p>`;
  }
}

async function viewToday(el) {
  el.innerHTML = `<div class="view-head"><div><h1>Today</h1>
    <p class="lede">${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p></div>
    <a class="btn" href="#/">+ Capture</a></div>
    <h2>Captured today</h2><div id="today"></div>
    <section id="rediscover" hidden><h2>Rediscover</h2><div id="rediscover-body"></div></section>
    <h2>Patterns in your archive</h2><div id="patterns"></div>
    <h2>Recently touched</h2><div id="touched"></div>`;
  const [recent, modified, pats] = await Promise.all([
    api("/api/entries?limit=200"),
    api("/api/entries?sort=modified&limit=6"),
    api("/api/insights/patterns"),
  ]);
  const today = new Date().toDateString();
  const todays = recent.items.filter((e) => new Date(e.created_at).toDateString() === today);
  $("#today", el).innerHTML = gridHTML(todays, "Nothing captured today yet.");
  $("#touched", el).innerHTML = gridHTML(modified.items, "No entries yet.");
  api(`/api/insights/rediscover?today=${localISODate()}&tz_offset=${new Date().getTimezoneOffset()}`).then((r) => {
    if (!r.on_this_day.length && !r.resurfaced.length) return;
    $("#rediscover-body", el).innerHTML = (r.on_this_day.length ? `<p class="small muted">Captured on the ${ordinal(new Date().getDate())} of an earlier month:</p>
      ${gridHTML(r.on_this_day, "")}` : "")
      + (r.resurfaced.length ? `<p class="small muted">Unfinished ideas you haven't touched in over a month (a different few each day):</p>
      ${gridHTML(r.resurfaced, "")}` : "");
    $("#rediscover", el).hidden = false;
  }).catch(() => {});
  $("#patterns", el).innerHTML = pats.patterns.length
    ? pats.patterns.map((p) => prov("infer", "AI inference · pattern", `
        <p class="body">${esc(p.observation)}</p>
        <div class="small muted">Evidence (${p.count} entries):</div>
        <ul class="small">${p.evidence.map((ev) => `<li><a href="#/entry/${ev.id}">${esc(ev.title || "Untitled")}</a>
          <span class="muted">· ${esc(label(ev.entry_type))} · ${fmtDate(ev.created_at)}</span></li>`).join("")}
          ${p.count > p.evidence.length ? `<li class="muted">…and ${p.count - p.evidence.length} more</li>` : ""}</ul>`,
        "computed from your entries")).join("")
    : `<p class="empty">No recurring patterns yet. Patterns appear once three or more entries share a concept — always with the entries that support them.</p>`;
}

const archiveState = JSON.parse(store("nexus.archiveFilters") || "{}");

async function viewArchive(el) {
  const [s, projects, tags, trash] = await Promise.all([getSettings(), api("/api/projects"), api("/api/tags"), api("/api/trash")]);
  const f = archiveState;
  el.innerHTML = `<div class="view-head"><div><h1>Creative Archive</h1>
    <p class="lede">Everything you've captured. Your words are never altered.</p></div>
    <div class="row"><span class="muted small" id="count"></span><button type="button" class="btn ghost small" data-act="select">Select</button>
      <a class="btn ghost small" href="#/timeline">Timeline</a><a class="btn ghost small" href="#/characters">Characters</a><a class="btn ghost small" href="#/tags">Tags</a><a class="btn ghost small" href="#/map">Connections map</a>${trash.total ? `<a class="btn ghost small" href="#/trash">Trash (${trash.total})</a>` : ""}</div></div>
    <details class="filterbox" ${matchMedia("(min-width: 641px)").matches ? "open" : ""}>
    <summary class="btn ghost small">Filters &amp; sort</summary>
    <form class="filters" id="filters">
      <label class="field">Type<select name="entry_type">${options(s.vocab.entry_types, f.entry_type, "All types")}</select></label>
      <label class="field">Status<select name="status">${options(s.vocab.statuses, f.status, "All statuses")}</select></label>
      <label class="field">Project<select name="project_id"><option value="">All projects</option>${projects
        .map((p) => `<option value="${p.id}"${String(p.id) === f.project_id ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label class="field">Tag<select name="tag"><option value="">All tags</option>${tags
        .map((t) => `<option value="${esc(t.name)}"${t.name === f.tag ? " selected" : ""}>${esc(t.name)} (${t.count})</option>`).join("")}</select></label>
      <label class="field">From<input type="date" name="date_from" value="${esc(f.date_from || "")}"></label>
      <label class="field">To<input type="date" name="date_to" value="${esc(f.date_to || "")}"></label>
      <label class="field">Sort<select name="sort">
        ${[["newest", "Newest"], ["oldest", "Oldest"], ["modified", "Recently modified"], ["alpha", "Alphabetical"]]
          .map(([v, l]) => `<option value="${v}"${v === (f.sort || "newest") ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <button class="btn ghost small" type="reset">Clear</button>
    </form></details>
    <h2 class="sr-only">Entries</h2><div id="results"></div>`;
  const form = $("#filters", el);
  async function load() {
    const fd = new FormData(form);
    const params = new URLSearchParams({ limit: "300" });
    for (const [k, v] of fd) { archiveState[k] = v; if (v) params.set(k, v); }
    store("nexus.archiveFilters", JSON.stringify(archiveState));
    const data = await api("/api/entries?" + params);
    $("#count", el).textContent = `${data.total} entr${data.total === 1 ? "y" : "ies"}`;
    $("#results", el).innerHTML = gridHTML(data.items, "No entries match these filters.");
    selection.clear();
    drawSelection();
  }
  form.addEventListener("change", load);
  form.addEventListener("reset", () => setTimeout(load));

  // --- select several entries and act on them together
  const selection = new Set();
  let selecting = false;
  const results = $("#results", el);
  el.insertAdjacentHTML("beforeend", `<div class="bulkbar" id="bulkbar" hidden>
    <strong id="bulk-count"></strong><button type="button" class="btn ghost small" data-bulk="all">Select all shown</button>
    <select id="bulk-project" aria-label="Project"><option value="">Add to project…</option>${projects.map((p) =>
      `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select>
    <select id="bulk-status" aria-label="Status"><option value="">Set status…</option>${s.vocab.statuses.map((x) =>
      `<option value="${esc(x)}">${esc(label(x))}</option>`).join("")}</select>
    <button type="button" class="btn ghost small" data-bulk="tag">Add tag</button>
    <button type="button" class="btn ghost small" data-bulk="export">Export</button>
    <button type="button" class="btn ghost small danger" data-bulk="trash">Move to trash</button>
    <span class="spacer"></span><button type="button" class="btn small" data-bulk="done">Done</button></div>`);
  const bar = $("#bulkbar", el);
  const cardId = (card) => +card.getAttribute("href").split("/").pop();
  function drawSelection() {
    $$(".card", results).forEach((c) => c.classList.toggle("selected", selection.has(cardId(c))));
    results.classList.toggle("selecting", selecting);
    bar.hidden = !selecting;
    $("#bulk-count", bar).textContent = `${selection.size} selected`;
  }
  $("[data-act=select]", el).onclick = () => { selecting = !selecting; selection.clear(); drawSelection(); };
  results.addEventListener("click", (ev) => {
    const card = ev.target.closest(".card");
    if (!selecting || !card) return;
    ev.preventDefault();
    const id = cardId(card);
    selection.has(id) ? selection.delete(id) : selection.add(id);
    drawSelection();
  });
  const run = async (body, done) => {
    if (!selection.size) return toast("Tap entries to select them first.");
    try {
      const r = await api("/api/entries/bulk", { method: "POST", json: { entry_ids: [...selection], ...body } });
      toast(done(r.changed));
      load();
    } catch (err) { toast(err.message, true); }
  };
  $("#bulk-project", bar).onchange = (ev) => {
    if (!ev.target.value) return;
    run({ action: "add_to_project", project_id: +ev.target.value }, (n) => `Added ${n} to the project.`);
    ev.target.value = "";
  };
  $("#bulk-status", bar).onchange = (ev) => {
    if (!ev.target.value) return;
    run({ action: "set_status", status: ev.target.value }, (n) => `Updated ${n}.`);
    ev.target.value = "";
  };
  bar.addEventListener("click", (ev) => {
    const act = ev.target.dataset.bulk;
    if (act === "done") { selecting = false; selection.clear(); drawSelection(); }
    if (act === "all") { $$(".card", results).forEach((c) => selection.add(cardId(c))); drawSelection(); }
    if (act === "tag") {
      const tag = prompt(`Tag to add to ${selection.size} entr${selection.size === 1 ? "y" : "ies"}:`);
      if (tag && tag.trim()) run({ action: "add_tag", tag }, (n) => `Tagged ${n}.`);
    }
    if (act === "export") {
      if (!selection.size) return toast("Tap entries to select them first.");
      exportShare({ entry_ids: [...selection].join(",") });
    }
    if (act === "trash" && selection.size && confirm(`Move ${selection.size} entr${selection.size === 1 ? "y" : "ies"} to the trash? You can restore them from the Trash.`)) {
      run({ action: "trash" }, (n) => `Moved ${n} to the trash.`);
    }
  });
  await load();
}

async function viewTimeline(el) {
  const PAGE = 500, items = [];
  let total = 0;
  const more = async () => {
    const data = await api(`/api/entries?limit=${PAGE}&offset=${items.length}&sort=newest`);
    items.push(...data.items);
    total = data.total;
  };
  const draw = () => {
    const months = new Map();
    for (const e of items) {
      const d = new Date(e.created_at), key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
      (months.get(key) || months.set(key, { label: d.toLocaleDateString(undefined, { month: "long", year: "numeric" }), items: [] }).get(key)).items.push(e);
    }
    el.innerHTML = `<div class="view-head"><div><h1>Timeline</h1>
      <p class="lede">Everything you've captured, month by month, like flipping back through a sketchbook.</p></div>
      <a class="btn ghost small" href="#/archive">Archive list</a></div>
      ${months.size ? `<nav class="month-jump" aria-label="Months">${[...months].map(([k, m]) =>
        `<a href="#/timeline" data-month="${k}">${esc(m.label)} <span class="muted">${m.items.length}</span></a>`).join("")}</nav>` : ""}
      <div data-browse>${[...months].map(([k, m]) => `<section class="month" id="m-${k}"><h2>${esc(m.label)}</h2>
        <div class="strip">${m.items.map((e) => { const pic = picOf(e);
          return tileHtml({ id: e.id, title: e.title, entry_type: e.entry_type, thumb_url: pic && (pic.thumb_url || pic.preview_url),
            excerpt: e.excerpt || e.ai_summary || e.attachments[0]?.original_filename },
            `${new Date(e.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short" })} · ${esc(label(e.entry_type))}`); }).join("")}</div></section>`).join("")
        || `<p class="empty">Nothing captured yet.</p>`}</div>
      ${total > items.length ? `<p class="row"><button type="button" class="btn" id="older">Show older (${total - items.length} more)</button></p>` : ""}`;
    const scroll = (id) => $(`#m-${id}`, el)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    $$("[data-month]", el).forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); scroll(a.dataset.month); }));
    const older = $("#older", el);
    if (older) older.onclick = async () => {
      older.disabled = true; older.textContent = "Loading…";
      const y = scrollY;
      try { await more(); draw(); scrollTo(0, y); } catch (err) { toast(err.message); older.disabled = false; }
    };
  };
  await more();
  draw();
}

/** One small picture-or-text tile linking to an entry (Timeline, Characters). */
function tileHtml(r, note) {
  return `<a class="tile${r.thumb_url ? "" : " text"}" href="#/entry/${r.id}" title="${esc(r.title)}">
    ${r.thumb_url ? `<img src="${esc(r.thumb_url)}" alt="" loading="lazy">` : `<span>${esc((r.excerpt || label(r.entry_type)).slice(0, 120))}</span>`}
    <b>${esc(r.title)}</b><i>${note}</i></a>`;
}

async function viewCharacters(el) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const world = params.get("kind") === "world";
  const chars = await api(`/api/characters${world ? "?kind=world" : ""}`);
  const linkNote = (x, who) => `${x.direction === "incoming" ? `${esc(label(x.relationship_type))} → ${esc(who)}` : `${esc(who)} ${esc(label(x.relationship_type))} →`}${
    x.origin === "ai" && x.status !== "accepted" ? " · AI suggested" : ""}`;
  el.innerHTML = `<p><a href="#/archive" class="muted small">← Creative Archive</a></p>
    <div class="view-head"><div><h1>${world ? "Places &amp; worlds" : "Characters"}</h1>
    <p class="lede">${world ? "Each place and world with the sketches, scenes and notes set there." : "Each character with the sketches, scenes and notes connected to them."}</p></div>
    <div class="seg" role="group" aria-label="Show"><a class="btn small ${world ? "ghost" : ""}" href="#/characters" ${world ? "" : 'aria-current="page"'}>Characters</a><a class="btn small ${world ? "" : "ghost"}" href="#/characters?kind=world" ${world ? 'aria-current="page"' : ""}>Places &amp; worlds</a></div></div>
    ${chars.length ? `<nav class="month-jump" aria-label="${world ? "Places" : "Characters"}">${chars.map((c) =>
      `<a href="#/characters" data-char="${c.character.id}">${esc(c.character.title)} <span class="muted">${c.link_count + c.mention_count}</span></a>`).join("")}</nav>` : ""}
    ${chars.map((c) => { const e = c.character, pic = picOf(e);
      return `<section class="panel character" id="c-${e.id}">
        <div class="char-head">${pic ? `<img src="${esc(pic.thumb_url || pic.preview_url)}" alt="" loading="lazy">` : ""}
          <div><h2><a href="#/entry/${e.id}">${esc(e.title)}</a></h2>
          ${e.excerpt ? `<p class="small">${esc(e.excerpt)}</p>` : e.ai_summary ? `<p class="small muted">${esc(e.ai_summary)}</p>` : ""}
          ${e.projects.length ? `<p class="small muted">In ${e.projects.map((p) => `<a href="#/projects/${p.id}">${esc(p.name)}</a>`).join(", ")}</p>` : ""}</div></div>
        <h3>Linked <span class="muted small">${c.link_count}</span></h3>
        ${c.links.length ? `<div class="strip">${c.links.map((x) => tileHtml(x.entry, linkNote(x, e.title))).join("")}</div>`
          : `<p class="small muted">Nothing linked yet. Open a sketch or scene and use “Link to another idea”.</p>`}
        ${c.link_count > c.links.length ? `<p class="small muted">…and ${c.link_count - c.links.length} more on <a href="#/entry/${e.id}">their page</a>.</p>` : ""}
        ${c.name_searched ? `<h3>Mentions “${esc(c.name_searched)}” <span class="muted small">${c.mention_count}</span></h3>
          <p class="small muted">Entries whose own words contain the name (a text match, not the AI).</p>
          ${c.mentions.length ? `<div class="strip">${c.mentions.map((r) => tileHtml(r, new Date(r.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))).join("")}</div>`
            : `<p class="small muted">No other entry mentions this name.</p>`}
          ${c.mention_count > c.mentions.length ? `<p class="small muted">…and ${c.mention_count - c.mentions.length} more: <a href="#/search?q=${encodeURIComponent(c.name_searched)}">search for them</a>.</p>` : ""}` : ""}
      </section>`; }).join("")
      || (world ? `<p class="empty">No places or worlds yet. Capture one and set its type to <b>worldbuilding</b> (or tag it “place” or “world”), then link scenes to it.</p>`
        : `<p class="empty">No characters yet. Capture one and set its type to <b>character</b> (or tag it “character”), then link sketches and scenes to it.</p>`)}`;
  $$("[data-char]", el).forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); $(`#c-${a.dataset.char}`, el)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); }));
  const wanted = new URLSearchParams(location.hash.split("?")[1] || "").get("c");
  if (wanted) $(`#c-${CSS.escape(wanted)}`, el)?.scrollIntoView();
}

async function viewTags(el) {
  const tags = await api("/api/tags");
  el.innerHTML = `<p><a href="#/archive" class="muted small">← Creative Archive</a></p>
    <div class="view-head"><div><h1>Tags</h1><p class="lede">Rename a tag to fix a typo, or rename it to an existing tag to merge the two.
      Deleting a tag removes it from every entry (the entries stay).</p></div></div>
    ${tags.length ? `<ul class="tag-admin">${tags.map((t) => `<li>
      <a href="#/archive" data-filter-tag="${esc(t.name)}" class="tag">${esc(t.name)}</a><span class="muted small">${t.count} entr${t.count === 1 ? "y" : "ies"}</span>
      <span class="spacer"></span><button class="btn ghost small" data-rename="${esc(t.name)}">Rename</button>
      <button class="btn ghost small danger" data-delete-tag="${esc(t.name)}">Delete</button></li>`).join("")}</ul>`
      : `<p class="empty">No tags yet.</p>`}`;
  $$("[data-filter-tag]", el).forEach((a) => (a.onclick = () => { archiveState.tag = a.dataset.filterTag; store("nexus.archiveFilters", JSON.stringify(archiveState)); }));
  $$("[data-rename]", el).forEach((b) => (b.onclick = async () => {
    const to = prompt(`Rename “${b.dataset.rename}” to:`, b.dataset.rename);
    if (!to || !to.trim() || to.trim().toLowerCase() === b.dataset.rename) return;
    try {
      const r = await api(`/api/tags/${encodeURIComponent(b.dataset.rename)}`, { method: "PATCH", json: { new_name: to } });
      toast(r.merged ? `Merged into “${to.trim().toLowerCase()}” (${r.changed} entries).` : `Renamed on ${r.changed} entries.`);
      render();
    } catch (err) { toast(err.message, true); }
  }));
  $$("[data-delete-tag]", el).forEach((b) => (b.onclick = async () => {
    if (!confirm(`Remove the tag “${b.dataset.deleteTag}” from every entry? The entries themselves stay.`)) return;
    try {
      const r = await api(`/api/tags/${encodeURIComponent(b.dataset.deleteTag)}`, { method: "DELETE" });
      toast(`Removed from ${r.changed} entries.`);
      render();
    } catch (err) { toast(err.message, true); }
  }));
}

async function viewIdeas(el) {
  el.innerHTML = `<div class="view-head"><div><h1>Idea Incubator</h1>
    <p class="lede">Raw and exploring ideas. Nothing here is a task or a deadline — it's a place to keep ideas alive and let them grow.</p></div>
    <a class="btn" href="#/">+ New idea</a></div><h2 class="sr-only">Ideas</h2><div id="ideas"></div>`;
  const data = await api("/api/entries?status=raw,exploring&limit=300");
  $("#ideas", el).innerHTML = gridHTML(data.items, "The incubator is empty. Capture something half-formed.");
}

async function viewProjects(el) {
  const s = await getSettings();
  el.innerHTML = `<div class="view-head"><div><h1>Projects</h1>
    <p class="lede">Gather characters, scenes, sketches and research into the works they belong to. An entry can live in many projects.</p></div></div>
    <form class="filters" id="newp">
      <label class="field" style="flex:2 1 220px">Name<input type="text" name="name" required placeholder="The Hollow Sun"></label>
      <label class="field">Type<select name="project_type">${options(s.vocab.project_types, "other")}</select></label>
      <label class="field" style="flex:3 1 260px">Description<input type="text" name="description" placeholder="Optional"></label>
      <button class="btn primary" type="submit">CREATE</button>
    </form>
    <div id="plist"></div>`;
  async function load() {
    const [projects, trashed] = await Promise.all([api("/api/projects"), api("/api/projects?trashed=true")]);
    $("#plist", el).innerHTML = projects.length
      ? `<div class="grid">${projects.map((p) => `<a class="card" href="#/projects/${p.id}">
          <div class="meta"><span class="pill type">${esc(label(p.project_type))}</span><span class="pill">${esc(p.status)}</span>
          <span class="spacer"></span><span>${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}</span></div>
          <h3>${esc(p.name)}</h3>${p.description ? `<p class="summary">${esc(p.description)}</p>` : ""}
          <div class="meta">Updated ${fmtDate(p.updated_at)}</div></a>`).join("")}</div>`
      : `<p class="empty">No projects yet.</p>`;
    if (trashed.length) {
      $("#plist", el).insertAdjacentHTML("beforeend",
        `<p class="small" style="margin-top:16px"><a href="#/trash">${trashed.length} project${trashed.length === 1 ? "" : "s"} in the trash</a></p>`);
    }
  }
  $("#newp", el).onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api("/api/projects", { method: "POST", json: Object.fromEntries(fd) });
      e.target.reset();
      toast("Project created.");
      load();
    } catch (err) { toast(err.message, true); }
  };
  await load();
}

async function viewProject(el, id) {
  const p = await api(`/api/projects/${id}`);
  el.innerHTML = `<p><a href="#/projects" class="muted small">← Projects</a></p>
    <div class="view-head"><div><div class="meta"><span class="pill type">${esc(label(p.project_type))}</span><span class="pill">${esc(p.status)}</span></div>
    <h1>${esc(p.name)}</h1>${p.description ? `<p class="lede">${esc(p.description)}</p>` : ""}</div></div>
    <section class="panel project-analysis" id="panalysis"><p class="loading">Loading…</p></section>
    <h2>Capture into this project</h2><div id="cap"></div>
    <h2>${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}</h2><div id="pentries">${gridHTML(p.entries, "No entries yet. Capture above, or add existing entries from their detail page.")}</div>
    <p class="row" style="margin-top:32px"><a class="btn" href="#/map?project=${p.id}&all=1">See connections</a>
      <a class="btn" href="#/projects/${p.id}/print">Story bible (print)</a>
      <button class="btn" data-act="export-project">Export project (.nexus)</button>
      <button class="btn danger ghost" data-act="trash-project">Move project to trash</button></p>`;
  $("[data-act=export-project]", el).onclick = () => exportShare({ project_id: String(p.id) });
  mountProjectAnalysis($("#panalysis", el), p);
  mountCapture($("#cap", el), { projectId: p.id, onCaptured: () => render() });
  $("[data-act=trash-project]", el).onclick = async () => {
    if (!confirm(`Move “${p.name}” to the trash? Its ${p.entry_count} entr${p.entry_count === 1 ? "y stays" : "ies stay"} in your archive, and you can restore the project from the Trash.`)) return;
    try {
      await api(`/api/projects/${p.id}`, { method: "DELETE" });
      toast("Project moved to trash. Its entries are untouched.");
      location.hash = "#/projects";
    } catch (err) { toast(err.message, true); }
  };
}

const BIBLE_SECTIONS = [
  ["Characters", (e) => e.entry_type === "character"],
  ["Places & worlds", (e) => e.entry_type === "worldbuilding"],
  ["Story", (e) => ["story", "scene", "dialogue", "comic", "movie", "short_film"].includes(e.entry_type)],
  ["Art", (e) => ["sketch", "artwork"].includes(e.entry_type) || !!picOf(e)],
  ["Notes & ideas", () => true],
];

/** A clean, printable "story bible" of a project: your material, grouped, oldest first. */
async function viewBible(el, id) {
  const { project: p, entries } = await api(`/api/projects/${id}/bible`);
  const groups = BIBLE_SECTIONS.map(([name]) => [name, []]);
  for (const e of entries) groups[BIBLE_SECTIONS.findIndex(([, test]) => test(e))][1].push(e);
  const block = (labelText, body) => body ? `<div class="bible-text"><span class="bible-label">${labelText}</span>${esc(body)}</div>` : "";
  const draw = (withAI) => groups.filter(([, list]) => list.length).map(([name, list]) => `<section class="bible-section"><h2>${esc(name)}</h2>
    ${list.map((e) => { const pic = picOf(e);
      return `<article class="bible-entry"><h3>${esc(e.title)}</h3>
        <p class="small muted">${esc(label(e.entry_type))} · ${fmtDate(e.created_at)}${e.tags.length ? ` · ${e.tags.map((t) => esc(t.name)).join(", ")}` : ""}</p>
        ${pic ? `<img src="${esc(pic.preview_url)}" alt="${esc(pic.original_filename)}" loading="lazy">` : ""}
        ${block("Your words", e.original_text)}${block("Your notes", e.user_notes)}${block("Transcript", e.transcript)}
        ${withAI && e.ai_summary ? block("AI summary (not your words)", e.ai_summary) : ""}</article>`; }).join("")}</section>`).join("");
  el.innerHTML = `<div class="no-print"><p class="row"><a href="#/projects/${p.id}" class="muted small">← ${esc(p.name)}</a></p>
    <div class="row"><button type="button" class="btn primary" data-act="print">PRINT / SAVE AS PDF</button>
      <label class="check small"><input type="checkbox" id="bible-ai"> Include AI summaries (labelled)</label></div></div>
    <div class="bible"><h1>${esc(p.name)}</h1>${p.description ? `<p class="lede">${esc(p.description)}</p>` : ""}
      <p class="small muted">${entries.length} entr${entries.length === 1 ? "y" : "ies"} · ${esc(label(p.project_type))} · printed ${fmtDate(new Date().toISOString())}</p>
      <div id="bible-body">${draw(false)}</div>${entries.length ? "" : `<p class="empty">This project has no entries yet.</p>`}</div>`;
  $("#bible-ai", el).onchange = (ev) => ($("#bible-body", el).innerHTML = draw(ev.target.checked));
  $("[data-act=print]", el).onclick = () => window.print();
}

const SEARCH_EXAMPLES = ["memory", "villains", "ideas about alternate realities", "unfinished comic ideas", "sketches with monsters"];

async function viewSearch(el, _id, query) {
  const q = query.get("q") || "";
  el.innerHTML = `<div class="view-head"><div><h1>Search</h1><p class="lede">Search naturally across your words, transcripts and the AI's notes.</p></div>
    <a class="btn small" href="#/ask">Ask a question instead</a></div>
    <form class="searchbox" id="sform"><input type="search" name="q" value="${esc(q)}" placeholder="e.g. unfinished comic ideas" aria-label="Search" autofocus>
    <button class="btn primary" type="submit">SEARCH</button></form>
    <div class="suggestions">${SEARCH_EXAMPLES.map((x) => `<button type="button" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>
    <div id="sresults"></div>`;
  $("#sform", el).onsubmit = (e) => {
    e.preventDefault();
    location.hash = "#/search?q=" + encodeURIComponent($("input", e.target).value);
  };
  $$("[data-q]", el).forEach((b) => (b.onclick = () => (location.hash = "#/search?q=" + encodeURIComponent(b.dataset.q))));
  if (!q.trim()) return;
  const r = await api("/api/search", { method: "POST", json: { query: q } });
  const i = r.interpreted;
  const understood = [
    i.entry_types.length && `type: ${i.entry_types.map(label).join(", ")}`,
    i.statuses.length && `status: ${i.statuses.join(", ")}`,
    i.source_types.length && `source: ${i.source_types.join(", ")}`,
    i.terms.length && `words: ${i.terms.join(", ")}`,
  ].filter(Boolean).join(" · ");
  const snippet = (s) => esc(s).replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
  const how = r.mode.includes("meaning") ? "words + meaning" : "words only";
  const badges = (x) => x.matched_by.map((m) => m === "meaning"
    ? `<span class="match meaning" title="Similar in meaning, even if the words differ">≈ meaning ${pct(x.similarity)}</span>`
    : m === "words" ? `<span class="match words" title="Contains your search words">words</span>` : "").join("");
  $("#sresults", el).innerHTML = `
    ${r.note ? `<p class="notice small">${esc(r.note)}</p>` : ""}
    <p class="small muted">Understood as — ${esc(understood || "everything")}${r.relaxed ? " · <strong>nothing matched the type/status exactly, so these are looser matches</strong>" : ""}
    · ${r.results.length} result${r.results.length === 1 ? "" : "s"} <span class="confidence">(${esc(how)})</span></p>
    ${r.results.length ? `<div class="grid">${r.results.map((x) => cardHTML(x.entry,
      `${x.matched_by.some((m) => m !== "filters") ? `<div class="meta">${badges(x)}</div>` : ""}
       ${x.snippet ? `<p class="snippet">${snippet(x.snippet)}</p>` : ""}`)).join("")}</div>`
      : `<p class="empty">Nothing found for “${esc(q)}”.</p>`}`;
}

/* ------------------------------------------------------------ sharing */
/** Download a .nexus file for someone else's NEXUS. Asks once for the name shown to them. */
async function exportShare(params) {
  let by = (await getSettings().catch(() => null))?.owner_name || store("nexus.myName");
  if (!by) {
    by = prompt("Your name, shown to whoever imports this (for example “Andrew”):", "");
    if (by === null) return;
    by = by.trim();
    if (by) {
      store("nexus.myName", by);
      api("/api/preferences", { method: "PUT", json: { owner_name: by } }).then(() => getSettings(true)).catch(() => {});
    }
  }
  const a = document.createElement("a");
  a.href = "/api/share/export?" + new URLSearchParams({ ...params, by: by || "" });
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
  toast("Preparing the .nexus file… it downloads in a moment.");
}

/** A button that imports a .nexus file someone sent you. */
function mountImport(box) {
  box.innerHTML = `<h3>Import from someone's NEXUS</h3>
    <p class="small muted">Got a <code>.nexus</code> file from a friend? Their words and files come in exactly as they sent
      them, marked with their name, and your NEXUS organizes them with its own AI. Importing the same file twice adds nothing.</p>
    <button class="btn small" data-act="import">Choose a .nexus file…</button>
    <input type="file" hidden accept=".nexus,.zip,application/zip,application/octet-stream">
    <p class="small" id="import-result"></p>`;
  const input = $("input", box), out = $("#import-result", box);
  $("[data-act=import]", box).onclick = () => input.click();
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file, file.name);
    out.innerHTML = `<span class="spinner inline"></span>Importing ${esc(file.name)}…`;
    try {
      const r = await api("/api/share/import", { method: "POST", body: fd, timeout: 600000 });
      const n = r.imported.length, had = r.already_had.length;
      out.innerHTML = `Imported ${n} entr${n === 1 ? "y" : "ies"} from ${esc(r.by)}${had ? ` (${had} ${had === 1 ? "was" : "were"} already here)` : ""}.
        ${r.project_id ? `<a href="#/projects/${r.project_id}">Open ${esc(r.project_name)}</a>` : `<a href="#/archive">Open the archive</a>`}`;
      toast(n ? "Imported. Your NEXUS is organizing the new entries." : "Nothing new: you already had everything in that file.");
    } catch (err) {
      out.innerHTML = `<span style="color:var(--danger)">${esc(err.offline ? "NEXUS isn't reachable right now." : err.message)}</span>`;
    }
    input.value = "";
  };
}

/** Settings: everyday preferences, changed here instead of in .env. They apply straight away. */
async function mountPreferences(box) {
  let d;
  try {
    d = await api("/api/preferences", { noCopy: true });
  } catch (err) {
    box.innerHTML = `<h3>Preferences</h3><p class="small muted">${err.offline ? "Preferences can be changed when NEXUS is on." : esc(err.message)}</p>`;
    return;
  }
  const v = d.values, models = d.choices.installed_models;
  // "nomic-embed-text" and "nomic-embed-text:latest" are the same model.
  const sameModel = (a, b) => !!a && !!b && (a === b || `${a}:latest` === b || a === `${b}:latest`);
  const modelField = (name, current, allowSame) => models.length
    ? `<select name="${name}">${allowSame ? `<option value=""${!current ? " selected" : ""}>Same as the AI model</option>` : ""}${
        [...new Set([...(current ? [current] : []), ...models.filter((m) => !sameModel(m, current))])].map((m) =>
          `<option value="${esc(m)}"${m === current ? " selected" : ""}>${esc(m)}${models.some((x) => sameModel(x, m)) ? "" : " (not installed)"}</option>`).join("")}</select>`
    : `<input type="text" name="${name}" value="${esc(current || "")}" placeholder="${allowSame ? "same as the AI model" : "gemma3:12b"}">`;
  const strict = Math.round(((v.semantic_min_similarity - 0.4) / 0.45) * 100);
  box.innerHTML = `<div class="row"><h3 style="margin:0">Preferences</h3><span class="spacer"></span>
      ${d.from_app.length ? `<button type="button" class="btn ghost small" data-act="prefs-reset">Reset to defaults</button>` : ""}</div>
    <form id="prefs-form" class="prefs-grid">
      <label class="field">Your name <input type="text" name="owner_name" maxlength="80" value="${esc(v.owner_name)}" placeholder="shown when you share with a friend"></label>
      <label class="field">AI model ${modelField("ollama_model", v.ollama_model)}</label>
      <label class="field">Model for pictures ${modelField("ollama_vision_model", v.ollama_vision_model, true)}</label>
      <label class="field">Search-by-meaning model ${modelField("embedding_model", v.embedding_model)}</label>
      <label class="field">Speech-to-text model <select name="whisper_model">${d.choices.whisper_models.map((m) =>
        `<option${m === v.whisper_model ? " selected" : ""}>${m}</option>`).join("")}</select></label>
      <label class="field">Voice note language <input type="text" name="whisper_language" value="${esc(v.whisper_language)}" placeholder="detect automatically" maxlength="3"></label>
      <label class="field">Search-by-meaning strictness <input type="range" name="semantic_min_similarity" min="0.4" max="0.85" step="0.01" value="${v.semantic_min_similarity}">
        <span class="small muted" id="strict-note">${strict < 35 ? "looser: more results, some unrelated" : strict > 65 ? "stricter: fewer, closer matches" : "balanced"} (${v.semantic_min_similarity})</span></label>
      <label class="check"><input type="checkbox" name="art_auto_analyze"${v.art_auto_analyze ? " checked" : ""}> Analyze new pictures automatically</label>
      <label class="field wide">Backup folder <input type="text" name="backup_dir" value="${esc(v.backup_dir)}" placeholder="for example D:\\NEXUS Backups"></label>
      <label class="field">Automatic backup every (days) <input type="number" name="backup_every_days" min="0" max="365" step="1" value="${v.backup_every_days}"></label>
      <label class="field">Backups to keep <input type="number" name="backup_keep" min="1" max="100" step="1" value="${v.backup_keep}"></label>
      <div class="wide model-dl"><span class="small"><strong>Download a model:</strong></span>
        ${d.choices.suggested_models.filter((m) => !models.some((x) => sameModel(x, m.name))).map((m) =>
          `<button type="button" class="btn ghost small" data-pull="${esc(m.name)}" title="${esc(m.note)}">${esc(m.name)}</button>`).join("")}
        <input type="text" id="pull-name" placeholder="other model name" aria-label="Model to download" style="max-width:180px">
        <button type="button" class="btn ghost small" data-pull-custom>Download</button>
        <div id="pulls" class="small"></div>
        <p class="small muted">${d.choices.suggested_models.map((m) => `<strong>${esc(m.name)}</strong>: ${esc(m.note)}`).join(" · ")}</p></div>
      <div class="row wide"><button class="btn primary small" type="submit">Save preferences</button>
        <span class="small muted">They apply straight away. ${models.length ? "" : "Ollama isn't answering, so type model names."}</span></div>
    </form>`;
  const form = $("#prefs-form", box);
  const showPulls = async () => {
    if (!box.isConnected) return;
    let list = [];
    try { list = await api("/api/models/pulls", { noCopy: true }); } catch { return; }
    $("#pulls", box).innerHTML = list.map((p) => {
      const pct = p.total ? Math.round((p.completed / p.total) * 100) : null;
      return `<div class="pull"><strong>${esc(p.name)}</strong> ${p.state === "downloading"
        ? `<progress max="100" ${pct === null ? "" : `value="${pct}"`}></progress> ${pct === null ? esc(p.status) : pct + "%"}`
        : p.state === "done" ? "✓ downloaded" : `<span style="color:var(--danger)">failed: ${esc(p.error || "")}</span>`}</div>`;
    }).join("");
    if (list.some((p) => p.state === "downloading")) setTimeout(showPulls, 1000);
    else if (list.some((p) => p.state === "done" && !box.dataset.refreshed)) { box.dataset.refreshed = "1"; mountPreferences(box); }
  };
  const pull = async (name) => {
    if (!name) return;
    try { await api("/api/models/pull", { method: "POST", json: { name } }); delete box.dataset.refreshed; showPulls(); }
    catch (err) { toast(err.message, true); }
  };
  $$("[data-pull]", box).forEach((b) => (b.onclick = () => pull(b.dataset.pull)));
  $("[data-pull-custom]", box).onclick = () => pull($("#pull-name", box).value.trim());
  showPulls();
  form.semantic_min_similarity.oninput = (ev) => { $("#strict-note", box).textContent = `(${ev.target.value})`; };
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(form), changes = {};
    for (const key of ["owner_name", "ollama_model", "ollama_vision_model", "embedding_model", "whisper_model", "whisper_language", "backup_dir"]) {
      const val = (f.get(key) ?? "").trim();
      if (val !== String(v[key] ?? "")) changes[key] = val;
    }
    for (const key of ["semantic_min_similarity", "backup_every_days", "backup_keep"]) if (+f.get(key) !== +v[key]) changes[key] = +f.get(key);
    if (form.art_auto_analyze.checked !== v.art_auto_analyze) changes.art_auto_analyze = form.art_auto_analyze.checked;
    if (!Object.keys(changes).length) return toast("Nothing changed.");
    try {
      await api("/api/preferences", { method: "PUT", json: changes });
      await getSettings(true);
      toast("Preferences saved.");
      render();
    } catch (err) { toast(err.message, true); }
  };
  $("[data-act=prefs-reset]", box)?.addEventListener("click", async () => {
    if (!confirm("Go back to the defaults (and anything set in the .env file) for these preferences?")) return;
    await api("/api/preferences", { method: "PUT", json: Object.fromEntries(d.from_app.map((k) => [k, null])) });
    await getSettings(true);
    toast("Back to the defaults.");
    render();
  });
}

/** Settings panel: this device — app version and the copy of the archive saved on it. */
async function mountDevice(box) {
  const head = `<h3>This device</h3><dl class="kv"><dt>NEXUS version</dt><dd>${esc(APP_VERSION || "—")}</dd></dl>`;
  if (HAS_OFFLINE_COPY && !box.dataset.listening) {
    box.dataset.listening = "1";
    const again = () => (box.isConnected ? mountDevice(box) : window.removeEventListener("nexus:synced", again));
    window.addEventListener("nexus:synced", again);
  }
  if (!HAS_OFFLINE_COPY) {
    box.innerHTML = head + `<p class="small muted">You're on the computer that runs NEXUS, so everything is read straight from its
      database. Phones and other devices keep their own copy for when this computer is off.</p>`;
    return;
  }
  const snap = await offlineCopy.snapshot();
  let pictures = 0;
  const withPic = snap ? snap.entries.filter((e) => picOf(e)?.thumb_url).length : 0;
  try { pictures = (await (await caches.open(THUMB_CACHE)).keys()).length; } catch { /* no Cache API */ }
  box.innerHTML = head + (snap
    ? `<p class="health ok"><span class="dot"></span>Copy saved on this device</p><dl class="kv">
        <dt>Saved</dt><dd>${fmtDateTime(snap.generated_at)}</dd>
        <dt>Entries</dt><dd>${snap.entries.length}</dd><dt>Projects</dt><dd>${snap.projects.length}</dd>
        <dt>Pictures</dt><dd>${pictures}${withPic > pictures ? ` of ${withPic}${withPic > MAX_OFFLINE_THUMBS ? ` (the newest ${MAX_OFFLINE_THUMBS} are kept)` : syncing ? " (still saving)" : ""}` : ""}</dd></dl>`
    : `<p class="health bad"><span class="dot"></span>${syncing ? "Saving a copy on this device…" : "No copy saved on this device yet"}</p>`)
    + (lastSyncError ? `<p class="small" style="color:var(--danger)">The last attempt to save it failed: ${esc(lastSyncError)}</p>` : "")
    + `<button class="btn small" data-act="sync-now">Update the saved copy now</button>
    <p class="small muted">While NEXUS is reachable, this device keeps a read-only copy of your archive (refreshed every few
      minutes), so you can browse it when the PC is off.</p>`;
  $("[data-act=sync-now]", box).onclick = async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = "Saving…";
    lastUnreachable = 0;
    await syncOfflineCopy({ force: true });
    toast(lastSync && Date.now() - lastSync < 60000 ? "Saved an up-to-date copy on this device." : "Couldn't reach NEXUS to update the copy.", !(lastSync && Date.now() - lastSync < 60000));
    mountDevice(box);
  };
}

/** Settings panel: backups of the database and every original file. */
async function mountBackups(box) {
  let b;
  try {
    b = await api("/api/backups", { noCopy: true });
  } catch (err) {
    box.innerHTML = `<h3>Backups</h3><p class="small muted">${err.offline ? "Backups are managed on the PC when NEXUS is on." : esc(err.message)}</p>`;
    return;
  }
  const latest = b.backups[0];
  const age = latest ? Math.floor((Date.now() - new Date(latest.created_at)) / 86400000) : null;
  box.innerHTML = `<h3>Backups</h3>
    <p class="health ${latest && age <= Math.max(b.every_days, 1) ? "ok" : "bad"}"><span class="dot"></span>${latest
      ? `Last backup ${age === 0 ? "today" : age === 1 ? "yesterday" : `${age} days ago`} (${fmtSize(latest.size_bytes)})`
      : "No backup yet"}</p>
    ${b.running ? `<p class="small"><span class="spinner inline"></span>Backing up… this can take a minute with many pictures.</p>`
      : `<button class="btn small primary" data-act="backup-now">Back up now</button>`}
    ${b.last_error ? `<p class="small" style="color:var(--danger)">Last backup failed: ${esc(b.last_error)}</p>` : ""}
    ${b.backups.length ? `<ul class="small backup-list">${b.backups.map((x) => `<li><a href="/api/backups/${encodeURIComponent(x.name)}" download>${
      esc(fmtDateTime(x.created_at))}</a> · ${fmtSize(x.size_bytes)}${x.automatic ? " · automatic" : ""}</li>`).join("")}</ul>` : ""}
    <p class="small muted">Each backup is one zip with your database and every original file.
      ${b.every_days > 0 ? `A new one is made every ${b.every_days} day${b.every_days === 1 ? "" : "s"}; ` : "Automatic backups are off; "}
      the newest ${b.keep} are kept. Folder: <code>${esc(b.folder)}</code></p>
    <p class="small" style="margin-top:12px"><a class="btn small ghost" href="/api/export/markdown" download>Export as readable files</a></p>
    <p class="small muted">A zip with a Markdown file for every entry and project (your words, notes and the AI's layers in
      separate, labeled sections) plus every original file. It opens in any notes app, without NEXUS.</p>
    ${b.folder_is_inside_data ? `<p class="small notice">Tip: backups are on the same drive as NEXUS. Set <code>NEXUS_BACKUP_DIR</code>
      in <code>.env</code> to another drive, a USB stick or a OneDrive/iCloud folder, so one broken disk can't take both.</p>` : ""}`;
  $("[data-act=backup-now]", box)?.addEventListener("click", async () => {
    try {
      await api("/api/backups", { method: "POST" });
      toast("Backing up in the background…");
    } catch (err) { toast(err.message, true); }
    mountBackups(box);
  });
  if (b.running) setTimeout(() => box.isConnected && mountBackups(box), 3000);
}

/* ------------------------------------------------------- connections map */
const TYPE_PALETTE = ["#e8a33d", "#8e6bd6", "#3f8fd2", "#2fa58c", "#d0573f", "#5fae4a", "#c9559b", "#8a8f98",
  "#d98c2b", "#4aa3b5", "#b5883f", "#6d7fd6"];
const typeColor = (t) => TYPE_PALETTE[[...String(t)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % TYPE_PALETTE.length];

/** Spread nodes out (Fruchterman-Reingold): linked ideas pull together, all ideas push apart. */
function forceLayout(nodes, edges) {
  const n = nodes.length, idx = new Map(nodes.map((d, i) => [d.id, i]));
  const x = new Float64Array(n), y = new Float64Array(n);
  nodes.forEach((_, i) => { const r = 34 * Math.sqrt(i + 1), a = i * 2.39996; x[i] = r * Math.cos(a); y[i] = r * Math.sin(a); });
  const links = edges.map((e) => [idx.get(e.source), idx.get(e.target)]).filter(([a, b]) => a !== undefined && b !== undefined && a !== b);
  const k = 95, iterations = n > 400 ? 120 : 320;
  for (let it = 0; it < iterations; it++) {
    const temp = 4 + 50 * (1 - it / iterations);
    const fx = new Float64Array(n), fy = new Float64Array(n);
    const push = (i, j) => {
      const dx = x[i] - x[j], dy = y[i] - y[j], d2 = dx * dx + dy * dy + 0.01, f = (k * k) / d2;
      fx[i] += dx * f; fy[i] += dy * f; fx[j] -= dx * f; fy[j] -= dy * f;
    };
    if (n <= 300) {
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) push(i, j);
    } else {
      // Big maps: only nearby ideas push each other apart (a grid of cells), so the layout stays fast.
      const cell = k * 3, grid = new Map(), key = (cx, cy) => cx * 100003 + cy;
      for (let i = 0; i < n; i++) {
        const c = key(Math.floor(x[i] / cell), Math.floor(y[i] / cell));
        (grid.get(c) || grid.set(c, []).get(c)).push(i);
      }
      for (let i = 0; i < n; i++) {
        const cx = Math.floor(x[i] / cell), cy = Math.floor(y[i] / cell);
        for (let ox = -1; ox <= 1; ox++) for (let oy = -1; oy <= 1; oy++) {
          for (const j of grid.get(key(cx + ox, cy + oy)) || []) if (j > i) push(i, j);
        }
      }
    }
    for (const [a, b] of links) {
      const dx = x[b] - x[a], dy = y[b] - y[a], f = Math.sqrt(dx * dx + dy * dy) / k;
      fx[a] += dx * f; fy[a] += dy * f; fx[b] -= dx * f; fy[b] -= dy * f;
    }
    for (let i = 0; i < n; i++) {
      fx[i] -= x[i] * 0.03; fy[i] -= y[i] * 0.03;  // gentle pull to the middle keeps islands close
      const d = Math.hypot(fx[i], fy[i]) || 1, step = Math.min(d, temp);
      x[i] += (fx[i] / d) * step; y[i] += (fy[i] / d) * step;
    }
  }
  nodes.forEach((d, i) => { d.x = x[i]; d.y = y[i]; });
}

/** The same graph, built from the phone's offline copy. */
function graphFromCopy(snap, q) {
  const pid = q.get("project_id"), withSuggested = q.get("include_suggested") === "true";
  const entries = snap.entries.filter((e) => !pid || e.projects.some((p) => String(p.id) === pid));
  const ids = new Set(entries.map((e) => e.id)), edges = new Map();
  const wanted = (status) => status === "accepted" || (withSuggested && status === "suggested");
  if (snap.edges) {
    for (const [id, source, target, type, origin, status] of snap.edges) {
      if (wanted(status) && ids.has(source) && ids.has(target)) edges.set(id, { id, source, target, type, origin, status,
        yours: origin === "user" || status === "accepted" });
    }
  } else {  // a copy saved by an older NEXUS
    for (const [eid, rels] of Object.entries(snap.relationships || {})) {
      for (const r of rels) {
        if (!wanted(r.status)) continue;
        const [source, target] = r.direction === "outgoing" ? [+eid, r.other.id] : [r.other.id, +eid];
        if (ids.has(source) && ids.has(target)) edges.set(r.id, { id: r.id, source, target, type: r.relationship_type,
          origin: r.origin, status: r.status, yours: r.origin === "user" || r.status === "accepted" });
      }
    }
  }
  return { nodes: entries.map((e) => ({ id: e.id, title: e.title, entry_type: e.entry_type, status: e.status,
    thumb_url: picOf(e)?.thumb_url || null, project_ids: e.projects.map((p) => p.id), shared_by: e.shared_from?.by || null })),
    edges: [...edges.values()] };
}

async function viewMap(el, _id, query) {
  const projects = await api("/api/projects");
  const pid = query.get("project") || "", suggested = query.get("suggested") === "1", everything = query.get("all") === "1";
  const focus = +query.get("focus") || null;
  el.innerHTML = `<div class="view-head"><div><h1>Connections</h1>
    <p class="lede">How your ideas link up. Lines are links you made or accepted${suggested ? "; dashed lines are AI suggestions" : ""}. Tap an idea to open it.</p></div></div>
    <form class="filters map-filters" id="mapf">
      <label class="field">Project<select name="project"><option value="">All projects</option>${projects.map((p) =>
        `<option value="${p.id}"${String(p.id) === pid ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label class="check"><input type="checkbox" name="suggested"${suggested ? " checked" : ""}> Show AI suggestions</label>
      <label class="check"><input type="checkbox" name="all"${everything ? " checked" : ""}> Show ideas without links</label>
    </form>
    <div class="map-wrap"><svg id="map" role="group" aria-label="Map of linked ideas"></svg>
      <div class="map-zoom"><button type="button" class="btn small" data-zoom="1.3" aria-label="Zoom in">+</button>
      <button type="button" class="btn small" data-zoom="0.77" aria-label="Zoom out">−</button>
      <button type="button" class="btn small" data-zoom="fit">Fit</button></div></div>
    <div class="map-legend small" id="map-legend"></div>`;
  $("#mapf", el).onchange = () => {
    const f = new FormData($("#mapf", el)), qs = new URLSearchParams();
    if (f.get("project")) qs.set("project", f.get("project"));
    if (f.get("suggested")) qs.set("suggested", "1");
    if (f.get("all")) qs.set("all", "1");
    location.hash = "#/map" + (qs.toString() ? "?" + qs : "");
  };
  const params = new URLSearchParams({ include_suggested: String(suggested) });
  if (pid) params.set("project_id", pid);
  const g = await api("/api/graph?" + params);
  const degree = new Map();
  g.edges.forEach((e) => { degree.set(e.source, (degree.get(e.source) || 0) + 1); degree.set(e.target, (degree.get(e.target) || 0) + 1); });
  const nodes = g.nodes.filter((n) => everything || degree.has(n.id) || n.id === focus);
  const svg = $("#map", el);
  if (!nodes.length) {
    $(".map-wrap", el).innerHTML = `<p class="empty">No links yet${pid ? " in this project" : ""}. Link ideas from an entry's page
      (<em>Related ideas → Link to another idea</em>), accept AI suggestions, or tick <em>Show ideas without links</em>.</p>`;
    return;
  }
  forceLayout(nodes, g.edges);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const edges = g.edges.filter((e) => byId.has(e.source) && byId.has(e.target));
  const radius = (n) => 9 + 2.2 * Math.sqrt(degree.get(n.id) || 0);
  const short = (t) => (t.length > 24 ? t.slice(0, 22) + "…" : t);
  const NS = "http://www.w3.org/2000/svg";
  svg.innerHTML = `<defs>${nodes.filter((n) => n.thumb_url).map((n) =>
      `<clipPath id="clip${n.id}"><circle cx="${n.x}" cy="${n.y}" r="${radius(n) + 5}"/></clipPath>`).join("")}</defs>
    <g class="edges">${edges.map((e) => { const a = byId.get(e.source), b = byId.get(e.target);
      return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" data-a="${a.id}" data-b="${b.id}"
        class="edge ${e.yours ? "yours" : "suggested"} ${e.type === "contradicts" ? "contradicts" : ""}"><title>${esc(a.title)} — ${esc(label(e.type))} → ${esc(b.title)}${e.yours ? "" : " (AI suggestion)"}</title></line>`; }).join("")}</g>
    <g class="nodes">${nodes.map((n) => { const r = radius(n);
      return `<g class="node${n.id === focus ? " focus" : ""}" data-id="${n.id}" tabindex="0" role="link" aria-label="${esc(n.title)}">
        <title>${esc(n.title)} · ${esc(label(n.entry_type))}${n.shared_by ? ` · from ${esc(n.shared_by)}` : ""}</title>
        ${n.thumb_url ? `<image href="${esc(n.thumb_url)}" x="${n.x - r - 5}" y="${n.y - r - 5}" width="${2 * r + 10}" height="${2 * r + 10}"
            preserveAspectRatio="xMidYMid slice" clip-path="url(#clip${n.id})"/>
          <circle cx="${n.x}" cy="${n.y}" r="${r + 5}" class="ring" stroke="${typeColor(n.entry_type)}"/>`
          : `<circle cx="${n.x}" cy="${n.y}" r="${r}" fill="${typeColor(n.entry_type)}" class="dot"/>`}
        <text x="${n.x}" y="${n.y + r + (n.thumb_url ? 19 : 14)}">${esc(short(n.title))}</text></g>`; }).join("")}</g>`;
  const types = [...new Set(nodes.map((n) => n.entry_type))].sort();
  $("#map-legend", el).innerHTML = types.map((t) => `<span><i style="background:${typeColor(t)}"></i>${esc(label(t))}</span>`).join("")
    + `<span><b class="ln yours"></b>your link</span>${suggested ? `<span><b class="ln suggested"></b>AI suggestion</span>` : ""}
       <span><b class="ln contradicts"></b>contradicts</span> · ${nodes.length} idea${nodes.length === 1 ? "" : "s"}, ${edges.length} link${edges.length === 1 ? "" : "s"}`;

  // --- view box: fit, zoom, pan (mouse, touch and pinch)
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y), padX = 120, padY = 60;  // room for labels at the sides
  const fit = { x: Math.min(...xs) - padX, y: Math.min(...ys) - padY,
    w: Math.max(...xs) - Math.min(...xs) + 2 * padX, h: Math.max(...ys) - Math.min(...ys) + 2 * padY };
  let vb = { ...fit };
  const apply = () => {
    svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    // Labels stay a readable size on screen, however far the map is zoomed out (phones especially).
    const r = svg.getBoundingClientRect(), perPx = Math.max(vb.w / (r.width || 1), vb.h / (r.height || 1));
    svg.style.setProperty("--label", `${Math.min(11 * perPx, 40).toFixed(1)}px`);
  };
  const zoomAt = (f, cx = vb.x + vb.w / 2, cy = vb.y + vb.h / 2) => {
    const w = Math.min(Math.max(vb.w / f, 80), fit.w * 6), s = w / vb.w;
    vb = { x: cx - (cx - vb.x) * s, y: cy - (cy - vb.y) * s, w, h: vb.h * s };
    apply();
  };
  apply();
  if (focus && byId.has(focus)) { const f = byId.get(focus); vb = { x: f.x - 250, y: f.y - 180, w: 500, h: 360 }; apply(); }
  const toSvg = (ev) => { const r = svg.getBoundingClientRect(); return { x: vb.x + ((ev.clientX - r.left) / r.width) * vb.w, y: vb.y + ((ev.clientY - r.top) / r.height) * vb.h }; };
  $$("[data-zoom]", el).forEach((b) => (b.onclick = () => { if (b.dataset.zoom === "fit") { vb = { ...fit }; apply(); } else zoomAt(+b.dataset.zoom); }));
  svg.addEventListener("wheel", (ev) => { ev.preventDefault(); const p = toSvg(ev); zoomAt(ev.deltaY < 0 ? 1.15 : 0.87, p.x, p.y); }, { passive: false });
  const pointers = new Map();
  let moved = 0, pinch = null;
  svg.addEventListener("pointerdown", (ev) => { pointers.set(ev.pointerId, ev); moved = 0; svg.setPointerCapture(ev.pointerId);
    if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); } });
  svg.addEventListener("pointermove", (ev) => {
    const last = pointers.get(ev.pointerId);
    if (!last) return;
    pointers.set(ev.pointerId, ev);
    if (pointers.size === 2 && pinch) {
      const [a, b] = [...pointers.values()], d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      zoomAt(d / pinch); pinch = d; moved += 10; return;
    }
    const r = svg.getBoundingClientRect(), dx = ev.clientX - last.clientX, dy = ev.clientY - last.clientY;
    moved += Math.abs(dx) + Math.abs(dy);
    vb.x -= (dx / r.width) * vb.w; vb.y -= (dy / r.height) * vb.h; apply();
  });
  const up = (ev) => {
    pointers.delete(ev.pointerId);
    if (pointers.size < 2) pinch = null;
    if (moved < 6 && ev.type === "pointerup") {
      const node = document.elementFromPoint(ev.clientX, ev.clientY)?.closest(".node");
      if (node) { location.hash = `#/entry/${node.dataset.id}`; return; }
      // A near miss still counts (small dots on a phone): the closest idea within about a fingertip.
      const p = toSvg(ev), perPx = vb.w / (svg.getBoundingClientRect().width || 1);
      let best = null, bestPx = 22;
      for (const n of nodes) {
        const px = (Math.hypot(n.x - p.x, n.y - p.y) - radius(n)) / perPx;
        if (px < bestPx) { bestPx = px; best = n; }
      }
      if (best) location.hash = `#/entry/${best.id}`;
    }
  };
  svg.addEventListener("pointerup", up);
  svg.addEventListener("pointercancel", up);
  // Highlight an idea's own links on hover / keyboard focus.
  const highlight = (id) => {
    svg.classList.toggle("dim", !!id);
    $$(".edge", svg).forEach((l) => l.classList.toggle("hot", !!id && (l.dataset.a === id || l.dataset.b === id)));
    const near = new Set([id, ...$$(".edge.hot", svg).flatMap((l) => [l.dataset.a, l.dataset.b])]);
    $$(".node", svg).forEach((n) => n.classList.toggle("hot", !!id && near.has(n.dataset.id)));
  };
  $$(".node", svg).forEach((n) => {
    n.addEventListener("pointerenter", () => highlight(n.dataset.id));
    n.addEventListener("pointerleave", () => highlight(null));
    n.addEventListener("focus", () => highlight(n.dataset.id));
    n.addEventListener("keydown", (ev) => { if (ev.key === "Enter") location.hash = `#/entry/${n.dataset.id}`; });
  });
  if (focus) highlight(String(focus));
}

/** "← Previous · 3 of 40 · Next →" on an entry opened from a grid, plus swiping on its picture. */
function mountBrowseNav(el, id) {
  let ids = [];
  try { ids = JSON.parse(store("nexus.browse", undefined, true) || "[]"); } catch { /* none */ }
  const i = ids.indexOf(id);
  if (i < 0 || ids.length < 2) return;
  const prev = ids[i - 1], next = ids[i + 1];
  el.querySelector(".detail")?.insertAdjacentHTML("beforebegin", `<nav class="row small browse-nav" id="browse-nav" aria-label="Browse">
    ${prev ? `<a href="#/entry/${prev}" rel="prev" class="btn ghost small">← Previous</a>` : `<span></span>`}
    <span class="muted">${i + 1} of ${ids.length}</span>
    ${next ? `<a href="#/entry/${next}" rel="next" class="btn ghost small">Next →</a>` : `<span></span>`}</nav>`);
  const pic = $(".media-preview", el);
  if (!pic) return;
  let x0 = null, y0 = 0;
  pic.addEventListener("touchstart", (ev) => { if (ev.touches.length === 1) { x0 = ev.touches[0].clientX; y0 = ev.touches[0].clientY; } }, { passive: true });
  pic.addEventListener("touchend", (ev) => {
    if (x0 === null) return;
    const t = ev.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 60 && Math.abs(dy) < 50) { const to = dx < 0 ? next : prev; if (to) location.hash = `#/entry/${to}`; }
  });
}

/* ---------------------------------------------------------- manual links */
/** "Link to another idea": find an entry by title/words, pick how they're related, link. */
function mountLinker(el, e, rels) {
  const q = $("#link-q", el), out = $("#link-results", el);
  if (!q) return;
  let all = null;
  const linked = new Set(rels.map((r) => r.other.id));
  const show = async () => {
    if (!all) {
      try { all = (await api("/api/entries?limit=500&sort=modified")).items.filter((x) => x.id !== e.id); }
      catch (err) { out.innerHTML = `<p class="small muted">${esc(err.message)}</p>`; return; }
    }
    const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = all.filter((x) => words.every((w) => `${x.title} ${x.excerpt || ""} ${x.ai_summary || ""}`.toLowerCase().includes(w))).slice(0, 8);
    out.innerHTML = hits.length ? hits.map((x) => `<button type="button" class="link-hit" data-link="${x.id}">
        <strong>${esc(x.title)}</strong> <span class="muted small">${esc(label(x.entry_type))}${linked.has(x.id) ? " · already linked" : ""}</span></button>`).join("")
      : `<p class="small muted">No entries match.</p>`;
    $$("[data-link]", out).forEach((b) => (b.onclick = async () => {
      const type = $("#link-type", el).value;
      try {
        await api(`/api/entries/${e.id}/relationships`, { method: "POST", json: { target_entry_id: +b.dataset.link, relationship_type: type } });
        toast("Linked."); render();
      } catch (err) { toast(err.message, true); }
    }));
  };
  q.addEventListener("input", show);
  $(".linker", el).addEventListener("toggle", (ev) => { if (ev.target.open) { show(); q.focus(); } });
}

/* ------------------------------------------------------ project analysis */
function renderProjectAnalysis(a, titles) {
  const r = a.result || {};
  const refs = (ids) => (ids || []).length ? ` <span class="refs">${ids.map((id) => {
    const t = titles[id] || `#${id}`;
    return `<a class="cite" href="#/entry/${id}" title="${esc(t)}">${esc(t.length > 30 ? t.slice(0, 28) + "…" : t)}</a>`;
  }).join(" ")}</span>` : "";
  const rows = (items, main, sub) => (items || []).length ? `<ul class="pa-list">${items.map((x) =>
    `<li><strong>${esc(x[main])}</strong>${sub && x[sub] ? ` — ${esc(x[sub])}` : ""}${refs(x.entries)}</li>`).join("")}</ul>` : "";
  const plain = (items) => (items || []).length ? `<ul class="pa-list">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const section = (kind, title, body, why) => body ? prov(kind, title, body, why) : "";
  return `
    ${section("ai", "Overview", r.overview ? `<p>${esc(r.overview)}</p>` : "", "a reading of your material, not canon")}
    ${section("ai", "Characters", rows(r.characters, "name", "description"), "who appears, and where")}
    ${section("ai", "Story threads", rows(r.story_threads, "thread"))}
    ${section("ai", "World", rows(r.world, "element"))}
    ${section("ai", "Strongest ideas", rows(r.strongest_ideas, "idea", "why"), "already working, in the AI's reading")}
    ${section("infer", "Contradictions", rows(r.contradictions, "issue"), "entries that don't agree")}
    ${section("infer", "Open questions", plain(r.open_questions), "not decided yet")}
    ${section("infer", "Gaps", plain(r.gaps), "often needed, not here yet")}
    ${section("infer", "Possible next steps", plain(r.next_steps), "your choice, not mine")}
    ${section("unknown", "Uncertain", plain(r.uncertainties))}
    <p class="small muted">Version ${a.version} · ${esc(a.model || "")} · based on ${a.entry_ids.length} entr${a.entry_ids.length === 1 ? "y" : "ies"}
      · ${fmtDateTime(a.completed_at || a.created_at)}</p>`;
}

async function mountProjectAnalysis(box, p, shownId) {
  let versions;
  try {
    versions = await api(`/api/projects/${p.id}/analyses`, { noCopy: true });
  } catch (err) {
    box.innerHTML = `<h3>Project analysis</h3><p class="small muted">${err.offline ? "Available when NEXUS is on." : esc(err.message)}</p>`;
    return;
  }
  if (!box.isConnected) return;
  const titles = Object.fromEntries(p.entries.map((e) => [e.id, e.title]));
  const latest = versions[0];
  const running = latest && ["queued", "running"].includes(latest.status);
  const done = versions.filter((v) => v.status === "done");
  const shown = done.find((v) => v.id === shownId) || done[0];
  box.innerHTML = `<div class="row"><h3 style="margin:0">Project analysis</h3><span class="spacer"></span>
      ${done.length > 1 ? `<select id="pa-version" aria-label="Version">${done.map((v) =>
        `<option value="${v.id}"${v.id === shown.id ? " selected" : ""}>Version ${v.version} · ${fmtDate(v.created_at)}</option>`).join("")}</select>` : ""}
      ${running ? "" : `<button class="btn small ${latest ? "" : "primary"}" data-act="analyze-project">${latest ? "Analyze again" : "Analyze this project"}</button>`}</div>
    ${!latest ? `<p class="small muted">The local AI reads every entry in this project (words, notes, transcripts, sketches' analyses) and
      describes the characters, story threads and world, what's strongest, what contradicts itself and what's still open —
      each point linked to the entries behind it. Earlier versions are kept.</p>` : ""}
    ${running ? `<p class="small"><span class="spinner inline"></span>Reading ${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}… this can take a minute or two.</p>` : ""}
    ${latest?.status === "failed" ? `<p class="small" style="color:var(--danger)">The last analysis failed: ${esc(latest.error || "unknown error")}</p>` : ""}
    ${shown ? renderProjectAnalysis(shown, titles) : ""}`;
  $("#pa-version", box)?.addEventListener("change", (ev) => mountProjectAnalysis(box, p, +ev.target.value));
  $("[data-act=analyze-project]", box)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { await api(`/api/projects/${p.id}/analyses`, { method: "POST" }); }
    catch (err) { toast(err.message, true); }
    mountProjectAnalysis(box, p);
  });
  if (running) setTimeout(() => box.isConnected && mountProjectAnalysis(box, p, shownId), 3000);
}

/* ------------------------------------------------------------- Ask NEXUS */
const ASK_EXAMPLES = ["What villains have I come up with?", "Which ideas involve memory?",
  "What have I drawn lately?", "Which comic ideas are still unfinished?"];
const askHistory = [];  // this visit only; nothing is stored

/** The model's plain-text answer as safe HTML: citations become links, "- " lines a list. */
function answerBody(text, titles) {
  const cite = (html) => html.replace(/\[#(\d+)\]|#(\d+)\b/g, (m, a, b) => {
    const id = a || b;
    if (!(id in titles)) return m;
    const t = titles[id] || "Untitled";
    return `<a class="cite" href="#/entry/${id}" title="${esc(t)}">${esc(t.length > 36 ? t.slice(0, 34) + "…" : t)}</a>`;
  });
  const inline = (line) => cite(esc(line)).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  let html = "", inList = false;
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (item && !inList) { html += "<ul>"; inList = true; }
    if (!item && inList) { html += "</ul>"; inList = false; }
    if (item) html += `<li>${inline(item[1])}</li>`;
    else if (line) html += `<p>${inline(line)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

function answerHTML(a) {
  const titles = Object.fromEntries((a.sources || []).map((s) => [s.entry.id, s.entry.title]));
  const body = a.pending
    ? `<p class="small"><span class="spinner inline"></span>Reading your archive… the local model can take up to a minute.</p>`
    : a.error ? `<p class="small" style="color:var(--danger)">${esc(a.error)}</p>`
    : `<div class="answer">${answerBody(a.answer, titles)}</div>`;
  const cited = (a.sources || []).filter((x) => x.cited).length;
  return `<section class="ask-item">
    <p class="ask-q">${esc(a.question)}</p>
    ${prov("ai", "AI answer · only from your archive", body, a.provider || "")}
    ${a.note ? `<p class="small muted">${esc(a.note)}</p>` : ""}
    ${a.sources?.length ? `<p class="small muted">Based on ${a.sources.length} entr${a.sources.length === 1 ? "y" : "ies"}${
      cited ? ` · ${cited} cited in the answer` : ""}. Check them: the answer is the AI's reading, not a fact.</p>
      <div class="grid">${a.sources.map((x) => cardHTML(x.entry,
        x.cited ? `<div class="meta"><span class="match words">cited</span></div>` : "")).join("")}</div>` : ""}
  </section>`;
}

async function viewAsk(el, _id, query) {
  el.innerHTML = `<div class="view-head"><div><h1>Ask NEXUS</h1>
    <p class="lede">Ask about your own ideas. Answers come only from your archive, with links to the entries they're based on.</p></div>
    <a class="btn ghost small" href="#/search">Search instead</a></div>
    <form class="searchbox" id="aform"><input type="text" name="q" placeholder="e.g. What villains have I come up with?"
      aria-label="Your question" autocomplete="off" enterkeyhint="send">
    <button class="btn primary" type="submit">ASK</button></form>
    <div class="suggestions">${ASK_EXAMPLES.map((x) => `<button type="button" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>
    <div id="answers"></div>`;
  const box = $("#answers", el);
  const draw = () => { if (box.isConnected) box.innerHTML = askHistory.map(answerHTML).join(""); };
  async function run(question) {
    question = question.trim();
    if (question.length < 2) return;
    const item = { question, pending: true };
    askHistory.unshift(item);
    draw();
    try {
      Object.assign(item, await api("/api/ask", { method: "POST", json: { question }, timeout: 240000 }));
    } catch (err) {
      item.error = err.offline ? "NEXUS isn't reachable right now. Asking needs your PC on (your saved copy can still be searched)." : err.message;
    }
    item.pending = false;
    draw();
  }
  $("#aform", el).onsubmit = (e) => {
    e.preventDefault();
    const input = $("input", e.target);
    run(input.value);
    input.value = "";
  };
  $$("[data-q]", el).forEach((b) => (b.onclick = () => run(b.dataset.q)));
  draw();
  if (query.get("q")) run(query.get("q"));
  else if (matchMedia("(min-width: 641px)").matches) $("input", el).focus();
}

function health(h) {
  if (!h) return "";
  return `<p class="health ${h.ok ? "ok" : "bad"}"><span class="dot"></span>${esc(h.detail)}</p>`;
}

async function viewSettings(el) {
  const s = await getSettings(true);
  const ai = s.ai_provider;
  el.innerHTML = `<div class="view-head"><div><h1>Settings</h1>
    <p class="lede">Everyday choices are in <strong>Preferences</strong> below and apply straight away. Advanced settings live in the
      <code>.env</code> file next to <code>run.py</code> (restart after changing it). API keys stay in the environment and are never stored in the database.</p></div></div>
    <div class="grid">
      <div class="panel"><h3>AI provider</h3>${health(ai.health)}<dl class="kv">
        <dt>Active</dt><dd>${esc(ai.name)}</dd>
        <dt>Sends data off this machine</dt><dd>${ai.external == null ? "—" : ai.external ? "Yes" : "No"}</dd>
        ${ai.health?.installed ? `<dt>Installed models</dt><dd>${esc(ai.health.installed.join(", ") || "none")}</dd>` : ""}
        <dt>Available</dt><dd>${esc(ai.available.join(", "))}</dd></dl>
        <p class="small muted">Set <code>NEXUS_AI_PROVIDER</code> and <code>NEXUS_OLLAMA_MODEL</code>.</p></div>
      <div class="panel"><h3>Transcription</h3>${health(s.transcription_provider.health)}<dl class="kv">
        <dt>Active</dt><dd>${esc(s.transcription_provider.name)}</dd>
        <dt>Available</dt><dd>${esc(s.transcription_provider.available.join(", "))}</dd></dl>
        <p class="small muted">Set <code>NEXUS_TRANSCRIPTION_PROVIDER</code> and <code>NEXUS_WHISPER_MODEL</code>.</p></div>
      <div class="panel"><h3>Background work</h3><dl class="kv">
        <dt>Mode</dt><dd>${esc(s.processing.mode)}</dd><dt>In progress</dt><dd>${s.processing.active} entr${s.processing.active === 1 ? "y" : "ies"}</dd></dl>
        <p class="small muted">Captures are saved instantly; transcription and organizing happen afterwards, one at a time.</p></div>
      <div class="panel"><h3>Search by meaning</h3>${health(s.search.health)}<dl class="kv">
        <dt>Word search</dt><dd>${s.search.mode === "fts5" ? "On (full-text)" : "On (basic)"}</dd>
        <dt>Meaning model</dt><dd>${esc(s.search.embedding_model || "—")}</dd>
        ${s.search.index ? `<dt>Indexed</dt><dd>${s.search.index.indexed} of ${s.search.index.entries} entries</dd>` : ""}</dl>
        ${s.search.semantic_enabled && s.search.index?.pending
          ? `<button class="btn small" data-act="reindex">Index ${s.search.index.pending} entr${s.search.index.pending === 1 ? "y" : "ies"} now</button>` : ""}
        <p class="small muted">Finds ideas by what they're about, not just exact words. Set <code>NEXUS_EMBEDDING_MODEL</code>.</p></div>
      <div class="panel"><h3>Storage</h3><dl class="kv">
        <dt>Data folder</dt><dd>${esc(s.storage.data_dir)}</dd><dt>Database</dt><dd>${esc(s.storage.database)}</dd>
        <dt>Upload limit</dt><dd>${s.storage.max_upload_mb} MB</dd><dt>Server</dt><dd>${esc(s.server.host)}:${s.server.port}</dd></dl></div>
      <div class="panel"><h3>Archive</h3><dl class="kv">
        <dt>Entries</dt><dd>${s.counts.entries}</dd><dt>Projects</dt><dd>${s.counts.projects}</dd>
        <dt>Tags</dt><dd>${s.counts.tags}</dd><dt>In trash</dt><dd><a href="#/trash">${s.counts.trash} · view trash</a></dd></dl></div>
      <div class="panel" id="backups"><h3>Backups</h3><p class="loading">Loading…</p></div>
      <div class="panel" id="import"></div>
      <div class="panel" id="device"></div>
      <div class="panel" id="lock"></div>
      ${HAS_OFFLINE_COPY ? "" : `<div class="panel" id="phone"></div>`}
      ${HAS_OFFLINE_COPY ? "" : `<div class="panel" id="updates"><h3>Updates</h3>
        <p class="small muted">NEXUS ${esc(APP_VERSION || "")} · your ideas and settings are never touched by an update.</p>
        <button type="button" class="btn small" data-act="check-update">Check for updates</button><div id="update-info"></div></div>`}
      <div class="panel" id="report"><h3>Something not working?</h3>
        <p class="small muted">Make a report to paste when asking for help: versions, whether the AI and search are healthy,
          counts and recent log lines. It never includes what your entries say, and nothing is sent anywhere: you see it first and copy it yourself.</p>
        <button type="button" class="btn small" data-act="report">Make a problem report</button></div>
    </div>`;
  el.querySelector(".view-head").insertAdjacentHTML("afterend", `<h2 class="sr-only">Settings</h2><section class="panel prefs" id="prefs"><p class="loading">Loading…</p></section>`);
  mountPreferences($("#prefs", el));
  mountBackups($("#backups", el));
  mountImport($("#import", el));
  mountDevice($("#device", el));
  mountLock($("#lock", el));
  if ($("#phone", el)) mountPhone($("#phone", el));
  $("[data-act=check-update]", el)?.addEventListener("click", (ev) => checkForUpdate(ev.target, $("#update-info", el)));
  if (location.hash.includes("check-update=1") && $("[data-act=check-update]", el)) {  // from the tray menu
    $("#updates", el).scrollIntoView();
    $("[data-act=check-update]", el).click();
  }
  $("[data-act=report]", el).onclick = async (ev) => {
    const box = $("#report", el);
    ev.target.disabled = true;
    try {
      const { text } = await api("/api/problem-report");
      ev.target.remove();
      box.insertAdjacentHTML("beforeend", `<textarea class="report" readonly rows="12" aria-label="Problem report">${esc(text)}</textarea>
        <div class="row"><button type="button" class="btn small" data-act="copy-report">Copy</button></div>`);
      $("[data-act=copy-report]", box).onclick = async () => {
        const area = $("textarea", box);
        try { await navigator.clipboard.writeText(area.value); } catch { area.select(); document.execCommand("copy"); }
        toast("Copied. Paste it wherever you're asking for help.");
      };
    } catch (err) {
      toast(err.offline ? "The report can be made when NEXUS is on." : err.message, true);
      ev.target.disabled = false;
    }
  };
  $("[data-act=reindex]", el)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try {
      const r = await api("/api/search/reindex", { method: "POST" });
      toast(`Indexing ${r.queued} entr${r.queued === 1 ? "y" : "ies"} in the background…`);
      setTimeout(() => { if (location.hash === "#/settings") render(); }, 4000);
    } catch (err) { toast(err.message, true); ev.target.disabled = false; }
  });
}

/** Settings → Updates (on the computer NEXUS runs on): check git, then run the update script. */
async function checkForUpdate(button, box) {
  button.disabled = true; button.textContent = "Checking…";
  let info;
  try { info = await api("/api/update", { timeout: 120000 }); }
  catch (err) { box.innerHTML = `<p class="small" style="color:var(--danger)">${esc(err.message)}</p>`; return; }
  finally { button.disabled = false; button.textContent = "Check again"; }
  if (!info.behind) {
    box.innerHTML = `<p class="health ${info.reason ? "bad" : "ok"}"><span class="dot"></span>${esc(info.reason || "NEXUS is up to date.")}</p>`;
    return;
  }
  box.innerHTML = `<p class="health ok"><span class="dot"></span>NEXUS ${esc(info.latest_version || "")} is available
      (${info.behind} change${info.behind === 1 ? "" : "s"}).</p>
    ${info.changes.length ? `<ul class="small">${info.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    ${info.can_update ? `<button type="button" class="btn primary small" data-act="update-now">UPDATE NOW</button>
      <p class="small muted">NEXUS stops for a minute or two while it updates, then this page reloads by itself.</p>`
      : `<p class="small" style="color:var(--danger)">${esc(info.reason || "")}</p>`}`;
  $("[data-act=update-now]", box)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { await api("/api/update", { method: "POST", timeout: 60000 }); }
    catch (err) { toast(err.message, true); ev.target.disabled = false; return; }
    box.innerHTML = `<p class="small"><span class="spinner inline"></span>Updating… NEXUS is stopping, downloading, setting up and starting again.</p>`;
    const from = APP_VERSION, started = Date.now();
    const poll = async () => {
      try {
        const r = await fetch("/api/ping", { cache: "no-store" });
        const v = r.ok && (await r.json()).version;
        if (v && v !== from) { location.reload(); return; }
      } catch { /* still restarting */ }
      if (Date.now() - started > 6 * 60 * 1000) {
        box.innerHTML = `<p class="small" style="color:var(--danger)">The update is taking longer than expected. Details are in
          <code>data/update.log</code> in the NEXUS folder. If NEXUS doesn't come back, run the update script from a terminal.</p>`;
        return;
      }
      setTimeout(poll, 3000);
    };
    setTimeout(poll, 5000);
  });
}

async function viewTrash(el) {
  const [data, projects] = await Promise.all([api("/api/trash"), api("/api/projects?trashed=true")]);
  el.innerHTML = `<p><a href="#/archive" class="muted small">← Creative Archive</a></p>
    <div class="view-head"><div><h1>Trash</h1>
    <p class="lede">Things you moved here are hidden, but nothing is destroyed. Restore brings them back exactly as they were.
      <strong>Delete forever</strong> removes something and its files for good (older backups may still contain it).</p></div>
    ${data.items.length || projects.length ? `<button class="btn ghost small danger" data-act="empty-trash">Empty trash</button>` : ""}</div>
    <h2>Entries</h2>
    ${data.items.length ? `<div class="grid">${data.items.map((e) => `<div class="card trashed">
      <div class="meta"><span class="pill type">${esc(label(e.entry_type))}</span><span class="spacer"></span>
        <span>Trashed ${fmtDate(e.deleted_at)}</span></div>
      <h3>${esc(e.title)}</h3>
      ${e.ai_summary || e.excerpt ? `<p class="summary">${esc(e.excerpt || e.ai_summary)}</p>` : ""}
      <div class="row"><button class="btn small" data-restore="${e.id}">Restore</button>
        <button class="btn small ghost danger" data-forever="${e.id}" data-title="${esc(e.title)}">Delete forever</button></div></div>`).join("")}</div>`
      : `<p class="empty">No entries in the trash.</p>`}
    <h2>Projects</h2>
    ${projects.length ? `<div class="grid">${projects.map((p) => `<div class="card trashed">
      <div class="meta"><span class="pill type">${esc(label(p.project_type))}</span><span class="spacer"></span>
        <span>Trashed ${fmtDate(p.deleted_at)}</span></div>
      <h3>${esc(p.name)}</h3>
      <p class="summary">${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}${p.description ? " · " + esc(p.description) : ""}</p>
      <div class="row"><button class="btn small" data-restore-project="${p.id}">Restore</button>
        <button class="btn small ghost danger" data-forever-project="${p.id}" data-title="${esc(p.name)}">Delete forever</button></div></div>`).join("")}</div>`
      : `<p class="empty">No projects in the trash.</p>`}`;
  const forever = async (url, what) => {
    try { await api(url, { method: url.endsWith("empty") ? "POST" : "DELETE" }); toast(`${what} deleted forever.`); render(); }
    catch (err) { toast(err.message, true); }
  };
  $$("[data-forever]", el).forEach((b) => (b.onclick = () => {
    if (confirm(`Delete “${b.dataset.title}” forever? Its words, notes, files and history are removed and can't be restored (older backups may still have it).`))
      forever(`/api/trash/entries/${b.dataset.forever}`, "Entry");
  }));
  $$("[data-forever-project]", el).forEach((b) => (b.onclick = () => {
    if (confirm(`Delete the project “${b.dataset.title}” forever? Its entries are not touched.`))
      forever(`/api/trash/projects/${b.dataset.foreverProject}`, "Project");
  }));
  $("[data-act=empty-trash]", el)?.addEventListener("click", () => {
    if (confirm(`Empty the trash? ${data.items.length} entr${data.items.length === 1 ? "y" : "ies"} and ${projects.length} project${projects.length === 1 ? "" : "s"} are deleted forever, with their files. This can't be undone.`))
      forever("/api/trash/empty", "Everything in the trash was");
  });
  $$("[data-restore-project]", el).forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try {
      await api(`/api/projects/${b.dataset.restoreProject}/restore`, { method: "POST" });
      toast("Project restored.");
      render();
    } catch (err) { toast(err.message, true); b.disabled = false; }
  }));
  $$("[data-restore]", el).forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try {
      await api(`/api/entries/${b.dataset.restore}/restore`, { method: "POST" });
      toast("Restored to the archive.");
      render();
    } catch (err) { toast(err.message, true); b.disabled = false; }
  }));
}

/* ------------------------------------------------------------ revisions */
const REV_FIELDS = [
  ["title", "Title"], ["entry_type", "Type"], ["status", "Status"], ["user_notes", "Your notes"],
  ["transcript", "Transcript"], ["tags", "Tags"], ["project_ids", "Projects"], ["user_confirmed", "Confirmed"],
  ["ai_summary", "AI summary"], ["ai_interpretation", "AI interpretation"], ["ai_inference", "AI inference"],
];

function revValue(key, v, projects) {
  if (v == null || v === "" || (Array.isArray(v) && !v.length)) return "—";
  if (key === "tags") return v.map((t) => t.name + (t.source === "ai" ? " (AI)" : "")).join(", ");
  if (key === "project_ids") return v.map((pid) => projects.find((p) => p.id === pid)?.name || `#${pid}`).join(", ");
  if (key === "entry_type") return label(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  const text = String(v);
  return text.length > 240 ? text.slice(0, 237) + "…" : text;
}

const sameAs = (a, b) => REV_FIELDS.every(([k]) => JSON.stringify(a[k] ?? null) === JSON.stringify(b[k] ?? null));

function revisionHTML(r, prev, latest, projects) {
  const cur = r.content_snapshot || {};
  const isLatest = r.id === latest.id;
  const canRestore = !isLatest && !sameAs(cur, latest.content_snapshot || {});
  let body;
  if (!prev) {
    body = `<p class="small muted">The original capture.</p>`;
  } else {
    const before = prev.content_snapshot || {};
    const changes = REV_FIELDS.filter(([k]) => JSON.stringify(cur[k] ?? null) !== JSON.stringify(before[k] ?? null));
    body = changes.length
      ? `<table class="diff">${changes.map(([k, l]) => `<tr><th>${l}</th><td>
          <span class="before">${esc(revValue(k, before[k], projects))}</span>
          <span class="arrow">→</span> <span class="after">${esc(revValue(k, cur[k], projects))}</span></td></tr>`).join("")}</table>`
      : `<p class="small muted">No visible changes (internal data only).</p>`;
  }
  return `<li><details>
    <summary><strong>v${r.version_number}</strong> ${esc(r.change_description)}${isLatest ? ` <span class="pill">current</span>` : ""}
      <span class="muted">· ${fmtDateTime(r.created_at)}</span></summary>
    ${body}
    ${canRestore ? `<button class="btn small" data-restore-version="${r.version_number}">Restore this version</button>` : ""}
    <details class="raw"><summary class="small muted">Raw data</summary>
      <pre class="snapshot">${esc(JSON.stringify(cur, null, 2))}</pre></details>
  </details></li>`;
}

/* ------------------------------------------------------------ entry detail */
async function viewEntry(el, id) {
  const [e, rels, revs, s, projects] = await Promise.all([
    api(`/api/entries/${id}`), api(`/api/entries/${id}/relationships`), api(`/api/entries/${id}/revisions`),
    getSettings(), api("/api/projects"),
  ]);
  const m = e.ai_metadata || {};
  const hasImage = !!picOf(e);
  const srcBadge = (src) => `<span class="source-badge ${src}">${src === "user" ? "yours" : "AI"}</span>`;
  const original = (a) => (["image/jpeg", "image/png"].includes(a.mime_type) ? ""
    : `<p class="small muted">Showing a preview. <a href="${esc(a.url)}">Download the original ${esc(a.original_filename)}</a> (never changed).</p>`);
  const media = e.attachments.map((a) =>
    a.preview_url ? `<div class="media-preview"><img src="${esc(a.preview_url)}" data-thumb="${esc(a.thumb_url || "")}" alt="${esc(a.original_filename)}"></div>${original(a)}`
    : a.kind === "audio" ? `<div class="media-preview"><audio controls preload="metadata" src="${esc(a.url)}"></audio></div>`
    : `<p><a class="btn small" href="${esc(a.url)}">Download ${esc(a.original_filename)}</a></p>`).join("");
  const list = (arr) => `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  const transcriptInfo = m.transcription;

  const who = e.shared_from?.by;
  const userBlock = e.original_text
    ? prov("user", who ? `${who}'s material` : "User material", `<div class="body">${esc(e.original_text)}</div>`,
        who ? `exactly as ${who} wrote it · never modified` : "exactly as you captured it · never modified")
    : m.page_of ? prov("unknown", "User material", `<div class="body">This picture is page ${m.page_of.page} of your PDF, drawn from it by NEXUS.
        <a href="#/entry/${m.page_of.id}">The PDF</a> is the original, kept exactly as you saved it.</div>`)
    : prov("unknown", "User material", `<div class="body">No words were written with this ${esc(label(e.source_type))} capture. The original file above is the user material.</div>`);

  const canTranscribe = e.source_type === "voice" && e.attachments.some((a) => a.kind === "audio") && !isBusy(e);
  const transcriptBlock = e.source_type === "voice"
    ? (e.transcript
      ? prov(m.transcript_source === "user" ? "user" : "ai", "Transcript", `<div class="body" id="transcript-text">${esc(e.transcript)}</div>
          <div class="row" style="margin-top:8px"><button class="btn ghost small" data-act="edit-transcript">Correct transcript</button>
          ${canTranscribe && m.transcript_source !== "user" ? `<button class="btn ghost small" data-act="transcribe">Transcribe again</button>` : ""}</div>`,
          m.transcript_source === "user" ? "corrected by you"
            : m.transcript_source === "shared" ? `machine-generated on ${who || "their"}'s NEXUS`
            : `machine-generated from your recording (${transcriptInfo?.provider || "unknown"})`)
      : prov("unknown", "Transcript · unknown", `<div class="body">${esc(transcriptInfo?.message || (isBusy(e) ? "Transcription is on its way…" : "No transcript exists yet."))}</div>
          ${canTranscribe ? `<div class="row" style="margin-top:8px"><button class="btn small" data-act="transcribe">Transcribe now</button></div>` : ""}`))
    : "";

  const v = m.visual;
  const visualRows = v ? [["Looks like", v.possible_type && label(v.possible_type)], ["Subjects", v.visual_subjects],
    ["Characters", v.characters], ["Objects", v.objects], ["Setting", v.setting], ["Style", v.style], ["Colours", v.colors]] : [];
  const visualBlock = v ? (visualRows.some(([, x]) => x && x.length)
    ? prov("ai", "AI interpretation · image", `<dl class="kv">${visualRows.map(([k, x]) =>
        `<dt>${k}</dt><dd>${x && x.length ? esc(Array.isArray(x) ? x.join(", ") : x) : `<span class="muted">unknown</span>`}</dd>`).join("")}</dl>`,
        "what the model saw — not what you said")
    : prov("unknown", "Image · unknown", `<div class="body">The picture's content hasn't been analysed.</div>`)) : "";

  const busyBanner = isBusy(e) ? `<div class="busy-banner" id="busy"><span class="spinner"></span>
    <div><strong>NEXUS is ${esc(busyLabel(e.processing_status))}</strong><br>
    <span class="small muted">Your original is already saved. This page updates by itself when the local AI finishes.</span></div></div>` : "";

  const analysisBlocks = e.analyzed_at ? `
    ${prov("ai", "AI summary", `<div class="body">${esc(e.ai_summary || "—")}</div>`, `${e.ai_provider} · ${fmtDateTime(e.analyzed_at)}`)}
    ${prov("ai", "AI interpretation", `<div class="body">${esc(e.ai_interpretation || "—")}</div>
      ${m.concepts?.length ? `<div class="small" style="margin-top:8px">Concepts: ${m.concepts.map((c) => `<span class="tag ai">${esc(c)}</span>`).join(" ")}</div>` : ""}`,
      `overall confidence ${pct(e.confidence)}`)}
    ${prov("infer", "AI inference", `<div class="body">${esc(e.ai_inference || "—")}</div>
      ${m.potential_projects?.length ? `<div class="small" style="margin-top:8px">Might belong to: ${m.potential_projects.map((p) =>
        `<a href="#/projects/${p.id}">${esc(p.name)}</a> <span class="confidence">${pct(p.confidence)}</span>`).join(", ")}</div>` : ""}`,
      "possibilities, not facts")}
    ${m.uncertainties?.length || m.analysis_error ? prov("unknown", "Unknown / uncertain",
      list([...(m.analysis_error ? [m.analysis_error] : []), ...(m.uncertainties || [])])) : ""}
    ${visualBlock}
    ${m.method ? `<p class="small muted">Method: ${esc(m.method)}</p>` : ""}`
    : isBusy(e) ? ""
    : prov("unknown", "Not analysed", `<div class="body">${esc(m.analysis_error || "The AI organizer hasn't looked at this entry yet.")}</div>`);

  const isPdf = e.attachments.some((a) => a.mime_type === "application/pdf");
  const pagesBlock = m.page_of ? `<nav class="row small page-nav" id="page-nav" aria-label="Pages">Page ${m.page_of.page} of ${m.page_of.pages} ·
      <a href="#/entry/${m.page_of.id}">the whole PDF</a></nav>`
    : isPdf && m.pages?.length ? `<nav class="page-list small" aria-label="Pages"><span class="muted">Pages as entries:</span> ${m.pages.map((pid, i) =>
      `<a href="#/entry/${pid}">${i + 1}</a>`).join(" ")}</nav>
      <div class="row small split-pages"><span class="muted">Read as a comic sequence (the local AI, 6 pages at a time):</span>
      ${Array.from({ length: Math.ceil(m.pages.length / 6) }, (_, g) => { const from = g * 6, to = Math.min(from + 6, m.pages.length);
        return `<button type="button" class="btn small ghost" data-seq="${m.pages.slice(from, to).join(",")}">Pages ${from + 1}–${to}</button>`; }).join("")}</div>`
    : isPdf ? `<div class="row small split-pages"><button type="button" class="btn small" data-act="split-pages">Split into pages</button>
      <label class="check"><input type="checkbox" id="split-analyze"> Analyze each page (slower)</label>
      <span class="muted">One picture entry per page, linked to this PDF. The PDF stays as it is.</span></div>` : "";
  const inProjects = new Set(e.projects.map((p) => p.id));
  el.innerHTML = `<p class="row"><a href="javascript:history.back()" class="muted small">← Back</a><span class="spacer"></span>${LEGEND}</p>
  <div class="detail"><div>
    <div class="meta"><span>${esc(label(e.source_type))} capture</span><span>·</span><span>${fmtDateTime(e.created_at)}</span></div>
    ${busyBanner}
    <h1 class="detail-title" id="title">${esc(e.title)}</h1>
    <div class="row">
      ${srcBadge(e.title_source)}<span class="small muted">title</span>
      <button class="btn ghost small" data-act="edit-title">Edit title</button>
      <span class="spacer"></span>
      <label class="small muted">Type <select id="type">${options(s.vocab.entry_types, e.entry_type)}</select></label>${srcBadge(e.entry_type_source)}
      <label class="small muted">Status <select id="status">${options(s.vocab.statuses, e.status)}</select></label>
    </div>
    ${!e.user_confirmed && (e.title_source === "ai" || e.entry_type_source === "ai") && e.analyzed_at
      ? `<p class="small muted">The title and type above were chosen by the AI. <button class="btn small" data-act="confirm">Confirm them</button> to keep them fixed.</p>` : ""}
    ${media}
    ${pagesBlock}
    ${userBlock}
    ${e.shared_from?.notes ? prov("user", `${who}'s notes`, `<div class="body">${esc(e.shared_from.notes)}</div>`, `came with the share · never modified`) : ""}
    ${prov("user", "Your notes", `<div class="body" id="notes-view">${e.user_notes ? esc(e.user_notes) : `<span class="muted">No notes yet.</span>`}</div>
      <div class="row" style="margin-top:8px"><button class="btn ghost small" data-act="edit-notes">${e.user_notes ? "Edit notes" : "Add notes"}</button></div>`,
      "additions you've written later")}
    ${transcriptBlock}
    ${analysisBlocks}
    ${hasImage ? `<div id="artpanel"></div>` : ""}
    ${isBusy(e) ? "" : `<div class="row"><button class="btn" data-act="analyze">Re-run AI organizer</button>
      <span class="small muted">Regenerates AI data only. Your words, title and type choices are kept.</span></div>`}
  </div>

  <aside class="side"><h2 class="sr-only">Tags, projects and links</h2>
    <div class="panel"><h3>Tags</h3>
      <div class="meta" id="tags">${e.tags.length ? tagsHTML(e.tags, true) : `<span class="muted small">No tags.</span>`}</div>
      <form class="row" id="addtag"><input type="text" name="tag" placeholder="Add tag" style="flex:1"><button class="btn small">Add</button></form>
      <p class="small muted">Dashed = AI tag. Removing an AI tag stops it coming back.</p></div>

    <div class="panel"><h3>Projects</h3>
      ${e.projects.length ? e.projects.map((p) => `<div class="row"><a href="#/projects/${p.id}">${esc(p.name)}</a><span class="spacer"></span>
        <button class="btn ghost small" data-remove-project="${p.id}" aria-label="Remove from ${esc(p.name)}">×</button></div>`).join("") : `<span class="muted small">Not in a project.</span>`}
      ${projects.some((p) => !inProjects.has(p.id)) ? `<div class="row"><select id="addproj" style="flex:1"><option value="">Add to project…</option>${projects
        .filter((p) => !inProjects.has(p.id)).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select></div>` : ""}</div>

    <div class="panel"><h3>Related ideas</h3>
      ${rels.length ? rels.map((r) => `<div class="rel ${esc(r.status)}">
        <div class="row small"><span class="source-badge ${r.origin}">${esc(r.label)}</span><span class="spacer"></span><span class="confidence">${pct(r.confidence)}</span></div>
        <div>${r.direction === "incoming"
          ? `<a href="#/entry/${r.other.id}">${esc(r.other.title)}</a> <span class="muted small">${esc(label(r.relationship_type))} → this</span>`
          : `<span class="muted small">${esc(label(r.relationship_type))} →</span> <a href="#/entry/${r.other.id}">${esc(r.other.title)}</a>`}</div>
        ${r.rationale ? `<div class="why">${esc(r.rationale)}</div>` : ""}
        ${r.origin === "ai" && r.status !== "accepted" ? `<div class="row">
          <button class="btn small" data-rel="${r.id}" data-status="accepted">Accept</button>
          <button class="btn small ghost" data-rel="${r.id}" data-status="rejected">Reject</button>
          ${r.status !== "ignored" ? `<button class="btn small ghost" data-rel="${r.id}" data-status="ignored">Ignore</button>` : ""}</div>`
          : `<div class="row"><button class="btn small ghost" data-rel="${r.id}" data-status="rejected" data-unlink>Remove link</button></div>`}
      </div>`).join("") : `<span class="muted small">No related entries found yet.</span>`}
      <p class="small"><a href="#/map?focus=${e.id}${rels.some((r) => r.status === "suggested") ? "&suggested=1" : ""}">See it on the connections map →</a></p>
      ${e.entry_type === "character" || e.tags.some((t) => t.name === "character") ? `<p class="small"><a href="#/characters?c=${e.id}">Everything about this character →</a></p>`
        : e.entry_type === "worldbuilding" || e.tags.some((t) => KIND_OF.world[1].includes(t.name)) ? `<p class="small"><a href="#/characters?kind=world&amp;c=${e.id}">Everything set here →</a></p>` : ""}
      <details class="linker"><summary class="btn small ghost">+ Link to another idea</summary>
        <div class="linker-body">
          <input type="search" id="link-q" placeholder="Find an entry by title or words" aria-label="Find an entry to link" autocomplete="off">
          <div id="link-results" class="link-results"></div>
          <div class="row"><select id="link-type" aria-label="How they're related">${s.vocab.relationship_types.map((t) =>
            `<option value="${esc(t)}"${t === "related_to" ? " selected" : ""}>${esc(label(t))}</option>`).join("")}</select></div>
          <p class="small muted">A link you make is yours: the AI never removes it or suggests it again.</p>
        </div></details></div>

    ${hasImage ? `<div class="panel" id="evopanel"></div>` : ""}

    <div class="panel"><h3>Share</h3>
      <p class="small muted">Send this to someone else's NEXUS as a <code>.nexus</code> file (AirDrop, email, USB).
        Your words and original files go; the AI notes stay here.</p>
      <button class="btn small" data-act="export-entry">Export this entry</button></div>

    <div class="panel"><h3>Source information</h3><dl class="kv">
      ${m.duplicate_of ? `<dt>Same file as</dt><dd><a href="#/entry/${m.duplicate_of.id}">${esc(m.duplicate_of.title)}</a></dd>` : ""}
      ${who ? `<dt>Shared by</dt><dd>${esc(who)} · from their NEXUS${e.shared_from.exported_at ? `, sent ${fmtDate(e.shared_from.exported_at)}` : ""}</dd>` : ""}
      <dt>Source</dt><dd>${esc(label(e.source_type))}</dd>
      <dt>Created</dt><dd>${fmtDateTime(e.created_at)}</dd>
      <dt>Modified</dt><dd>${fmtDateTime(e.updated_at)}</dd>
      ${e.attachments.map((a) => `<dt>File</dt><dd>${esc(a.original_filename)}</dd><dt>Format</dt><dd>${esc(a.mime_type)} · ${fmtSize(a.size_bytes)}</dd>
        <dt>SHA-256</dt><dd class="confidence" title="${esc(a.sha256)}">${esc(a.sha256.slice(0, 16))}…</dd>`).join("")}
      <dt>AI provider</dt><dd>${esc(e.ai_provider || "—")}</dd>
      <dt>Confirmed</dt><dd>${e.user_confirmed ? "Yes, by you" : "No"}</dd>
      <dt>Entry id</dt><dd>#${e.id}</dd></dl></div>

    <div class="panel"><h3>Revision history</h3>
      <p class="small muted">Every change is kept. Restoring adds a new version, so nothing is ever lost.</p>
      <ol class="revisions">${revs.map((r, i) => revisionHTML(r, revs[i + 1], revs[0], projects)).join("")}</ol></div>

    <button class="btn danger ghost" data-act="trash">Move to trash</button>
  </aside></div>`;

  const put = async (body, msg) => {
    try { await api(`/api/entries/${id}`, { method: "PUT", json: body }); if (msg) toast(msg); render(); }
    catch (err) { toast(err.message, true); }
  };
  $("[data-act=export-entry]", el).onclick = () => exportShare({ entry_ids: String(e.id) });
  $("#type", el).onchange = (ev) => put({ entry_type: ev.target.value }, "Type set by you.");
  $("#status", el).onchange = (ev) => put({ status: ev.target.value }, "Status updated.");
  $("[data-act=confirm]", el)?.addEventListener("click", () => put({ user_confirmed: true }, "Confirmed."));
  mountBrowseNav(el, e.id);
  $("[data-act=split-pages]", el)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Splitting…";
    try {
      const pages = await api(`/api/entries/${e.id}/split-pages?analyze=${$("#split-analyze", el).checked}`, { method: "POST", timeout: 120000 });
      toast(`${pages.length} pages saved as entries.`);
      render();
    } catch (err) { toast(err.message, true); ev.target.disabled = false; ev.target.textContent = "Split into pages"; }
  });
  $$("[data-seq]", el).forEach((b) => (b.onclick = () => {
    const [first, ...rest] = b.dataset.seq.split(",").map(Number);
    if (!rest.length) return toast("A sequence needs at least two pages.", true);
    b.disabled = true;
    startArt(first, "sequence", rest).catch((err) => { toast(err.message, true); b.disabled = false; });
  }));
  if (m.page_of) api(`/api/entries/${m.page_of.id}`).then((pdf) => {  // previous / next page
    const ids = pdf.ai_metadata?.pages || [], i = ids.indexOf(e.id), nav = $("#page-nav", el);
    if (!nav || i < 0) return;
    nav.insertAdjacentHTML("afterbegin", i > 0 ? `<a href="#/entry/${ids[i - 1]}" rel="prev">← Page ${i}</a> · ` : "");
    nav.insertAdjacentHTML("beforeend", i < ids.length - 1 ? ` · <a href="#/entry/${ids[i + 1]}" rel="next">Page ${i + 2} →</a>` : "");
  }).catch(() => {});
  $("[data-act=edit-title]", el).onclick = () => {
    const v = prompt("Title (leave empty to use the AI's suggestion):", e.title);
    if (v !== null) put({ title: v }, "Title saved.");
  };
  $("[data-act=edit-notes]", el).onclick = () => {
    const view = $("#notes-view", el);
    view.innerHTML = `<textarea rows="6" aria-label="Your notes">${esc(e.user_notes || "")}</textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary small" data-act="save-notes">SAVE NOTES</button></div>`;
    $("textarea", view).focus();
    $("[data-act=save-notes]", view).onclick = () => put({ user_notes: $("textarea", view).value }, "Notes saved.");
  };
  $("[data-act=analyze]", el)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try { await api(`/api/entries/${id}/analyze`, { method: "POST" }); toast("Organizing again…"); render(); }
    catch (err) { toast(err.message, true); ev.target.disabled = false; }
  });
  $$("[data-act=transcribe]", el).forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { await api(`/api/entries/${id}/transcribe`, { method: "POST" }); toast("Transcribing…"); render(); }
    catch (err) { toast(err.message, true); b.disabled = false; }
  }));
  $("[data-act=edit-transcript]", el)?.addEventListener("click", () => {
    const view = $("#transcript-text", el);
    view.innerHTML = `<textarea rows="6" aria-label="Transcript">${esc(e.transcript || "")}</textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary small" data-act="save-transcript">SAVE TRANSCRIPT</button>
      <span class="small muted">The recording itself is never changed.</span></div>`;
    $("textarea", view).focus();
    $("[data-act=save-transcript]", view).onclick = () => put({ transcript: $("textarea", view).value }, "Transcript corrected.");
  });
  if (isBusy(e)) {
    const route = location.hash;
    const banner = $("#busy", el);
    watchEntry(e.id, banner, () => { if (location.hash === route) render(); },
      (x) => { const t = $("strong", banner); if (t) t.textContent = `NEXUS is ${busyLabel(x.processing_status)}`; });
  }
  const currentTags = e.tags.map((t) => t.name);
  $$("[data-remove-tag]", el).forEach((b) => (b.onclick = () =>
    put({ tags: currentTags.filter((t) => t !== b.dataset.removeTag) }, "Tag removed.")));
  $("#addtag", el).onsubmit = (ev) => {
    ev.preventDefault();
    const v = ev.target.tag.value.trim();
    if (v) put({ tags: [...currentTags, v] }, "Tag added.");
  };
  $("#addproj", el)?.addEventListener("change", async (ev) => {
    if (!ev.target.value) return;
    try { await api(`/api/projects/${ev.target.value}/entries`, { method: "POST", json: { entry_id: e.id } }); toast("Added to project."); render(); }
    catch (err) { toast(err.message, true); }
  });
  $$("[data-remove-project]", el).forEach((b) => (b.onclick = async () => {
    await api(`/api/projects/${b.dataset.removeProject}/entries/${e.id}`, { method: "DELETE" });
    toast("Removed from project."); render();
  }));
  $$("[data-rel]", el).forEach((b) => (b.onclick = async () => {
    if (b.hasAttribute("data-unlink") && !confirm("Remove this link? The AI won't suggest it again.")) return;
    try {
      await api(`/api/relationships/${b.dataset.rel}`, { method: "PATCH", json: { status: b.dataset.status } });
      toast(b.hasAttribute("data-unlink") ? "Link removed." : `Relationship ${b.dataset.status}.`);
      render();
    } catch (err) { toast(err.message, true); }
  }));
  mountLinker(el, e, rels);
  if (hasImage) mountArtPanels(el, e);
  $$("[data-restore-version]", el).forEach((b) => (b.onclick = async () => {
    const v = b.dataset.restoreVersion;
    if (!confirm(`Bring this entry back to version ${v}? The current version stays in the history, so you can always come back to it.`)) return;
    try { await api(`/api/entries/${id}/revisions/${v}/restore`, { method: "POST" }); toast(`Restored version ${v}.`); render(); }
    catch (err) { toast(err.message, true); }
  }));
  $("[data-act=trash]", el).onclick = async () => {
    if (!confirm("Move this entry to the trash? It will be hidden, but the original material is kept and can be restored.")) return;
    await api(`/api/entries/${id}`, { method: "DELETE" });
    toast("Moved to trash. Original preserved.");
    location.hash = "#/archive";
  };
}

/* ------------------------------------------------------ visual art intelligence */
const ART_MODES = {
  analysis: { label: "Analyze", hint: "Layered analysis: what's there, how it works, possibilities." },
  critique: { label: "Critique", hint: "A deeper, specific critique: what works, what could be stronger, why." },
  artist: { label: "Artist mode", hint: "A creative collaborator that asks questions instead of rewriting your idea." },
  page: { label: "Comic page", hint: "Layout, reading order, panel hierarchy, page-turn potential." },
  storyboard: { label: "Storyboard", hint: "Camera, staging, pacing and clarity. Rendering is not judged." },
  sequence: { label: "Sequence", hint: "Panels in order: what each tells the reader and what changes." },
  compare: { label: "Compare A / B", hint: "What changed between two images, without declaring a winner." },
  consistency: { label: "Character consistency", hint: "Same character, several drawings: what differs, what stays." },
  evolution: { label: "Evolution", hint: "Versions over time: how the piece developed." },
};
const ONE_IMAGE_MODES = ["analysis", "critique", "artist", "page", "storyboard"];
const MANY_IMAGE_MODES = ["sequence", "compare", "consistency", "evolution", "storyboard"];
const ART_ACTIVE = ["queued", "running"];

const FIELD_LABELS = {
  focal_point: "Focal point", character_placement: "Character placement", depth_layers: "Foreground / midground / background",
  negative_space: "Negative space", directional_movement: "Directional movement", eye_flow: "How the eye travels",
  balance: "Balance", cropping: "Cropping", shape_language: "Shape language", silhouette: "Silhouette",
  linework: "Linework & inking", value: "Value", lighting: "Lighting", color: "Color", perspective: "Perspective",
  depth: "Depth", texture_and_detail: "Texture & detail", stylization: "Stylization",
  pose_and_gesture: "Pose & gesture", expression: "Expression", costume_and_design: "Costume & design",
  proportions_and_anatomy: "Proportions & anatomy", readability: "Readability",
  visual_hierarchy: "Visual hierarchy", emotional_read: "Emotional read", told_without_words: "Told without words",
  needs_words: "Needs dialogue or captions", panel_function: "Panel function", shot_type: "Shot type",
  camera_angle: "Camera angle", pacing: "Pacing", reader_eye_flow: "Reader eye flow",
  layout: "Layout", panel_hierarchy: "Panel hierarchy", reading_order: "Reading order", gutters: "Gutters",
  transitions: "Transitions", dialogue_density: "Dialogue density", visual_climax: "Visual climax", page_turn: "Page turn",
  continuity: "Continuity", screen_direction: "Screen direction", camera_consistency: "Camera consistency",
  time_progression: "Time progression", action_progression: "Action progression",
  reader_comprehension: "Reader comprehension", composition: "Composition", character: "Character",
  anatomy: "Anatomy", storytelling: "Storytelling", facial_structure: "Facial structure", hair: "Hair",
  costume: "Costume", proportions: "Proportions", accessories: "Accessories", weapon: "Weapon", markings: "Markings",
};

function kvBlock(obj, skip = []) {
  const rows = Object.entries(obj || {}).filter(([k, v]) => !skip.includes(k) && v && !(Array.isArray(v) && !v.length));
  if (!rows.length) return "";
  return `<dl class="kv art-kv">${rows.map(([k, v]) => `<dt>${esc(FIELD_LABELS[k] || label(k))}</dt>
    <dd>${Array.isArray(v) ? `<ul>${v.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : esc(v)}</dd>`).join("")}</dl>`;
}

function listBlock(items) {
  return items?.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
}

function artSection(kind, title, body, why = "") {
  return body && body.replace(/<[^>]*>/g, "").trim() ? prov(kind, title, body, why) : "";
}

/** Turn a structured analysis into a readable artistic critique. */
function renderArtAnalysis(a) {
  const d = a.details || {};
  const st = a.storytelling_analysis || {};
  const c = d.critique || {};
  return `
    ${a.question ? prov("user", "You asked", `<div class="body">${esc(a.question)}</div>`) : ""}
    ${artSection("ai", "Artist mode · collaborator", d.collaborator_response ? `<div class="body">${esc(d.collaborator_response)}</div>` : "")}
    ${artSection("ai", "What I see", a.visual_description ? `<div class="body">${esc(a.visual_description)}</div>` : "",
      "literal description" + (d.artwork_type ? " · reads as " + label(d.artwork_type) : ""))}
    ${a.mode === "critique" ? `
      ${artSection("ai", "Critique · what is working", c.what_is_working ? `<div class="body">${esc(c.what_is_working)}</div>` : "")}
      ${artSection("ai", "Critique · what could be stronger", c.what_could_be_stronger
        ? `<div class="body">${esc(c.what_could_be_stronger)}</div>${c.why_it_matters ? `<p class="small"><strong>Why it matters:</strong> ${esc(c.why_it_matters)}</p>` : ""}` : "")}
      ${artSection("ai", "Critique · possible approaches", listBlock(c.possible_approaches))}
      ${artSection("infer", "Critique · questions to consider", listBlock(c.questions_to_consider))}
      ${artSection("infer", "Critique · optional experiments", listBlock(c.optional_experiments), "only if you want to")}` : ""}
    ${artSection("ai", "Comparison · what changed", listBlock(d.comparison?.what_changed) + kvBlock(d.comparison, ["what_changed"]),
      "differences, not a verdict")}
    ${artSection("ai", a.mode === "evolution" ? "How it evolved" : "Character consistency",
      (d.consistency?.same_character ? `<p><strong>Same character?</strong> ${esc(d.consistency.same_character)}</p>` : "")
      + kvBlock(d.consistency, ["same_character", "differences", "possibly_intentional", "constants"])
      + (d.consistency?.differences?.length ? `<p class="small"><strong>Differences</strong></p>${listBlock(d.consistency.differences)}` : "")
      + (d.consistency?.possibly_intentional?.length ? `<p class="small"><strong>Possibly intentional</strong></p>${listBlock(d.consistency.possibly_intentional)}` : "")
      + (d.consistency?.constants?.length ? `<p class="small"><strong>Stays constant</strong></p>${listBlock(d.consistency.constants)}` : ""),
      "differences aren't assumed to be mistakes")}
    ${artSection("ai", "Page", kvBlock(d.page))}
    ${artSection("ai", "Sequence", kvBlock(d.sequence))}
    ${artSection("ai", "Panel by panel", d.panels?.length ? `<ol class="panels">${d.panels.map((p) =>
      `<li><strong>${esc(p.panel || "")}</strong> ${esc(p.information || "")}${p.what_changes ? `<br><span class="muted small">Changes: ${esc(p.what_changes)}</span>` : ""}</li>`).join("")}</ol>` : "")}
    ${artSection("ai", "Composition", kvBlock(a.composition_analysis))}
    ${artSection("ai", "Artistic analysis", kvBlock(a.artistic_analysis))}
    ${artSection("ai", "Character", kvBlock(a.character_analysis))}
    ${artSection("ai", "Environment", d.environment ? `<div class="body">${esc(d.environment)}</div>` : "")}
    ${artSection("ai", "Storytelling · visible", st.visible_information ? `<div class="body">${esc(st.visible_information)}</div>` : "", "what the image shows")}
    ${artSection("infer", "Storytelling · possible interpretation", st.possible_interpretation ? `<div class="body">${esc(st.possible_interpretation)}</div>` : "", "not confirmed story information")}
    ${artSection("ai", "Visual storytelling", kvBlock(st, ["visible_information", "possible_interpretation"]))}
    ${artSection("ai", "Sequential art", kvBlock(a.sequential_art_analysis))}
    <div class="levels">
      ${artSection("ai", "Preserve", listBlock(a.strengths), "already working, in the AI's reading")}
      ${artSection("ai", "Develop", listBlock(a.development_opportunities), "could be strengthened")}
      ${artSection("infer", "Experiment", listBlock(a.experiments), "optional, never required")}
    </div>
    ${d.creative_directions?.length ? prov("infer", "Creative directions", `<div class="directions">${d.creative_directions.map((o, i) =>
      `<div class="direction"><strong>Option ${String.fromCharCode(65 + i)}: ${esc(o.name)}</strong><p>${esc(o.description)}</p></div>`).join("")}</div>`,
      "your choice, not mine") : ""}
    ${artSection("infer", "Questions for you", listBlock(d.questions))}
    ${artSection("unknown", "Unclear / uncertain", listBlock(a.uncertainties))}
    ${d.knowledge_references?.length ? `<p class="small muted">Drew on your library: ${d.knowledge_references.map(esc).join(", ")}</p>` : ""}
    ${d.style_signals?.length ? `<p class="small muted">Style signals noticed: ${d.style_signals.map(esc).join(", ")}</p>` : ""}
    <p class="small muted">${esc(a.model || "")} · version ${a.analysis_version} · ${fmtDateTime(a.completed_at || a.created_at)}
      — observations and suggestions, not rules. You decide.</p>`;
}

async function startArt(entryId, mode, related = [], question = null) {
  const a = await api(`/api/entries/${entryId}/art`, { method: "POST", json: { mode, related_entry_ids: related, question } });
  location.hash = `#/art/analysis/${a.id}`;
}

function askQuestionThenStart(entryId) {
  const q = prompt("Artist mode: what would you like to think through? (optional)", "");
  if (q === null) return;
  startArt(entryId, "artist", [], q).catch((err) => toast(err.message, true));
}

async function viewArtAnalysis(el, id) {
  const a = await api(`/api/art/analyses/${id}`);
  const mode = ART_MODES[a.mode] || { label: a.mode };
  const busy = ART_ACTIVE.includes(a.status);
  const thumbs = a.images.map((img, i) => `<a class="art-thumb" href="#/entry/${img.entry_id}">
    ${img.url ? `<img src="${esc(img.url)}" alt="">` : ""}<span>${a.mode === "compare" ? (i ? "B" : "A") : `Image ${i + 1}`} · ${esc(img.title)}</span></a>`).join("");
  el.innerHTML = `<p><a href="#/entry/${a.entry_id}" class="muted small">← Back to the artwork</a> · <a href="#/art" class="muted small">Art</a></p>
    <div class="view-head"><div><div class="meta"><span class="pill type">${esc(mode.label)}</span><span class="pill">version ${a.analysis_version}</span></div>
      <h1>Visual Art Intelligence</h1><p class="lede">${esc(mode.hint || "")}</p></div>
      ${busy ? "" : `<button class="btn" data-act="again">Run again</button>`}</div>
    <div class="art-thumbs">${thumbs}</div>
    ${busy ? `<div class="busy-banner" id="artbusy"><span class="spinner"></span><div><strong>Looking at the artwork…</strong><br>
      <span class="small muted">The local model is studying the image. This page updates by itself (usually under a minute or two).</span></div></div>` : ""}
    ${a.status === "failed" ? prov("unknown", "Analysis couldn't run", `<div class="body">${esc(a.error || "Unknown error")}</div>`) : ""}
    ${a.status === "done" ? renderArtAnalysis(a) : ""}
    <h2>Other modes</h2><div class="row">${ONE_IMAGE_MODES.map((m) =>
      `<button class="btn small" data-mode="${m}" title="${esc(ART_MODES[m].hint)}">${ART_MODES[m].label}</button>`).join("")}
      <a class="btn ghost small" href="#/art">More (compare, sequence…)</a></div>`;
  $$("[data-mode]", el).forEach((b) => (b.onclick = () =>
    b.dataset.mode === "artist" ? askQuestionThenStart(a.entry_id) : startArt(a.entry_id, b.dataset.mode).catch((err) => toast(err.message, true))));
  $("[data-act=again]", el)?.addEventListener("click", () =>
    startArt(a.entry_id, a.mode, a.related_entry_ids, a.question).catch((err) => toast(err.message, true)));
  if (busy) {
    const node = $("#artbusy", el);
    const route = location.hash;
    const tick = async () => {
      if (!node.isConnected) return;
      try {
        const x = await api(`/api/art/analyses/${id}`);
        if (!ART_ACTIVE.includes(x.status)) { if (location.hash === route) render(); return; }
      } catch { /* try again */ }
      setTimeout(tick, 2500);
    };
    setTimeout(tick, 2500);
  }
}

async function viewEvolution(el, id) {
  const chain = await api(`/api/entries/${id}/evolution`);
  const img = picOf;
  el.innerHTML = `<p><a href="#/entry/${id}" class="muted small">← Back</a></p>
    <div class="view-head"><div><h1>How it evolved</h1>
    <p class="lede">Every version linked with “new version of”, oldest first. Nothing is overwritten; each version keeps its own history.</p></div>
    ${chain.length > 1 ? `<button class="btn primary" data-act="evo">ANALYZE THE EVOLUTION</button>` : ""}</div>
    ${chain.length > 1 ? `<ol class="timeline">${chain.map((e, i) => `<li><a class="card" href="#/entry/${e.id}">
      ${img(e) ? `<img class="thumb" src="${esc(img(e).thumb_url || img(e).preview_url)}" alt="" loading="lazy">` : ""}
      <div class="meta"><span class="pill type">Version ${i + 1}</span><span class="spacer"></span><span>${fmtDate(e.created_at)}</span></div>
      <h3>${esc(e.title)}</h3>${e.excerpt ? `<p class="summary">${esc(e.excerpt)}</p>` : ""}</a></li>`).join("")}</ol>`
    : `<p class="empty">This artwork isn't linked to other versions yet. On a newer drawing, use “This is a new version of…” to connect them.</p>`}`;
  $("[data-act=evo]", el)?.addEventListener("click", () => {
    const ids = chain.map((e) => e.id).filter((x) => img(chain.find((e) => e.id === x)));
    if (ids.length > 6) toast("Analyzing the 6 most recent versions.");
    const use = ids.slice(-6);
    startArt(use[0], "evolution", use.slice(1)).catch((err) => toast(err.message, true));
  });
}

/** Art panels on an entry page (only for entries with an image). */
async function mountArtPanels(el, e) {
  const panel = $("#artpanel", el), evo = $("#evopanel", el);
  if (!panel) return;
  const [list, gallery, chain] = await Promise.all([
    api(`/api/entries/${e.id}/art`), api("/api/art/gallery"), api(`/api/entries/${e.id}/evolution`),
  ]).catch(() => [[], [], []]);
  if (!panel.isConnected) return;
  const latest = list[0];
  const others = gallery.filter((g) => g.entry.id !== e.id);
  const busy = latest && ART_ACTIVE.includes(latest.status);
  panel.innerHTML = prov("ai", "Visual Art Intelligence", `
    ${!latest ? `<p class="muted">No art analysis yet.</p>`
      : busy ? `<p><span class="spinner inline"></span> Analyzing the artwork (${esc(ART_MODES[latest.mode]?.label || latest.mode)})…</p>`
      : latest.status === "failed" ? `<p class="muted">Last analysis couldn't run: ${esc(latest.error || "")}</p>`
      : `<p class="body">${esc(latest.visual_description || "")}</p>
         ${latest.strengths?.[0] ? `<p class="small"><strong>Preserve:</strong> ${esc(latest.strengths[0])}</p>` : ""}
         ${latest.development_opportunities?.[0] ? `<p class="small"><strong>Develop:</strong> ${esc(latest.development_opportunities[0])}</p>` : ""}`}
    ${latest && !busy ? `<p><a href="#/art/analysis/${latest.id}">Open full analysis →</a>
      ${list.length > 1 ? `<span class="muted small"> · ${list.length} versions: ${list.slice(0, 6).map((x) =>
        `<a href="#/art/analysis/${x.id}">v${x.analysis_version} ${esc(ART_MODES[x.mode]?.label || x.mode)}</a>`).join(", ")}</span>` : ""}</p>` : ""}
    <div class="row">${ONE_IMAGE_MODES.map((m) => `<button class="btn small" data-mode="${m}" title="${esc(ART_MODES[m].hint)}">${ART_MODES[m].label}</button>`).join("")}</div>
    ${others.length ? `<div class="row" style="margin-top:8px"><select id="cmpwith" style="flex:1"><option value="">Compare with another artwork…</option>
      ${others.map((g) => `<option value="${g.entry.id}">${esc(g.entry.title)}</option>`).join("")}</select></div>` : ""}`,
    "an artist's reading, not a verdict");
  $$("[data-mode]", panel).forEach((b) => (b.onclick = () =>
    b.dataset.mode === "artist" ? askQuestionThenStart(e.id) : startArt(e.id, b.dataset.mode).catch((err) => toast(err.message, true))));
  $("#cmpwith", panel)?.addEventListener("change", (ev) => {
    if (ev.target.value) startArt(e.id, "compare", [Number(ev.target.value)]).catch((err) => toast(err.message, true));
  });
  if (busy) watchArt(latest.id, panel, () => mountArtPanels(el, e));

  if (!evo) return;
  const linkable = others.filter((g) => !chain.some((c) => c.id === g.entry.id));
  evo.innerHTML = `<h3>Art development</h3>
    ${chain.length > 1 ? `<p class="small">Version ${chain.findIndex((c) => c.id === e.id) + 1} of ${chain.length}.
      <a href="#/art/evolution/${e.id}">Show how it evolved →</a></p>` : `<p class="small muted">Not linked to other versions yet.</p>`}
    ${linkable.length ? `<select id="versionof"><option value="">This is a new version of…</option>
      ${linkable.map((g) => `<option value="${g.entry.id}">${esc(g.entry.title)} (${fmtDate(g.entry.created_at)})</option>`).join("")}</select>` : ""}`;
  $("#versionof", evo)?.addEventListener("change", async (ev) => {
    if (!ev.target.value) return;
    try {
      await api(`/api/entries/${e.id}/relationships`, { method: "POST",
        json: { target_entry_id: Number(ev.target.value), relationship_type: "evolved_from" } });
      toast("Linked as a new version.");
      render();
    } catch (err) { toast(err.message, true); }
  });
}

function watchArt(analysisId, node, onDone) {
  const tick = async () => {
    if (!node.isConnected) return;
    try {
      const a = await api(`/api/art/analyses/${analysisId}`);
      if (!ART_ACTIVE.includes(a.status)) return onDone(a);
    } catch { /* keep trying */ }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
}

/* Art hub: artwork gallery (multi-select), creative memory, library */
async function viewArt(el, _id, query) {
  const tab = query.get("tab") || "artwork";
  const tabs = [["artwork", "Artwork"], ["memory", "Creative memory"], ["library", "Art library"]];
  el.innerHTML = `<div class="view-head"><div><h1>Art</h1>
    <p class="lede">Visual Art Intelligence reads your artwork like an illustrator and visual storyteller would: it observes, explains and asks. You decide.</p></div></div>
    <div class="tabs">${tabs.map(([k, l]) => `<a href="#/art?tab=${k}" class="${k === tab ? "active" : ""}">${l}</a>`).join("")}</div>
    <div id="arttab"></div>`;
  const box = $("#arttab", el);
  if (tab === "memory") return artMemory(box);
  if (tab === "library") return artLibrary(box);
  return artGallery(box);
}

async function artGallery(box) {
  const items = await api("/api/art/gallery");
  if (!items.length) {
    box.innerHTML = `<p class="empty">No artwork yet. Upload a sketch, panel or page from the <a href="#/">capture</a> screen: JPEG, PNG, TIFF, PSD, SVG, PDF, WebP, HEIC and more.</p>`;
    return;
  }
  const picked = [];
  box.innerHTML = `<div class="selbar" id="selbar"><span id="selcount" class="small muted">Tap artworks to select them. Order matters (Image 1, 2…).</span>
      <span class="spacer"></span><span id="selactions" class="row"></span></div>
    <div class="art-grid">${items.map((g) => {
      const img = picOf(g.entry);
      return `<div class="art-tile" data-id="${g.entry.id}">
        <button type="button" class="pick" aria-label="Select ${esc(g.entry.title)}"><span class="num"></span></button>
        <a href="#/entry/${g.entry.id}">${img ? `<img src="${esc(img.thumb_url || img.preview_url)}" alt="" loading="lazy">` : ""}
        <span class="cap">${esc(g.entry.title)}</span></a>
        <span class="small muted">${g.analyses ? `${g.analyses} analys${g.analyses === 1 ? "is" : "es"}` : "not analyzed"}${
          g.latest_status && ART_ACTIVE.includes(g.latest_status) ? " · working…" : ""}</span></div>`;
    }).join("")}</div>`;
  const refresh = () => {
    $$(".art-tile", box).forEach((t) => {
      const i = picked.indexOf(Number(t.dataset.id));
      t.classList.toggle("picked", i >= 0);
      $(".num", t).textContent = i >= 0 ? i + 1 : "";
    });
    const n = picked.length;
    $("#selcount", box).textContent = n ? `${n} selected` : "Tap artworks to select them. Order matters (Image 1, 2…).";
    const modes = n === 0 ? [] : n === 1 ? ONE_IMAGE_MODES : MANY_IMAGE_MODES.filter((m) => m !== "compare" || n === 2);
    $("#selactions", box).innerHTML = modes.map((m) =>
      `<button class="btn small" data-mode="${m}" title="${esc(ART_MODES[m].hint)}">${ART_MODES[m].label}</button>`).join("")
      + (n ? `<button class="btn ghost small" data-act="clear">Clear</button>` : "");
    $$("[data-mode]", box).forEach((b) => (b.onclick = () => {
      const [first, ...rest] = picked;
      if (b.dataset.mode === "artist") return askQuestionThenStart(first);
      startArt(first, b.dataset.mode, rest).catch((err) => toast(err.message, true));
    }));
    $("[data-act=clear]", box)?.addEventListener("click", () => { picked.length = 0; refresh(); });
  };
  $$(".art-tile .pick", box).forEach((b) => (b.onclick = () => {
    const id = Number(b.closest(".art-tile").dataset.id);
    const i = picked.indexOf(id);
    if (i >= 0) picked.splice(i, 1); else if (picked.length < 6) picked.push(id); else toast("Up to 6 images at a time.", true);
    refresh();
  }));
  refresh();
}

async function artMemory(box) {
  const prefs = await api("/api/art/preferences");
  const confirmed = prefs.filter((p) => p.status === "confirmed");
  const suggested = prefs.filter((p) => p.status === "suggested");
  box.innerHTML = `
    <p class="lede">Preferences you confirm here are shared with the AI as context (never as rules). Patterns NEXUS notices
      are only suggestions, always shown with the artworks behind them, until you confirm them.</p>
    <h2>Your confirmed preferences</h2>
    ${confirmed.length ? `<div class="meta">${confirmed.map((p) => `<span class="tag user">${esc(p.statement)}
      <button data-pref="${p.id}" data-status="rejected" aria-label="Remove">×</button></span>`).join(" ")}</div>` : `<p class="muted small">None yet.</p>`}
    <form class="row" id="addpref" style="margin-top:12px"><input type="text" name="statement" placeholder="e.g. I prefer heavy silhouettes and dramatic lighting" style="flex:1">
      <button class="btn small">Add</button></form>
    <h2>Observed patterns · AI inference</h2>
    ${suggested.length ? suggested.map((p) => prov("infer", "Observed pattern", `
      <div class="body">${p.evidence.length} of your artworks show “${esc(p.statement)}”.</div>
      <div class="art-thumbs small">${p.evidence.map((i) => `<a class="art-thumb" href="#/entry/${i.entry_id}">${i.url ? `<img src="${esc(i.url)}" alt="">` : ""}<span>${esc(i.title)}</span></a>`).join("")}</div>
      <div class="row"><button class="btn small" data-pref="${p.id}" data-status="confirmed">Yes, that's my preference</button>
        <button class="btn ghost small" data-pref="${p.id}" data-status="rejected">No</button></div>`,
      "not assumed until you confirm")).join("")
      : `<p class="muted small">None yet. A pattern appears once at least 3 different artworks share the same trait.</p>`}`;
  $$("[data-pref]", box).forEach((b) => (b.onclick = async () => {
    await api(`/api/art/preferences/${b.dataset.pref}`, { method: "PATCH", json: { status: b.dataset.status } });
    toast(b.dataset.status === "confirmed" ? "Saved as your preference." : "Removed.");
    artMemory(box);
  }));
  $("#addpref", box).onsubmit = async (ev) => {
    ev.preventDefault();
    const v = ev.target.statement.value.trim();
    if (!v) return;
    try { await api("/api/art/preferences", { method: "POST", json: { statement: v } }); artMemory(box); }
    catch (err) { toast(err.message, true); }
  };
}

async function artLibrary(box) {
  const [items, meta] = await Promise.all([api("/api/art/knowledge"), api("/api/art/topics")]);
  box.innerHTML = `
    <p class="lede">Add notes from art books, courses and references (anatomy, perspective, storyboarding, inking…).
      When they're relevant, the analysis draws on them and tells you which note it used.</p>
    <form class="filters" id="addk">
      <label class="field" style="flex:2 1 220px">Title<input type="text" name="title" required placeholder="e.g. Silhouette and readability"></label>
      <label class="field">Topic<select name="topic">${options(meta.topics, "composition")}</select></label>
      <label class="field" style="flex:2 1 200px">Source<input type="text" name="source" placeholder="Book, course or link (optional)"></label>
      <label class="field" style="flex:1 1 100%">Notes<textarea name="content" rows="6" required placeholder="Paste or write the principle in your own words…"></textarea></label>
      <button class="btn primary" type="submit">ADD TO LIBRARY</button>
    </form>
    ${items.length ? items.map((k) => `<details class="panel libitem"><summary><strong>${esc(k.title)}</strong>
        <span class="pill">${esc(k.topic)}</span>${k.source ? ` <span class="muted small">${esc(k.source)}</span>` : ""}</summary>
        <div class="body small" style="white-space:pre-wrap">${esc(k.content)}</div>
        <div class="row"><button class="btn danger ghost small" data-delk="${k.id}">Remove</button></div></details>`).join("")
      : `<p class="empty">Your art library is empty.</p>`}`;
  $("#addk", box).onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      await api("/api/art/knowledge", { method: "POST", json: Object.fromEntries(new FormData(ev.target)) });
      toast("Added to your art library.");
      artLibrary(box);
    } catch (err) { toast(err.message, true); }
  };
  $$("[data-delk]", box).forEach((b) => (b.onclick = async () => {
    if (!confirm("Remove this note from the library?")) return;
    await api(`/api/art/knowledge/${b.dataset.delk}`, { method: "DELETE" });
    artLibrary(box);
  }));
}

/* ----------------------------------------------------------------- router */
const ROUTES = [
  [/^\/?$/, viewHome, ""],
  [/^\/today$/, viewToday, "today"],
  [/^\/archive$/, viewArchive, "archive"],
  [/^\/ideas$/, viewIdeas, "ideas"],
  [/^\/projects$/, viewProjects, "projects"],
  [/^\/projects\/(\d+)$/, viewProject, "projects"],
  [/^\/projects\/(\d+)\/print$/, viewBible, "projects"],
  [/^\/entry\/(\d+)$/, viewEntry, "archive"],
  [/^\/search$/, viewSearch, "search"],
  [/^\/ask$/, viewAsk, "ask"],
  [/^\/map$/, viewMap, "map"],
  [/^\/tags$/, viewTags, "archive"],
  [/^\/timeline$/, viewTimeline, "archive"],
  [/^\/characters$/, viewCharacters, "archive"],
  [/^\/settings$/, viewSettings, "settings"],
  [/^\/trash$/, viewTrash, "archive"],
  [/^\/art$/, viewArt, "art"],
  [/^\/art\/analysis\/(\d+)$/, viewArtAnalysis, "art"],
  [/^\/art\/evolution\/(\d+)$/, viewEvolution, "art"],
];

let renderToken = 0;
async function render() {
  const token = ++renderToken;
  const [path, qs] = (location.hash.slice(1) || "/").split("?");
  const query = new URLSearchParams(qs || "");
  const match = ROUTES.find(([re]) => re.test(path)) || ROUTES[0];
  const [re, view, nav] = match;
  const id = path.match(re)?.[1];
  $$(".nav a, .tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  // Each render gets its own container; a slower, superseded view keeps
  // writing into its detached container and never clobbers the new one.
  const container = document.createElement("div");
  container.innerHTML = `<p class="loading">Loading…</p>`;  // replaced by the view as soon as it has something
  $("#view").replaceChildren(container);
  try {
    await view(container, id, query);
  } catch (err) {
    if (token !== renderToken) return;
    container.innerHTML = err.offline
      ? `<p class="empty">NEXUS isn't reachable right now. Is the PC on and NEXUS running?<br>
          You can still <a href="#/">capture ideas</a>: they wait on this device and are sent automatically.${
          HAS_OFFLINE_COPY ? `<br><br>This page isn't in the copy saved on this device${offlineCopy._snap ? "" :
            " (there's no saved copy yet: open NEXUS once while your PC is on)"}.` : ""}</p>`
      : `<p class="empty">Something went wrong: ${esc(err.message)}</p>`;
  }
}

window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });

// Opening an entry from a grid (Archive, Timeline, a project, Characters, Art…) remembers the
// entries around it, so its page can offer previous / next (buttons, ← →, or a swipe on the picture).
document.addEventListener("click", (ev) => {
  const a = ev.target.closest?.('a[href^="#/entry/"]'), list = a && (a.closest("[data-browse]") || a.closest(".grid, .strip, .art-grid"));
  if (!list) return;
  const ids = [...new Set($$('a[href^="#/entry/"]', list).map((x) => Number(x.getAttribute("href").split("/")[2])))];
  store("nexus.browse", JSON.stringify(ids), true);
}, true);

// Keyboard shortcuts (desktop): / search, n new capture, a ask, m map, ? list them.
document.addEventListener("keydown", (ev) => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.target.closest?.("input, textarea, select, [contenteditable]")) return;
  if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && $("#browse-nav")) {
    $(`#browse-nav a[rel=${ev.key === "ArrowLeft" ? "prev" : "next"}]`)?.click();
    return;
  }
  const go = { "/": "#/search", n: "#/", a: "#/ask", m: "#/map", t: "#/today" }[ev.key];
  if (go) {
    ev.preventDefault();
    if (location.hash === go || (go === "#/" && !location.hash)) render(); else location.hash = go;
    setTimeout(() => $("#view input[type=search], #view #cap-text, #view #aform input")?.focus(), 150);
  } else if (ev.key === "?") {
    toast("Shortcuts: / search · n new capture · a ask · m map · t today · Ctrl/⌘+Enter capture");
  }
});
/** True if the service worker just had to open the app from its saved copy (NEXUS unreachable). */
async function pageCameFromCopy() {
  try {
    const mark = await caches.match("/__nexus-page-offline");
    return !!mark && Date.now() - Number(await mark.text()) < 15000;
  } catch {
    return false;
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  if (HAS_OFFLINE_COPY && "caches" in window && (await pageCameFromCopy())) markUnreachable();
  render();
  $("#outbox").onclick = () => { lastUnreachable = 0; flushOutbox({ quiet: false }); };
  reachable().then((ok) => { if (ok) { flushOutbox(); syncOfflineCopy({ force: true }); } }).catch(() => {});
  // While the offline copy is showing, check now and then whether NEXUS is back.
  setInterval(async () => {
    if ($("#offline-bar").hidden || document.hidden) return;
    lastUnreachable = 0;
    if (await reachable().catch(() => false)) {
      showOfflineBar(false);
      flushOutbox();
      syncOfflineCopy({ force: true });
      render();
    }
  }, 20000);
  setInterval(() => { if (!document.hidden && $("#offline-bar").hidden) syncOfflineCopy(); }, 60000);
  // iPhones have no background sync: retry while the app is open, and whenever it comes back.
  setInterval(() => { if (!$("#outbox").hidden) flushOutbox(); }, 20000);
  window.addEventListener("online", () => flushOutbox());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    flushOutbox();
    syncOfflineCopy();
    reachable().catch(() => {});  // also notices a newer NEXUS on the PC
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
});
