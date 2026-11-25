(function (root) {
  "use strict";
  // Page notes for a PDF opened on this device. A note is a pinned text label; it is kept in this
  // browser only and is never written into the PDF file. Limits are viewer bounds, not retention rules.
  var MAX_NOTES = 200, MAX_TEXT = 500, MAX_FILES = 30;
  function validNote(n) {
    return !!n && typeof n === "object" && typeof n.id === "string" && /^[a-z0-9]{6,20}$/.test(n.id) &&
      Number.isInteger(n.page) && n.page >= 1 && n.page <= 100000 &&
      typeof n.x === "number" && n.x >= 0 && n.x <= 1 && typeof n.y === "number" && n.y >= 0 && n.y <= 1 &&
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
    var id = newId(rand || Math.random), tries = 0;
    while (tries++ < 20 && notes.some(function (o) { return o.id === id; })) id = newId(Math.random);
    var n = { id: id, page: page, x: clamp(x), y: clamp(y), text: text };
    return validNote(n) ? { notes: notes.concat([n]) } : { error: "That note could not be saved." };
  }
  function remove(notes, id) { return notes.filter(function (n) { return n.id !== id; }); }
  function forPage(notes, p) { return notes.filter(function (n) { return n.page === p; }); }
  // Plain-text export. File name is shown as given by the person's own file.
  function toText(notes, name) {
    var sorted = notes.slice().sort(function (a, b) { return a.page - b.page || a.y - b.y || a.x - b.x; });
    var out = ["Notes for " + String(name || "PDF file").slice(0, 120), "Kept on this device. These notes are not inside the PDF file.", ""];
    sorted.forEach(function (n) { out.push("Page " + n.page + ": " + n.text); });
    return out.join("\n") + "\n";
  }
  var api = { MAX_NOTES: MAX_NOTES, MAX_TEXT: MAX_TEXT, MAX_FILES: MAX_FILES, load: load, dump: dump, add: add, remove: remove, forPage: forPage, toText: toText, validNote: validNote };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowPdfNotes = api;
})(typeof self !== "undefined" ? self : this);
