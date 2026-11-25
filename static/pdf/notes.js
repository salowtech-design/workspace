(function (root) {
  "use strict";
  // Page notes for a PDF opened on this device. A note is a pinned text label; it is kept in this
  // browser only and is never written into the PDF file. Limits are viewer bounds, not retention rules.
  var MAX_NOTES = 200, MAX_TEXT = 500, MAX_FILES = 30;
  function validNote(n) {
    return !!n && typeof n === "object" && typeof n.id === "string" && /^[a-z0-9]{6,20}$/.test(n.id) &&
      Number.isInteger(n.page) && n.page >= 1 && n.page <= 100000 &&
      ((n.x === null && n.y === null) || (typeof n.x === "number" && n.x >= 0 && n.x <= 1 && typeof n.y === "number" && n.y >= 0 && n.y <= 1)) &&
      typeof n.text === "string" && n.text.length >= 1 && n.text.length <= MAX_TEXT;
  }
  // raw is a stored string or null. Anything that is not exactly a valid list is unreadable and is kept, never replaced.
  function load(raw) {
    if (raw === null || raw === undefined) return { notes: [], unreadable: false };
    var v; try { v = JSON.parse(raw); } catch (e) { return { notes: [], unreadable: true }; }
    if (!v || v.v !== 1 || !Array.isArray(v.notes) || v.notes.length > MAX_NOTES) return { notes: [], unreadable: true };
    var seen = {};
    for (var i = 0; i < v.notes.length; i++) {
      var n = v.notes[i]; if (!validNote(n) || seen[n.id]) return { notes: [], unreadable: true };
      seen[n.id] = true;
    }
    return { notes: v.notes, unreadable: false };
  }
  function dump(notes) { return JSON.stringify({ v: 1, notes: notes }); }
  function newId(rand) { var s = ""; while (s.length < 10) s += Math.floor(rand() * 36).toString(36); return s; }
  // Returns { notes } or { error }.
  function add(notes, page, x, y, text, rand) {
    text = typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
    if (!text) return { error: "Write something for the note first." };
    if (text.length > MAX_TEXT) return { error: "A note can be up to " + MAX_TEXT + " characters." };
    if (notes.length >= MAX_NOTES) return { error: "This file already has " + MAX_NOTES + " notes. Delete one first." };
    var clamp = function (v) { return typeof v !== "number" || isNaN(v) ? 0 : Math.max(0, Math.min(1, v)); };
    var pageLevel = x === null && y === null; // a note for the whole page, with no pin position
    var id = newId(rand || Math.random), tries = 0;
    while (tries++ < 20 && notes.some(function (o) { return o.id === id; })) id = newId(Math.random);
    var n = { id: id, page: page, x: pageLevel ? null : clamp(x), y: pageLevel ? null : clamp(y), text: text };
    return validNote(n) ? { notes: notes.concat([n]) } : { error: "That note could not be saved." };
  }
  function remove(notes, id) { return notes.filter(function (n) { return n.id !== id; }); }
  function forPage(notes, p) { return notes.filter(function (n) { return n.page === p; }); }
  // Plain-text export. File name is shown as given by the person's own file.
  function toText(notes, name) {
    var sorted = notes.slice().sort(function (a, b) { return a.page - b.page || (a.y || 0) - (b.y || 0) || (a.x || 0) - (b.x || 0); });
    var out = ["Notes for " + String(name || "PDF file").slice(0, 120), "Kept on this device. These notes are not inside the PDF file.", ""];
    sorted.forEach(function (n) { out.push("Page " + n.page + ": " + n.text); });
    return out.join("\n") + "\n";
  }
  // What a save may do, given what this tab last knew is stored (lastRaw), what is stored now (curRaw),
  // how many notes the new list has, and how many files already have notes. Pure, so it can be tested.
  function plan(lastRaw, curRaw, nextLen, fileCount) {
    if (curRaw !== lastRaw) return "stale";
    if (nextLen === 0) return curRaw === null ? "none" : "remove";
    if (curRaw === null && fileCount >= MAX_FILES) return "limit";
    return "set";
  }
  // SHA-256 of the file bytes as hex. Uses the browser's crypto when it offers it (secure pages), else a plain
  // implementation, so the key follows the file's content and never a value the file supplies about itself.
  var K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  function sha256Plain(bytes) {
    var len = bytes.length, total = ((len + 9 + 63) >> 6) << 6, buf = new Uint8Array(total);
    buf.set(bytes); buf[len] = 0x80;
    var dv = new DataView(buf.buffer), bits = len * 8;
    dv.setUint32(total - 8, Math.floor(bits / 4294967296)); dv.setUint32(total - 4, bits >>> 0);
    var h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19], w = new Int32Array(64);
    for (var o = 0; o < total; o += 64) {
      for (var i = 0; i < 16; i++) w[i] = dv.getInt32(o + i * 4);
      for (i = 16; i < 64; i++) {
        var a15 = w[i - 15], a2 = w[i - 2];
        var s0 = ((a15 >>> 7) | (a15 << 25)) ^ ((a15 >>> 18) | (a15 << 14)) ^ (a15 >>> 3);
        var s1 = ((a2 >>> 17) | (a2 << 15)) ^ ((a2 >>> 19) | (a2 << 13)) ^ (a2 >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      var A = h[0], B = h[1], C = h[2], D = h[3], E = h[4], F = h[5], G = h[6], H = h[7];
      for (i = 0; i < 64; i++) {
        var S1 = ((E >>> 6) | (E << 26)) ^ ((E >>> 11) | (E << 21)) ^ ((E >>> 25) | (E << 7));
        var t1 = (H + S1 + ((E & F) ^ (~E & G)) + K[i] + w[i]) | 0;
        var S0 = ((A >>> 2) | (A << 30)) ^ ((A >>> 13) | (A << 19)) ^ ((A >>> 22) | (A << 10));
        var t2 = (S0 + ((A & B) ^ (A & C) ^ (B & C))) | 0;
        H = G; G = F; F = E; E = (D + t1) | 0; D = C; C = B; B = A; A = (t1 + t2) | 0;
      }
      h[0] = (h[0] + A) | 0; h[1] = (h[1] + B) | 0; h[2] = (h[2] + C) | 0; h[3] = (h[3] + D) | 0;
      h[4] = (h[4] + E) | 0; h[5] = (h[5] + F) | 0; h[6] = (h[6] + G) | 0; h[7] = (h[7] + H) | 0;
    }
    return h.map(function (x) { return ("00000000" + (x >>> 0).toString(16)).slice(-8); }).join("");
  }
  function sha256Hex(buf) {
    var bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
    var c = root.crypto && root.crypto.subtle;
    if (c && typeof c.digest === "function") {
      return c.digest("SHA-256", bytes).then(function (d) {
        return Array.prototype.map.call(new Uint8Array(d), function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      });
    }
    return Promise.resolve(sha256Plain(bytes));
  }
  var api = { plan: plan, sha256Hex: sha256Hex, sha256Plain: sha256Plain, MAX_NOTES: MAX_NOTES, MAX_TEXT: MAX_TEXT, MAX_FILES: MAX_FILES, load: load, dump: dump, add: add, remove: remove, forPage: forPage, toText: toText, validNote: validNote };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowPdfNotes = api;
})(typeof self !== "undefined" ? self : this);
