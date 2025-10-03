/* global self, caches, indexedDB */

const CACHE_VERSION = 'v11';                // bump on change
const RUNTIME_CACHE = `runtime-${CACHE_VERSION}`;
const STATIC_CACHE  = `static-${CACHE_VERSION}`;
const OFFLINE_URLS = [
  '/', '/offline', '/stock', '/restock', '/jobs', '/equipment', '/map',
  '/bits', '/usage', '/handover', '/travel', '/refuel',
  '/static/style.css'
];

const IDB_NAME = 'rigapp-queue';
const IDB_STORE = 'requests';

// -------- IndexedDB helpers ---------------------------------------------------
function idbOpen(){
  return new Promise((resolve, reject)=>{
    const req = indexedDB.open(IDB_NAME, 2);
    req.onupgradeneeded = (e)=>{
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        const store = db.createObjectStore(IDB_STORE, { keyPath: 'id', autoIncrement: true });
        store.createIndex('hash', 'hash', { unique: false });
        store.createIndex('nextAt', 'nextAt', { unique: false });
      } else {
        const store = e.target.transaction.objectStore(IDB_STORE);
        if (!store.indexNames.contains('hash')) store.createIndex('hash', 'hash', { unique: false });
        if (!store.indexNames.contains('nextAt')) store.createIndex('nextAt', 'nextAt', { unique: false });
      }
    };
    req.onerror = ()=>reject(req.error);
    req.onsuccess = ()=>resolve(req.result);
  });
}

async function idbAdd(obj){
  const db = await idbOpen();
  return new Promise((resolve, reject)=>{
    const tx = db.transaction(IDB_STORE, 'readwrite');
    tx.oncomplete = ()=>resolve();
    tx.onerror = ()=>reject(tx.error);
    tx.objectStore(IDB_STORE).add(obj);
  });
}
async function idbListAll(){
  const db = await idbOpen();
  return new Promise((resolve, reject)=>{
    const tx = db.transaction(IDB_STORE, 'readonly');
    const store = tx.objectStore(IDB_STORE);
    const out = [];
    store.openCursor().onsuccess = (e)=>{
      const cur = e.target.result;
      if (cur){ out.push(cur.value); cur.continue(); }
      else resolve(out);
    };
    tx.onerror = ()=>reject(tx.error);
  });
}
async function idbPut(obj){
  const db = await idbOpen();
  return new Promise((resolve, reject)=>{
    const tx = db.transaction(IDB_STORE, 'readwrite');
    tx.oncomplete = ()=>resolve();
    tx.onerror = ()=>reject(tx.error);
    tx.objectStore(IDB_STORE).put(obj);
  });
}
async function idbDelete(id){
  const db = await idbOpen();
  return new Promise((resolve, reject)=>{
    const tx = db.transaction(IDB_STORE, 'readwrite');
    tx.oncomplete = ()=>resolve();
    tx.onerror = ()=>reject(tx.error);
    tx.objectStore(IDB_STORE).delete(id);
  });
}

// simple stable hash
async function hashRequest(method, url, body){
  const enc = new TextEncoder();
  const data = enc.encode([method, url, body || ''].join('¦'));
  const buf = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,'0')).join('');
}

// -------- install/activate ----------------------------------------------------
self.addEventListener('install', (event)=>{
  event.waitUntil((async ()=>{
    const cache = await caches.open(STATIC_CACHE);
    await cache.addAll(OFFLINE_URLS);
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event)=>{
  event.waitUntil((async ()=>{
    const keys = await caches.keys();
    await Promise.all(keys.filter(k=>!k.endsWith(CACHE_VERSION)).map(k=>caches.delete(k)));
    await self.clients.claim();
  })());
});

// -------- fetch handler -------------------------------------------------------
self.addEventListener('fetch', (event)=>{
  const req = event.request;

  // root-level assets served by app (manifest/sw) may be cached too
  if (req.method === 'GET') {
    event.respondWith(networkFirst(req));
    return;
  }

  // Non-GET (form posts etc.) → try online, else queue and 303 redirect
  event.respondWith((async ()=>{
    try {
      const res = await fetch(req.clone());
      return res;
    } catch (err) {
      // offline path — serialize
      const body = await req.clone().arrayBuffer().then(b=>new TextDecoder().decode(b)).catch(()=>null);
      const headers = {};
      req.headers.forEach((v,k)=>{ headers[k] = v; });
      const serialized = {
        method: req.method,
        url: req.url,
        headers,
        body,
        retries: 0,
        addedAt: Date.now(),
        nextAt: Date.now(),
      };
      serialized.hash = await hashRequest(serialized.method, serialized.url, serialized.body);

      // dedupe identical pending
      const all = await idbListAll();
      if (!all.some(x=>x.hash === serialized.hash)) {
        await idbAdd(serialized);
        postAllClients({type:'queue:enqueued'});
        try { await self.registration.sync.register('sync:offline-queue'); } catch (_) {}
      }

      // return 303 redirect to keep UX consistent
      const loc = headers['referer'] || '/';
      return new Response('<!doctype html><title>Queued</title>', {
        status: 303,
        headers: { 'Content-Type':'text/html; charset=utf-8', 'Location': loc }
      });
    }
  })());
});

// GET strategy
async function networkFirst(request){
  try {
    const res = await fetch(request);
    const cache = await caches.open(RUNTIME_CACHE);
    // only cache 200s and same-origin HTML/CSS/JS
    if (res.ok && new URL(request.url).origin === self.location.origin) {
      cache.put(request, res.clone());
    }
    return res;
  } catch (err) {
    const cache = await caches.open(RUNTIME_CACHE);
    const hit = await cache.match(request);
    if (hit) return hit;
    // final fallback: our offline page if available
    const staticCache = await caches.open(STATIC_CACHE);
    const off = await staticCache.match('/offline');
    return off || new Response('Offline', { status: 503 });
  }
}

// -------- background sync -----------------------------------------------------
self.addEventListener('sync', (event)=>{
  if (event.tag === 'sync:offline-queue') {
    event.waitUntil(processQueue());
  }
});

async function processQueue(){
  postAllClients({type:'queue:sync-start'});
  let items = await idbListAll();
  items.sort((a,b)=> (a.nextAt||0) - (b.nextAt||0));
  const now = Date.now();
  for (const item of items) {
    if ((item.nextAt || 0) > now) continue; // backoff delay
    const {method, url, body, headers} = item;
    try {
      const res = await fetch(url, {
        method,
        headers,
        body: body != null ? body : undefined,
      });

      if (res.status === 409 || res.status === 412) {
        // conflict — drop and notify
        await idbDelete(item.id);
        postAllClients({type:'queue:conflict'});
        continue;
      }

      if (!res.ok && res.status >= 500) throw new Error('server');

      // success or client error → drop the item
      await idbDelete(item.id);
    } catch (e) {
      // backoff: 1s, 5s, 30s, 2m, 10m (cap)
      const backoffs = [1000, 5000, 30000, 120000, 600000];
      const r = (item.retries || 0);
      const wait = backoffs[Math.min(r, backoffs.length-1)];
      item.retries = r + 1;
      item.nextAt = Date.now() + wait;
      await idbPut(item);
    }
  }
  postAllClients({type:'queue:sync-complete'});
}

async function postAllClients(msg){
  const clis = await self.clients.matchAll({ includeUncontrolled: true, type: 'window' });
  for (const c of clis) c.postMessage(msg);
}
