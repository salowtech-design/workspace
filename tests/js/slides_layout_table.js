const S = require("../../static/slides/model.js");
let n = 0, bad = 0;
const ok = (d, c) => { n++; if (!c) { bad++; console.log("FAIL", d); } };
const J = (x) => JSON.stringify(x);
let d = S.newDeck();
// old decks: layout-built slides stay unlinked and a v1 to v8 deck round-trips through validate unchanged
let plain = S.ops.addSlide(d, 0, "title");
ok("addSlide with a layout is NOT linked (explicit linking only)", !plain.slides[1].lay && plain.slides[1].elements.every((e) => e.ph === undefined));
ok("an old deck round-trips through validate unchanged and stays below v9", J(S.validate(plain)) === J(plain) && S.validate(plain).v < 9);
let sty = S.ops.updateElement(plain, 1, plain.slides[1].elements[0].id, { b: true });
ok("a styled old deck round-trips unchanged at its version", J(S.validate(sty)) === J(sty) && sty.v === 4);
// link
let L = S.ops.addLinkedSlide(plain, 1, "title");
ok("addLinkedSlide links: lay, ph 0 and 1, version 9", L && L.v === 9 && L.slides[2].lay === "title" && L.slides[2].elements.map((e) => e.ph).join() === "0,1");
ok("the linked slide looks like the layout (same geometry as the unlinked one)", ["x", "y", "w", "h", "size", "text"].every((f) => L.slides[2].elements[0][f] === plain.slides[1].elements[0][f] && L.slides[2].elements[1][f] === plain.slides[1].elements[1][f]));
ok("blank has no slots and cannot be linked", S.ops.addLinkedSlide(plain, 0, "blank") === null && S.ops.addLinkedSlide(plain, 0, "nope") === null);
ok("layoutSlot reads the built-in values", S.layoutSlot(L, "title", 0).size === 54 && S.layoutSlot(L, "title", 5) === null);
// edit layout updates linked, leaves unlinked
const t0 = L.slides[2].elements[0].id;
let E = S.ops.setLayoutSlot(L, "title", 0, { size: 60, y: 150 });
ok("layout edit changes the linked item", E && E.slides[2].elements[0].size === 60 && E.slides[2].elements[0].y === 150);
ok("layout edit leaves an unlinked slide with the same layout alone", E.slides[1].elements[0].size === 54 && E.slides[1].elements[0].y === 170);
ok("layout edit leaves the other slot alone", E.slides[2].elements[1].size === 28 && E.slides[2].elements[1].y === 290);
ok("the edited layout is stored in deck.lays at v9 and survives validate", J(E.lays) === J({ title: [{ y: 150, size: 60 }] }) && J(S.validate(E)) === J(E));
ok("layoutSlot now reads the edited values", S.layoutSlot(E, "title", 0).size === 60);
// local override kept
let O = S.ops.updateElement(L, 2, t0, { size: 40 });
ok("a local edit keeps the link (ph and lay survive updateElement)", O.slides[2].elements[0].ph === 0 && O.slides[2].lay === "title" && O.v === 9);
let O2 = S.ops.setLayoutSlot(O, "title", 0, { size: 60, y: 150 });
ok("a locally changed size is kept while the untouched y follows", O2.slides[2].elements[0].size === 40 && O2.slides[2].elements[0].y === 150);
// equality limitation: set back to the layout value, inherits again
let B = S.ops.updateElement(O, 2, t0, { size: 54 });
let B2 = S.ops.setLayoutSlot(B, "title", 0, { size: 60 });
ok("LIMIT: setting a field back to the layout value makes it inherit again", B2.slides[2].elements[0].size === 60);
// override survives two layout edits
let O3 = S.ops.setLayoutSlot(O2, "title", 0, { size: 70 });
ok("an override survives a second layout edit", O3.slides[2].elements[0].size === 40);
// a second edit chain: follows each time when never touched
let C = S.ops.setLayoutSlot(E, "title", 0, { y: 120 }); C = S.ops.setLayoutSlot(C, "title", 0, { y: 100 });
ok("an untouched field follows every layout edit", C.slides[2].elements[0].y === 100 && J(C.lays.title[0]) === J({ y: 100, size: 60 }));
// editing a layout back to built-in drops the override entry
let R = S.ops.setLayoutSlot(E, "title", 0, { size: 54, y: 170 });
ok("editing back to the built-in values removes the stored override entirely (no empty entry) and linked items follow", R.slides[2].elements[0].size === 54 && R.slides[2].elements[0].y === 170 && R.lays === undefined && R.v === 9 && J(S.validate(R)) === J(R));
ok("with no lays and no linked slide the deck drops below v9 again", (() => { const x = S.ops.unlinkSlide(R, 2); return x.v < 9 && x.lays === undefined; })());
ok("an empty override entry in a file is accepted without data loss and not kept", (() => { const x = JSON.parse(J(E)); x.lays.title = [{}, {}]; const v = S.validate(x); return v && v.lays === undefined; })());
ok("no change returns null; bad key and slot return null", S.ops.setLayoutSlot(L, "title", 0, { size: 54 }) === null && S.ops.setLayoutSlot(L, "blank", 0, { size: 9 }) === null && S.ops.setLayoutSlot(L, "title", 7, { size: 9 }) === null);
ok("values are clamped to the limits", S.ops.setLayoutSlot(L, "title", 0, { size: 999 }).slides[2].elements[0].size === 120);
// geometry that does not fit: validate clamps x so the item stays on the slide
let G = S.ops.setLayoutSlot(L, "title", 0, { x: 960 });
ok("a slot position that would leave the slide is clamped in the layout itself, so the item equals the layout", G && S.layoutSlot(G, "title", 0).x === 160 && G.slides[2].elements[0].x === 160 && J(G.lays.title[0]) === J({ x: 160 }));
let G2 = S.ops.setLayoutSlot(G, "title", 0, { x: 100 });
ok("so the linked item still follows the next layout edit after a clamp", G2.slides[2].elements[0].x === 100);
let G3 = S.ops.setLayoutSlot(L, "title", 0, { w: 960, h: 540 });
ok("a slot as big as the slide is allowed, and one larger is clamped to the slide", G3 && S.layoutSlot(G3, "title", 0).w === 960 && G3.slides[2].elements[0].w === 960 && S.ops.setLayoutSlot(L, "title", 0, { w: 5000 }).slides[2].elements[0].w === 960);
// unlink
let U = S.ops.unlinkSlide(E, 2);
ok("unlink keeps the values and drops lay and ph", U.slides[2].elements[0].size === 60 && !U.slides[2].lay && U.slides[2].elements.every((e) => e.ph === undefined));
ok("unlink of a slide that is not linked is null", S.ops.unlinkSlide(plain, 1) === null);
let U2 = S.ops.setLayoutSlot(U, "title", 0, { size: 80 });
ok("after unlink the slide no longer follows layout edits", U2.slides[2].elements[0].size === 60);
// duplicates
let DS = S.ops.duplicateSlide(E, 2);
ok("a duplicated linked slide is linked too, with its own ids", DS.slides[3].lay === "title" && DS.slides[3].elements[0].ph === 0 && DS.slides[3].elements[0].id !== DS.slides[2].elements[0].id && S.validate(DS));
let DE = S.ops.duplicateElement(E, 2, E.slides[2].elements[0].id);
ok("a duplicated placeholder item is an ordinary item (no ph)", DE.deck.slides[2].elements[1].ph === undefined && DE.deck.slides[2].elements.length === 3 && S.validate(DE.deck));
// refusals (never silently drop linkage)
const m = (f) => { const x = JSON.parse(J(E)); f(x); return S.validate(x); };
ok("a hand-edited slot that does not fit the slide is refused", m((x) => { x.lays.title[0].x = 500; x.lays.title[0].w = 800; }) === null);
ok("a master item with ph is refused", (() => { const x = JSON.parse(J(E)); x.master = { elements: [{ id: "m1", type: "text", x: 10, y: 10, w: 100, h: 50, fill: "theme", text: "a", size: 20, ph: 0 }] }; return S.validate(x) === null; })());
ok("unknown layout key on a slide is refused", m((x) => { x.slides[2].lay = "wide"; }) === null);
ok("a blank layout link is refused", m((x) => { x.slides[2].lay = "blank"; }) === null);
ok("ph without lay is refused", m((x) => { delete x.slides[2].lay; }) === null);
ok("ph out of range is refused", m((x) => { x.slides[2].elements[0].ph = 2; }) === null);
ok("ph that is not a whole number is refused", m((x) => { x.slides[2].elements[0].ph = 0.5; }) === null && m((x) => { x.slides[2].elements[0].ph = "0"; }) === null);
ok("two items on one slot are refused", m((x) => { x.slides[2].elements[1].ph = 0; }) === null);
ok("ph on an image item is refused", m((x) => { x.slides[2].elements[0].type = "shape"; }) === null);
ok("lays with an unknown key, a bad field value or too many slots is refused", m((x) => { x.lays.wide = []; }) === null && m((x) => { x.lays.title[0].size = 3; }) === null && m((x) => { x.lays.title[0].size = 60.5; }) === null && m((x) => { x.lays.title = [{}, {}, {}]; }) === null && m((x) => { x.lays = []; }) === null);
ok("v9 metadata under an old version number is accepted and the version is recomputed to 9", m((x) => { x.v = 4; }).v === 9);
ok("version 11 is still refused", m((x) => { x.v = 11; }) === null);
ok("lays alone (no slide linked) keeps version 9 so older code refuses it", (() => { const x = S.ops.unlinkSlide(E, 2); return x.v === 9 && x.lays; })());
// theme, master and animation stay intact on a linked slide
let T = S.ops.setTheme(E, "dark"); ok("theme change keeps links and layouts", T.slides[2].lay === "title" && T.lays && S.validate(T) && T.v === 9);
// a linked item with its own wider size: its x is clamped by the slide, and still follows the layout
(() => {
  let a = S.ops.updateElement(L, 2, L.slides[2].elements[0].id, { w: 900 });
  ok("setup: a local width 900 is kept and the slide clamps that item's x to 60 (layout x is still 80)", a.slides[2].elements[0].w === 900 && a.slides[2].elements[0].x === 60 && S.layoutSlot(a, "title", 0).x === 80);
  let b = S.ops.setLayoutSlot(a, "title", 0, { x: 200 });
  ok("a layout x past what the wider item fits is clamped for that item (x 60), the width override is kept", b.slides[2].elements[0].x === 60 && b.slides[2].elements[0].w === 900 && S.layoutSlot(b, "title", 0).x === 160);
  let c = S.ops.setLayoutSlot(b, "title", 0, { x: 30 });
  ok("REPRO: the next layout edit still moves that item (it was following, only clamped)", c.slides[2].elements[0].x === 30 && c.slides[2].elements[0].w === 900);
  let c2 = S.ops.setLayoutSlot(b, "title", 0, { x: 40 });
  ok("and a smaller layout x is followed again exactly", c2.slides[2].elements[0].x === 40);
  let d1 = S.ops.updateElement(b, 2, b.slides[2].elements[0].id, { x: 10 });
  let d2 = S.ops.setLayoutSlot(d1, "title", 0, { x: 120 });
  ok("a real local x (10, not the clamped value) is kept as an override", d2.slides[2].elements[0].x === 10);
  let e1 = S.ops.setLayoutSlot(a, "title", 0, { w: 960 });
  ok("a layout width edit with a local width: the local width stays, x stays valid", e1.slides[2].elements[0].w === 900 && S.validate(e1));
  let f1 = S.ops.updateElement(L, 2, L.slides[2].elements[0].id, { h: 500 });
  let f2 = S.ops.setLayoutSlot(f1, "title", 0, { y: 300 }); let f3 = S.ops.setLayoutSlot(f2, "title", 0, { y: 30 });
  ok("the same holds for y with a taller local height (y 40 clamp, then follows again)", f2.slides[2].elements[0].y === 40 && f3.slides[2].elements[0].y === 30 && f3.slides[2].elements[0].h === 500);
  let g1 = S.ops.setLayoutSlot(a, "title", 0, { w: 700 });
  ok("LIMIT: an item whose x equals the clamped layout x counts as following even if the user typed that value", g1 && S.validate(g1));
})();
// export, import and PDF of a linked deck
const raw = J(E), lr = S.loadDeck(raw);
ok("Export text of a linked deck loads back as the same deck (links and layout edit intact), readable", !lr.unreadable && J(lr.deck) === J(E) && lr.deck.v === 9);
ok("a damaged v9 deck (ph out of range) is unreadable and its text is kept, never blanked", (() => { const x = JSON.parse(raw); x.slides[2].elements[0].ph = 9; const t = J(x), r = S.loadDeck(t); return r.unreadable && r.raw === t; })());
const path = require("path"), PL = require(path.join(__dirname, "../../static/pdf/vendor/pdf-lib.min.js")), P = require("../../static/slides/pdf.js");
(async () => {
  const bytes = await P.build(PL, S, E), doc = await PL.PDFDocument.load(bytes);
  ok("PDF of a deck with a linked slide builds one page per slide", doc.getPageCount() === E.slides.length);
  const bytes2 = await P.build(PL, S, U), doc2 = await PL.PDFDocument.load(bytes2);
  ok("PDF of the same deck after unlink builds the same page count", doc2.getPageCount() === U.slides.length);
  console.log(`${n - bad}/${n} layout rows passed`);
  process.exit(bad ? 1 : 0);
})();
