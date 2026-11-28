// Slides align item to the slide: model rows for ops.alignElement.
const S = require("../../static/slides/model.js");
let n = 0, fails = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const el = (id, x, y, w, h, extra) => Object.assign({ id, type: "text", x, y, w, h, fill: "theme", size: 20, text: "t" }, extra || {});
const deck = (...els) => { const d = S.newDeck(); d.slides = [{ id: "s0", elements: els, notes: "" }]; return d; };
const d0 = deck(el("a", 100, 100, 300, 50, { b: true, text: "Hello" }), el("b", 0, 0, 960, 540));
const get = (d, id) => d.slides[0].elements.find((e) => e.id === id);
const al = (m, id) => S.ops.alignElement(d0, 0, id || "a", m);

ok("left puts x at 0 and leaves y and size", (() => { const e = get(al("left"), "a"); return e.x === 0 && e.y === 100 && e.w === 300 && e.h === 50; })());
ok("right puts the right edge on the slide edge (x = 960 - width)", get(al("right"), "a").x === 660);
ok("center puts the box in the middle across (x = 330)", get(al("center"), "a").x === 330);
ok("top puts y at 0 and leaves x", (() => { const e = get(al("top"), "a"); return e.y === 0 && e.x === 100; })());
ok("bottom puts the bottom edge on the slide edge (y = 540 - height)", get(al("bottom"), "a").y === 490);
ok("middle puts the box in the middle down (y = 245)", get(al("middle"), "a").y === 245);
ok("text, style and id are kept", (() => { const e = get(al("left"), "a"); return e.text === "Hello" && e.b === true && e.id === "a"; })());
ok("other items are untouched", JSON.stringify(get(al("left"), "b")) === JSON.stringify(get(d0, "b")));
ok("the source deck is unchanged", get(d0, "a").x === 100);
ok("an odd width centres with rounding (width 301 gives x = 330)", (() => { const d = deck(el("o", 0, 0, 301, 50)); return get(S.ops.alignElement(d, 0, "o", "center"), "o").x === 330; })());
ok("an item already at the edge gives null (nothing to do)", S.ops.alignElement(d0, 0, "b", "left") === null && S.ops.alignElement(d0, 0, "b", "right") === null && S.ops.alignElement(d0, 0, "b", "top") === null && S.ops.alignElement(d0, 0, "b", "bottom") === null);
ok("a full-size item is already centred, so centre and middle give null", S.ops.alignElement(d0, 0, "b", "center") === null && S.ops.alignElement(d0, 0, "b", "middle") === null);
ok("an unknown mode, item or slide gives null", al("diagonal") === null && al("left", "zz") === null && S.ops.alignElement(d0, 5, "a", "left") === null && al(undefined) === null);
ok("a shape and a picture align the same way", (() => { const d = deck(Object.assign(el("r", 50, 50, 100, 100), { type: "rect" }), { id: "p", type: "image", x: 5, y: 5, w: 200, h: 100, fill: "theme", size: 32, text: "pic", img: "a".repeat(64) }); return get(S.ops.alignElement(d, 0, "r", "right"), "r").x === 860 && get(S.ops.alignElement(d, 0, "p", "bottom"), "p").y === 440; })());
ok("a layout-linked item keeps its link after aligning", (() => { let d = S.ops.addLinkedSlide(S.newDeck(), 0, "title"); const e = d.slides[1].elements[0]; const x = S.ops.alignElement(d, 1, e.id, "left"); const g = x.slides[1].elements[0]; return g.ph === e.ph && g.x === 0; })());
ok("master items align through master_alignElement", (() => { const r = S.ops.master_addElement(d0, "text"); if (!r) return false; const m = r.deck.master.elements.find((e) => e.id === r.id); const x = S.ops.master_alignElement(r.deck, r.id, "right"); return !!x && x.master.elements.find((e) => e.id === r.id).x === 960 - m.w; })());
ok("an aligned deck passes validate and is one history step", (() => { const h = new S.History(d0, { save() { return true; } }, 100); h.change(al("left")); const one = h.past.length === 1; h.undo(); return one && get(h.deck, "a").x === 100 && S.validate(JSON.parse(JSON.stringify(al("right")))) !== null; })());
console.log((fails ? "FAILED " : "") + (n - fails) + "/" + n + " slides align rows passed");
process.exit(fails ? 1 : 0);
