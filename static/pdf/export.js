// Builds a copy of an open PDF with the person's notes added as standard text annotations (browser only).
// The original bytes are never changed; the result is a separate file. Notes with no pin go to the top-left corner.
(function (root) {
  "use strict";
  var SIZE = 18, MARGIN = 0.02;
  // Where a displayed point (u, v as fractions from the top-left of the page as shown) sits in the page's own
  // coordinates, given its box [x0, y0, w, h] and /Rotate (a multiple of 90).
  function toPage(box, rot, u, v) {
    var x0 = box[0], y0 = box[1], w = box[2], h = box[3];
    rot = ((Math.round(rot / 90) * 90) % 360 + 360) % 360;
    if (rot === 90) return [x0 + v * w, y0 + u * h];
    if (rot === 180) return [x0 + (1 - u) * w, y0 + v * h];
    if (rot === 270) return [x0 + (1 - v) * w, y0 + (1 - u) * h];
    return [x0 + u * w, y0 + (1 - v) * h];
  }
  // Text annotations are drawn upright by some viewers (poppler re-anchors them at the upper-left corner of the box as the
  // page is stored) and as stored by others (pdf.js). Place that corner so the first kind centres the icon on the point,
  // and keep the box on the page so the second kind always shows it. On rotated pages the two can differ by up to one
  // icon width (18 pt); unrotated pages agree.
  function rectFor(box, rot, u, v) {
    var turned = (((Math.round(rot / 90) * 90) % 360) + 360) % 360 % 180 === 90;
    var dw = turned ? box[3] : box[2], dh = turned ? box[2] : box[3], half = SIZE / 2;
    var au = Math.min(Math.max(u - half / dw, 0), Math.max(0, 1 - SIZE / dw)), av = Math.min(Math.max(v - half / dh, 0), Math.max(0, 1 - SIZE / dh));
    var a = toPage(box, rot, au, av);
    // Keep the box itself on the page too, so a viewer that draws it as stored (not re-anchored) still shows it.
    var x = Math.min(Math.max(a[0], box[0]), box[0] + box[2] - SIZE), y = Math.min(Math.max(a[1] - SIZE, box[1]), box[1] + box[3] - SIZE);
    return [x, y, x + SIZE, y + SIZE];
  }
  function fail(code, msg) { var e = new Error(msg); e.code = code; return e; }
  // Intersection of the crop box and media box, as the viewer shows it; the media box if they do not overlap.
  function shownBox(page) {
    var m = page.getMediaBox(), c = page.getCropBox();
    var x0 = Math.max(m.x, c.x), y0 = Math.max(m.y, c.y), x1 = Math.min(m.x + m.width, c.x + c.width), y1 = Math.min(m.y + m.height, c.y + c.height);
    return x1 - x0 > SIZE && y1 - y0 > SIZE ? [x0, y0, x1 - x0, y1 - y0] : [m.x, m.y, m.width, m.height];
  }
  function bytesHave(bytes, text) {
    var t = []; for (var i = 0; i < text.length; i++) t.push(text.charCodeAt(i));
    var n = bytes.length - t.length, f = t[0];
    for (var j = 0; j <= n; j++) { if (bytes[j] !== f) continue; var k = 1; while (k < t.length && bytes[j + k] === t[k]) k++; if (k === t.length) return true; }
    return false;
  }
  // True when the file looks digitally signed (a signature field with a value, certification permissions, or a byte range).
  function looksSigned(L, pdf, bytes) {
    try {
      if (pdf.catalog.lookup(L.PDFName.of("Perms")) instanceof L.PDFDict) return true;
      var af = pdf.catalog.lookup(L.PDFName.of("AcroForm")), seen = 0;
      var walk = function (arr, depth) {
        if (!(arr instanceof L.PDFArray) || depth > 8) return false;
        for (var i = 0; i < arr.size() && seen < 5000; i++) {
          var f = arr.lookup(i); seen++;
          if (!(f instanceof L.PDFDict)) continue;
          if (f.lookup(L.PDFName.of("FT")) === L.PDFName.of("Sig") && f.lookup(L.PDFName.of("V")) instanceof L.PDFDict) return true;
          if (walk(f.lookup(L.PDFName.of("Kids")), depth + 1)) return true;
        }
        return false;
      };
      if (af instanceof L.PDFDict && walk(af.lookup(L.PDFName.of("Fields")), 0)) return true;
    } catch (e) { /* fall through to the byte scan */ }
    return bytesHave(bytes, "/ByteRange");
  }
  function build(PDFLib, bytes, notes) {
    var L = PDFLib;
    return L.PDFDocument.load(bytes, { ignoreEncryption: false, updateMetadata: false }).then(function (pdf) {
      var ctx = pdf.context, count = pdf.getPageCount(), done = 0, signed = looksSigned(L, pdf, bytes);
      var ap = ctx.formXObject([
        L.pushGraphicsState(), L.setFillingRgbColor(1, 0.85, 0.2), L.setStrokingRgbColor(0.35, 0.28, 0), L.setLineWidth(1),
        L.moveTo(0.5, 0.5), L.lineTo(17.5, 0.5), L.lineTo(17.5, 17.5), L.lineTo(0.5, 17.5), L.closePath(), L.fillAndStroke(),
        L.moveTo(4, 12.5), L.lineTo(14, 12.5), L.moveTo(4, 9), L.lineTo(14, 9), L.moveTo(4, 5.5), L.lineTo(11, 5.5), L.stroke(),
        L.popGraphicsState()
      ], { BBox: [0, 0, SIZE, SIZE] });
      var apRef = ctx.register(ap);
      // Check every page's existing annotation list first, so a list that is not an array stops the copy before any change.
      var targets = [];
      notes.forEach(function (n) {
        if (n.page < 1 || n.page > count) return;
        var page = pdf.getPage(n.page - 1), cur = page.node.lookup(L.PDFName.of("Annots"));
        if (cur !== undefined && !(cur instanceof L.PDFArray)) throw fail("annots", "annotations list is not an array");
        targets.push([n, page, cur]);
      });
      targets.forEach(function (t) {
        var n = t[0], page = t[1], cur = page.node.lookup(L.PDFName.of("Annots"));
        var u = typeof n.x === "number" ? n.x : MARGIN, v = typeof n.y === "number" ? n.y : MARGIN;
        var r = rectFor(shownBox(page), page.getRotation().angle, u, v);
        var annot = ctx.obj({
          Type: "Annot", Subtype: "Text", Rect: r, Contents: L.PDFHexString.fromText(n.text), T: L.PDFHexString.fromText("Salow note"),
          Name: "Note", F: 4, C: [1, 0.85, 0.2], Open: false, AP: { N: apRef }
        });
        var ref = ctx.register(annot);
        if (cur instanceof L.PDFArray) cur.push(ref); else page.node.set(L.PDFName.of("Annots"), ctx.obj([ref]));
        done++;
      });
      return pdf.save({ useObjectStreams: false }).then(function (out) { return { bytes: out, added: done, signed: signed }; });
    }, function (e) { throw fail(/encrypt/i.test(String(e && e.message)) ? "encrypted" : "damaged", "could not read"); });
  }
  var api = { MAX_BYTES: 20 * 1024 * 1024, toPage: toPage, rectFor: rectFor, build: build, looksSigned: looksSigned, shownBox: shownBox, SIZE: SIZE };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfNotesExport = api;
})(typeof self !== "undefined" ? self : this);
