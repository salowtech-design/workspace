// Node rows for the Slides PDF builder. Run: node tests/js/slides_pdf.js
const path = require("path"), L = require(path.join(__dirname, "../../static/pdf/vendor/pdf-lib.min.js"));
const S = require("../../static/slides/model.js"), P = require("../../static/slides/pdf.js");
let n = 0, fails = 0;
function ok(name, cond) { n++; if (!cond) { fails++; console.log("FAIL " + name); } }
const el = (o) => Object.assign({ fill: "theme", text: "", size: 24, x: 100, y: 100, w: 300, h: 80 }, o);
const deck = (slides, theme) => ({ v: 1, theme: theme || "light", slides });
const sl = (id, els, notes) => ({ id, elements: els, notes: notes || "" });
(async () => {
  const d = deck([sl("s1", [el({ id: "a", type: "text", text: "Hello world" }), el({ id: "b", type: "rect", y: 250 }), el({ id: "c", type: "ellipse", x: 500, y: 250, text: "Round" })], "SECRET-NOTE"), sl("s2", [])], "dark");
  const bytes = await P.build(L, S, d);
  const doc = await L.PDFDocument.load(bytes);
  ok("two slides make two pages", doc.getPageCount() === 2);
  const pg = doc.getPage(0);
  ok("each page is 960 x 540 points", pg.getWidth() === 960 && pg.getHeight() === 540);
  const raw = Buffer.from(bytes).toString("latin1");
  ok("speaker notes are not written", !raw.includes("SECRET-NOTE"));
  ok("it is a PDF with the standard font only (no embedded font file)", raw.startsWith("%PDF-") && raw.includes("/Helvetica") && !raw.includes("/FontFile"));
  ok("an empty slide still makes a page", doc.getPage(1).getWidth() === 960);
  const bad = await P.build(L, S, deck([sl("s1", [el({ id: "a", type: "text", text: "ok" })]), sl("s2", [el({ id: "b", type: "text", text: "tick \u2713 and \u65e5\u672c" })])])).then(() => null, (e) => e);
  ok("characters the font cannot draw are refused with a code", bad && bad.code === "chars");
  ok("the refusal names the slide, the item and the characters", bad && /slide 2 item 1/.test(bad.message) && bad.message.includes("\u2713") && bad.message.includes("\u65e5"));
  ok("the refusal says no PDF was made", bad && /no PDF was made/.test(bad.message));
  const okc = await P.build(L, S, deck([sl("s1", [el({ id: "a", type: "text", text: "Caf\u00e9 \u00fcber \u20ac5 \u201cquoted\u201d" })])]));
  ok("Latin-1 and Windows punctuation (euro, curly quotes) are accepted", okc.length > 500);
  const emoji = await P.build(L, S, deck([sl("s1", [el({ id: "a", type: "text", text: "party \u{1F389}" })])])).then(() => null, (e) => e);
  ok("an emoji is refused, not dropped", emoji && emoji.code === "chars" && emoji.items[0].chars[0] === "\u{1F389}");
  const long = await P.build(L, S, deck([sl("s1", [el({ id: "a", type: "text", text: "word ".repeat(150), size: 12, w: 400, h: 300 })])]));
  ok("long wrapped text builds", long.length > 500);
  const many = deck(Array.from({ length: 50 }, (_, i) => sl("p" + i, [el({ id: "e" + i, type: "text", text: "Slide " + i })])));
  ok("fifty slides make fifty pages", (await L.PDFDocument.load(await P.build(L, S, many))).getPageCount() === 50);
  ok("white space inside item text is not a refusal", (await P.build(L, S, deck([sl("s1", [el({ id: "a", type: "text", text: "a\n\tb  c" })])]))).length > 300);
  {
    // Background colour: read the page's own content stream and take the first fill colour (the page background rectangle).
    const fillOf = (doc2, k) => { const c = doc2.getPage(k).node.Contents(), parts = c instanceof L.PDFArray ? c.asArray().map((r) => doc2.context.lookup(r)) : [c], txt = parts.map((st) => Buffer.from(L.decodePDFRawStream(st).decode()).toString("latin1")).join("\n"), m = /([0-9.]+) ([0-9.]+) ([0-9.]+) rg/.exec(txt); return m ? [1, 2, 3].map((i) => parseFloat(m[i])) : null; };
    const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.substr(i, 2), 16) / 255), near = (a, b) => a && b && a.every((v, i) => Math.abs(v - b[i]) < 0.003);
    let bd = S.ops.addElement(S.newDeck(), 0, "text"); bd = S.ops.updateElement(bd.deck, 0, bd.id, { text: "Hi" });
    bd = S.ops.setBackground(bd, 0, "#e6eef7"); bd = S.ops.addSlide(bd, 0);
    const bytes2 = await P.build(L, S, bd), doc2 = await L.PDFDocument.load(bytes2);
    ok("pdf background: page 1 is filled with the slide's own colour (read from the page content stream)", near(fillOf(doc2, 0), hexRgb("#e6eef7")));
    ok("pdf background: page 2 without a colour is filled with the theme background", near(fillOf(doc2, 1), hexRgb("#ffffff")));
    ok("pdf background: the colour is not the theme background on page 1 (the check can tell them apart)", !near(fillOf(doc2, 0), hexRgb("#ffffff")));
    let dk = S.ops.setTheme(S.newDeck(), "dark"); dk = S.ops.setBackground(dk, 0, "#233a4d");
    const doc3 = await L.PDFDocument.load(await P.build(L, S, dk));
    ok("pdf background: a dark colour on a dark theme is drawn as chosen", near(fillOf(doc3, 0), hexRgb("#233a4d")));
  }
  console.log(`${n - fails}/${n} slides pdf rows passed`);
  process.exit(fails ? 1 : 0);
})();
