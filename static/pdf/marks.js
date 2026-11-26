(function (root) {
  "use strict";
  // Area highlights and freehand pen strokes drawn over a page. They are kept in this browser on this device, per file (keyed by the file's SHA-256, like notes),
  // and become standard Highlight and Ink annotations in a separate downloaded copy. An area highlight is a rectangle the person drags.
  // A text-line highlight ("tx") is the whole text pieces (as pdf.js splits them, usually a line) that a dragged area touches, one box per piece. Neither
  // selects letters or copies text: there is no character range. Positions are fractions (0 to 1) of the page as shown, from the top-left.
  var MAX_FILES = 30, MAX_STORED = 400000, MAX_MARKS = 300, MAX_STROKES = 20, MAX_POINTS = 2000, MAX_TX = 200, KINDS = { hl: 1, ink: 1, tx: 1 };
  function unit(x) { return typeof x === "number" && x >= 0 && x <= 1; }
  function validMark(m) {
    if (!m || typeof m !== "object" || typeof m.id !== "string" || !/^[a-z0-9]{6,20}$/.test(m.id)) return false;
    if (!Number.isInteger(m.page) || m.page < 1 || m.page > 100000 || !KINDS[m.kind]) return false;
    if (m.kind === "tx") return Array.isArray(m.r) && m.r.length >= 1 && m.r.length <= MAX_TX && m.r.every(function (q) { return Array.isArray(q) && q.length === 4 && q.every(unit) && q[2] > q[0] && q[3] > q[1]; });
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
  // Text highlight: rects are the boxes of the text runs the person's dragged area touched (one box per run), as fractions of the page.
  function addText(marks, page, rects, rand) {
    if (marks.length >= MAX_MARKS) return { error: "This file already has " + MAX_MARKS + " marks. Remove one first." };
    var rs = (Array.isArray(rects) ? rects : []).map(function (q) { return Array.isArray(q) && q.length === 4 ? [clamp(q[0]), clamp(q[1]), clamp(q[2]), clamp(q[3])] : null; }).filter(function (q) { return q && q[2] - q[0] > 0.001 && q[3] - q[1] > 0.001; });
    if (!rs.length) return { error: "No text under that area. Drag over some text, or use Area highlight for any part of the page." };
    if (rs.length > MAX_TX) return { error: "That area covers too many pieces of text. Drag over less." };
    var m = { id: fresh(marks, rand), page: page, kind: "tx", r: rs };
    return validMark(m) ? { marks: marks.concat([m]) } : { error: "That highlight could not be added." };
  }
  function remove(marks, id) { return marks.filter(function (m) { return m.id !== id; }); }
  function forPage(marks, p) { return marks.filter(function (m) { return m.page === p; }); }
  // Local keeping, the same pattern as notes: a stored value is either exactly a valid list or unreadable (and then left alone, never replaced).
  function load(raw) {
    if (raw === null || raw === undefined) return { marks: [], unreadable: false };
    var v; try { v = JSON.parse(raw); } catch (e) { return { marks: [], unreadable: true }; }
    if (!v || v.v !== 1 || !Array.isArray(v.marks) || v.marks.length > MAX_MARKS) return { marks: [], unreadable: true };
    var seen = {};
    for (var i = 0; i < v.marks.length; i++) { var m = v.marks[i]; if (!validMark(m) || seen[m.id]) return { marks: [], unreadable: true }; seen[m.id] = true; }
    return { marks: v.marks, unreadable: false };
  }
  function dump(marks) { return JSON.stringify({ v: 1, marks: marks }); }
  // What a save may do. last is what this tab last saw stored, cur is what is stored now.
  function plan(last, cur, next, fileCount) {
    if (cur !== last) return "stale";
    if (!next.length) return cur === null ? "none" : "remove";
    if (cur === null && fileCount >= MAX_FILES) return "limit";
    if (dump(next).length > MAX_STORED) return "size";
    return "set";
  }
  // The guarded write. Returns {stale, raw}, {limit}, {size}, {err}, or {ok, raw}.
  function applySave(store, key, last, next, countFiles) {
    var cur; try { cur = store.getItem(key); } catch (e) { return { err: "read" }; }
    var act = plan(last, cur, next, cur === null ? countFiles() : 0);
    if (act === "stale") return { stale: true, raw: cur };
    if (act === "limit") return { limit: true };
    if (act === "size") return { size: true };
    try { if (act === "remove") store.removeItem(key); else if (act === "set") store.setItem(key, dump(next)); } catch (e) { return { err: "write" }; }
    return { ok: true, raw: act === "set" ? dump(next) : null };
  }
  function fail(code, msg) { var e = new Error(msg); e.code = code; return e; }
  // A copy of the file with the marks as annotations. E is PdfNotesExport (for the page-box and rotation maths and the signed check).
  function build(L, E, bytes, marks, opt) {
    var flat = !!(opt && opt.flat);
    // pdf-lib drops XFA data when it saves; a copy that silently lost it would differ from the file, so such files are refused (the same rule as filled copies).
    return L.PDFDocument.load(bytes, { ignoreEncryption: false, updateMetadata: false, preserveXFA: true }).then(function (pdf) {
      try { var af = pdf.catalog.lookup(L.PDFName.of("AcroForm")); if (af instanceof L.PDFDict && af.get(L.PDFName.of("XFA")) !== undefined) throw fail("xfa", "xfa form"); } catch (e) { if (e && e.code === "xfa") throw e; }
      var ctx = pdf.context, count = pdf.getPageCount(), signed = E.looksSigned(L, pdf, bytes), done = 0, N = L.PDFName.of, targets = [];
      marks.forEach(function (m) {
        if (!validMark(m)) throw fail("mark", "invalid mark");
        if (m.page > count) return;
        var page = pdf.getPage(m.page - 1), cur = page.node.lookup(N("Annots"));
        if (cur !== undefined && !(cur instanceof L.PDFArray)) throw fail("annots", "annotations list is not an array");
        targets.push([m, page]);
      });
      // Flat copy: a signed file is refused (a rewrite would break the signature and flattening adds to it). Existing annotations and form fields are left exactly as they are.
      if (flat && signed) throw fail("signed", "signed file");
      var gs = ctx.obj({ Type: "ExtGState", BM: "Multiply" });
      targets.forEach(function (t) {
        var m = t[0], page = t[1], box = E.shownBox(page), rot = page.getRotation().angle, annot, cur = page.node.lookup(N("Annots"));
        var at = function (u, v) { return E.toPage(box, rot, u, v); };
        if (m.kind === "tx") {
          var bs = m.r.map(function (q) { var a = at(q[0], q[1]), b = at(q[2], q[3]); return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]; });
          var ux0 = Math.min.apply(null, bs.map(function (b) { return b[0]; })), uy0 = Math.min.apply(null, bs.map(function (b) { return b[1]; })), ux1 = Math.max.apply(null, bs.map(function (b) { return b[2]; })), uy1 = Math.max.apply(null, bs.map(function (b) { return b[3]; }));
          var tops = [L.pushGraphicsState(), L.setGraphicsState(N("GS")), L.setFillingRgbColor(1, 0.92, 0.2)], quads = [];
          bs.forEach(function (b) { tops.push(L.moveTo(b[0], b[1]), L.lineTo(b[2], b[1]), L.lineTo(b[2], b[3]), L.lineTo(b[0], b[3]), L.closePath(), L.fill()); quads.push(b[0], b[3], b[2], b[3], b[0], b[1], b[2], b[1]); });
          tops.push(L.popGraphicsState());
          var apT = ctx.formXObject(tops, { BBox: [ux0, uy0, ux1, uy1], Resources: { ExtGState: { GS: gs } } });
          annot = ctx.obj({ Type: "Annot", Subtype: "Highlight", Rect: [ux0, uy0, ux1, uy1], QuadPoints: quads, C: [1, 0.92, 0.2], F: 4, T: L.PDFHexString.fromText("Salow text highlight"), AP: { N: ctx.register(apT) } });
        } else if (m.kind === "hl") {
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
        if (flat) {
          // The mark's appearance is drawn into the page content (its box is in page space, so it needs no matrix) and no annotation is added.
          var apRef = annot.get(N("AP")).get(N("N")), xn = page.node.newXObject("SalowFlat" + done, apRef);
          page.pushOperators(L.pushGraphicsState(), L.drawObject(xn), L.popGraphicsState());
          done++; return;
        }
        var ref = ctx.register(annot);
        if (cur instanceof L.PDFArray) cur.push(ref); else page.node.set(N("Annots"), ctx.obj([ref]));
        done++;
      });
      return pdf.save({ useObjectStreams: false }).then(function (out) { if (out.length > E.MAX_BYTES) throw fail("size", "copy too large"); return { bytes: out, added: done, signed: signed }; });
    }, function (e) { if (e && e.code) throw e; throw fail(/encrypt/i.test(String(e && e.message)) ? "encrypted" : "damaged", "could not read"); });
  }
  var api = { MAX_FILES: MAX_FILES, MAX_STORED: MAX_STORED, load: load, dump: dump, plan: plan, applySave: applySave, MAX_MARKS: MAX_MARKS, MAX_STROKES: MAX_STROKES, MAX_POINTS: MAX_POINTS, validMark: validMark, addHighlight: addHighlight, addText: addText, MAX_TX: MAX_TX, addInk: addInk, remove: remove, forPage: forPage, build: build };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfMarks = api;
})(typeof self !== "undefined" ? self : this);
