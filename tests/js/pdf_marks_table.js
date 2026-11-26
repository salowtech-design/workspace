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

  const rotated = await pdfWith((dd, pg) => pg.setRotation(P.degrees(90)));
  const rr = await M.build(P, E, rotated, M.addHighlight([], 1, 0, 0, 0.5, 0.25, rnd).marks);
  const dr = await P.PDFDocument.load(rr.bytes), rrect = dr.getPage(0).node.lookup(P.PDFName.of("Annots")).lookup(0).lookup(P.PDFName.of("Rect")).asArray().map((x) => x.asNumber());
  ok("on a page rotated 90 degrees the display's top-left quarter-wide band maps like toPage does", (() => { const a = E.toPage([0, 0, 400, 600], 90, 0, 0), b = E.toPage([0, 0, 400, 600], 90, 0.5, 0.25); return Math.abs(rrect[0] - Math.min(a[0], b[0])) < 1e-6 && Math.abs(rrect[2] - Math.max(a[0], b[0])) < 1e-6 && Math.abs(rrect[1] - Math.min(a[1], b[1])) < 1e-6 && Math.abs(rrect[3] - Math.max(a[1], b[1])) < 1e-6; })());
  ok("a rotated highlight is not the same rectangle as the unrotated one (so the rotation row can fail)", Math.abs(rrect[2] - rrect[0] - 200) > 1);
  ok("marks for a page the file does not have are skipped", (await M.build(P, E, src, [{ id: "abcdefgh", page: 9, kind: "hl", r: [0.1, 0.1, 0.2, 0.2] }])).added === 0);
  ok("an invalid mark makes the copy refuse", (await code(() => M.build(P, E, src, [{ id: "x", page: 1, kind: "hl", r: [0, 0, 1, 1] }]))) === "mark");
  const odd = await pdfWith((dd, pg) => pg.node.set(P.PDFName.of("Annots"), dd.context.obj({ A: 1 })));
  ok("an annotation list that is not an array stops the copy", (await code(() => M.build(P, E, odd, m))) === "annots");
  ok("garbage bytes are reported as damaged", (await code(() => M.build(P, E, Buffer.from("nope"), m))) === "damaged");
  const withAnnot = await pdfWith((dd, pg) => pg.node.set(P.PDFName.of("Annots"), dd.context.obj([dd.context.register(dd.context.obj({ Type: "Annot", Subtype: "Text", Rect: [1, 1, 5, 5] }))])));
  const wa = await P.PDFDocument.load((await M.build(P, E, withAnnot, m)).bytes);
  ok("existing annotations are kept and the new ones follow them", wa.getPage(0).node.lookup(P.PDFName.of("Annots")).size() === 3);
  const sg = fs.readFileSync(__dirname + "/fixtures/form_signed.pdf");
  ok("a signed input is reported as signed", (await M.build(P, E, sg, [])).signed === true && r.signed === false);
  console.log((n - bad) + "/" + n + " pdf marks rows passed");
})();
done.catch((e) => { console.log("ERROR " + e.stack); process.exit(1); });
