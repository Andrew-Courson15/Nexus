/* NEXUS front end — vanilla JS, hash-routed single page. */
"use strict";

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const label = (s) => String(s ?? "").replace(/_/g, " ");
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

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
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

/** Quick check (3 s at most) whether NEXUS answers right now. */
async function reachable() {
  try {
    await api("/api/ping", { timeout: 3000 });
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
const MAX_OFFLINE_THUMBS = 500;
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
    if (!this._loaded) {
      this._snap = (await this.get("snapshot").catch(() => null)) || null;
      this._loaded = true;
    }
    return this._snap;
  },
  async save(snap) {
    this._snap = snap;
    await this.put("snapshot", snap);
  },
};

let lastSync = 0, syncing = false;
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
    await saveThumbnails(snap);
  } catch { /* unreachable or storage full: keep the previous copy */ } finally {
    syncing = false;
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

/** Word search over the offline copy (search by meaning needs NEXUS). */
function searchCopy(snap, query) {
  const terms = [...new Set((query.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [])
    .filter((t) => t.length > 1 && !STOP_WORDS.has(t)))];
  const stems = terms.map(stem);
  const results = [];
  for (const e of snap.entries) {
    const fields = [e.title, e.original_text, e.user_notes, e.transcript, e.ai_summary, e.ai_interpretation,
      e.tags.map((t) => t.name).join(" "), (e.ai_metadata?.concepts || []).join(" ")].filter(Boolean);
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
async function offlineAnswer(path, opts = {}) {
  const snap = await offlineCopy.snapshot();
  const [p, qs] = path.split("?");
  const q = new URLSearchParams(qs || "");
  const entry = (id) => snap?.entries.find((e) => e.id === +id);
  let m, out;
  if (snap) {
    if (p === "/api/entries") out = listFromCopy(snap, q);
    else if ((m = p.match(/^\/api\/entries\/(\d+)$/))) out = entry(m[1]);
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/relationships$/))) out = entry(m[1]) && (snap.relationships[m[1]] || []);
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/revisions$/))) out = entry(m[1]) && [];
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/art$/))) out = entry(m[1]) && (snap.art[m[1]] || []);
    else if ((m = p.match(/^\/api\/entries\/(\d+)\/evolution$/))) out = entry(m[1]) && [entry(m[1])];
    else if ((m = p.match(/^\/api\/art\/analyses\/(\d+)$/))) out = Object.values(snap.art).flat().find((a) => a.id === +m[1]);
    else if (p === "/api/projects") out = q.get("trashed") === "true" ? [] : snap.projects;
    else if ((m = p.match(/^\/api\/projects\/(\d+)$/))) {
      const proj = snap.projects.find((x) => x.id === +m[1]);
      out = proj && { ...proj, entries: snap.entries.filter((e) => e.projects.some((x) => x.id === proj.id)) };
    } else if (p === "/api/trash") out = { items: [], total: 0 };
    else if (p === "/api/search") out = searchCopy(snap, opts.json?.query || "");
  }
  if (out === undefined && COPY_LISTS.includes(path)) out = await offlineCopy.get("url:" + path).catch(() => undefined);
  if (out === undefined || out === null || out === false) return undefined;
  showOfflineBar(true, snap?.generated_at);
  return out;
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
    if (recentlyUnreachable()) throw unreachable();  // known offline: save instantly
    // Files can take a while to upload, so check first; text just gets a short time limit.
    // (If a timed-out capture did arrive after all, its id stops the re-send duplicating it.)
    if (item.file && !(await reachable())) throw unreachable();
    return { entry: await sendCapture(item, item.file ? undefined : 5000) };
  } catch (err) {
    if (!err.offline) throw err;
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
        if (err.offline) break;  // still unreachable: try again later
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
    pending.innerHTML = `<div class="recorder"><strong>${files.length} files ready</strong>
      <span class="muted small">${files.map((f) => esc(f.name)).join(", ")}</span>
      <input type="text" placeholder="A few words for all of them (optional)" aria-label="Note">
      <div class="row"><button class="btn primary small" data-act="saveall">SAVE ALL</button><button class="btn ghost small" data-act="discard">Discard</button></div></div>`;
    $("[data-act=discard]", pending).onclick = () => { pending.hidden = true; pending.innerHTML = ""; };
    $("[data-act=saveall]", pending).onclick = async (ev) => {
      ev.target.disabled = true;
      const note = $("input", pending).value.trim() || null;
      let queued = 0, saved = 0, last = null;
      const failed = [];
      for (const file of files) {
        try {
          const r = await captureOrQueue({ file, filename: file.name, note, project_id: opts.projectId || null });
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
  el.innerHTML = `<div id="cap"></div>
    <div id="just"></div>
    <h2>Recently captured</h2><div id="recent"><p class="loading">Loading…</p></div>`;
  const loadRecent = async () => {
    try {
      const data = await api("/api/entries?limit=9");
      $("#recent", el).innerHTML = gridHTML(data.items, "Nothing captured yet. Your first idea goes in the box above.");
    } catch (err) {
      if (!err.offline) throw err;
      $("#recent", el).innerHTML = `<p class="empty">NEXUS isn't reachable right now, but you can still capture.
        Ideas are kept on this device and sent automatically when NEXUS is back.</p>`;
    }
  };
  const cap = mountCapture($("#cap", el), {
    onCaptured: (entry) => {
      const just = $("#just", el);
      just.innerHTML = `<h2>Just captured</h2><div class="grid">${cardHTML(entry)}</div>`;
      loadRecent();
      if (isBusy(entry)) {
        const show = (e) => { just.innerHTML = `<h2>Just captured</h2><div class="grid">${cardHTML(e)}</div>`; };
        watchEntry(entry.id, just, (e) => { show(e); loadRecent(); }, show);
      }
    },
  });
  cap.focus();
  await loadRecent();
}

async function viewToday(el) {
  el.innerHTML = `<div class="view-head"><div><h1>Today</h1>
    <p class="lede">${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p></div>
    <a class="btn" href="#/">+ Capture</a></div>
    <h2>Captured today</h2><div id="today"></div>
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
  $("#patterns", el).innerHTML = pats.patterns.length
    ? pats.patterns.map((p) => prov("infer", "AI inference · pattern", `
        <p class="body">${esc(p.observation)}</p>
        <div class="small muted">Evidence (${p.count} entries):</div>
        <ul class="small">${p.evidence.map((ev) => `<li><a href="#/entry/${ev.id}">${esc(ev.title || "Untitled")}</a>
          <span class="muted">· ${esc(label(ev.entry_type))} · ${fmtDate(ev.created_at)}</span></li>`).join("")}</ul>`,
        "computed from your entries")).join("")
    : `<p class="empty">No recurring patterns yet. Patterns appear once three or more entries share a concept — always with the entries that support them.</p>`;
}

const archiveState = JSON.parse(store("nexus.archiveFilters") || "{}");

async function viewArchive(el) {
  const [s, projects, tags, trash] = await Promise.all([getSettings(), api("/api/projects"), api("/api/tags"), api("/api/trash")]);
  const f = archiveState;
  el.innerHTML = `<div class="view-head"><div><h1>Creative Archive</h1>
    <p class="lede">Everything you've captured. Your words are never altered.</p></div>
    <div class="row"><span class="muted small" id="count"></span>${trash.total ? `<a class="btn ghost small" href="#/trash">Trash (${trash.total})</a>` : ""}</div></div>
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
    <div id="results"></div>`;
  const form = $("#filters", el);
  async function load() {
    const fd = new FormData(form);
    const params = new URLSearchParams({ limit: "300" });
    for (const [k, v] of fd) { archiveState[k] = v; if (v) params.set(k, v); }
    store("nexus.archiveFilters", JSON.stringify(archiveState));
    const data = await api("/api/entries?" + params);
    $("#count", el).textContent = `${data.total} entr${data.total === 1 ? "y" : "ies"}`;
    $("#results", el).innerHTML = gridHTML(data.items, "No entries match these filters.");
  }
  form.addEventListener("change", load);
  form.addEventListener("reset", () => setTimeout(load));
  await load();
}

async function viewIdeas(el) {
  el.innerHTML = `<div class="view-head"><div><h1>Idea Incubator</h1>
    <p class="lede">Raw and exploring ideas. Nothing here is a task or a deadline — it's a place to keep ideas alive and let them grow.</p></div>
    <a class="btn" href="#/">+ New idea</a></div><div id="ideas"></div>`;
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
    <h2>Capture into this project</h2><div id="cap"></div>
    <h2>${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}</h2><div id="pentries">${gridHTML(p.entries, "No entries yet. Capture above, or add existing entries from their detail page.")}</div>
    <p style="margin-top:32px"><button class="btn danger ghost" data-act="trash-project">Move project to trash</button></p>`;
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
    <p class="lede">NEXUS is configured through the <code>.env</code> file next to <code>run.py</code>. Restart after changing it. API keys stay in the environment and are never stored in the database.</p></div></div>
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
    </div>`;
  mountBackups($("#backups", el));
  $("[data-act=reindex]", el)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try {
      const r = await api("/api/search/reindex", { method: "POST" });
      toast(`Indexing ${r.queued} entr${r.queued === 1 ? "y" : "ies"} in the background…`);
      setTimeout(() => { if (location.hash === "#/settings") render(); }, 4000);
    } catch (err) { toast(err.message, true); ev.target.disabled = false; }
  });
}

async function viewTrash(el) {
  const [data, projects] = await Promise.all([api("/api/trash"), api("/api/projects?trashed=true")]);
  el.innerHTML = `<p><a href="#/archive" class="muted small">← Creative Archive</a></p>
    <div class="view-head"><div><h1>Trash</h1>
    <p class="lede">Things you moved here are hidden, but nothing is destroyed. Restore brings them back exactly as they were.</p></div></div>
    <h2>Entries</h2>
    ${data.items.length ? `<div class="grid">${data.items.map((e) => `<div class="card trashed">
      <div class="meta"><span class="pill type">${esc(label(e.entry_type))}</span><span class="spacer"></span>
        <span>Trashed ${fmtDate(e.deleted_at)}</span></div>
      <h3>${esc(e.title)}</h3>
      ${e.ai_summary || e.excerpt ? `<p class="summary">${esc(e.excerpt || e.ai_summary)}</p>` : ""}
      <div class="row"><button class="btn small" data-restore="${e.id}">Restore</button></div></div>`).join("")}</div>`
      : `<p class="empty">No entries in the trash.</p>`}
    <h2>Projects</h2>
    ${projects.length ? `<div class="grid">${projects.map((p) => `<div class="card trashed">
      <div class="meta"><span class="pill type">${esc(label(p.project_type))}</span><span class="spacer"></span>
        <span>Trashed ${fmtDate(p.deleted_at)}</span></div>
      <h3>${esc(p.name)}</h3>
      <p class="summary">${p.entry_count} entr${p.entry_count === 1 ? "y" : "ies"}${p.description ? " · " + esc(p.description) : ""}</p>
      <div class="row"><button class="btn small" data-restore-project="${p.id}">Restore</button></div></div>`).join("")}</div>`
      : `<p class="empty">No projects in the trash.</p>`}`;
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

  const userBlock = e.original_text
    ? prov("user", "User material", `<div class="body">${esc(e.original_text)}</div>`, "exactly as you captured it · never modified")
    : prov("unknown", "User material", `<div class="body">No words were written with this ${esc(label(e.source_type))} capture. The original file above is the user material.</div>`);

  const canTranscribe = e.source_type === "voice" && e.attachments.some((a) => a.kind === "audio") && !isBusy(e);
  const transcriptBlock = e.source_type === "voice"
    ? (e.transcript
      ? prov(m.transcript_source === "user" ? "user" : "ai", "Transcript", `<div class="body" id="transcript-text">${esc(e.transcript)}</div>
          <div class="row" style="margin-top:8px"><button class="btn ghost small" data-act="edit-transcript">Correct transcript</button>
          ${canTranscribe && m.transcript_source !== "user" ? `<button class="btn ghost small" data-act="transcribe">Transcribe again</button>` : ""}</div>`,
          m.transcript_source === "user" ? "corrected by you" : `machine-generated from your recording (${transcriptInfo?.provider || "unknown"})`)
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
    ${userBlock}
    ${prov("user", "Your notes", `<div class="body" id="notes-view">${e.user_notes ? esc(e.user_notes) : `<span class="muted">No notes yet.</span>`}</div>
      <div class="row" style="margin-top:8px"><button class="btn ghost small" data-act="edit-notes">${e.user_notes ? "Edit notes" : "Add notes"}</button></div>`,
      "additions you've written later")}
    ${transcriptBlock}
    ${analysisBlocks}
    ${hasImage ? `<div id="artpanel"></div>` : ""}
    ${isBusy(e) ? "" : `<div class="row"><button class="btn" data-act="analyze">Re-run AI organizer</button>
      <span class="small muted">Regenerates AI data only. Your words, title and type choices are kept.</span></div>`}
  </div>

  <aside class="side">
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
        <div><span class="muted small">${esc(label(r.relationship_type))} →</span> <a href="#/entry/${r.other.id}">${esc(r.other.title)}</a></div>
        ${r.rationale ? `<div class="why">${esc(r.rationale)}</div>` : ""}
        ${r.origin === "ai" && r.status !== "accepted" ? `<div class="row">
          <button class="btn small" data-rel="${r.id}" data-status="accepted">Accept</button>
          <button class="btn small ghost" data-rel="${r.id}" data-status="rejected">Reject</button>
          ${r.status !== "ignored" ? `<button class="btn small ghost" data-rel="${r.id}" data-status="ignored">Ignore</button>` : ""}</div>` : ""}
      </div>`).join("") : `<span class="muted small">No related entries found yet.</span>`}</div>

    ${hasImage ? `<div class="panel" id="evopanel"></div>` : ""}

    <div class="panel"><h3>Source information</h3><dl class="kv">
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
  $("#type", el).onchange = (ev) => put({ entry_type: ev.target.value }, "Type set by you.");
  $("#status", el).onchange = (ev) => put({ status: ev.target.value }, "Status updated.");
  $("[data-act=confirm]", el)?.addEventListener("click", () => put({ user_confirmed: true }, "Confirmed."));
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
    try { await api(`/api/relationships/${b.dataset.rel}`, { method: "PATCH", json: { status: b.dataset.status } }); toast(`Relationship ${b.dataset.status}.`); render(); }
    catch (err) { toast(err.message, true); }
  }));
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
      ${artSection("user", "Preserve", listBlock(a.strengths), "already working")}
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
  [/^\/entry\/(\d+)$/, viewEntry, "archive"],
  [/^\/search$/, viewSearch, "search"],
  [/^\/ask$/, viewAsk, "ask"],
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
          You can still <a href="#/">capture ideas</a>: they wait on this device and are sent automatically.</p>`
      : `<p class="empty">Something went wrong: ${esc(err.message)}</p>`;
  }
}

window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });
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
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { flushOutbox(); syncOfflineCopy(); } });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
});
