const G = require("../../static/pdf/pages.js"), E = require("../../static/pdf/export.js"), P = require("../../static/pdf/vendor/pdf-lib.min.js"), crypto = require("crypto");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
async function mk(count, sizeOf) { const d = await P.PDFDocument.create(); const f = await d.embedFont(P.StandardFonts.Helvetica); for (let i = 1; i <= count; i++) { const pg = d.addPage(sizeOf ? sizeOf(i) : [300, 400]); pg.drawText("P" + i, { x: 20, y: 300, size: 40, font: f }); } return d.save({ useObjectStreams: false }); }
const pageTexts = async (b) => { const d = await P.PDFDocument.load(b); return d.getPages().map((p) => p.getSize().width + "x" + p.getSize().height + "r" + p.getRotation().angle); };
const code = async (parts) => { try { await G.build(P, parts); return "none"; } catch (e) { return e.code || "other:" + e.message; } };
const done = (async () => {
  ok("ranges: single, list, range, open end", eq(G.parseRanges("1-3, 5,7-", 8).pages, [1, 2, 3, 5, 7, 8]));
  ok("ranges keep the order typed, including repeats", eq(G.parseRanges("3,1,3", 5).pages, [3, 1, 3]));
  ok("ranges: empty, text, zero, backwards, out of range and over-long are refused with a reason", ["", "a", "0", "3-1", "9", "1-9", "1,,2", "-2", "1-2-3"].every((t) => G.parseRanges(t, 5).error) && G.parseRanges("1-1000000", 5).error);
  ok("ranges: a list that would pass the page limit is refused", G.parseRanges("1-2000", 3000).error && G.parseRanges("1-1000", 3000).pages.length === 1000);
  const a = await mk(6), b = await mk(2, () => [500, 200]), ha = sha(a), hb = sha(b);
  const r1 = await G.build(P, [{ bytes: a, pages: [2, 4, 6], name: "a" }]);
  const d1 = await P.PDFDocument.load(r1.bytes);
  ok("split keeps exactly the chosen pages in order", r1.pageCount === 3 && d1.getPageCount() === 3);
  ok("inputs are untouched", sha(a) === ha && sha(b) === hb);
  const r2 = await G.build(P, [{ bytes: a, pages: null, name: "a" }, { bytes: b, pages: null, name: "b" }]);
  ok("merge puts the second file after the first and keeps page sizes", r2.pageCount === 8 && eq((await pageTexts(r2.bytes)).slice(5), ["300x400r0", "500x200r0", "500x200r0"]));
  const sz = await mk(5, (i) => [100 + i * 10, 400]);
  const r3 = await G.build(P, [{ bytes: sz, pages: [3, 1, 3], name: "sz" }]);
  ok("pages come out in the order asked, repeats included, by their own sizes", eq(await pageTexts(r3.bytes), ["130x400r0", "110x400r0", "130x400r0"]));
  // inherited page attributes and rotation survive
  const inh = await (async () => { const d = await P.PDFDocument.create(); const pg = d.addPage([300, 400]); pg.setRotation(P.degrees(90)); const pages = d.catalog.lookup(P.PDFName.of("Pages")); pages.set(P.PDFName.of("MediaBox"), d.context.obj([0, 0, 612, 792])); pg.node.delete(P.PDFName.of("MediaBox")); return d.save({ useObjectStreams: false }); })();
  const r4 = await G.build(P, [{ bytes: inh, pages: [1], name: "inh" }]);
  ok("an inherited page size and a rotation are kept in the new file", eq(await pageTexts(r4.bytes), ["612x792r90"]));
  ok("an out of range page is refused", (await code([{ bytes: a, pages: [7], name: "a" }])) === "range");
  ok("damaged input is refused and names the file", (await (async () => { try { await G.build(P, [{ bytes: a, pages: null, name: "a" }, { bytes: new Uint8Array([1, 2, 3]), pages: null, name: "bad.pdf" }]); return ""; } catch (e) { return e.code + ":" + e.name2; } })()) === "damaged:bad.pdf");
  ok("no parts and too many parts are refused", (await code([])) === "files" && (await code(Array.from({ length: G.MAX_FILES + 1 }, () => ({ bytes: b, pages: null, name: "b" })))) === "files");
  const big = await (async () => { const d = await P.PDFDocument.create(); d.addPage([100, 100]); d.context.register(d.context.stream(crypto.randomBytes(G.MAX_BYTES + 100))); return d.save({ useObjectStreams: false }); })();
  ok("inputs over the size limit are refused", (await code([{ bytes: big, pages: null, name: "big" }])) === "size");
  // form pages: the definition is not carried, and that is the stated limit
  const fm = await (async () => { const d = await P.PDFDocument.create(), pg = d.addPage([300, 300]); const t = d.getForm().createTextField("x"); t.setText("val"); t.addToPage(pg, { x: 10, y: 10, width: 100, height: 20 }); return d.save({ useObjectStreams: false }); })();
  const r5 = await G.build(P, [{ bytes: fm, pages: null, name: "fm" }]);
  const d5 = await P.PDFDocument.load(r5.bytes);
  ok("a new file from a form page has no form definition (stated limit)", d5.getForm().getFields().length === 0 && d5.getPageCount() === 1);
  ok("build names a signed input so the page can warn", await (async () => { const d = await P.PDFDocument.create(); d.addPage([100, 100]); const sig = d.context.obj({ Type: "Sig", Filter: "Adobe.PPKLite" }); const f = d.context.obj({ FT: "Sig", T: P.PDFHexString.fromText("s"), V: d.context.register(sig), Kids: [] }); d.catalog.set(P.PDFName.of("AcroForm"), d.context.obj({ Fields: [d.context.register(f)], SigFlags: 3 })); const bytes = await d.save({ useObjectStreams: false }); const l = await P.PDFDocument.load(bytes); const r = await G.build(P, [{ bytes: a, pages: [1], name: "plain" }, { bytes, pages: null, name: "signed.pdf" }], E.looksSigned); return eq(r.signed, ["signed.pdf"]); })());
  console.log(`${n - bad}/${n} pdf pages rows passed`);
  process.exit(bad ? 1 : 0);
})();
done.catch((e) => { console.log("CRASH", e); process.exit(1); });
