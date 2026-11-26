// Builds a new PDF from chosen pages of the open file, or from the open file followed by other PDFs (browser only).
// The inputs are never changed and nothing is stored. Only the pages are copied: the form definition, outline (bookmarks),
// named destinations and document-level settings of the inputs are not carried into the new file.
(function (root) {
  "use strict";
  var MAX_BYTES = 20 * 1024 * 1024, MAX_PAGES = 1000, MAX_FILES = 10;
  function fail(code, msg, extra) { var e = new Error(msg); e.code = code; if (extra) e.name2 = extra; return e; }
  // "1-3, 5, 7-" style text to a list of 1-based page numbers in the order typed. A range is low-high; "7-" means 7 to the end.
  function parseRanges(text, n) {
    var t = String(text || "").replace(/\s+/g, "");
    if (!t) return { error: "Type the pages to keep, for example 1-3,5." };
    if (t.length > 200) return { error: "That list is too long." };
    var out = [], parts = t.split(","), i;
    for (i = 0; i < parts.length; i++) {
      var m = /^(\d{1,6})(?:(-)(\d{0,6}))?$/.exec(parts[i]);
      if (!m) return { error: "\"" + parts[i].slice(0, 20) + "\" is not a page or a range like 2-4." };
      var a = parseInt(m[1], 10), b = m[2] ? (m[3] === "" ? n : parseInt(m[3], 10)) : a;
      if (a < 1 || b < 1 || a > n || b > n) return { error: "This file has " + n + " page" + (n === 1 ? "" : "s") + ", so " + parts[i].slice(0, 20) + " is out of range." };
      if (b < a) return { error: "\"" + parts[i].slice(0, 20) + "\" runs backwards. Write the lower page first." };
      for (var p = a; p <= b; p++) { out.push(p); if (out.length > MAX_PAGES) return { error: "A new file is limited to " + MAX_PAGES + " pages." }; }
    }
    return { pages: out };
  }
  function load(L, bytes, name) {
    return L.PDFDocument.load(bytes, { ignoreEncryption: false, updateMetadata: false }).catch(function (e) {
      throw fail(/encrypt/i.test(String(e && e.message)) ? "encrypted" : "damaged", "could not read", name);
    });
  }
  // parts: [{ bytes, pages (1-based list, or null for every page), name }]. Returns { bytes, pageCount }.
  function build(L, parts, looksSigned) {
    if (!parts.length || parts.length > MAX_FILES) return Promise.reject(fail("files", "too many or no files"));
    var total = 0; parts.forEach(function (p) { total += p.bytes.length; });
    if (total > MAX_BYTES) return Promise.reject(fail("size", "inputs too large"));
    return L.PDFDocument.create({ updateMetadata: false }).then(function (out) {
      var chain = Promise.resolve(), count = 0, signed = [];
      parts.forEach(function (p) {
        chain = chain.then(function () { return load(L, p.bytes, p.name); }).then(function (src) {
          if (looksSigned && looksSigned(L, src, p.bytes)) signed.push(p.name);
          var idx = p.pages ? p.pages.map(function (x) { return x - 1; }) : src.getPageIndices();
          if (idx.some(function (i) { return i < 0 || i >= src.getPageCount(); })) throw fail("range", "page out of range", p.name);
          count += idx.length; if (count > MAX_PAGES) throw fail("pages", "too many pages");
          return out.copyPages(src, idx).then(function (pgs) { pgs.forEach(function (pg) { out.addPage(pg); }); });
        });
      });
      return chain.then(function () { return out.save({ useObjectStreams: false }); }).then(function (bytes) {
        if (bytes.length > MAX_BYTES) throw fail("size", "copy too large");
        return { bytes: bytes, pageCount: count, signed: signed };
      });
    });
  }
  var api = { MAX_BYTES: MAX_BYTES, MAX_PAGES: MAX_PAGES, MAX_FILES: MAX_FILES, parseRanges: parseRanges, build: build };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfPages = api;
})(typeof self !== "undefined" ? self : this);
