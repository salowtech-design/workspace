const M = require("../../static/pdf/marks.js"), E = require("../../static/pdf/export.js"), P = require("../../static/pdf/vendor/pdf-lib.min.js"), crypto = require("crypto"), fs = require("fs");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const code = async (f) => { try { await f(); return "none"; } catch (e) { return e.code || "other"; } };
async function pdfWith(setup) { const d = await P.PDFDocument.create(), pg = d.addPage([400, 600]); if (setup) setup(d, pg); return d.save({ useObjectStreams: false }); }
const rnd = (() => { let s = 7; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
const done = (async () => {
  let m = [];
  ok("a highlight with corners in any order is kept in order", (() => { const r = M.addHighlight(m, 1, 0.5, 0.6, 0.1, 0.2, rnd); return r.marks && r.marks[0].r.join() === "0.1,0.2,0.5,0.6"; })());
  ok("a sliver of a highlight is refused", !!M.addHighlight(m, 1, 0.1, 0.1, 0.1, 0.5, rnd).error);
  ok("out-of-range and NaN corners are clamped, never stored raw", (() => { const r = M.addHighlight(m, 1, -4, NaN, 9, 0.5, rnd); return r.marks && r.marks[0].r.every((x) => x >= 0 && x <= 1); })());
  ok("a stroke with one point is dropped and nothing is added", !!M.addInk(m, 1, [[[0.1, 0.1]]], rnd).error);
  ok("an ink of more than the point cap is refused", !!M.addInk(m, 1, [Array.from({ length: 2001 }, (_, i) => [0.5, (i % 100) / 100])], rnd).error);
  ok("more than the stroke cap is refused", !!M.addInk(m, 1, Array.from({ length: 21 }, () => [[0.1, 0.1], [0.2, 0.2]]), rnd).error);
  ok("validMark rejects wrong kind, bad id, missing fields, out-of-range", ![{}, null, { id: "abcdef", page: 1, kind: "x" }, { id: "ab", page: 1, kind: "hl", r: [0, 0, 1, 1] }, { id: "abcdef", page: 1, kind: "hl", r: [0, 0, 2, 1] }, { id: "abcdef", page: 0, kind: "hl", r: [0, 0, 1, 1] }, { id: "abcdef", page: 1, kind: "ink", s: [[[0, 0], [NaN, 1]]] }].some(M.validMark));
  let many = []; for (let i = 0; i < M.MAX_MARKS; i++) many = M.addHighlight(many, 1, 0.1, 0.1, 0.3, 0.3, rnd).marks;
  ok("the mark cap stops further adds", !!M.addHighlight(many, 1, 0.1, 0.1, 0.3, 0.3, rnd).error && many.length === M.MAX_MARKS);
  ok("ids are unique across marks", new Set(many.map((x) => x.id)).size === many.length);

  const src = await pdfWith(), before = sha(src);
  m = M.addHighlight([], 1, 0.25, 0.1, 0.75, 0.2, rnd).marks;
  m = M.addInk(m, 1, [[[0.1, 0.5], [0.2, 0.55], [0.3, 0.5]]], rnd).marks;
  const r = await M.build(P, E, src, m);
  ok("the input bytes are unchanged", sha(src) === before);
  const d = await P.PDFDocument.load(r.bytes), an = d.getPage(0).node.lookup(P.PDFName.of("Annots"));
  const subs = []; for (let i = 0; i < an.size(); i++) subs.push(String(an.lookup(i).lookup(P.PDFName.of("Subtype"))));
  ok("a Highlight and an Ink annotation are added and counted", r.added === 2 && subs.join() === "/Highlight,/Ink");
  const hl = an.lookup(0), rect = hl.lookup(P.PDFName.of("Rect")).asArray().map((x) => x.asNumber());
  ok("an unrotated highlight lands at the right page coordinates (400x600 page: x 100..300, y 480..540)", Math.abs(rect[0] - 100) < 1e-6 && Math.abs(rect[2] - 300) < 1e-6 && Math.abs(rect[1] - 480) < 1e-6 && Math.abs(rect[3] - 540) < 1e-6);
  ok("the highlight has four quad points inside its rect and a Multiply appearance", hl.lookup(P.PDFName.of("QuadPoints")).size() === 8 && /Multiply/.test(Buffer.from(r.bytes).toString("latin1")) && !!hl.lookup(P.PDFName.of("AP")));
  const ink = an.lookup(1), il = ink.lookup(P.PDFName.of("InkList"));
  ok("the ink keeps one stroke of three points in page coordinates", il.size() === 1 && il.lookup(0).size() === 6 && Math.abs(il.lookup(0).lookup(0).asNumber() - 40) < 1e-6 && Math.abs(il.lookup(0).lookup(1).asNumber() - 300) < 1e-6);

  // Hand-worked expected rectangles (not computed with toPage): a 400x600 page, a highlight over the top-left quarter-width, quarter-height of the display.
  const hr = async (setup, r) => { const f = await pdfWith(setup), o = await M.build(P, E, f, M.addHighlight([], 1, r[0], r[1], r[2], r[3], rnd).marks), dd = await P.PDFDocument.load(o.bytes); return dd.getPage(0).node.lookup(P.PDFName.of("Annots")).lookup(0).lookup(P.PDFName.of("Rect")).asArray().map((q) => q.asNumber()); };
  const near = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 1e-6);
  ok("rotation 90: display top-left (u 0..0.5, v 0..0.25) is x 0..100, y 0..300 on a 400x600 page", near(await hr((dd, pg) => pg.setRotation(P.degrees(90)), [0, 0, 0.5, 0.25]), [0, 0, 100, 300]));
  ok("rotation 180: x 200..400, y 0..150", near(await hr((dd, pg) => pg.setRotation(P.degrees(180)), [0, 0, 0.5, 0.25]), [200, 0, 400, 150]));
  ok("rotation 270: x 300..400, y 300..600", near(await hr((dd, pg) => pg.setRotation(P.degrees(270)), [0, 0, 0.5, 0.25]), [300, 300, 400, 600]));
  ok("a crop box with a non-zero origin: crop [50,100,300,400], top-left quarter is x 50..200, y 300..500", near(await hr((dd, pg) => pg.setCropBox(50, 100, 300, 400), [0, 0, 0.5, 0.5]), [50, 300, 200, 500]));
  const rotated = await pdfWith((dd, pg) => pg.setRotation(P.degrees(90)));
  const rr = await M.build(P, E, rotated, M.addHighlight([], 1, 0, 0, 0.5, 0.25, rnd).marks);
  const dr = await P.PDFDocument.load(rr.bytes), rrect = dr.getPage(0).node.lookup(P.PDFName.of("Annots")).lookup(0).lookup(P.PDFName.of("Rect")).asArray().map((x) => x.asNumber());
  ok("on a page rotated 90 degrees the display's top-left quarter-wide band maps like toPage does", (() => { const a = E.toPage([0, 0, 400, 600], 90, 0, 0), b = E.toPage([0, 0, 400, 600], 90, 0.5, 0.25); return Math.abs(rrect[0] - Math.min(a[0], b[0])) < 1e-6 && Math.abs(rrect[2] - Math.max(a[0], b[0])) < 1e-6 && Math.abs(rrect[1] - Math.min(a[1], b[1])) < 1e-6 && Math.abs(rrect[3] - Math.max(a[1], b[1])) < 1e-6; })());
  ok("a rotated highlight is not the same rectangle as the unrotated one (so the rotation row can fail)", Math.abs(rrect[2] - rrect[0] - 200) > 1);
  ok("marks for a page the file does not have are skipped", (await M.build(P, E, src, [{ id: "abcdefgh", page: 9, kind: "hl", r: [0.1, 0.1, 0.2, 0.2] }])).added === 0);
  ok("an invalid mark makes the copy refuse", (await code(() => M.build(P, E, src, [{ id: "x", page: 1, kind: "hl", r: [0, 0, 1, 1] }]))) === "mark");
  // Text highlight (rects of text runs).
  ok("a text highlight keeps one rect per run and drops slivers", (() => { const r = M.addText([], 1, [[0.1, 0.1, 0.4, 0.15], [0.1, 0.2, 0.3, 0.25], [0.5, 0.5, 0.5, 0.6]], rnd); return r.marks && r.marks[0].kind === "tx" && r.marks[0].r.length === 2; })());
  ok("a text highlight with nothing under the area is refused with a plain message", /No text under that area/.test(M.addText([], 1, [], rnd).error || "") && !!M.addText([], 1, [[0.1, 0.1, 0.1, 0.1]], rnd).error);
  ok("a text highlight over the rect cap is refused", !!M.addText([], 1, Array.from({ length: M.MAX_TX + 1 }, (_, i) => [0.1, (i % 90) / 100, 0.3, (i % 90) / 100 + 0.005]), rnd).error);
  ok("validMark rejects a text highlight with a bad rect or no rects", ![{ id: "abcdef", page: 1, kind: "tx", r: [] }, { id: "abcdef", page: 1, kind: "tx", r: [[0.3, 0.1, 0.2, 0.2]] }, { id: "abcdef", page: 1, kind: "tx", r: [[0, 0, 2, 1]] }, { id: "abcdef", page: 1, kind: "tx", r: "x" }].some(M.validMark));
  const txm = M.addText([], 1, [[0.1, 0.1, 0.5, 0.2], [0.1, 0.25, 0.4, 0.35]], rnd).marks;
  ok("a text highlight survives the stored round trip", JSON.stringify(M.load(M.dump(txm)).marks) === JSON.stringify(txm));
  const txPdf = await M.build(P, E, await pdfWith(), txm);
  const txDoc = await P.PDFDocument.load(txPdf.bytes), txA = txDoc.getPage(0).node.lookup(P.PDFName.of("Annots")).lookup(0);
  ok("a text highlight becomes one Highlight annotation with one quad per run", txPdf.added === 1 && String(txA.get(P.PDFName.of("Subtype"))) === "/Highlight" && txA.get(P.PDFName.of("QuadPoints")).size() === 16);
  // Keeping marks on the device (pure parts).
  const st = () => { const o = {}; return { o, getItem: (k) => (k in o ? o[k] : null), setItem: (k, v) => { o[k] = v; }, removeItem: (k) => { delete o[k]; } }; };
  const two = M.addInk(M.addHighlight([], 1, 0.1, 0.1, 0.4, 0.4, rnd).marks, 2, [[[0.1, 0.1], [0.5, 0.5]]], rnd).marks;
  ok("a saved list loads back exactly", JSON.stringify(M.load(M.dump(two)).marks) === JSON.stringify(two) && !M.load(M.dump(two)).unreadable);
  ok("nothing stored loads as an empty, readable list", (() => { const l = M.load(null); return !l.unreadable && l.marks.length === 0; })());
  ok("garbage, wrong version, a bad mark and duplicate ids are unreadable", ["not json", "{}", JSON.stringify({ v: 2, marks: [] }), JSON.stringify({ v: 1, marks: [{ id: "abcdef", page: 1, kind: "x" }] }), JSON.stringify({ v: 1, marks: [two[0], two[0]] })].every((r) => M.load(r).unreadable));
  const s1 = st(); const r1 = M.applySave(s1, "k", null, two, () => 0);
  ok("a first save writes the list and reports what it wrote", r1.ok && s1.o.k === M.dump(two) && r1.raw === s1.o.k);
  ok("a save against a baseline that is no longer stored is refused as stale, and writes nothing", (() => { const s = st(); s.o.k = M.dump(two); const r = M.applySave(s, "k", null, [two[0]], () => 0); return r.stale === true && s.o.k === M.dump(two); })());
  ok("saving an empty list removes the key; with nothing stored it does nothing", (() => { const s = st(); M.applySave(s, "k", null, two, () => 0); const r = M.applySave(s, "k", s.o.k, [], () => 0); return r.ok && !("k" in s.o) && M.applySave(st(), "k", null, [], () => 0).ok; })());
  ok("a new file is refused at the file limit, an existing one still saves", M.applySave(st(), "k", null, two, () => M.MAX_FILES).limit === true && (() => { const s = st(); s.o.k = M.dump(two); return M.applySave(s, "k", s.o.k, [two[0]], () => M.MAX_FILES).ok === true; })());
  ok("a list over the stored size cap is refused and the old value stays", (() => { const s = st(); s.o.k = M.dump(two); let big = []; const pts = Array.from({ length: 2000 }, (_, i) => [Math.random(), Math.random()]); for (let i = 0; i < 12; i++) big.push({ id: "big" + String(i).padStart(4, "0"), page: 1, kind: "ink", s: [pts] }); const r = M.applySave(s, "k", s.o.k, big, () => 0); return r.size === true && s.o.k === M.dump(two) && big.every(M.validMark); })());
  ok("a browser that throws on write reports an error and keeps the old value", (() => { const s = st(); s.o.k = M.dump(two); s.setItem = () => { throw new Error("q"); }; const r = M.applySave(s, "k", s.o.k, [two[0]], () => 0); return r.err === "write" && s.o.k === M.dump(two); })());
  ok("a browser that throws on read reports an error", M.applySave({ getItem() { throw new Error("x"); } }, "k", null, two, () => 0).err === "read");

  const odd = await pdfWith((dd, pg) => pg.node.set(P.PDFName.of("Annots"), dd.context.obj({ A: 1 })));
  ok("an annotation list that is not an array stops the copy", (await code(() => M.build(P, E, odd, m))) === "annots");
  ok("garbage bytes are reported as damaged", (await code(() => M.build(P, E, Buffer.from("nope"), m))) === "damaged");
  const withAnnot = await pdfWith((dd, pg) => pg.node.set(P.PDFName.of("Annots"), dd.context.obj([dd.context.register(dd.context.obj({ Type: "Annot", Subtype: "Text", Rect: [1, 1, 5, 5] }))])));
  const wa = await P.PDFDocument.load((await M.build(P, E, withAnnot, m)).bytes);
  ok("existing annotations are kept and the new ones follow them", wa.getPage(0).node.lookup(P.PDFName.of("Annots")).size() === 3);
  const sg = fs.readFileSync(__dirname + "/fixtures/form_signed.pdf");
  // Flattened copy: marks drawn into the page, no annotation added.
  const flatMarks = M.addInk(M.addText(M.addHighlight([], 1, 0.1, 0.1, 0.4, 0.2, rnd).marks, 1, [[0.1, 0.3, 0.5, 0.4]], rnd).marks, 1, [[[0.1, 0.5], [0.3, 0.6]]], rnd).marks;
  const flat = await M.build(P, E, await pdfWith(), flatMarks, { flat: true });
  const flatDoc = await P.PDFDocument.load(flat.bytes), flatPg = flatDoc.getPage(0);
  const flatXo = flatPg.node.Resources().lookup(P.PDFName.of("XObject"));
  const flatContent = Buffer.from(flat.bytes).toString("latin1");
  ok("a flat copy adds no annotation and draws each mark into the page as a form object", flat.added === 3 && (() => { const an = flatPg.node.lookup(P.PDFName.of("Annots")); return an === undefined || an.size() === 0; })() && !/\/Subtype\s*\/(Highlight|Ink)/.test(flatContent) && flatXo.keys().filter((k) => /^\/SalowFlat/.test(String(k))).length === 3);
  // Retention in the flat copy, on a file with a text field (with a value), an existing annotation and drawn text. Same field names and value, same annotation count, same pdftotext output.
  {
    const cp = require("child_process"), src = await P.PDFDocument.create(), pgS = src.addPage([400, 600]), font = await src.embedFont(P.StandardFonts.Helvetica);
    pgS.drawText("Selectable sample line", { x: 40, y: 500, size: 18, font });
    const tf = src.getForm().createTextField("name"); tf.setText("Ada"); tf.addToPage(pgS, { x: 40, y: 400, width: 150, height: 24 });
    const ex = src.context.obj({ Type: "Annot", Subtype: "Text", Rect: [300, 500, 320, 520], Contents: P.PDFHexString.fromText("keep me"), F: 4 });
    pgS.node.set(P.PDFName.of("Annots"), src.context.obj([...pgS.node.lookup(P.PDFName.of("Annots")).asArray(), src.context.register(ex)]));
    const srcBytes = await src.save({ useObjectStreams: false });
    const fl = await M.build(P, E, srcBytes, M.addHighlight([], 1, 0.1, 0.1, 0.4, 0.2, rnd).marks, { flat: true });
    const o = await P.PDFDocument.load(srcBytes), f = await P.PDFDocument.load(fl.bytes);
    const info = (dd) => ({ fields: dd.getForm().getFields().map((x) => x.getName() + "=" + x.getText()).join("|"), annots: dd.getPage(0).node.lookup(P.PDFName.of("Annots")).size(), types: dd.getPage(0).node.lookup(P.PDFName.of("Annots")).asArray().map((r) => String(dd.context.lookup(r).get(P.PDFName.of("Subtype")))).join(",") });
    const tx = (b) => cp.execFileSync("pdftotext", ["-", "-"], { input: Buffer.from(b) }).toString();
    ok("a flat copy keeps the field names and value, the existing annotations (same count and kinds) and the page text (pdftotext equal); not byte-identical", JSON.stringify(info(o)) === JSON.stringify(info(f)) && info(o).fields === "name=Ada" && tx(srcBytes).includes("Selectable sample line") && tx(srcBytes) === tx(fl.bytes) && fl.added === 1);
  }
  ok("a flat copy of a signed input is refused with the signed code", await M.build(P, E, sg, flatMarks, { flat: true }).then(() => false, (e) => e.code === "signed"));
  ok("the same marks on a signed input without the flat option still build (held by the viewer)", (await M.build(P, E, sg, flatMarks)).signed === true);
  ok("a signed input is reported as signed", (await M.build(P, E, sg, [])).signed === true && r.signed === false);
  ok("an XFA form is refused, not copied without its XFA data", (await code(() => M.build(P, E, fs.readFileSync(__dirname + "/fixtures/form_xfa.pdf"), m))) === "xfa");
{
  const sg = [[[0, 0.5], [0.5, 1], [1, 0]]], m0 = [];
  const r = M.addSignature(m0, 2, sg, 3, 0.5, 0.5, 0.75, 0.22, rnd);
  ok("signature: placed as one ink mark on the page, centred, inside the page", !r.error && r.marks.length === 1 && r.marks[0].kind === "ink" && r.marks[0].page === 2 && r.marks[0].s[0].length === 3 && r.marks[0].s[0].every((q) => q[0] >= 0.39 && q[0] <= 0.61 && q[1] >= 0 && q[1] <= 1) && M.validMark(r.marks[0]));
  ok("signature: keeps the pad's shape on the page (width 0.22 of the page, height = 0.22 * pageAspect / padAspect)", (() => { const s0 = r.marks[0].s[0], w = Math.abs(s0[2][0] - s0[0][0]), h = Math.abs(s0[1][1] - s0[0][1]) * 2; return Math.abs(w - 0.22) < 1e-9 && Math.abs(h - 0.22 * 0.75 / 3) < 1e-9; })());
  ok("signature: near a corner it is moved to stay on the page", (() => { const q = M.addSignature([], 1, sg, 3, 0.99, 0.99, 0.75, 0.22, rnd).marks[0].s[0]; return q.every((p) => p[0] <= 1 && p[1] <= 1) && Math.max(...q.map((p) => p[0])) === 1; })());
  ok("signature: a bad size, shape or page ratio is refused, and an empty signature is refused", !!M.addSignature([], 1, sg, 3, 0.5, 0.5, 0.75, 0, rnd).error && !!M.addSignature([], 1, sg, 0, 0.5, 0.5, 0.75, 0.2, rnd).error && !!M.addSignature([], 1, [], 3, 0.5, 0.5, 0.75, 0.2, rnd).error);
  ok("signature: too tall for the page at that size is refused", !!M.addSignature([], 1, sg, 0.25, 0.5, 0.5, 5, 0.9, rnd).error);
  ok("signature: the mark cap still applies", !!M.addSignature(Array.from({ length: 300 }, (_, k) => ({ id: "abcdef" + String(k).padStart(4, "0"), page: 1, kind: "hl", r: [0.1, 0.1, 0.2, 0.2] })), 1, sg, 3, 0.5, 0.5, 0.75, 0.22, rnd).error);
}
  console.log((n - bad) + "/" + n + " pdf marks rows passed");
})();
done.catch((e) => { console.log("ERROR " + e.stack); process.exit(1); });
