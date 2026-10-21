/* Offline shell: stale-while-revalidate for the app, network-first for the data JSON.
   SWR paints instantly from cache AND refetches in the background, so a deploy reaches every
   browser on its NEXT visit — no manual version bump, no hard refresh. Data is always network-first
   (freshest when online; cached copy only as the offline fallback). */

const SHELL = "macro-shell-v26";  // SWR self-refreshes the shell now; bump only to force a one-time
// re-cache or when changing this SW's own logic (v26: shell cache-first -> stale-while-revalidate).
const ASSETS = ["./", "index.html", "styles.css", "app.js", "manifest.webmanifest",
  "icon.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
  "favicon-32x32.png", "favicon-16x16.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);

  if (url.pathname.endsWith(".json")) {
    // network-first: freshest data wins; fall back to the last cached copy offline.
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // shell: stale-while-revalidate — instant paint from cache, refresh in the background so the
  // NEXT visit runs the latest deploy. {cache:"no-cache"} revalidates past any stale HTTP cache.
  e.respondWith(
    caches.match(e.request).then((cached) => {
      const fresh = fetch(e.request, { cache: "no-cache" })
        .then((res) => {
          if (res && res.ok) caches.open(SHELL).then((c) => c.put(e.request, res.clone()));
          return res;
        })
        .catch(() => cached);
      return cached || fresh;
    })
  );
});
