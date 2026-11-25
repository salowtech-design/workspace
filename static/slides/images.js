/* Salow Slides pictures: checks a picture file and keeps its bytes in this browser only (IndexedDB). Nothing is uploaded.
 * A deck holds only the SHA-256 of a picture, so the same bytes are stored once and two decks (or Undo history) that name the
 * same picture can never break each other. Bytes are never deleted by this code: there is no cleanup, so Undo, Redo and Clear deck
 * cannot lose a picture that something still refers to. The limits below are technical bounds from browser storage and memory,
 * not a policy about what people may keep.
 * Checks before a picture is accepted: size, the file signature, a well formed header read WITHOUT decoding (so a huge canvas
 * declared in a few bytes is refused before any pixel memory is used), the pixel count, the end marker, and finally a real decode
 * by the browser whose size must match the header. */
(function (root) {
  "use strict";
  var LIMITS = { MAX_BYTES: 2000000, MAX_DIM: 4096, MAX_PIXELS: 12000000, MAX_DECK_BYTES: 10000000 };
  function u32(b, i) { return ((b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]) >>> 0; }
  function fail(reason) { return { ok: false, reason: reason }; }
  function dims(type, w, h) {
    if (!w || !h) return fail("The picture has no size.");
    if (w > LIMITS.MAX_DIM || h > LIMITS.MAX_DIM) return fail("The picture is " + w + " by " + h + " pixels. The limit is " + LIMITS.MAX_DIM + " on each side.");
    if (w * h > LIMITS.MAX_PIXELS) return fail("The picture has " + Math.round((w * h) / 1e6) + " million pixels. The limit is " + LIMITS.MAX_PIXELS / 1e6 + " million.");
    return { ok: true, type: type, w: w, h: h };
  }
  var PNG_SIG = [137, 80, 78, 71, 13, 10, 26, 10];
  function sniffPng(b) {
    if (b.length < 45) return fail("The file is too short to be a PNG.");
    if (u32(b, 8) !== 13 || b[12] !== 73 || b[13] !== 72 || b[14] !== 68 || b[15] !== 82) return fail("The PNG header is damaged.");
    var depth = b[24], ct = b[25], okDepth = { 0: [1, 2, 4, 8, 16], 2: [8, 16], 3: [1, 2, 4, 8], 4: [8, 16], 6: [8, 16] };
    if (!okDepth[ct] || okDepth[ct].indexOf(depth) < 0 || b[26] !== 0 || b[27] !== 0 || b[28] > 1) return fail("The PNG header is damaged.");
    // walk the chunks by their lengths: each must fit inside the file, and the last must be IEND with nothing after it
    var i = 8, sawIdat = false, end = false;
    while (i + 12 <= b.length) {
      var len = u32(b, i), name = String.fromCharCode(b[i + 4], b[i + 5], b[i + 6], b[i + 7]);
      if (len > b.length || i + 12 + len > b.length) return fail("The PNG is cut short or damaged.");
      if (name === "IDAT") sawIdat = true;
      if (name === "IEND") { end = true; i += 12 + len; break; }
      i += 12 + len;
    }
    if (!end || !sawIdat) return fail("The PNG is cut short or damaged.");
    if (i !== b.length) return fail("The PNG has extra bytes after its end.");
    return dims("image/png", u32(b, 16), u32(b, 20));
  }
  function sniffJpeg(b) {
    if (b.length < 30) return fail("The file is too short to be a JPEG.");
    var i = 2, w = 0, h = 0, comps = 0;
    while (i + 4 <= b.length) {
      if (b[i] !== 255) return fail("The JPEG is damaged.");
      var m = b[i + 1];
      if (m === 255) { i++; continue; }
      if (m === 217) break;
      var len = (b[i + 2] << 8) | b[i + 3];
      if (len < 2 || i + 2 + len > b.length) return fail("The JPEG is cut short or damaged.");
      // SOF0, SOF1, SOF2 (baseline, extended, progressive). Arithmetic or lossless frames are not accepted.
      if (m === 192 || m === 193 || m === 194) {
        if (len < 8) return fail("The JPEG is damaged.");
        if (b[i + 4] !== 8) return fail("Only 8-bit JPEG pictures are accepted.");
        h = (b[i + 5] << 8) | b[i + 6]; w = (b[i + 7] << 8) | b[i + 8]; comps = b[i + 9];
        if (comps !== 1 && comps !== 3) return fail("Only grey or colour (YCbCr) JPEG pictures are accepted. CMYK is not.");
      } else if (m === 195 || (m >= 197 && m <= 207 && m !== 196 && m !== 200 && m !== 204)) return fail("This kind of JPEG is not accepted.");
      if (m === 218) {  // start of scan: skip the compressed data to the last bytes
        if (!w) return fail("The JPEG has no frame header.");
        if (b[b.length - 2] !== 255 || b[b.length - 1] !== 217) return fail("The JPEG is cut short or damaged.");
        return dims("image/jpeg", w, h);
      }
      i += 2 + len;
    }
    return fail("The JPEG is cut short or damaged.");
  }
  // bytes: Uint8Array. Returns { ok:true, type, w, h } or { ok:false, reason }. The file name and any declared type are never used.
  function sniff(b) {
    if (!b || typeof b.length !== "number" || !b.length) return fail("The file is empty.");
    if (b.length > LIMITS.MAX_BYTES) return fail("The file is " + (b.length / 1e6).toFixed(1) + " MB. The limit is " + LIMITS.MAX_BYTES / 1e6 + " MB per picture.");
    var isPng = PNG_SIG.every(function (v, k) { return b[k] === v; });
    if (isPng) return sniffPng(b);
    if (b[0] === 255 && b[1] === 216 && b[2] === 255) return sniffJpeg(b);
    return fail("Only PNG and JPEG pictures are accepted.");
  }
  function hex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (x) { return ("0" + x.toString(16)).slice(-2); }).join(""); }
  function sha256(b) {
    var g = typeof globalThis !== "undefined" ? globalThis : root, c = (root.crypto || g.crypto) && (root.crypto || g.crypto).subtle;
    if (!c) return Promise.reject(new Error("This page cannot compute a picture fingerprint here (it needs a secure connection)."));
    return c.digest("SHA-256", b).then(hex);
  }
  // Full check: sniff, hash, then a real decode whose size must equal the header. Resolves { hash, type, w, h, bytes } or rejects Error(message).
  function qualify(b) {
    var s = sniff(b); if (!s.ok) return Promise.reject(new Error(s.reason));
    return sha256(b).then(function (hash) {
      if (typeof root.createImageBitmap !== "function") throw new Error("This browser cannot check the picture, so it was not added.");
      return root.createImageBitmap(new Blob([b], { type: s.type })).then(function (bm) {
        var ok = bm.width === s.w && bm.height === s.h; if (bm.close) bm.close();
        if (!ok) throw new Error("The picture does not match its own header, so it was not added.");
        return { hash: hash, type: s.type, w: s.w, h: s.h, bytes: b };
      }, function () { throw new Error("The browser could not read this picture. It may be damaged."); });
    });
  }
  // IndexedDB store, one database per person (name carries the same per-person key as the deck), so one person's pictures are never read under another's key.
  function Store(name, idb) {
    this.name = name; this.idb = idb || (root.indexedDB || null); this.db = null;
  }
  Store.prototype.open = function () {
    var s = this; if (s.db) return Promise.resolve(s.db);
    if (!s.idb) return Promise.reject(new Error("This browser does not allow pictures to be kept here."));
    return new Promise(function (res, rej) {
      var r; try { r = s.idb.open(s.name, 1); } catch (e) { rej(new Error("This browser would not open its picture storage.")); return; }
      r.onupgradeneeded = function () { r.result.createObjectStore("img", { keyPath: "hash" }); };
      r.onsuccess = function () { s.db = r.result; res(s.db); };
      r.onerror = function () { rej(new Error("This browser would not open its picture storage.")); };
      r.onblocked = function () { rej(new Error("This browser would not open its picture storage.")); };
    });
  };
  function tx(s, mode, fn) {
    return s.open().then(function (db) {
      return new Promise(function (res, rej) {
        var t = db.transaction("img", mode), out;
        try { out = fn(t.objectStore("img")); } catch (e) { rej(new Error("This browser would not use its picture storage.")); return; }
        t.oncomplete = function () { res(out && out.result !== undefined ? out.result : undefined); };
        t.onabort = t.onerror = function () { rej(new Error(t.error && t.error.name === "QuotaExceededError" ? "This browser is out of room for pictures. Nothing was added." : "This browser could not save the picture. Nothing was added.")); };
      });
    });
  }
  // put never replaces different bytes under the same hash: a record is only written when absent
  Store.prototype.put = function (rec) {
    var s = this; return s.get(rec.hash).then(function (have) {
      if (have) return false;
      return tx(s, "readwrite", function (o) { return o.add({ hash: rec.hash, type: rec.type, w: rec.w, h: rec.h, size: rec.bytes.length, bytes: rec.bytes.buffer.slice(rec.bytes.byteOffset, rec.bytes.byteOffset + rec.bytes.length) }); }).then(function () { return true; });
    });
  };
  Store.prototype.get = function (hash) { return tx(this, "readonly", function (o) { return o.get(hash); }); };
  // size in bytes of each named picture that is present; names that are missing are listed in missing
  Store.prototype.sizes = function (hashes) {
    var s = this, uniq = hashes.filter(function (h, k) { return hashes.indexOf(h) === k; });
    return Promise.all(uniq.map(function (h) { return s.get(h).then(function (r) { return { h: h, r: r }; }); })).then(function (all) {
      var total = 0, missing = []; all.forEach(function (x) { if (x.r) total += x.r.size; else missing.push(x.h); }); return { total: total, missing: missing };
    });
  };
  var api = { LIMITS: LIMITS, sniff: sniff, sha256: sha256, qualify: qualify, Store: Store };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSlidesImages = api;
})(typeof self !== "undefined" ? self : this);
