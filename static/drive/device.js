/* Salow Drive, this device: keeps a file's bytes in this browser only (IndexedDB) and tells the server only the name, size and
 * SHA-256. Nothing here uploads bytes. Order of work: read the file, hash it, store it, READ IT BACK and hash again, and only then
 * tell Drive. "On this device" is said only after a read back whose hash matches; saving metadata alone never says it.
 * The browser type of a file and its name are claims: bytes are never rendered inline. Open means a forced download of the local
 * copy as an opaque attachment. Nothing here knows whether any other device holds a copy. */
(function (root) {
  "use strict";
  function sha256(bytes) {
    var c = root.crypto || (typeof require !== "undefined" ? require("crypto").webcrypto : null);
    if (!c || !c.subtle) return Promise.reject(new Error("This browser cannot check files."));
    return c.subtle.digest("SHA-256", bytes).then(function (d) {
      return Array.prototype.map.call(new Uint8Array(d), function (x) { return (x < 16 ? "0" : "") + x.toString(16); }).join("");
    });
  }
  function Store(name, idb) { this.name = name; this.idb = idb || (root.indexedDB || null); this.db = null; }
  Store.prototype.open = function () {
    var s = this; if (s.db) return Promise.resolve(s.db);
    if (!s.idb) return Promise.reject(new Error("This browser does not allow files to be kept on this device."));
    return new Promise(function (res, rej) {
      var r, msg = "This browser would not open its file storage.";
      try { r = s.idb.open(s.name, 1); } catch (e) { rej(new Error(msg)); return; }
      r.onupgradeneeded = function () { r.result.createObjectStore("blob", { keyPath: "sha256" }); };
      r.onsuccess = function () { s.db = r.result; res(s.db); };
      r.onerror = r.onblocked = function () { rej(new Error(msg)); };
    });
  };
  function tx(s, mode, fn) {
    return s.open().then(function (db) {
      return new Promise(function (res, rej) {
        var t = db.transaction("blob", mode), out;
        try { out = fn(t.objectStore("blob")); } catch (e) { rej(new Error("This browser would not use its file storage.")); return; }
        t.oncomplete = function () { res(out && out.result !== undefined ? out.result : undefined); };
        t.onabort = t.onerror = function () { rej(new Error(t.error && t.error.name === "QuotaExceededError" ? "This device is out of room. Nothing was saved." : "This browser could not save the file. Nothing was saved.")); };
      });
    });
  }
  Store.prototype.get = function (hash) { return tx(this, "readonly", function (o) { return o.get(hash); }); };
  // Content addressed: a record is only written when absent, so different bytes can never replace a stored copy.
  Store.prototype.put = function (hash, bytes) {
    var s = this; return s.get(hash).then(function (have) {
      if (have) return false;
      var buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length);
      return tx(s, "readwrite", function (o) { return o.add({ sha256: hash, size: bytes.length, bytes: buf }); }).then(function () { return true; });
    });
  };
  // "on-device" only when the stored bytes hash to the name they are filed under; "damaged" when they do not; else "not-on-device".
  Store.prototype.state = function (hash) {
    return this.get(hash).then(function (rec) {
      if (!rec) return { state: "not-on-device" };
      return sha256(rec.bytes).then(function (h) { return h === hash && rec.size === rec.bytes.byteLength ? { state: "on-device", size: rec.size } : { state: "damaged" }; });
    });
  };
  function readJson(resp) {
    var type = resp.headers && resp.headers.get ? (resp.headers.get("content-type") || "") : "";
    if (resp.redirected || resp.status === 401 || resp.status === 302 || type.indexOf("application/json") < 0) return Promise.resolve({ signin: true });
    return resp.json().then(function (j) { return { status: resp.status, body: j }; }, function () { return { signin: true }; });
  }
  function post(deps, url, fields) {
    var f = new URLSearchParams(); Object.keys(fields).forEach(function (k) { f.append(k, fields[k]); });
    return deps.fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": deps.csrf, "Content-Type": "application/x-www-form-urlencoded" }, body: f.toString() }).then(readJson, function () { return { network: true }; });
  }
  var SIGNIN = "Your session needs signing in again. ", NET = "Drive could not be reached. ";
  // file: { name, type, arrayBuffer() }. Returns { ok, stage, message, ... }; never throws.
  function addFile(deps, file, folderId) {
    var bytes, hash, local = "";
    return file.arrayBuffer().then(function (ab) {
      bytes = new Uint8Array(ab); return sha256(bytes);
    }).then(function (h) {
      hash = h; return deps.store.put(hash, bytes);
    }).then(function () { return deps.store.state(hash); }).then(function (st) {
      if (st.state !== "on-device") return { ok: false, stage: "readback", message: "The saved copy did not read back correctly. Nothing was added to Drive." };
      local = "The file is on this device. ";
      var claim = file.type || "";
      var mk = function (ct) { var d = { name: file.name, content_type: ct }; if (folderId) d.folder = folderId; return post(deps, deps.urls.fileNew, d); };
      return mk(claim).then(function (r) { return r.body && r.status === 400 && claim ? mk("") : r; }).then(function (r) {
        if (r.signin) return { ok: false, stage: "signin", onDevice: true, message: local + SIGNIN + "It is not in Drive yet." };
        if (r.network) return { ok: false, stage: "network", onDevice: true, message: local + NET + "It is not in Drive yet." };
        if (r.status !== 201 || !r.body.ok) return { ok: false, stage: "record", onDevice: true, message: local + (r.body && r.body.error ? r.body.error + " " : "") + "It is not in Drive." };
        var fid = r.body.file.id;
        return post(deps, deps.urls.version(fid), { base_version: 0, size: bytes.length, sha256: hash }).then(function (v) {
          var no = local + "Drive has the name but no saved version yet. ";
          if (v.signin) return { ok: false, stage: "signin", fileId: fid, onDevice: true, message: no + SIGNIN };
          if (v.network) return { ok: false, stage: "network", fileId: fid, onDevice: true, message: no + NET };
          if (v.status !== 200 || !v.body.ok) return { ok: false, stage: "version", fileId: fid, onDevice: true, message: no + (v.body && v.body.error ? v.body.error : "") };
          return { ok: true, stage: "done", fileId: fid, version: v.body.file.version, size: v.body.file.size, sha256: hash, onDevice: true, message: "Saved to Drive. The file is on this device." };
        });
      });
    }).catch(function (e) {
      return { ok: false, stage: hash ? "local" : "read", message: (e && e.message) || "The file could not be read." };
    });
  }
  // A download name only (the stored name is never changed): no path, no control, bidi or invisible format characters, bounded. Never used as a type or a location.
  function safeName(n) {
    n = String(n || "").replace(/[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb\\\/:*?"<>|]+/g, "_").replace(/^\.+/, "").slice(0, 120);
    return n || "file";
  }
  // Open = save the verified local copy as an attachment. The bytes are re-checked first; the type given to the browser is always
  // application/octet-stream and the result is never shown inline, whatever the file claims to be.
  function openLocal(deps, name, hash) {
    return deps.store.state(hash).then(function (st) {
      if (st.state !== "on-device") return { ok: false, state: st.state, message: st.state === "damaged" ? "The copy on this device is damaged and was not opened." : "This file is not on this device." };
      return deps.store.get(hash).then(function (rec) {
        var blob = new deps.Blob([rec.bytes], { type: "application/octet-stream" });
        deps.download(deps.URL.createObjectURL(blob), safeName(name));
        return { ok: true, state: "on-device" };
      });
    }, function (e) { return { ok: false, message: e.message }; });
  }
  var api = { Store: Store, sha256: sha256, addFile: addFile, openLocal: openLocal, safeName: safeName };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowDriveDevice = api;
})(typeof self !== "undefined" ? self : this);
