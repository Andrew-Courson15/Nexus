/* NEXUS service worker: the app opens (and can capture) even when NEXUS can't be reached.
 *
 * When the PC is off, requests over Tailscale don't fail - they hang. So nothing here waits
 * on the network for long:
 * - Pages: network with a 3 s time limit, then the saved copy.
 * - Static files: saved copy first (refreshed in the background), network if not saved yet.
 * - Thumbnails (/api/uploads/N/thumb): the copy saved on this phone first. The app fills this
 *   cache while NEXUS is reachable, so pictures still show when the PC is off.
 * - Full pictures (/api/uploads/N/preview): network, and when that fails the saved thumbnail.
 * - Other /api/ requests are never cached here; the app keeps its own offline copy. */
const CACHE = "nexus-__VERSION__";
const SHELL = ["/", "/static/css/style.css?v=__VERSION__", "/static/js/app.js?v=__VERSION__",
  "/static/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png"];
const PAGE_TIMEOUT_MS = 3000;
const FILE_TIMEOUT_MS = 6000;
const THUMBS = "media-thumbs";  // not versioned: survives app updates (the app trims it)

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("nexus-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
  });
}

// Left for the app when the page had to come from the saved copy, so it goes straight to its
// offline copy instead of waiting on the network a second time.
const OFFLINE_MARK = "/__nexus-page-offline";

async function page(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await withTimeout(fetch(req), PAGE_TIMEOUT_MS);
    // PC on but NEXUS off: Tailscale answers with an empty 502. That counts as unreachable too.
    if (res.status >= 500) throw new Error(`NEXUS answered ${res.status}`);
    if (res.ok) cache.put("/", res.clone());
    cache.delete(OFFLINE_MARK);
    return res;
  } catch {
    await cache.put(OFFLINE_MARK, new Response(String(Date.now())));
    const saved = (await cache.match("/")) || (await caches.match("/"));
    return saved || new Response(
      "<!doctype html><meta name=viewport content='width=device-width'><body style='background:#0f1114;color:#ebe7df;" +
      "font:16px system-ui;padding:40px 20px'><h2>NEXUS isn't reachable</h2><p>Open the app once while your PC is on " +
      "so it can work offline next time.</p>", { headers: { "Content-Type": "text/html" } });
  }
}

async function file(req) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(req);
  const refresh = fetch(req).then((res) => { if (res.ok) cache.put(req, res.clone()); return res; });
  if (saved) {
    refresh.catch(() => {});
    return saved;
  }
  try {
    const res = await withTimeout(refresh, FILE_TIMEOUT_MS);
    if (res.status >= 500) throw new Error("unreachable");
    return res;
  } catch {
    return (await caches.match(req, { ignoreSearch: true })) || Response.error();
  }
}

async function thumb(req) {
  const cache = await caches.open(THUMBS);
  const saved = await cache.match(req);
  if (saved) return saved;
  try {
    const res = await withTimeout(fetch(req), FILE_TIMEOUT_MS);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch {
    return Response.error();
  }
}

async function preview(req, id) {
  try {
    const res = await withTimeout(fetch(req), FILE_TIMEOUT_MS * 2);
    if (res.status >= 500) throw new Error("unreachable");
    return res;
  } catch {
    const cache = await caches.open(THUMBS);
    for (const key of await cache.keys()) {
      if (new URL(key.url).pathname === `/api/uploads/${id}/thumb`) return cache.match(key);
    }
    return Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  const media = url.pathname.match(/^\/api\/uploads\/(\d+)\/(thumb|preview)$/);
  if (media) {
    event.respondWith(media[2] === "thumb" ? thumb(req) : preview(req, media[1]));
    return;
  }
  if (url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") event.respondWith(page(req));
  else if (url.pathname.startsWith("/static/")) event.respondWith(file(req));
});
