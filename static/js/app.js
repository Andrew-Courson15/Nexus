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

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
    delete init.json;
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw unreachable();
  }
  // 502-504: the private link is up but NEXUS on the PC isn't answering.
  if ([502, 503, 504].includes(res.status)) throw unreachable();
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new Error(msg);
  }
  return data;
}

function unreachable() {
  const err = new Error("Can't reach NEXUS right now.");
  err.offline = true;
  return err;
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
function sendCapture(item) {
  if (item.file) {
    const fd = new FormData();
    fd.append("file", item.file, item.filename || item.file.name || "capture");
    if (item.note) fd.append("note", item.note);
    if (item.project_id) fd.append("project_id", item.project_id);
    fd.append("client_id", item.client_id);
    fd.append("captured_at", item.captured_at);
    return api("/api/uploads", { method: "POST", body: fd });
  }
  return api("/api/entries", {
    method: "POST",
    json: { original_text: item.text, project_ids: item.project_id ? [item.project_id] : [],
            client_id: item.client_id, captured_at: item.captured_at },
  });
}

/** Save now if NEXUS is reachable, otherwise keep it on this device. Returns {entry} or {queued: true}. */
async function captureOrQueue(fields) {
  const item = { client_id: newClientId(), captured_at: new Date().toISOString(), ...fields };
  try {
    return { entry: await sendCapture(item) };
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
  const img = e.attachments.find((a) => a.kind === "image");
  const audio = e.attachments.find((a) => a.kind === "audio");
  return `<a class="card" href="#/entry/${e.id}">
    ${img ? `<img class="thumb" src="${esc(img.url)}" alt="" loading="lazy">` : ""}
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
        <input type="file" hidden accept="image/*,audio/*,.pdf,.txt,.md,.doc,.docx">
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
      ${file.type.startsWith("image/") ? `<img src="${previewURL}" alt="" style="max-height:220px;border-radius:8px">` : ""}
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

  function takeFile(file) {
    if (!file) return;
    const kind = file.type.startsWith("image/") ? "Image" : file.type.startsWith("audio/") ? "Audio" : "File";
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
  fileInput.onchange = () => { takeFile(fileInput.files[0]); fileInput.value = ""; };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); capture(); } });
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag"); });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", (e) => {
    box.classList.remove("drag");
    if (e.dataTransfer.files.length) { e.preventDefault(); takeFile(e.dataTransfer.files[0]); }
  });
  return { focus: () => ta.focus() };
}

/* ------------------------------------------------------------------ views */
async function viewHome(el) {
  el.innerHTML = `<div id="cap"></div>
    <div id="just"></div>
    <h2>Recently captured</h2><div id="recent"></div>`;
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
  el.innerHTML = `<div class="view-head"><div><h1>Search</h1><p class="lede">Search naturally across your words, transcripts and the AI's notes.</p></div></div>
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
    </div>`;
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
  const srcBadge = (src) => `<span class="source-badge ${src}">${src === "user" ? "yours" : "AI"}</span>`;
  const media = e.attachments.map((a) =>
    a.kind === "image" ? `<div class="media-preview"><img src="${esc(a.url)}" alt="${esc(a.original_filename)}"></div>`
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
  [/^\/settings$/, viewSettings, "settings"],
  [/^\/trash$/, viewTrash, "archive"],
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
window.addEventListener("DOMContentLoaded", () => {
  render();
  $("#outbox").onclick = () => flushOutbox({ quiet: false });
  flushOutbox();
  // iPhones have no background sync: retry while the app is open, and whenever it comes back.
  setInterval(() => { if (!$("#outbox").hidden) flushOutbox(); }, 20000);
  window.addEventListener("online", () => flushOutbox());
  document.addEventListener("visibilitychange", () => { if (!document.hidden) flushOutbox(); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
});
