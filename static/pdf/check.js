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
  var api = { MAX_BYTES: MAX_BYTES, check: check, pageIndex: pageIndex, zoomStep: zoomStep };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowPdfCheck = api;
})(typeof self !== "undefined" ? self : this);
