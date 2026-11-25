const E = require("../../static/pdf/export.js"), P = require("../../static/pdf/vendor/pdf-lib.min.js"), crypto = require("crypto");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const near = (a, b) => Math.abs(a - b) < 1e-6;
const box = [10, 20, 200, 400];
ok("rotation 0: top-left of the shown page is the box top-left", (() => { const p = E.toPage(box, 0, 0, 0); return near(p[0], 10) && near(p[1], 420); })());
ok("rotation 90: shown top-left is the box bottom-left", (() => { const p = E.toPage(box, 90, 0, 0); return near(p[0], 10) && near(p[1], 20); })());
ok("rotation 180: shown top-left is the box bottom-right", (() => { const p = E.toPage(box, 180, 0, 0); return near(p[0], 210) && near(p[1], 20); })());
ok("rotation 270 and -90 agree: shown top-left is the box top-right", (() => { const a = E.toPage(box, 270, 0, 0), b = E.toPage(box, -90, 0, 0); return near(a[0], 210) && near(a[1], 420) && near(b[0], a[0]) && near(b[1], a[1]); })());
ok("shown centre is the box centre and opposite shown corners are opposite box corners, for every rotation", [0, 90, 180, 270].every((r) => { const c = E.toPage(box, r, 0.5, 0.5), a = E.toPage(box, r, 0, 0), b = E.toPage(box, r, 1, 1); const dx = Math.abs(b[0] - a[0]), dy = Math.abs(b[1] - a[1]); return near(c[0], 110) && near(c[1], 220) && near(dx, 200) && near(dy, 400); }));
ok("rect is clamped inside the box", (() => { const r = E.rectFor(box, 0, 0, 1); return r[0] >= 10 && r[1] >= 20 && r[2] <= 210 && r[3] <= 420; })());
ok("rect has the fixed size", (() => { const r = E.rectFor(box, 0, 0.5, 0.5); return near(r[2] - r[0], E.SIZE) && near(r[3] - r[1], E.SIZE); })());
const done = (async () => {
  const src = await P.PDFDocument.create(); const a = src.addPage([300, 400]); const b = src.addPage([300, 400]); b.setRotation(P.degrees(90)); const c = src.addPage([300, 400]); c.setCropBox(50, 60, 100, 120);
  const bytes = await src.save(); const before = crypto.createHash("sha256").update(bytes).digest("hex");
  const res = await E.build(P, bytes, [{ page: 1, x: 0.25, y: 0.5, text: "café note" }, { page: 2, x: 0, y: 0, text: "rotated" }, { page: 3, x: null, y: null, text: "page level" }, { page: 4, x: 0, y: 0, text: "no such page" }]);
  ok("input bytes are untouched", crypto.createHash("sha256").update(bytes).digest("hex") === before);
  ok("three notes are added, the one for a missing page is skipped", res.added === 3);
  const out = await P.PDFDocument.load(res.bytes);
  const annots = (i) => { const l = out.getPage(i).node.lookup(P.PDFName.of("Annots")); return l ? l.asArray().map((r) => out.context.lookup(r)) : []; };
  ok("each page carries its own annotation, with the right subtype and text", [0, 1, 2].every((i) => annots(i).length === 1) && annots(0)[0].lookup(P.PDFName.of("Contents")).decodeText() === "café note" && annots(1)[0].lookup(P.PDFName.of("Subtype")).toString() === "/Text");
  ok("pinned note is centred where it was placed", (() => { const r = annots(0)[0].lookup(P.PDFName.of("Rect")).asArray().map((x) => x.asNumber()); return near((r[0] + r[2]) / 2, 75) && near((r[1] + r[3]) / 2, 200); })());
  ok("a cropped page keeps its note inside the crop box", (() => { const r = annots(2)[0].lookup(P.PDFName.of("Rect")).asArray().map((x) => x.asNumber()); return r[0] >= 50 && r[1] >= 60 && r[2] <= 150 && r[3] <= 180; })());
  ok("every annotation has an appearance and the page count is unchanged", out.getPageCount() === 3 && [0, 1, 2].every((i) => annots(i)[0].lookup(P.PDFName.of("AP")) !== undefined));
  const again = await E.build(P, res.bytes, [{ page: 1, x: 0.5, y: 0.5, text: "second" }]);
  const o2 = await P.PDFDocument.load(again.bytes);
  ok("existing annotations are kept when more are added", o2.getPage(0).node.lookup(P.PDFName.of("Annots")).asArray().length === 2);
  let refused = false; try { await E.build(P, new Uint8Array([1, 2, 3]), [{ page: 1, x: 0, y: 0, text: "x" }]); } catch (e) { refused = true; }
  ok("damaged input is refused, not silently written", refused);
})();
done.then(() => { console.log(`${n - bad}/${n} pdf export rows passed`); process.exit(bad ? 1 : 0); }, (e) => { console.log("ERR", e.message); process.exit(1); });
