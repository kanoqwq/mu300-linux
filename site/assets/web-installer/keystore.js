// Where this browser keeps its adb key: IndexedDB, in this site's own storage. The device remembers the public half
// once the user has allowed it, so the key has to be the same on the next visit. Clearing the site's data makes a
// new key (and the device asks again). Nothing else is stored by the page.
import { generateKey, validKey } from "./adb/key.js";

const DB = "mu300-web-installer", STORE = "keys", ID = "adb";

function open(idb) {
  return new Promise((resolve, reject) => {
    const r = idb.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const req = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(req.result);
    t.onerror = t.onabort = () => reject(t.error);
  });
}

// loadKey() -> { key, fresh, persistent }: the stored key, or a new one (fresh). persistent is false where the
// browser gives no storage (a private window with storage blocked): the key then lasts for this page only.
export async function loadKey(name = "web-installer", idb = globalThis.indexedDB) {
  let db = null;
  try { db = idb ? await open(idb) : null; } catch (e) { db = null; }
  if (db) {
    try {
      const k = await tx(db, "readonly", (s) => s.get(ID));
      if (validKey(k)) return { key: k, fresh: false, persistent: true };
    } catch (e) { /* unreadable: a new key */ }
  }
  const key = await generateKey(name);
  let persistent = false;
  if (db) {
    try { await tx(db, "readwrite", (s) => s.put(key, ID)); persistent = true; } catch (e) { persistent = false; }
  }
  return { key, fresh: true, persistent };
}

export async function forgetKey(idb = globalThis.indexedDB) {
  try {
    const db = await open(idb);
    await tx(db, "readwrite", (s) => s.delete(ID));
  } catch (e) { /* nothing stored */ }
}
