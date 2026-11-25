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
  // A SIZE square centred on the point, kept inside the box.
  function rectFor(box, rot, u, v) {
    var p = toPage(box, rot, u, v), half = SIZE / 2;
    var x = Math.min(Math.max(p[0] - half, box[0]), box[0] + box[2] - SIZE);
    var y = Math.min(Math.max(p[1] - half, box[1]), box[1] + box[3] - SIZE);
    return [x, y, x + SIZE, y + SIZE];
  }
  function build(PDFLib, bytes, notes) {
    var L = PDFLib;
    return L.PDFDocument.load(bytes, { ignoreEncryption: false, updateMetadata: false }).then(function (pdf) {
      var ctx = pdf.context, count = pdf.getPageCount(), done = 0;
      var ap = ctx.formXObject([
        L.pushGraphicsState(), L.setFillingRgbColor(1, 0.85, 0.2), L.setStrokingRgbColor(0.35, 0.28, 0), L.setLineWidth(1),
        L.moveTo(0.5, 0.5), L.lineTo(17.5, 0.5), L.lineTo(17.5, 17.5), L.lineTo(0.5, 17.5), L.closePath(), L.fillAndStroke(),
        L.moveTo(4, 12.5), L.lineTo(14, 12.5), L.moveTo(4, 9), L.lineTo(14, 9), L.moveTo(4, 5.5), L.lineTo(11, 5.5), L.stroke(),
        L.popGraphicsState()
      ], { BBox: [0, 0, SIZE, SIZE] });
      var apRef = ctx.register(ap);
      notes.forEach(function (n) {
        if (n.page < 1 || n.page > count) return;
        var page = pdf.getPage(n.page - 1), cb = page.getCropBox();
        var u = typeof n.x === "number" ? n.x : MARGIN, v = typeof n.y === "number" ? n.y : MARGIN;
        var r = rectFor([cb.x, cb.y, cb.width, cb.height], page.getRotation().angle, u, v);
        var annot = ctx.obj({
          Type: "Annot", Subtype: "Text", Rect: r, Contents: L.PDFHexString.fromText(n.text), T: L.PDFHexString.fromText("Salow note"),
          Name: "Note", F: 4, C: [1, 0.85, 0.2], Open: false, AP: { N: apRef }
        });
        var ref = ctx.register(annot), cur = page.node.lookup(L.PDFName.of("Annots"));
        if (cur instanceof L.PDFArray) cur.push(ref); else page.node.set(L.PDFName.of("Annots"), ctx.obj([ref]));
        done++;
      });
      return pdf.save({ useObjectStreams: false }).then(function (out) { return { bytes: out, added: done }; });
    });
  }
  var api = { toPage: toPage, rectFor: rectFor, build: build, SIZE: SIZE };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfNotesExport = api;
})(typeof self !== "undefined" ? self : this);
