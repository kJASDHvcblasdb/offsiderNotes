/* Minimal SW for offline pages + queued POST replays */

const CACHE_APP = "rigapp-app-v3";
const CACHE_STATIC = "rigapp-static-v3";
const STATIC_ASSETS = [
  "/static/style.css",
  "/static/client.js",
  "/static/offline.html",
  "/manifest.webmanifest"
];

// Install: cache static only
self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const c2 = await caches.open(CACHE_STATIC);
    await Promise.all(STATIC_ASSETS.map(async (u) => {
      try { await c2.add(u); } catch (_) {}
    }));
    self.skipWaiting();
  })());
});

// Activate: clean old caches
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter(k => ![CACHE_APP, CACHE_STATIC].includes(k))
        .map(k => caches.delete(k))
    );
    self.clients.claim();
  })());
});

function isHTML(request) {
  return request.headers.get("accept")?.includes("text/html");
}
function isStatic(request) {
  const u = new URL(request.url);
  return u.pathname.startsWith("/static/");
}

// Runtime caching
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // Static: offline-first
  if (isStatic(req)) {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      if (cached) return cached;
      try {
        const net = await fetch(req);
        const c = await caches.open(CACHE_STATIC);
        c.put(req, net.clone());
        return net;
      } catch {
        return cached || new Response("", { status: 504 });
      }
    })());
    return;
  }

  // HTML: network-first → cache → offline.html
  if (isHTML(req)) {
    event.respondWith((async () => {
      try {
        const net = await fetch(req);
        const c = await caches.open(CACHE_APP);
        c.put(req, net.clone());
        return net;
      } catch {
        const cached = await caches.match(req, { ignoreSearch: false });
        if (cached) return cached;
        const fallback = await caches.match("/static/offline.html");
        return fallback || new Response("<h1>Offline</h1>", { headers: { "Content-Type": "text/html" }});
      }
    })());
    return;
  }

  // Other GETs: stale-while-revalidate
  if (req.method === "GET") {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      const fetchPromise = fetch(req).then(net => {
        caches.open(CACHE_APP).then(c => c.put(req, net.clone()));
        return net;
      }).catch(() => cached);
      return cached || fetchPromise;
    })());
    return;
  }

  // Mutations: try network, otherwise queue
  if (["POST", "PUT", "PATCH", "DELETE"].includes(req.method)) {
    event.respondWith((async () => {
      try {
        return await fetch(req);
      } catch {
        const body = await safeExtractFormData(req);
        await enqueueRequest({ url: url.pathname + url.search, method: req.method, payload: body });
        notifyPending();
        return new Response(JSON.stringify({ queued: true }), {
          status: 202,
          headers: { "Content-Type": "application/json" }
        });
      }
    })());
  }
});

async function safeExtractFormData(req) {
  try {
    const fd = await req.clone().formData();
    const out = {};
    for (const [k, v] of fd.entries()) out[k] = v;
    return out;
  } catch {
    try { return await req.clone().json(); } catch { return {}; }
  }
}

/* --- IndexedDB queue --- */
const DB_NAME = "rigapp-queue";
const DB_STORE = "requests";

function idb() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB_NAME, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(DB_STORE, { keyPath: "id", autoIncrement: true });
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(open.error);
  });
}
async function enqueueRequest(obj) {
  const db = await idb();
  await new Promise((res, rej) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).add({ ...obj, ts: Date.now() });
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
}
async function dequeueAll() {
  const db = await idb();
  const items = await new Promise((res, rej) => {
    const tx = db.transaction(DB_STORE, "readonly");
    const req = tx.objectStore(DB_STORE).getAll();
    req.onsuccess = () => res(req.result || []);
    req.onerror = () => rej(req.error);
  });
  for (const it of items) {
    try {
      const body = new FormData();
      if (it.payload && typeof it.payload === "object") {
        for (const k in it.payload) body.append(k, it.payload[k]);
      }
      const resp = await fetch(it.url, { method: it.method, body });
      if (resp.ok) await removeById(it.id);
    } catch { /* keep queued */ }
  }
  return items.length;
}
async function removeById(id) {
  const db = await idb();
  await new Promise((res, rej) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).delete(id);
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
}

self.addEventListener("sync", (event) => {
  if (event.tag === "sync-form-queue") {
    event.waitUntil(dequeueAll());
  }
});

function notifyPending() {
  self.clients.matchAll({ type: "window" }).then(list => {
    for (const c of list) c.postMessage({ type: "offline-pending" });
  });
}
