/* NEXUS service worker: lets the app open (and capture) when NEXUS can't be reached.
 * - Pages and static files: network first, cached copy as fallback.
 * - /api/ requests are never cached; the app queues captures itself when offline. */
const CACHE = "nexus-__VERSION__";
const SHELL = ["/", "/static/css/style.css?v=__VERSION__", "/static/js/app.js?v=__VERSION__",
  "/static/manifest.webmanifest", "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png"];

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

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok && (req.mode === "navigate" || url.pathname.startsWith("/static/"))) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req.mode === "navigate" ? "/" : req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req.mode === "navigate" ? "/" : req, { ignoreSearch: req.mode === "navigate" })
        .then((hit) => hit || caches.match(req, { ignoreSearch: true })))
  );
});
