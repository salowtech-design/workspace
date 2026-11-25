(function (root) {
  "use strict";
  // Viewer limit only: a memory bound for this browser tab, not a storage or upload policy.
  var MAX_BYTES = 50 * 1024 * 1024;
  // head is the first bytes of the file as a latin1 string. A PDF header may follow up to 1024 bytes of junk.
  function check(name, size, head) {
    if (typeof size !== "number" || !(size > 0)) return { ok: false, why: "That file is empty." };
    if (size > MAX_BYTES) return { ok: false, why: "That file is larger than the " + Math.floor(MAX_BYTES / 1048576) + " MB this viewer opens." };
    if (typeof head !== "string" || head.slice(0, 1024).indexOf("%PDF-") < 0) return { ok: false, why: "That does not look like a PDF file." };
    return { ok: true, why: "" };
  }
  function pageIndex(cur, delta, count) { var n = cur + delta; return n < 1 ? 1 : n > count ? count : n; }
  function zoomStep(z, dir) { var l = [0.5, 0.75, 1, 1.25, 1.5, 2, 3], i = l.indexOf(z); if (i < 0) i = 2; i = Math.max(0, Math.min(l.length - 1, i + dir)); return l[i]; }
  // items: pdf.js text items. Returns the page text with at most budget characters in total.
  function takeText(items, budget) {
    var out = [], used = 0, cut = false;
    for (var i = 0; i < items.length; i++) {
      var it = items[i]; if (!it || typeof it.str !== "string") continue;
      if (used >= budget) { cut = true; break; }
      var piece = it.str.slice(0, budget - used); if (piece.length < it.str.length) cut = true;
      out.push(piece + (it.hasEOL ? "\n" : " ")); used += piece.length + 1;
    }
    return { text: out.join("").replace(/[ \t]+\n/g, "\n").trim(), cut: cut };
  }
  // Find helpers. Plain text only: no OCR, no fuzzy match. A query is 2 to 100 characters after trimming.
  var MAX_QUERY = 100, MAX_SCAN = 300;
  function normQuery(q) { if (typeof q !== "string") return null; q = q.replace(/\s+/g, " ").trim(); return q.length < 2 || q.length > MAX_QUERY ? null : q; }
  // Case-insensitive count of non-overlapping matches of query in text.
  function countIn(text, q) {
    if (typeof text !== "string" || typeof q !== "string" || !q) return 0;
    var t = text.replace(/\s+/g, " ").toLowerCase(), k = q.toLowerCase(), n = 0, i = 0;
    while ((i = t.indexOf(k, i)) >= 0) { n++; i += k.length; }
    return n;
  }
  // Pages to look at: the page after cur first, wrapping, the current page last. At most MAX_SCAN pages.
  function pageOrder(cur, count) {
    var out = [];
    for (var i = 1; i <= count && out.length < MAX_SCAN; i++) out.push(((cur - 1 + i) % count) + 1);
    return out;
  }
  // Newest-pick-wins sequence: begin() on each file pick or close, current(n) says whether n is still the newest.
  function Seq() { var n = 0; this.begin = function () { return ++n; }; this.current = function (m) { return m === n; }; }
  var api = { normQuery: normQuery, countIn: countIn, pageOrder: pageOrder, MAX_SCAN: MAX_SCAN, takeText: takeText, Seq: Seq, MAX_BYTES: MAX_BYTES, check: check, pageIndex: pageIndex, zoomStep: zoomStep };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowPdfCheck = api;
})(typeof self !== "undefined" ? self : this);
