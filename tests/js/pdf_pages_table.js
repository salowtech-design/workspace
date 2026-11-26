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
  // Pages that are not kept must not travel inside the new file (links and actions can point at them).
  const lk = await (async () => { const d = await P.PDFDocument.create(); const mkp = (t) => { const pg = d.addPage([300, 300]); pg.node.set(P.PDFName.of("Contents"), d.context.register(d.context.stream("BT 12 0 0 12 10 100 Tm (" + t + ") Tj ET"))); return pg; }; const p1 = mkp("KEEPME"), p2 = mkp("SECRETXYZ");
    const l1 = d.context.obj({ Type: "Annot", Subtype: "Link", Rect: [10, 10, 100, 40], Dest: [p2.ref, "Fit"] }), l2 = d.context.obj({ Type: "Annot", Subtype: "Link", Rect: [10, 50, 100, 80], A: { S: "GoTo", D: [p2.ref, "Fit"] } }), l3 = d.context.obj({ Type: "Annot", Subtype: "Link", Rect: [10, 90, 100, 120], A: { S: "URI", URI: P.PDFString.of("https://example.com") } });
    p1.node.set(P.PDFName.of("Annots"), d.context.obj([d.context.register(l1), d.context.register(l2), d.context.register(l3)])); return d.save({ useObjectStreams: false }); })();
  ok("the source really holds the unkept page's text (so the next row can fail)", Buffer.from(lk).toString("latin1").includes("SECRETXYZ"));
  const lr = await G.build(P, [{ bytes: lk, pages: [1], name: "lk" }]), lt = Buffer.from(lr.bytes).toString("latin1");
  ok("a page kept alone does not drag in the page its links point at", !lt.includes("SECRETXYZ") && lt.includes("KEEPME") && (lt.match(/\/Type\s*\/Page[^s]/g) || []).length === 1);
  ok("web links are kept, links to pages and page actions are dropped", lt.includes("example.com") && !/\/GoTo/.test(lt));
  const lk2 = await (async () => { const d = await P.PDFDocument.load(lk); const p = d.getPages(); p[0].node.set(P.PDFName.of("Odd"), d.context.obj([p[1].ref])); return d.save({ useObjectStreams: false }); })();
  ok("a reference the page cleaner does not know about is caught by the page-count check and the copy is refused", (await code([{ bytes: lk2, pages: [1], name: "lk2" }])) === "leak");
  // The backstop alone: the same real Dest-leak source with the reference cut switched off (test-only copy of the module text) must be refused.
  const noCut = (() => { const src = require("fs").readFileSync(require("path").join(__dirname, "../../static/pdf/pages.js"), "utf8"), m = { exports: {} }, mod = src.replace("cutReferences(L, src, idx);", ""); if (mod === src) throw new Error("cut call not found"); new Function("module", "exports", mod)(m, m.exports); return m.exports; })();
  let nc = ""; try { await noCut.build(P, [{ bytes: lk, pages: [1], name: "lk" }]); nc = "built"; } catch (e) { nc = e.code; }
  ok("with the reference cut switched off, the real Dest-leak source is refused by the page-count check", nc === "leak");
  const rp = await G.build(P, [{ bytes: lk, pages: [1, 2, 1], name: "rep" }]);
  ok("a page repeated (1,2,1) builds 3 pages and the guard does not refuse it", rp.pageCount === 3 && (Buffer.from(rp.bytes).toString("latin1").match(/\/Type\s*\/Page[^s]/g) || []).length === 3);
  const t3 = await (async () => { const d = await P.PDFDocument.create(); for (let i = 1; i <= 3; i++) { const pg = d.addPage([200, 200]); pg.node.set(P.PDFName.of("Contents"), d.context.register(d.context.stream("BT 12 0 0 12 10 100 Tm (PG" + i + ") Tj ET"))); } return d.save({ useObjectStreams: false }); })();
  const rr3 = await G.build(P, [{ bytes: t3, pages: [3, 1, 3], name: "t3" }]);
  ok("3,1,3 on a 3-page file builds 3 pages with 3 page objects and the guard does not refuse", rr3.pageCount === 3 && (Buffer.from(rr3.bytes).toString("latin1").match(/\/Type\s*\/Page[^s]/g) || []).length === 3);
  // The worst order the list can make for 100 pages (swapped pairs, 291 characters, no runs) must parse.
  const worst = []; for (let i = 1; i <= 100; i += 2) worst.push(i + 1, i); const wtxt = worst.join(",");
  const wp = G.parseRanges(wtxt, 100);
  ok("the longest list-made text (swapped pairs of 100 pages, over 200 characters) parses to all 100 pages", wtxt.length > 200 && wtxt.length <= 400 && !wp.error && wp.pages.length === 100);
  ok("text beyond 400 characters is still refused", !!G.parseRanges("1,".repeat(201) + "1", 100).error);
  // Populated actions: scripts, chains and page-level actions must be gone from the copy, a plain web link must stay.
  const act = await (async () => { const d = await P.PDFDocument.create(), pg = d.addPage([300, 300]), x = d.context, R = (o) => x.register(x.obj(o));
    const js = { S: "JavaScript", JS: P.PDFString.of("app.alert(JSMARK1)") };
    const w = R({ Type: "Annot", Subtype: "Widget", FT: "Btn", Rect: [10, 10, 60, 30], A: js, AA: { E: js } });
    const chain = R({ Type: "Annot", Subtype: "Link", Rect: [10, 40, 60, 60], A: { S: "URI", URI: P.PDFString.of("https://example.com/a"), Next: { S: "JavaScript", JS: P.PDFString.of("app.alert(JSMARK2)") } } });
    const lnk = R({ Type: "Annot", Subtype: "Link", Rect: [10, 70, 60, 90], A: { S: "URI", URI: P.PDFString.of("https://example.com/b") } });
    const launch = R({ Type: "Annot", Subtype: "Link", Rect: [10, 100, 60, 120], A: { S: "Launch", F: P.PDFString.of("LAUNCHMARK") } });
    pg.node.set(P.PDFName.of("Annots"), x.obj([w, chain, lnk, launch])); pg.node.set(P.PDFName.of("AA"), x.obj({ O: js })); return d.save({ useObjectStreams: false }); })();
  const at = Buffer.from((await G.build(P, [{ bytes: act, pages: [1], name: "act" }])).bytes).toString("latin1");
  ok("the source holds the script and launch markers (so the next row can fail)", Buffer.from(act).toString("latin1").includes("JSMARK1") && Buffer.from(act).toString("latin1").includes("LAUNCHMARK"));
  ok("scripts, chained actions, launch actions and page actions are gone from the copy", !/JSMARK|LAUNCHMARK|\/JavaScript|\/Launch|\/AA/.test(at));
  ok("a plain web link stays, the web link carrying a chain is dropped", at.includes("example.com/b") && !at.includes("example.com/a"));
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
