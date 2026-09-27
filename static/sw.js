/* NEXUS service worker: the app opens (and can capture) even when NEXUS can't be reached.
 *
 * When the PC is off, requests over Tailscale don't fail - they hang. So nothing here waits
 * on the network for long:
 * - Pages: network with a 3 s time limit, then the saved copy.
 * - Static files: saved copy first (refreshed in the background), network if not saved yet.
 * - /api/ requests are never cached; the app queues captures itself when offline. */
const CACHE = "nexus-__VERSION__";
const SHELL = ["/", "/static/css/style.css?v=__VERSION__", "/static/js/app.js?v=__VERSION__",
  "/static/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png"];
const PAGE_TIMEOUT_MS = 3000;
const FILE_TIMEOUT_MS = 6000;

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

async function page(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await withTimeout(fetch(req), PAGE_TIMEOUT_MS);
    if (res.ok) cache.put("/", res.clone());
    return res;
  } catch {
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
    return await withTimeout(refresh, FILE_TIMEOUT_MS);
  } catch {
    return (await caches.match(req, { ignoreSearch: true })) || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (req.mode === "navigate") event.respondWith(page(req));
  else if (url.pathname.startsWith("/static/")) event.respondWith(file(req));
});
