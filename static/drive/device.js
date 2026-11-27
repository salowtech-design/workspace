/* Salow Drive, this device: keeps a file's bytes in this browser only (IndexedDB) and tells the server only the name, size and
 * SHA-256. Nothing here uploads bytes. Order of work: read the file, hash it, store it, READ IT BACK and hash again, and only then
 * tell Drive. "On this device" after an add means a read back whose hash matched at that moment.
 * Limits stated plainly: browser storage can be cleared by the browser or the person, it is not encrypted by this code, and a power
 * cut during a save can lose it. Nothing here says a file works offline, or that any other device holds a copy.
 * The browser type of a file and its name are claims: bytes are never rendered inline. Open is a forced download of the re-checked
 * local copy as an opaque attachment. */
(function (root) {
  "use strict";
  function sha256(bytes) {
    var c = root.crypto || (typeof require !== "undefined" ? require("crypto").webcrypto : null);
    // crypto.subtle is missing on pages not served over a secure connection; the plain-code SHA-256 from the Slides picture checker
    // (tested against node's digest) is used then. It is slow on large files.
    if ((!c || !c.subtle) && root.SalowSlidesImages && root.SalowSlidesImages.sha256) return root.SalowSlidesImages.sha256(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes));
    if (!c || !c.subtle) return Promise.reject(new Error("This browser cannot check files."));
    return c.subtle.digest("SHA-256", bytes).then(function (d) {
      return Array.prototype.map.call(new Uint8Array(d), function (x) { return (x < 16 ? "0" : "") + x.toString(16); }).join("");
    });
  }
  // One database per name; the page names it after the signed-in person, so one person's local files are never listed or opened under another's.
  function Store(name, idb) { this.name = name; this.idb = idb || (root.indexedDB || null); this.db = null; this.opening = null; }
  Store.prototype.open = function () {
    var s = this; if (s.db) return Promise.resolve(s.db);
    if (s.opening) return s.opening;
    if (!s.idb) return Promise.reject(new Error("This browser does not allow files to be kept on this device."));
    s.opening = new Promise(function (res, rej) {
      var r, msg = "This browser would not open its file storage.";
      try { r = s.idb.open(s.name, 1); } catch (e) { rej(new Error(msg)); return; }
      r.onupgradeneeded = function () { if (!r.result.objectStoreNames || !r.result.objectStoreNames.contains("blob")) r.result.createObjectStore("blob", { keyPath: "sha256" }); };
      r.onsuccess = function () {
        var db = r.result; s.db = db;
        // another tab upgrading or the browser closing the connection: drop the cached handle so the next call opens a fresh one
        db.onversionchange = function () { try { db.close(); } catch (e) { /* already closed */ } if (s.db === db) s.db = null; };
        db.onclose = function () { if (s.db === db) s.db = null; };
        res(db);
      };
      r.onerror = r.onblocked = function () { rej(new Error(msg)); };
    }).then(function (db) { s.opening = null; return db; }, function (e) { s.opening = null; throw e; });
    return s.opening;
  };
  function tx(s, mode, fn) {
    return s.open().then(function (db) {
      return new Promise(function (res, rej) {
        var t, out;
        try { t = db.transaction("blob", mode); out = fn(t.objectStore("blob")); } catch (e) { s.db = null; rej(new Error("This browser would not use its file storage.")); return; }
        t.oncomplete = function () { res(out && out.result !== undefined ? out.result : undefined); };
        t.onabort = t.onerror = function () { rej(new Error(t.error && t.error.name === "QuotaExceededError" ? "This device is out of room. Nothing was saved." : "This browser could not save the file. Nothing was saved.")); };
      });
    });
  }
  Store.prototype.get = function (hash) { return tx(this, "readonly", function (o) { return o.get(hash); }); };
  // Cheap: is a record filed under this hash? No bytes are read and nothing is hashed. It does not say the bytes are intact.
  Store.prototype.has = function (hash) { return tx(this, "readonly", function (o) { return o.count(hash); }).then(function (n) { return n > 0; }); };
  // Full check: "on-device" only when the stored bytes hash to the name they are filed under; "damaged" when they do not.
  Store.prototype.state = function (hash) {
    return this.get(hash).then(function (rec) {
      if (!rec) return { state: "not-on-device" };
      return sha256(rec.bytes).then(function (h) { return h === hash && rec.size === rec.bytes.byteLength ? { state: "on-device", size: rec.size } : { state: "damaged" }; });
    });
  };
  // Content addressed. A healthy copy is left alone. An absent or DAMAGED copy is written (put, so two adds of the same file at once
  // cannot collide and a damaged record is repaired), because these bytes were hashed here and are exactly what the name says.
  // Returns "kept", "added" or "repaired". Always read back with state() afterwards.
  Store.prototype.save = function (bytes) {
    var s = this, hash, copy;
    return sha256(bytes).then(function (h) {
      hash = h; return s.state(hash);
    }).then(function (st) {
      if (st.state === "on-device") return "kept";
      copy = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length);
      return tx(s, "readwrite", function (o) { return o.put({ sha256: hash, size: bytes.length, bytes: copy }); }).then(function () { return st.state === "damaged" ? "repaired" : "added"; });
    }).then(function (how) { return { sha256: hash, how: how }; });
  };
  // Signed out (redirect or 401) is told apart from a reply Drive did not mean to send (a 403 or 500 page, or anything that is not JSON).
  function readJson(resp) {
    var type = resp.headers && resp.headers.get ? (resp.headers.get("content-type") || "") : "";
    if (resp.redirected || resp.status === 401 || resp.status === 302) return Promise.resolve({ signin: true });
    if (type.indexOf("application/json") < 0) return Promise.resolve({ unexpected: true, status: resp.status });
    return resp.json().then(function (j) { return { status: resp.status, body: j }; }, function () { return { unexpected: true, status: resp.status }; });
  }
  function post(deps, url, fields) {
    var f = new URLSearchParams(); Object.keys(fields).forEach(function (k) { f.append(k, fields[k]); });
    return deps.fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": deps.csrf, "Content-Type": "application/x-www-form-urlencoded" }, body: f.toString() }).then(readJson, function () { return { network: true }; });
  }
  var SIGNIN = "Your session needs signing in again. ", NET = "Drive could not be reached. ";
  function unexpected(r) { return "Drive gave an unexpected reply (" + r.status + "). "; }
  // Shared tail: tell Drive about a new version of a file whose id and current version we KNOW. The bytes are already stored and read back.
  function recordVersion(deps, fileId, baseVersion, hash, size, local, fresh) {
    var no = local + (fresh ? "Drive has the name but no saved version yet. " : "Drive has not saved this version. ");
    return post(deps, deps.urls.version(fileId), { base_version: baseVersion, size: size, sha256: hash }).then(function (v) {
      var out = { ok: false, fileId: fileId, baseVersion: baseVersion, sha256: hash, size: size, onDevice: true, resumable: true };
      if (v.signin) return Object.assign(out, { stage: "signin", message: no + SIGNIN });
      if (v.network) return Object.assign(out, { stage: "network", message: no + NET + "Whether it was saved is not known; check the list before retrying." });
      if (v.unexpected) return Object.assign(out, { stage: "unexpected", message: no + unexpected(v) });
      if (v.status !== 200 || !v.body.ok) return Object.assign(out, { stage: "version", resumable: v.status !== 409, message: no + (v.body && v.body.error ? v.body.error : "") });
      return { ok: true, stage: "done", fileId: fileId, version: v.body.file.version, size: v.body.file.size, sha256: hash, onDevice: true, message: "Saved to Drive. The file is on this device." };
    });
  }
  function store(deps, file) {
    var bytes;
    return file.arrayBuffer().then(function (ab) { bytes = new Uint8Array(ab); return deps.store.save(bytes); }).then(function (sv) {
      return deps.store.state(sv.sha256).then(function (st) { return { bytes: bytes, hash: sv.sha256, st: st }; });
    });
  }
  function fail(stage, e) { return { ok: false, stage: stage, message: (e && e.message) || "The file could not be read." }; }
  // New file: store, read back, create the Drive record, record version 1. Never throws.
  function addFile(deps, file, folderId) {
    var made;
    return store(deps, file).then(function (s) {
      if (s.st.state !== "on-device") return { ok: false, stage: "readback", message: "The saved copy did not read back correctly. Nothing was added to Drive." };
      var local = "The file is on this device. ", claim = file.type || "";
      var mk = function (ct) { var d = { name: file.name, content_type: ct }; if (folderId) d.folder = folderId; return post(deps, deps.urls.fileNew, d); };
      return mk(claim).then(function (r) { return r.body && r.status === 400 && claim && /type/i.test(r.body.error || "") ? mk("") : r; }).then(function (r) {
        var base = { onDevice: true, sha256: s.hash, size: s.bytes.length };
        if (r.signin) return Object.assign(base, { ok: false, stage: "signin", message: local + SIGNIN + "It is not in Drive yet." });
        // The request may have been processed before the connection failed: do not guess, and do not retry blindly (a retry could meet its own name).
        if (r.network) return Object.assign(base, { ok: false, stage: "ambiguous", message: local + NET + "Drive may or may not have created the name. Reload the list and look before trying again." });
        if (r.unexpected) return Object.assign(base, { ok: false, stage: "unexpected", message: local + unexpected(r) + "It is not in Drive." });
        if (r.status !== 201 || !r.body.ok) return Object.assign(base, { ok: false, stage: "record", message: local + (r.body && r.body.error ? r.body.error + " " : "") + "It is not in Drive." });
        made = r.body.file.id;
        return recordVersion(deps, made, 0, s.hash, s.bytes.length, local, true);
      });
    }).catch(function (e) { return fail("local", e); });
  }
  // Existing file the person owns or may edit: store, read back, record a new version from the version we last saw. Also the way to
  // finish a record that has no version yet (baseVersion 0). Never creates a file record, so it cannot meet a duplicate name.
  function addVersion(deps, fileId, baseVersion, file) {
    return store(deps, file).then(function (s) {
      if (s.st.state !== "on-device") return { ok: false, stage: "readback", message: "The saved copy did not read back correctly. Nothing was added to Drive." };
      return recordVersion(deps, fileId, baseVersion, s.hash, s.bytes.length, "The file is on this device. ", false);
    }).catch(function (e) { return fail("local", e); });
  }
  // A download name only (the stored name is never changed): no path, no control, bidi or invisible format characters, bounded.
  function safeName(n) {
    n = String(n || "").replace(/[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb\\\/:*?"<>|]+/g, "_").replace(/^\.+/, "").slice(0, 120);
    return n || "file";
  }
  // Open = save the verified local copy as an attachment. The bytes are re-checked first; the type given to the browser is always
  // application/octet-stream and the result is never shown inline, whatever the file claims to be. The blob URL is released afterwards.
  function openLocal(deps, name, hash) {
    return deps.store.state(hash).then(function (st) {
      if (st.state !== "on-device") return { ok: false, state: st.state, message: st.state === "damaged" ? "The copy on this device is damaged and was not opened." : "This file is not on this device." };
      return deps.store.get(hash).then(function (rec) {
        var blob = new deps.Blob([rec.bytes], { type: "application/octet-stream" }), url = deps.URL.createObjectURL(blob);
        try { deps.download(url, safeName(name)); } finally { (deps.later || setTimeout)(function () { deps.URL.revokeObjectURL(url); }, 60000); }
        return { ok: true, state: "on-device" };
      });
    }, function (e) { return { ok: false, message: e.message }; });
  }
  var api = { Store: Store, sha256: sha256, addFile: addFile, addVersion: addVersion, openLocal: openLocal, safeName: safeName };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowDriveDevice = api;
})(typeof self !== "undefined" ? self : this);
