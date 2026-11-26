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
  var LIMITS = { MAX_BYTES: 2000000, MAX_DIM: 4096, MAX_PIXELS: 12000000, MAX_DECK_BYTES: 10000000, MAX_DECK_PIXELS: 48000000, MAX_IMPORT_DECK: 7000000, MAX_IMPORT_FILE: 20500000 };  // import caps are sized from the worst-case deck JSON and export (tests/js/slides_images.js computes them)
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
  // SHA-256 in plain code, used only when the browser has no crypto.subtle (it is missing on pages not served over a secure connection).
  var K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
  function sha256Plain(b) {
    var h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19], n = b.length, total = ((n + 9 + 63) >> 6) << 6, m = new Uint8Array(total), w = new Array(64), i, t;
    m.set(b); m[n] = 0x80; var bits = n * 8; m[total - 4] = (bits >>> 24) & 255; m[total - 3] = (bits >>> 16) & 255; m[total - 2] = (bits >>> 8) & 255; m[total - 1] = bits & 255; m[total - 8] = Math.floor(n / 0x20000000) & 255;
    function rr(x, k) { return (x >>> k) | (x << (32 - k)); }
    for (var o = 0; o < total; o += 64) {
      for (i = 0; i < 16; i++) w[i] = (m[o + 4 * i] << 24) | (m[o + 4 * i + 1] << 16) | (m[o + 4 * i + 2] << 8) | m[o + 4 * i + 3];
      for (i = 16; i < 64; i++) { var s0 = rr(w[i - 15], 7) ^ rr(w[i - 15], 18) ^ (w[i - 15] >>> 3), s1 = rr(w[i - 2], 17) ^ rr(w[i - 2], 19) ^ (w[i - 2] >>> 10); w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0; }
      var a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
      for (i = 0; i < 64; i++) {
        var S1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25), ch = (e & f) ^ (~e & g), t1 = (hh + S1 + ch + K[i] + w[i]) | 0, S0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22), mj = (a & bb) ^ (a & c) ^ (bb & c), t2 = (S0 + mj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = bb; bb = a; a = (t1 + t2) | 0;
      }
      h[0] = (h[0] + a) | 0; h[1] = (h[1] + bb) | 0; h[2] = (h[2] + c) | 0; h[3] = (h[3] + d) | 0; h[4] = (h[4] + e) | 0; h[5] = (h[5] + f) | 0; h[6] = (h[6] + g) | 0; h[7] = (h[7] + hh) | 0;
    }
    return h.map(function (x) { t = (x >>> 0).toString(16); return ("00000000" + t).slice(-8); }).join("");
  }
  function sha256(b, forcePlain) {
    var g = typeof globalThis !== "undefined" ? globalThis : root, cr = root.crypto || g.crypto, c = cr && cr.subtle;
    if (!c || forcePlain) return Promise.resolve(sha256Plain(b));
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
      var total = 0, pixels = 0, missing = []; all.forEach(function (x) { if (x.r) { total += x.r.size; pixels += x.r.w * x.r.h; } else missing.push(x.h); }); return { total: total, pixels: pixels, missing: missing };
    });
  };
  var api = { LIMITS: LIMITS, sniff: sniff, sha256: sha256, qualify: qualify, Store: Store };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSlidesImages = api;
})(typeof self !== "undefined" ? self : this);
