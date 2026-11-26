(function (root) {
  "use strict";
  // Area highlights and freehand pen strokes drawn over a page. They live only in this tab while the file is open (nothing is stored),
  // and become standard Highlight and Ink annotations in a separate downloaded copy. A highlight is a rectangle the person drags,
  // not a selection of text: this viewer has no positioned text layer. Positions are fractions (0 to 1) of the page as shown, from the top-left.
  var MAX_MARKS = 300, MAX_STROKES = 20, MAX_POINTS = 2000, KINDS = { hl: 1, ink: 1 };
  function unit(x) { return typeof x === "number" && x >= 0 && x <= 1; }
  function validMark(m) {
    if (!m || typeof m !== "object" || typeof m.id !== "string" || !/^[a-z0-9]{6,20}$/.test(m.id)) return false;
    if (!Number.isInteger(m.page) || m.page < 1 || m.page > 100000 || !KINDS[m.kind]) return false;
    if (m.kind === "hl") return Array.isArray(m.r) && m.r.length === 4 && m.r.every(unit) && m.r[2] > m.r[0] && m.r[3] > m.r[1];
    if (!Array.isArray(m.s) || !m.s.length || m.s.length > MAX_STROKES) return false;
    var total = 0;
    return m.s.every(function (st) { total += Array.isArray(st) ? st.length : 0; return Array.isArray(st) && st.length >= 2 && total <= MAX_POINTS && st.every(function (p) { return Array.isArray(p) && p.length === 2 && unit(p[0]) && unit(p[1]); }); });
  }
  function newId(rand) { var s = ""; while (s.length < 10) s += Math.floor(rand() * 36).toString(36); return s; }
  function fresh(marks, rand) { var id = newId(rand || Math.random), t = 0; while (t++ < 20 && marks.some(function (o) { return o.id === id; })) id = newId(Math.random); return id; }
  var clamp = function (v) { return typeof v !== "number" || isNaN(v) ? 0 : Math.max(0, Math.min(1, v)); };
  // Returns { marks } or { error }. Corners may be given in any order; a rectangle thinner than 0.5% of the page in either direction is refused.
  function addHighlight(marks, page, u0, v0, u1, v1, rand) {
    if (marks.length >= MAX_MARKS) return { error: "This file already has " + MAX_MARKS + " marks. Remove one first." };
    var a = Math.min(clamp(u0), clamp(u1)), b = Math.min(clamp(v0), clamp(v1)), c = Math.max(clamp(u0), clamp(u1)), d = Math.max(clamp(v0), clamp(v1));
    if (c - a < 0.005 || d - b < 0.005) return { error: "Drag a larger area to highlight." };
    var m = { id: fresh(marks, rand), page: page, kind: "hl", r: [a, b, c, d] };
    return validMark(m) ? { marks: marks.concat([m]) } : { error: "That highlight could not be added." };
  }
  // strokes: a list of point lists. Strokes of fewer than 2 points are dropped.
  function addInk(marks, page, strokes, rand) {
    if (marks.length >= MAX_MARKS) return { error: "This file already has " + MAX_MARKS + " marks. Remove one first." };
    var s = (Array.isArray(strokes) ? strokes : []).map(function (st) { return (Array.isArray(st) ? st : []).map(function (p) { return [clamp(p && p[0]), clamp(p && p[1])]; }); }).filter(function (st) { return st.length >= 2; });
    if (!s.length) return { error: "Draw a line first." };
    var m = { id: fresh(marks, rand), page: page, kind: "ink", s: s };
    return validMark(m) ? { marks: marks.concat([m]) } : { error: "That drawing is too long. Draw it in smaller parts." };
  }
  function remove(marks, id) { return marks.filter(function (m) { return m.id !== id; }); }
  function forPage(marks, p) { return marks.filter(function (m) { return m.page === p; }); }
  function fail(code, msg) { var e = new Error(msg); e.code = code; return e; }
  // A copy of the file with the marks as annotations. E is PdfNotesExport (for the page-box and rotation maths and the signed check).
  function build(L, E, bytes, marks) {
    return L.PDFDocument.load(bytes, { ignoreEncryption: false, updateMetadata: false }).then(function (pdf) {
      var ctx = pdf.context, count = pdf.getPageCount(), signed = E.looksSigned(L, pdf, bytes), done = 0, N = L.PDFName.of, targets = [];
      marks.forEach(function (m) {
        if (!validMark(m)) throw fail("mark", "invalid mark");
        if (m.page > count) return;
        var page = pdf.getPage(m.page - 1), cur = page.node.lookup(N("Annots"));
        if (cur !== undefined && !(cur instanceof L.PDFArray)) throw fail("annots", "annotations list is not an array");
        targets.push([m, page]);
      });
      var gs = ctx.obj({ Type: "ExtGState", BM: "Multiply" });
      targets.forEach(function (t) {
        var m = t[0], page = t[1], box = E.shownBox(page), rot = page.getRotation().angle, annot, cur = page.node.lookup(N("Annots"));
        var at = function (u, v) { return E.toPage(box, rot, u, v); };
        if (m.kind === "hl") {
          var p0 = at(m.r[0], m.r[1]), p1 = at(m.r[2], m.r[3]), x0 = Math.min(p0[0], p1[0]), x1 = Math.max(p0[0], p1[0]), y0 = Math.min(p0[1], p1[1]), y1 = Math.max(p0[1], p1[1]);
          var ap = ctx.formXObject([L.pushGraphicsState(), L.setGraphicsState(N("GS")), L.setFillingRgbColor(1, 0.92, 0.2), L.moveTo(x0, y0), L.lineTo(x1, y0), L.lineTo(x1, y1), L.lineTo(x0, y1), L.closePath(), L.fill(), L.popGraphicsState()], { BBox: [x0, y0, x1, y1], Resources: { ExtGState: { GS: gs } } });
          annot = ctx.obj({ Type: "Annot", Subtype: "Highlight", Rect: [x0, y0, x1, y1], QuadPoints: [x0, y1, x1, y1, x0, y0, x1, y0], C: [1, 0.92, 0.2], F: 4, T: L.PDFHexString.fromText("Salow highlight"), AP: { N: ctx.register(ap) } });
        } else {
          var lists = m.s.map(function (st) { var o = []; st.forEach(function (p) { var q = at(p[0], p[1]); o.push(q[0], q[1]); }); return o; });
          var xs = [], ys = []; lists.forEach(function (l) { for (var i = 0; i < l.length; i += 2) { xs.push(l[i]); ys.push(l[i + 1]); } });
          var W = 2, bx0 = Math.min.apply(null, xs) - W, bx1 = Math.max.apply(null, xs) + W, by0 = Math.min.apply(null, ys) - W, by1 = Math.max.apply(null, ys) + W;
          var ops = [L.pushGraphicsState(), L.setStrokingRgbColor(0.85, 0.1, 0.1), L.setLineWidth(W), L.setLineCap(1), L.setLineJoin(1)];
          lists.forEach(function (l) { ops.push(L.moveTo(l[0], l[1])); for (var i = 2; i < l.length; i += 2) ops.push(L.lineTo(l[i], l[i + 1])); ops.push(L.stroke()); });
          ops.push(L.popGraphicsState());
          var apI = ctx.formXObject(ops, { BBox: [bx0, by0, bx1, by1] });
          annot = ctx.obj({ Type: "Annot", Subtype: "Ink", Rect: [bx0, by0, bx1, by1], InkList: lists, BS: { W: W }, C: [0.85, 0.1, 0.1], F: 4, T: L.PDFHexString.fromText("Salow drawing"), AP: { N: ctx.register(apI) } });
        }
        var ref = ctx.register(annot);
        if (cur instanceof L.PDFArray) cur.push(ref); else page.node.set(N("Annots"), ctx.obj([ref]));
        done++;
      });
      return pdf.save({ useObjectStreams: false }).then(function (out) { if (out.length > E.MAX_BYTES) throw fail("size", "copy too large"); return { bytes: out, added: done, signed: signed }; });
    }, function (e) { if (e && e.code) throw e; throw fail(/encrypt/i.test(String(e && e.message)) ? "encrypted" : "damaged", "could not read"); });
  }
  var api = { MAX_MARKS: MAX_MARKS, MAX_STROKES: MAX_STROKES, MAX_POINTS: MAX_POINTS, validMark: validMark, addHighlight: addHighlight, addInk: addInk, remove: remove, forPage: forPage, build: build };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfMarks = api;
})(typeof self !== "undefined" ? self : this);
