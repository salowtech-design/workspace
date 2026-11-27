const S = require("../../static/slides/model.js");
let n = 0, bad = 0;
const ok = (d, c) => { n++; if (!c) { bad++; console.log("FAIL", d); } };
const mem = (f) => ({ save() { return !(f && f.on); } });
let d = S.newDeck();
ok("new deck has one empty slide", d.slides.length === 1 && d.slides[0].elements.length === 0 && d.theme === "light");
const a = S.ops.addElement(d, 0, "text"); d = a.deck;
ok("add element", d.slides[0].elements.length === 1 && d.slides[0].elements[0].text === "Text");
ok("unknown element type refused", S.ops.addElement(d, 0, "script") === null);
for (let i = 0; i < 29; i++) d = S.ops.addElement(d, 0, "rect").deck;
ok("element cap of 30", S.ops.addElement(d, 0, "rect") === null);
d = S.ops.deleteElement(d, 0, d.slides[0].elements[5].id);
ok("delete element", d.slides[0].elements.length === 29);
ok("deleting a missing element is a no-op", S.ops.deleteElement(d, 0, "nope") === null);
let e = d.slides[0].elements[0];
let u = S.ops.updateElement(d, 0, e.id, { x: -50, y: 9999, w: 5000, size: 1000, text: "x".repeat(2000), fill: "red; background:url(x)" });
let e2 = u.slides[0].elements[0];
ok("update clamps size and position into the slide", e2.w === 960 && e2.x === 0 && e2.y <= 540 - e2.h && e2.size === 120 && e2.h >= 10);
ok("text is capped", e2.text.length === 1000);
ok("fill outside the list falls back to the theme", e2.fill === "theme");
ok("NaN becomes the minimum", S.ops.updateElement(d, 0, e.id, { x: "abc" }).slides[0].elements[0].x === 0);
ok("update does not touch the original (copy on write)", d.slides[0].elements[0].w === 400);
ok("unknown patch fields are ignored", !("evil" in S.ops.updateElement(d, 0, e.id, { evil: 1 }).slides[0].elements[0]));
let d2 = S.ops.addSlide(S.newDeck(), 0); d2 = S.ops.addSlide(d2, 1);
ok("add slides after a position", d2.slides.length === 3);
const ids = d2.slides.map((s) => s.id); const mv = S.ops.moveSlide(d2, 0, 2);
ok("move slide", mv.slides[2].id === ids[0] && mv.slides[0].id === ids[1]);
ok("move to the same place or out of range is a no-op", S.ops.moveSlide(d2, 1, 1) === null && S.ops.moveSlide(d2, 0, 3) === null);
ok("delete a slide but never the last", S.ops.deleteSlide(d2, 0).slides.length === 2 && S.ops.deleteSlide(S.newDeck(), 0) === null);
let big = S.newDeck(); for (let i = 0; i < 60; i++) { const x = S.ops.addSlide(big); if (x) big = x; }
ok("slide cap of 50", big.slides.length === 50 && S.ops.addSlide(big) === null);
let th = S.ops.addElement(S.newDeck(), 0, "text").deck, th2 = S.ops.addElement(th, 0, "rect").deck;
ok("theme change restyles inheriting elements", S.color(th2, th2.slides[0].elements[0]) === "#222222" && S.color(S.ops.setTheme(th2, "dark"), th2.slides[0].elements[0]) === "#f2f2f2");
ok("elements with their own fill keep it across themes", (() => { const p = S.ops.updateElement(th2, 0, th2.slides[0].elements[1].id, { fill: "#2e6f95" }); return S.color(S.ops.setTheme(p, "dark"), p.slides[0].elements[1]) === "#2e6f95"; })());
ok("unknown theme refused", S.ops.setTheme(th, "neon") === null && S.ops.setTheme(th, "light") === null);
ok("validate accepts a good deck", S.validate(JSON.parse(JSON.stringify(th2))) !== null);
ok("validate refuses non-decks", S.validate(null) === null && S.validate([]) === null && S.validate({ slides: [] }) === null && S.validate({ slides: "x" }) === null);
ok("validate refuses duplicate ids", (() => { const x = JSON.parse(JSON.stringify(th2)); x.slides[0].elements[1].id = x.slides[0].elements[0].id; return S.validate(x) === null; })());
ok("validate refuses too many slides or elements", (() => { const x = S.newDeck(); x.slides = Array.from({ length: 51 }, (_, i) => ({ id: "s" + i, elements: [] })); const y = S.newDeck(); y.slides[0].elements = Array.from({ length: 31 }, (_, i) => ({ id: "e" + i, type: "rect" })); return S.validate(x) === null && S.validate(y) === null; })());
ok("validate drops unknown element types by refusing the deck", S.validate({ slides: [{ id: "s", elements: [{ id: "e", type: "iframe" }] }] }) === null);
ok("validate clamps and cleans values", (() => { const v = S.validate({ theme: "x", slides: [{ id: "s", elements: [{ id: "e", type: "text", x: 5000, w: 20, size: 3, fill: "url(javascript:1)", text: 5, extra: 1 }] }] }); const e = v.slides[0].elements[0]; return v.theme === "light" && e.x === 940 && e.size === 8 && e.fill === "theme" && e.text === "" && !("extra" in e); })());
ok("validate drops prototype keys", (() => { const v = S.validate(JSON.parse('{"__proto__":{"x":1},"theme":"dark","slides":[{"id":"s","elements":[],"__proto__":{"y":1}}]}')); return v && v.theme === "dark" && v.x === undefined && ({}).x === undefined; })());
let st = { on: false }, h = new S.History(S.newDeck(), mem(st), 3);
for (let i = 0; i < 5; i++) h.change(S.ops.addSlide(h.deck));
ok("history is bounded", h.past.length === 3);
ok("undo and redo", h.undo() && h.deck.slides.length === 5 && h.redo() && h.deck.slides.length === 6);
ok("new edit clears redo", (h.undo(), h.change(S.ops.addSlide(h.deck)), h.future.length === 0));
ok("no-op is not a step", h.change(null) === false && h.change(S.copy(h.deck)) === false);
st.on = true; h.change(S.ops.addSlide(h.deck), "Added.");
ok("blocked storage failure stays with later notes", h.saveFailed && /would not store/.test(h.note) && /Added/.test(h.note) && (h.undo(), /would not store/.test(h.note) && /Undone/.test(h.note)));
st.on = false; h.change(S.ops.addSlide(h.deck)); ok("success clears failure", !h.saveFailed && h.note === "Saved on this device.");
ok("a deck marked with another version is refused", S.validate({ v: 8, slides: [{ id: "s", elements: [] }] }) === null && S.validate({ v: "2", slides: [{ id: "s", elements: [] }] }) === null && S.validate({ v: 1, slides: [{ id: "s", elements: [] }] }) !== null);
ok("ids must be plain", S.validate({ slides: [{ id: "a b", elements: [] }] }) === null && S.validate({ slides: [{ id: 's"><x', elements: [] }] }) === null && S.validate({ slides: [{ id: "s", elements: [{ id: "e".repeat(41), type: "rect" }] }] }) === null && S.validate({ slides: [{ id: "s_1-A", elements: [] }] }) !== null);
ok("loadDeck: nothing stored is a fresh deck, not unreadable", (() => { const r = S.loadDeck(null); return !r.unreadable && r.deck.slides.length === 1 && S.loadDeck("").unreadable === false; })());
ok("loadDeck: good deck loads", (() => { const r = S.loadDeck(JSON.stringify(S.newDeck())); return !r.unreadable && r.raw === null; })());
ok("loadDeck: broken json is unreadable and the raw text is kept", (() => { const r = S.loadDeck("{not json"); return r.unreadable && r.raw === "{not json"; })());
ok("loadDeck: a newer format is unreadable, not blank", (() => { const raw = JSON.stringify({ v: 8, slides: [{ id: "s", elements: [] }] }); const r = S.loadDeck(raw); return r.unreadable && r.raw === raw; })());
ok("loadDeck: one bad element makes the whole deck unreadable", S.loadDeck(JSON.stringify({ slides: [{ id: "s", elements: [{ id: "e", type: "bogus" }] }] })).unreadable);
const cs = S.FILLS.concat([S.THEMES.light.accent, S.THEMES.dark.accent, S.THEMES.warm.accent]).filter((f) => f !== "theme");
ok("text on every shape color has contrast of at least 4.5", cs.every((f) => S.contrast(f, S.onFill(f)) >= 4.5));
ok("theme text on every theme background has contrast of at least 4.5", Object.values(S.THEMES).every((t) => S.contrast(t.fg, t.bg) >= 4.5));
ok("light fills get dark text and dark fills get white", S.onFill("#ffffff") === "#111111" && S.onFill("#1f2430") === "#ffffff");
ok("item descriptions give text content for non-visual use", (() => { let d = S.ops.addElement(S.newDeck(), 0, "text").deck; d = S.ops.addElement(d, 0, "rect").deck; const x = S.describe(d, 0); return x.length === 2 && /Text: Text/.test(x[0]) && /Rectangle/.test(x[1]); })());
ok("history size is capped by deck size", (() => { let d = S.newDeck(); for (let i = 0; i < 29; i++) d = S.ops.addElement(d, 0, "text").deck; d.slides[0].elements.forEach((e) => (e.text = "x".repeat(1000))); const h2 = new S.History(d, mem(), 100); for (let i = 0; i < 60; i++) { const nd = S.copy(h2.deck); nd.slides[0].elements[0].x = i + 1; h2.change(nd); } return h2.past.length < 100 && h2.past.length >= 5; })());
ok("reserved property names are fine as ids", ["constructor", "toString", "valueOf", "__proto__", "hasOwnProperty"].every((id) => S.validate({ slides: [{ id, elements: [{ id: id + "x", type: "rect" }] }] }) !== null));
ok("duplicate ids are still refused", S.validate({ slides: [{ id: "a", elements: [{ id: "a", type: "rect" }] }] }) === null && S.validate({ slides: [{ id: "a", elements: [] }, { id: "a", elements: [] }] }) === null);
ok("a blocked history refuses change, undo, redo and never saves", (() => { let saves = 0; const d0 = S.newDeck(); const h3 = new S.History(d0, { save: () => { saves++; return true; } }); h3.change(S.ops.addElement(d0, 0, "text").deck); const before = saves; h3.blocked = true; const d1 = h3.deck; const r = [h3.change(S.ops.addElement(d1, 0, "rect").deck), h3.undo(), h3.redo()]; h3.persist("x"); return r.every((x) => x === false) && saves === before && h3.deck === d1 && /paused/.test(h3.note); })());
ok("unblocking restores editing", (() => { let saves = 0; const d0 = S.newDeck(); const h3 = new S.History(d0, { save: () => { saves++; return true; } }); h3.blocked = true; h3.blocked = false; return h3.change(S.ops.addElement(d0, 0, "text").deck) === true && saves === 1; })());
ok("show: next keys advance and stop at the last slide", S.showNav(0, 3, "ArrowRight") === 1 && S.showNav(2, 3, "PageDown") === 2 && S.showNav(1, 3, " ") === 2 && S.showNav(1, 3, "Enter") === 2);
ok("show: previous keys go back and stop at the first slide", S.showNav(2, 3, "ArrowLeft") === 1 && S.showNav(0, 3, "PageUp") === 0 && S.showNav(1, 3, "Backspace") === 0);
ok("show: Home and End jump", S.showNav(1, 5, "Home") === 0 && S.showNav(1, 5, "End") === 4);
ok("show: other keys and bad indexes do nothing", S.showNav(1, 3, "a") === null && S.showNav(1, 3, "Escape") === null && S.showNav(5, 3, "End") === null && S.showNav(0, 0, "End") === null && S.showNav(-1, 3, "Home") === null);
ok("moveElement swaps neighbours and refuses at the ends", (() => { let x = S.newDeck(); x = S.ops.addElement(x, 0, "rect").deck; x = S.ops.addElement(x, 0, "ellipse").deck; x = S.ops.addElement(x, 0, "text").deck; const ids = x.slides[0].elements.map((e) => e.id); const f = S.ops.moveElement(x, 0, ids[0], 1); const b = S.ops.moveElement(x, 0, ids[0], -1); const t = S.ops.moveElement(x, 0, ids[2], 1); return f.slides[0].elements.map((e) => e.id).join() === [ids[1], ids[0], ids[2]].join() && b === null && t === null && x.slides[0].elements[0].id === ids[0]; })());
ok("moveElement rejects bad slide, id and direction", (() => { let x = S.ops.addElement(S.newDeck(), 0, "rect").deck; const id = x.slides[0].elements[0].id; return S.ops.moveElement(x, 5, id, 1) === null && S.ops.moveElement(x, 0, "nope", 1) === null && S.ops.moveElement(x, 0, id, 2) === null; })());
ok("moveElement keeps items and passes validate", (() => { let x = S.ops.addElement(S.ops.addElement(S.newDeck(), 0, "rect").deck, 0, "text").deck; const m = S.ops.moveElement(x, 0, x.slides[0].elements[0].id, 1); return S.validate(m) !== null && m.slides[0].elements.length === 2; })());
ok("duplicateSlide copies after the source with fresh unique ids", (() => { let x = S.ops.addElement(S.newDeck(), 0, "rect").deck; x = S.ops.addElement(x, 0, "text").deck; const m = S.ops.duplicateSlide(x, 0); if (!m || m.slides.length !== 2 || S.validate(m) === null) return false; const a = m.slides[0], b = m.slides[1]; const ids = [a.id, b.id].concat(a.elements.map((e) => e.id), b.elements.map((e) => e.id)); return new Set(ids).size === ids.length; })());
ok("duplicateSlide keeps content and does not alias the source", (() => { let x = S.ops.addElement(S.newDeck(), 0, "text").deck; const m = S.ops.duplicateSlide(x, 0); m.slides[1].elements[0].text = "changed"; return m.slides[0].elements[0].text === "Text" && m.slides[1].elements.length === 1 && x.slides.length === 1; })());
ok("duplicateSlide refuses at the slide cap and for a bad index", (() => { let x = S.newDeck(); while (x.slides.length < S.MAX_SLIDES) x = S.ops.addSlide(x); return S.ops.duplicateSlide(x, 0) === null && S.ops.duplicateSlide(S.newDeck(), 3) === null; })());
ok("wrapLines breaks at spaces within the width", (() => { const l = S.wrapLines("one two three four five six", 20, 130); return l.length > 1 && l.every((x) => x.length <= Math.floor(130 / 11)) && l.join(" ") === "one two three four five six"; })());
ok("wrapLines cuts a word longer than a line and keeps every character", (() => { const l = S.wrapLines("a".repeat(50), 20, 110); return l.length === 5 && l.join("") === "a".repeat(50); })());
ok("wrapLines handles empty, blanks, tiny width and caps lines", S.wrapLines("", 20, 100).length === 0 && S.wrapLines("   ", 20, 100).length === 0 && S.wrapLines("ab", 120, 1).join("") === "ab" && S.wrapLines("ab ".repeat(333), 8, 10).join("").length === 666);
ok("textHeight counts lines at 1.2 times the size", S.textHeight(["a", "b", "c"], 10) === 36 && S.textHeight([], 10) === 0);
ok("slides carry notes, old decks without notes load as empty notes", (() => { const v = S.validate({ slides: [{ id: "a", elements: [] }] }); return v && v.slides[0].notes === "" && S.newDeck().slides[0].notes === ""; })());
ok("setNotes sets, bounds and refuses bad input", (() => { const x = S.newDeck(); const m = S.ops.setNotes(x, 0, "hello\nworld"); const big = S.ops.setNotes(x, 0, "n".repeat(5000)); return m.slides[0].notes === "hello\nworld" && big.slides[0].notes.length === S.MAX_NOTES && S.ops.setNotes(x, 3, "a") === null && S.ops.setNotes(x, 0, 5) === null && x.slides[0].notes === ""; })());
ok("notes survive validate, duplicate and non-string notes become empty", (() => { let x = S.ops.setNotes(S.newDeck(), 0, "keep"); const dup = S.ops.duplicateSlide(x, 0); const v = S.validate(JSON.parse(JSON.stringify(dup))); const bad = S.validate({ slides: [{ id: "a", elements: [], notes: { a: 1 } }] }); return v.slides[1].notes === "keep" && bad.slides[0].notes === ""; })());
ok("fmtClock formats minutes and seconds", S.fmtClock(0) === "0:00" && S.fmtClock(65) === "1:05" && S.fmtClock(3600) === "60:00" && S.fmtClock(-4) === "0:00" && S.fmtClock("x") === "0:00");
ok("wrapLines never splits an emoji and keeps every code point", (() => { const t = "\u{1F600}".repeat(23); const l = S.wrapLines(t, 20, 110); return l.length > 1 && l.join("") === t && l.every((x) => Array.from(x).every((c) => c.codePointAt(0) > 0xffff)) && l.every((x) => !/[\ud800-\udfff]/.test(x.replace(/[\ud800-\udbff][\udc00-\udfff]/g, ""))); })());
ok("wrapLines gives wide scripts two units and cuts them with no spaces", (() => { const t = "\u4e2d".repeat(30); const l = S.wrapLines(t, 20, 110); return l.join("") === t && l.every((x) => Array.from(x).length <= 5) && l.length === 6; })());
ok("wrapLines counts a long input fully, no line cap", (() => { const t = "w".repeat(1000); const l = S.wrapLines(t, 120, 66); return l.join("") === t && l.length === 500; })());
ok("wrapLines treats newlines and runs of blanks as one space, like the one-line drawing", S.wrapLines("a\n\n  b\tc", 20, 500).join("|") === "a b c");
ok("cut never splits a surrogate pair and leaves short text alone", (() => { const t = "\u{1F600}".repeat(5); const c = S.cut(t, 3); return Array.from(c).length === 3 && c === "\u{1F600}".repeat(3) && S.cut("abc", 10) === "abc"; })());
ok("validate bounds text and notes by code point", (() => { const v = S.validate({ slides: [{ id: "a", notes: "\u{1F600}".repeat(2500), elements: [{ id: "b", type: "text", text: "\u{1F600}".repeat(1200) }] }] }); return Array.from(v.slides[0].elements[0].text).length === 1000 && Array.from(v.slides[0].notes).length === 2000 && !/[\ud800-\udbff]$/.test(v.slides[0].notes); })());
ok("countCut counts only fields over the limits and tolerates bad input", S.countCut({ slides: [{ notes: "n".repeat(2001), elements: [{ text: "t".repeat(1001) }, { text: "ok" }, null, { text: 5 }] }, null, { notes: "n".repeat(2000) }] }) === 2 && S.countCut(null) === 0 && S.countCut({ slides: "x" }) === 0 && S.countCut({ slides: [{ elements: "no" }] }) === 0);
ok("countCut counts by code point like the cut itself", S.countCut({ slides: [{ elements: [{ text: "\u{1F600}".repeat(600) }] }] }) === 0 && S.countCut({ slides: [{ elements: [{ text: "\u{1F600}".repeat(1001) }] }] }) === 1);
ok("layouts: blank is the default and unknown names fall back to blank", S.ops.addSlide(S.newDeck ? S.newDeck() : { v: 1, theme: "light", slides: [{ id: "a", elements: [], notes: "" }] }, 0, "nope").slides[1].elements.length === 0);
ok("layouts: title makes two text items inside the slide with unique ids that validate", (() => { const d = S.ops.addSlide({ v: 1, theme: "light", slides: [{ id: "a", elements: [], notes: "" }] }, 0, "title"), s = d.slides[1]; return s.elements.length === 2 && s.elements.every((e) => e.type === "text" && e.x + e.w <= S.W && e.y + e.h <= S.H) && new Set(s.elements.map((e) => e.id)).size === 2 && S.validate(d) !== null; })());
ok("layouts: content makes a title and a body, and every layout item survives validate unchanged", (() => { const d = S.ops.addSlide({ v: 1, theme: "light", slides: [{ id: "a", elements: [], notes: "" }] }, 0, "content"); const v = S.validate(d); return v.slides[1].elements.length === 2 && JSON.stringify(v.slides[1].elements) === JSON.stringify(d.slides[1].elements); })());
ok("layouts: the slide limit still applies", S.ops.addSlide({ v: 1, theme: "light", slides: Array.from({ length: S.MAX_SLIDES }, (_, i) => ({ id: "s" + i, elements: [], notes: "" })) }, 0, "title") === null);
const OLD = require("./fixtures/slides_model_v1_validator.js");
const MK = (els, extra) => Object.assign({ v: 2, theme: "light", master: { elements: els }, slides: [{ id: "s1", elements: [], notes: "" }, { id: "s2", elements: [], notes: "", hideMaster: true }] }, extra || {});
const mel = (id, t) => ({ id, type: "text", x: 10, y: 10, w: 100, h: 30, fill: "theme", text: t, size: 20 });
{
  let md = S.ops.master_addElement(S.newDeck(), "text");
  ok("master: adding an item makes a version 2 deck with a master", md && md.deck.v === 2 && md.deck.master.elements.length === 1 && md.deck.slides[0].elements.length === 0);
  const id = md.deck.master.elements[0].id;
  let m2 = S.ops.master_updateElement(md.deck, id, { text: "Footer", y: 5000 });
  ok("master: update is clamped like any item and does not touch the original", m2.master.elements[0].text === "Footer" && m2.master.elements[0].y <= 540 - m2.master.elements[0].h && md.deck.master.elements[0].text === "Text");
  let m3 = S.ops.master_deleteElement(m2, id);
  ok("master: deleting the last item returns a version 1 deck with no master", m3.v === 1 && !("master" in m3));
  ok("master: deleting a missing item is a no-op", S.ops.master_deleteElement(m2, "nope") === null);
  let big = S.newDeck(); for (let i = 0; i < S.MAX_MASTER; i++) big = S.ops.master_addElement(big, "rect").deck;
  ok("master: capped at 10 items", S.ops.master_addElement(big, "rect") === null && big.master.elements.length === 10);
  let mv = S.ops.master_moveElement(big, big.master.elements[0].id, 1);
  ok("master: items can be moved forward", mv && mv.master.elements[1].id === big.master.elements[0].id);
  const h = S.ops.setHideMaster(md.deck, 0, true);
  ok("master: hiding on a slide sets the flag, drawing skips the master, and showing again clears it", h.slides[0].hideMaster === true && S.masterOf(h, 0).length === 0 && S.masterOf(md.deck, 0).length === 1 && !("hideMaster" in S.ops.setHideMaster(h, 0, false).slides[0]));
  ok("master: a hide flag on a deck with no master keeps it version 2 and turning it off returns to version 1", S.ops.setHideMaster(S.newDeck(), 0, true).v === 2 && S.ops.setHideMaster(S.ops.setHideMaster(S.newDeck(), 0, true), 0, false).v === 1);
  ok("master: setHideMaster refuses a non-boolean and a no-change", S.ops.setHideMaster(md.deck, 0, "yes") === null && S.ops.setHideMaster(md.deck, 0, false) === null);
}
ok("master: a deck with no master stays version 1 and equals what an older version reads", (() => { const raw = { v: 1, theme: "dark", slides: [{ id: "a", elements: [mel("x", "hi")], notes: "n" }] }; const v = S.validate(raw); return v.v === 1 && !("master" in v) && JSON.stringify(v) === JSON.stringify(OLD.validate(raw)); })());
ok("master: an old version 1 deck loads, is unchanged and has no master", (() => { const raw = JSON.stringify({ v: 1, theme: "warm", slides: [{ id: "a", elements: [mel("x", "hi")], notes: "" }] }); const r = S.loadDeck(raw); return !r.unreadable && r.deck.slides[0].elements[0].text === "hi" && S.masterOf(r.deck, 0).length === 0; })());
ok("master: validate round-trips a deck with a master and a hidden slide exactly", (() => { const v = S.validate(MK([mel("m1", "Footer")])); return v.v === 2 && JSON.stringify(S.validate(JSON.parse(JSON.stringify(v)))) === JSON.stringify(v) && v.slides[1].hideMaster === true && !("hideMaster" in v.slides[0]); })());
ok("master: damaged master data is refused whole, never partly kept", [MK([mel("m1", "a")], { master: [] }), MK([mel("m1", "a")], { master: { elements: "x" } }), MK([mel("m1", "a"), mel("m1", "b")]), MK([mel("s1", "a")]), MK(Array.from({ length: 11 }, (_, i) => mel("q" + i, "a"))), MK([{ id: "bad id", type: "text" }]), MK([mel("m1", "a")], { slides: [{ id: "s1", elements: [], hideMaster: "yes" }] })].every((x) => S.validate(x) === null));
ok("master: a version above 7 is refused", S.validate(MK([mel("m1", "a")], { v: 8 })) === null);
ok("master: text and numbers in the master are cut and clamped like slide items", (() => { const v = S.validate(MK([Object.assign(mel("m1", "x".repeat(2000)), { x: -5, size: 999, fill: "evil" })])); const e = v.master.elements[0]; return e.text.length === 1000 && e.x === 0 && e.size === 120 && e.fill === "theme"; })());
ok("master: the previous version's real validator refuses a deck with a master, and loadDeck keeps its stored text", (() => { const raw = JSON.stringify(S.validate(MK([mel("m1", "Footer")]))); const r = OLD.loadDeck(raw); return OLD.validate(JSON.parse(raw)) === null && r.unreadable === true && r.raw === raw; })());
ok("master: with the previous version, history stays blocked on that deck so a save cannot replace it", (() => { const raw = JSON.stringify(S.validate(MK([mel("m1", "Footer")]))); const r = OLD.loadDeck(raw); let saved = 0; const h = new OLD.History(r.deck, { save() { saved++; return true; } }); h.blocked = r.unreadable; const ch = h.change(OLD.ops.addSlide(r.deck, 0)); return ch === false && saved === 0; })());
ok("master: the new code also refuses a stored deck with an unknown version and keeps the text", (() => { const raw = JSON.stringify({ v: 9, slides: [{ id: "s", elements: [] }] }); const r = S.loadDeck(raw); return r.unreadable && r.raw === raw; })());
ok("master: duplicating a slide keeps its hide flag", S.ops.duplicateSlide(S.ops.setHideMaster(S.newDeck(), 0, true), 0).slides[1].hideMaster === true);
ok("master: describeEls lists items like describe", S.describeEls([mel("a", "Hello")]).join() === "1. Text: Hello" && S.describe({ slides: [{ elements: [mel("a", "Hello")] }] }, 0).join() === "1. Text: Hello");
ok("master: import counts master text over 1000 characters as cut", S.countCut(MK([mel("m1", "x".repeat(1500))])) === 1 && S.countCut(MK([mel("m1", "x".repeat(10))])) === 0);
{
  const t1 = S.ops.setTransition(S.newDeck(), 0, "fade");
  ok("transition: fade sets the flag and makes a version 3 deck, none returns to version 1", t1.slides[0].transition === "fade" && t1.v === 3 && S.ops.setTransition(t1, 0, "none").v === 1 && !("transition" in S.ops.setTransition(t1, 0, "none").slides[0]));
  ok("transition: bad names and no-change are refused", S.ops.setTransition(S.newDeck(), 0, "spin") === null && S.ops.setTransition(S.newDeck(), 0, "none") === null && S.ops.setTransition(t1, 0, "fade") === null && S.ops.setTransition(S.newDeck(), 5, "fade") === null);
  ok("transition: validate keeps fade and round-trips, 'none' is dropped, unknown names refuse the deck", S.validate(JSON.parse(JSON.stringify(t1))).slides[0].transition === "fade" && JSON.stringify(S.validate(JSON.parse(JSON.stringify(t1)))) === JSON.stringify(t1) && !("transition" in S.validate({ v: 1, slides: [{ id: "s", elements: [], transition: "none" }] }).slides[0]) && S.validate({ v: 3, slides: [{ id: "s", elements: [], transition: "spin" }] }) === null && S.validate({ v: 3, slides: [{ id: "s", elements: [], transition: 1 }] }) === null && S.validate({ v: 8, slides: [{ id: "s", elements: [] }] }) === null);
  ok("transition: the previous version refuses a deck that uses one and keeps the stored text", (() => { const raw = JSON.stringify(t1); const r = OLD.loadDeck(raw); return r.unreadable === true && r.raw === raw; })());
  ok("transition: duplicating keeps it and a deck with a master and a fade stays valid", S.ops.duplicateSlide(t1, 0).slides[1].transition === "fade" && S.validate(S.ops.master_addElement(t1, "text").deck) !== null);
}
const OLD2 = require("./fixtures/slides_model_v2_master_validator.js");
{
  const t1 = S.ops.setTransition(S.newDeck(), 0, "fade"), both = S.ops.master_addElement(t1, "text").deck, hid = S.ops.setHideMaster(S.ops.master_addElement(S.newDeck(), "text").deck, 0, true);
  ok("transition: a fade deck is version 3, with a master too", t1.v === 3 && both.v === 3 && S.validate(both).v === 3);
  ok("transition: the master-aware previous version (d3cd9e6) refuses version 3 whole, with and without a master, so no transition is silently dropped", OLD2.validate(JSON.parse(JSON.stringify(t1))) === null && OLD2.validate(JSON.parse(JSON.stringify(both))) === null);
  ok("transition: that version's loadDeck keeps the stored text and its history stays blocked", (() => { const raw = JSON.stringify(both), r = OLD2.loadDeck(raw); let saved = 0; const h = new OLD2.History(r.deck, { save() { saved++; return true; } }); h.blocked = r.unreadable; return r.unreadable === true && r.raw === raw && h.change(OLD2.ops.addSlide(r.deck, 0)) === false && saved === 0; })());
  ok("transition: that version still reads master-only and hide-only decks, which are version 2 and carry no transition", OLD2.validate(JSON.parse(JSON.stringify(hid))) !== null && S.ops.setTransition(S.ops.setTransition(both, 0, "none"), 0, "none") === null && S.ops.setTransition(both, 0, "none").v === 2);
  ok("transition: a version 2 deck from an earlier local build with a fade is read and saved back as version 3", (() => { const v = S.validate({ v: 2, slides: [{ id: "s", elements: [], transition: "fade" }] }); return v.v === 3 && v.slides[0].transition === "fade"; })());
}
// ---- text style (bold, italic, alignment): version 4 only when a style is used ----
{
  let d = S.newDeck(); const r = S.ops.addElement(d, 0, "text"); d = r.deck; const id = r.id, last = (x) => x.slides[0].elements[x.slides[0].elements.length - 1];
  ok("style: an unstyled deck stays version 1 and keeps no style fields", d.v === 1 && !("b" in last(d)) && !("al" in last(d)));
  let b = S.ops.updateElement(d, 0, id, { b: true, i: true, al: "center" });
  ok("style: bold, italic and centre are kept and make the deck version 4", b.v === 4 && last(b).b === true && last(b).i === true && last(b).al === "center");
  ok("style: a styled deck round-trips through validate", JSON.stringify(S.validate(JSON.parse(JSON.stringify(b)))) === JSON.stringify(b));
  ok("style: a version 4 deck is read by the current validator, an unknown version 8 is not", S.validate(JSON.parse(JSON.stringify(b))) !== null && S.validate(Object.assign(JSON.parse(JSON.stringify(b)), { v: 8 })) === null);
  ok("style: bad values are refused, nothing is changed", S.ops.updateElement(d, 0, id, { al: "diagonal" }) === null && S.ops.updateElement(d, 0, id, { b: "yes" }) === null && S.ops.updateElement(d, 0, id, { i: 1 }) === null);
  const back = S.ops.updateElement(b, 0, id, { b: false, i: false, al: "default" });
  ok("style: clearing every style drops the fields and the deck goes back to version 1", back.v === 1 && !("b" in last(back)) && !("i" in last(back)) && !("al" in last(back)));
  const partial = S.ops.updateElement(b, 0, id, { b: false });
  ok("style: clearing one style keeps the others and the version", partial.v === 4 && last(partial).i === true && last(partial).al === "center");
  const noFlag = S.validate({ v: 1, slides: [{ id: "s", elements: [{ id: "e1", type: "text", x: 0, y: 0, w: 100, h: 50, fill: "theme", text: "t", size: 20, b: true }] }] });
  ok("style: a version 1 deck that carries a style is read as version 4, not dropped", noFlag.v === 4 && noFlag.slides[0].elements[0].b === true);
  ok("style: junk style values are dropped on load", (() => { const x = S.validate({ v: 1, slides: [{ id: "s", elements: [{ id: "e1", type: "text", x: 0, y: 0, w: 100, h: 50, fill: "theme", text: "t", size: 20, b: "yes", al: "x" }] }] }); return x.v === 1 && !("b" in x.slides[0].elements[0]) && !("al" in x.slides[0].elements[0]); })());
  const m = S.ops.master_addElement(S.newDeck(), "text"); const md = m.deck || m;
  const mid = md.master.elements[0].id, ms = S.ops.master_updateElement(md, mid, { b: true });
  ok("style: a styled master item makes the deck version 4", ms && ms.v === 4 && ms.master.elements[0].b === true);
}
{
  const Old = require("./fixtures/slides_model_v3_before_style.js"); let d = S.newDeck(); const r = S.ops.addElement(d, 0, "text"); d = S.ops.updateElement(r.deck, 0, r.id, { b: true });
  ok("style: the previous (pre-style) validator refuses a styled deck, so it can never drop a style silently", d.v === 4 && Old.validate(JSON.parse(JSON.stringify(d))) === null && Old.loadDeck(JSON.stringify(d)).unreadable === true);
  ok("style: the previous validator still reads an unstyled deck from the current code", Old.validate(JSON.parse(JSON.stringify(S.newDeck()))) !== null);
}
{
  const H1 = "a".repeat(64), Old4 = require("./fixtures/slides_model_v4_before_images.js");
  const r = S.ops.addImage(S.newDeck(), 0, H1, 1000, 500, "A chart"); let d = r.deck; const e = d.slides[0].elements[0];
  ok("image: adding a picture makes a version 5 deck holding only a reference and the description", d.v === 5 && e.type === "image" && e.img === H1 && e.text === "A chart" && !("data" in e) && !("src" in e));
  ok("image: the picture is fitted into 600 by 400 and centred on the 960 by 540 slide", e.w === 600 && e.h === 300 && e.x === 180 && e.y === 120);
  ok("image: validate round-trips it and keeps version 5", S.validate(JSON.parse(JSON.stringify(d))).v === 5);
  ok("image: a reference that is not 64 lowercase hex characters refuses the deck", ["", "A".repeat(64), "a".repeat(63), "a".repeat(64) + "x", "../../x", "data:image/png;base64,AAAA"].every((h) => { const x = JSON.parse(JSON.stringify(d)); x.slides[0].elements[0].img = h; return S.validate(x) === null; }) && S.ops.addImage(S.newDeck(), 0, "zz", 10, 10, "") === null);
  ok("image: text styles are dropped from a picture and an extra src field is dropped", (() => { const x = JSON.parse(JSON.stringify(d)); Object.assign(x.slides[0].elements[0], { b: true, al: "right", src: "http://x" }); const y = S.validate(x).slides[0].elements[0]; return !("b" in y) && !("al" in y) && !("src" in y) && S.validate(x).v === 5; })());
  ok("image: the generic addElement refuses the picture type", S.ops.addElement(S.newDeck(), 0, "image") === null);
  ok("image: removing the picture returns the deck to version 1", S.ops.deleteElement(d, 0, e.id).v === 1);
  ok("image: a picture in the master is refused", S.validate({ v: 5, master: { elements: [Object.assign({}, e)] }, slides: [{ id: "s", elements: [] }] }) === null);
  let m = S.newDeck(); for (let i = 0; i < 20; i++) m = S.ops.addImage(m, 0, H1, 100, 100, "").deck;
  ok("image: 20 pictures per deck, the 21st is refused, and a deck with 21 is refused rather than trimmed", S.ops.addImage(m, 0, H1, 100, 100, "") === null && (() => { m.slides.push({ id: "s2", elements: [Object.assign({}, m.slides[0].elements[0], { id: "z1" })], notes: "" }); return S.validate(JSON.parse(JSON.stringify(m))) === null; })());
  ok("image: Undo of an added picture returns the deck to version 1 (history keeps decks only)", (() => { const h = new S.History(S.newDeck(), mem()); h.change(S.ops.addImage(h.deck, 0, H1, 10, 10, "").deck, "x"); const v5 = h.deck.v; h.undo(); return v5 === 5 && h.deck.v === 1; })());
  ok("image: describe names a picture", S.describe(d, 0)[0].startsWith("1. Picture"));
  ok("image: the previous (pre-image) validator refuses a version 5 deck so it can never drop a picture silently", d.v === 5 && Old4.validate(JSON.parse(JSON.stringify(d))) === null && Old4.loadDeck(JSON.stringify(d)).unreadable === true);
  ok("image: the previous validator still reads a styled version 4 deck from the current code", (() => { let t = S.ops.addElement(S.newDeck(), 0, "text"); t = S.ops.updateElement(t.deck, 0, t.id, { b: true }); return t.v === 4 && Old4.validate(JSON.parse(JSON.stringify(t))) !== null; })());
}
{
  const H1 = "b".repeat(64); let d = S.newDeck(); const t = S.ops.addElement(d, 0, "text"); d = S.ops.updateElement(t.deck, 0, t.id, { b: true });
  d = S.ops.addImage(d, 0, H1, 10, 10, "").deck; const pid = d.slides[0].elements[1].id;
  ok("image: deleting the last picture from a deck that also has a styled item goes to version 4, not 1", d.v === 5 && S.ops.deleteElement(d, 0, pid).v === 4);
  let m = S.ops.master_addElement(S.newDeck(), "text").deck; m = S.ops.addImage(m, 0, H1, 10, 10, "").deck;
  ok("image: deleting the last picture from a deck with a master goes to version 2", m.v === 5 && S.ops.deleteElement(m, 0, m.slides[0].elements[0].id).v === 2);
  let tr = S.ops.setTransition(S.ops.addImage(S.newDeck(), 0, H1, 10, 10, "").deck, 0, "fade");
  ok("image: deleting the last picture from a deck with a fade goes to version 3", tr.v === 5 && S.ops.deleteElement(tr, 0, tr.slides[0].elements[0].id).v === 3);
}
{
  // object animation (version 6)
  const A0 = { v: 1, theme: "light", slides: [{ id: "a", elements: [], notes: "" }] };
  let a1 = S.ops.addElement(A0, 0, "text"), a2 = S.ops.addElement(a1.deck || a1, 0, "rect"); const AD = a2.deck || a2; const [E1, E2] = AD.slides[0].elements.map((e) => e.id);
  let an = S.ops.setAnim(AD, 0, E1, "appear");
  ok("anim: setting an animation makes a version 6 deck that validates", an && an.v === 6 && S.validate(an) !== null && S.validate(an).slides[0].anim[0].el === E1);
  ok("anim: an unknown item, an unknown effect and a repeated setting are refused", S.ops.setAnim(AD, 0, "nope", "appear") === null && S.ops.setAnim(AD, 0, E1, "spin") === null && S.ops.setAnim(an, 0, E1, "appear") === null);
  let an2 = S.ops.setAnim(an, 0, E2, "fade");
  ok("anim: entries keep the order they were added in and moveAnim reorders them", an2.slides[0].anim.map((a) => a.el).join() === [E1, E2].join() && S.ops.moveAnim(an2, 0, E2, -1).slides[0].anim.map((a) => a.el).join() === [E2, E1].join() && S.ops.moveAnim(an2, 0, E1, -1) === null && S.ops.moveAnim(an2, 0, E2, 1) === null);
  ok("anim: changing the effect changes it in place; none removes it and the deck goes back to version 1", S.ops.setAnim(an2, 0, E1, "fade").slides[0].anim[0].fx === "fade" && S.ops.setAnim(S.ops.setAnim(an2, 0, E1, "none"), 0, E2, "none").v === 1 && !("anim" in S.ops.setAnim(S.ops.setAnim(an2, 0, E1, "none"), 0, E2, "none").slides[0]));
  ok("anim: deleting an animated item removes its animation entry", S.ops.deleteElement(an2, 0, E1).slides[0].anim.length === 1 && S.validate(S.ops.deleteElement(an2, 0, E1)) !== null);
  ok("anim: duplicating a slide gives the copy its own item ids in the animation list", (() => { const dd = S.ops.duplicateSlide(an2, 0), c = dd.slides[1]; return c.anim.length === 2 && c.anim.every((a, q) => c.elements.some((e) => e.id === a.el) && a.el !== an2.slides[0].anim[q].el) && S.validate(dd) !== null; })());
  const bad = (f) => { const d = JSON.parse(JSON.stringify(an2)); f(d.slides[0]); return S.validate(d) === null; };
  ok("anim: a refused file: unknown item, repeated item, unknown effect, not a list, too many", bad((s) => { s.anim[0].el = "zzz"; }) && bad((s) => { s.anim[1].el = s.anim[0].el; }) && bad((s) => { s.anim[0].fx = "spin"; }) && bad((s) => { s.anim = "x"; }) && bad((s) => { s.anim = Array.from({ length: 31 }, () => s.anim[0]); }));
  ok("anim: an empty list is dropped and the deck stays at its old version", (() => { const d = JSON.parse(JSON.stringify(AD)); d.slides[0].anim = []; const v = S.validate(d); return v && v.v === 1 && !("anim" in v.slides[0]); })());
  ok("anim: a deck with a picture and an animation is version 6 and removing the animation goes back to 5", (() => { const p = S.ops.addImage(AD, 0, "c".repeat(64), 10, 10, ""); const q = S.ops.setAnim(p.deck, 0, p.id, "appear"); return q.v === 6 && S.ops.setAnim(q, 0, p.id, "none").v === 5; })());
  ok("anim: validate itself marks an animated deck version 6, even when the file said version 1", (() => { const d = JSON.parse(JSON.stringify(an)); d.v = 1; return S.validate(d).v === 6; })());
  const stepD = an2;
  ok("anim show: forward keys reveal each entry before moving on, back keys hide them first", (() => { const f = (i, st, k) => JSON.stringify(S.showStep(stepD, i, st, k)); return f(0, 0, "ArrowRight") === '{"at":0,"step":1}' && f(0, 1, " ") === '{"at":0,"step":2}' && S.showStep(stepD, 0, 2, "ArrowRight") === null && f(0, 2, "ArrowLeft") === '{"at":0,"step":1}' && f(0, 0, "ArrowLeft") === "null"; })());
  ok("anim show: moving across slides, Home and End", (() => { const two = S.ops.addSlide(an2, 0), f = (i, st, k) => JSON.stringify(S.showStep(two, i, st, k)); return f(0, 2, "ArrowRight") === '{"at":1,"step":0}' && f(1, 0, "ArrowLeft") === '{"at":0,"step":2}' && f(1, 0, "Home") === '{"at":0,"step":0}' && f(0, 0, "End") === '{"at":1,"step":0}' && f(0, 0, "x") === "null"; })());
  ok("anim show: hiddenIds lists the items not shown yet", (() => { const h0 = S.hiddenIds(an2, 0, 0), h1 = S.hiddenIds(an2, 0, 1), h2 = S.hiddenIds(an2, 0, 2); return h0[E1] && h0[E2] && !h1[E1] && h1[E2] && !h2[E1] && !h2[E2]; })());
  ok("anim show: a slide without animation behaves as before", (() => { const f = (k) => JSON.stringify(S.showStep(AD, 0, 0, k)); return f("ArrowRight") === "null" && Object.keys(S.hiddenIds(AD, 0, 0)).length === 0; })());
}
{
  // timing: after previous (version 7)
  const A0 = { v: 1, theme: "light", slides: [{ id: "a", elements: [], notes: "" }] };
  let a1 = S.ops.addElement(A0, 0, "text"), a2 = S.ops.addElement(a1.deck || a1, 0, "rect"); const AD = a2.deck || a2; const [E1, E2] = AD.slides[0].elements.map((e) => e.id);
  const t1 = S.ops.setAnim(S.ops.setAnim(AD, 0, E1, "appear"), 0, E2, "fade"), t2 = S.ops.setAnimAfter(t1, 0, E2, true);
  ok("after: setting it makes a version 7 deck that validates and keeps the flag", t2 && t2.v === 7 && S.validate(t2) !== null && S.validate(t2).v === 7 && S.validate(t2).slides[0].anim[1].after === true && S.validate(t2).slides[0].anim[0].after === undefined);
  ok("after: setting it back to click returns to version 6, and a no-op is refused", S.ops.setAnimAfter(t2, 0, E2, false).v === 6 && S.ops.setAnimAfter(t1, 0, E2, false) === null && S.ops.setAnimAfter(t1, 0, "nope", true) === null);
  ok("after: a non-boolean flag in a file is refused, not guessed", (() => { const bad = JSON.parse(JSON.stringify(t2)); bad.slides[0].anim[1].after = "yes"; return S.validate(bad) === null; })());
  ok("after: validate marks the deck 7 even when the file said version 6", (() => { const d = JSON.parse(JSON.stringify(t2)); d.v = 6; return S.validate(d).v === 7; })());
  ok("after: the flag moves with its entry, and removing the animation removes it", (() => { const m = S.ops.moveAnim(t2, 0, E2, -1); return m.slides[0].anim[0].el === E2 && m.slides[0].anim[0].after === true && S.ops.setAnim(t2, 0, E2, "none").v === 6; })());
  ok("after: autoNext is true only when the next entry is after-previous", S.autoNext(t2, 0, 1) === true && S.autoNext(t2, 0, 0) === false && S.autoNext(t2, 0, 2) === false && S.autoNext(AD, 0, 0) === false && S.AUTO_MS === 1000);
  ok("after: duplicating a slide keeps the flag on the copy, and the deck stays valid at version 7", (() => { const d = S.ops.duplicateSlide(t2, 0); if (!d) return false; const v = S.validate(d); return d.slides.length === 2 && d.slides[1].anim.length === 2 && d.slides[1].anim[1].after === true && d.slides[1].anim[0].after === undefined && d.slides[1].anim[1].el !== E2 && v !== null && v.v === 7 && v.slides[1].anim[1].after === true; })());
}
{
  // snapping guides while dragging
  const A0 = { v: 1, theme: "light", slides: [{ id: "a", elements: [], notes: "" }] };
  let r1 = S.ops.addElement(A0, 0, "rect"), r2 = S.ops.addElement(r1.deck || r1, 0, "rect"); const D = r2.deck || r2; const [P, Q] = D.slides[0].elements.map((e) => e.id);
  const setp = (d, id, o) => S.ops.updateElement(d, 0, id, o);
  const D2 = setp(setp(D, P, { x: 100, y: 100, w: 200, h: 100 }), Q, { x: 500, y: 300, w: 100, h: 100 });
  ok("snap: a move within 6 of another item's left edge shifts to it and reports the line", (() => { const r = S.snapBox(D2, 0, Q, { x: 103, y: 300, w: 100, h: 100 }, "move"); return r.dx === -3 && r.vx.indexOf(100) >= 0; })());
  ok("snap: 7 away on x gives no shift on x", S.snapBox(D2, 0, Q, { x: 107, y: 301, w: 100, h: 100 }, "move").dx === 0);
  ok("snap: the middle of the item snaps to the middle of the slide", (() => { const r = S.snapBox(D2, 0, Q, { x: 432, y: 10, w: 100, h: 100 }, "move"); return r.dx === -2 && r.vx.indexOf(480) >= 0; })());
  ok("snap: the right edge snaps to the slide's right edge, the bottom to its bottom", (() => { const r = S.snapBox(D2, 0, Q, { x: 858, y: 438, w: 100, h: 100 }, "move"); return r.dx === 2 && r.dy === 2 && r.vx.indexOf(960) >= 0 && r.hy.indexOf(540) >= 0; })());
  ok("snap: an item never snaps to itself", (() => { const r = S.snapBox(D2, 0, Q, { x: 502, y: 302, w: 100, h: 100 }, "move"); return r.dx === 0 && r.dy === 0 && r.vx.length === 0 && r.hy.length === 0; })());
  ok("snap: resize snaps the right and bottom edge to the other item, and never the left or top", (() => { const r = S.snapBox(D2, 0, P, { x: 100, y: 100, w: 397, h: 198 }, "resize"), q = S.snapBox(D2, 0, P, { x: 104, y: 104, w: 150, h: 150 }, "resize"); return r.dx === 3 && r.dy === 2 && r.vx.indexOf(500) >= 0 && r.hy.indexOf(300) >= 0 && q.dx === 0 && q.dy === 0; })());
  ok("snap: the nearest of several lines wins", (() => { const r = S.snapBox(D2, 0, Q, { x: 298, y: 300, w: 100, h: 100 }, "move"); return r.dx === 2 && r.vx.indexOf(300) >= 0; })());
  ok("snap: a bad slide or box gives no shift", (() => { const r = S.snapBox(D2, 9, Q, { x: 1, y: 1, w: 1, h: 1 }, "move"), q = S.snapBox(D2, 0, Q, null, "move"); return r.dx === 0 && q.dx === 0; })());
}
ok("snap: after a snapped resize or move the existing clamps still apply (minimum size 10, inside the slide)", (() => { let d = S.newDeck(); const r = S.ops.addElement(d, 0, "rect"); d = r.deck || r; const id = d.slides[0].elements[0].id, a = S.ops.updateElement(d, 0, id, { w: 3 }).slides[0].elements[0], b = S.ops.updateElement(d, 0, id, { x: 955, w: 100 }).slides[0].elements[0]; return a.w === 10 && b.x + b.w <= 960 && b.w === 100; })());
{
  // Duplicate item
  const H64 = "a".repeat(64), snapJ = (x) => JSON.stringify(x);
  let q = S.newDeck(); const r1 = S.ops.addElement(q, 0, "text"); q = r1.deck;
  q = S.ops.updateElement(q, 0, r1.id, { text: "Hello", size: 40, fill: "#2e6f95", b: true, i: true, al: "center", x: 100, y: 120, w: 300, h: 90 });
  const r2 = S.ops.addElement(q, 0, "rect"); q = r2.deck;
  const before = snapJ(q), dup = S.ops.duplicateElement(q, 0, r1.id), els = dup && dup.deck.slides[0].elements;
  ok("dup: the copy has a fresh id and sits right above the original", !!dup && els.length === 3 && els[0].id === r1.id && els[1].id === dup.id && dup.id !== r1.id && els[2].id === r2.id);
  ok("dup: ids stay unique across the deck", new Set(els.map((e) => e.id)).size === 3);
  ok("dup: text, size, fill, bold, italic and align are kept", els[1].text === "Hello" && els[1].size === 40 && els[1].fill === "#2e6f95" && els[1].b === true && els[1].i === true && els[1].al === "center");
  ok("dup: the copy is 20 right and 20 down, same size", els[1].x === 120 && els[1].y === 140 && els[1].w === 300 && els[1].h === 90);
  ok("dup: the input deck is not changed", snapJ(q) === before);
  ok("dup: the result is a valid deck as it stands", snapJ(S.validate(dup.deck)) === snapJ(dup.deck));
  ok("dup: the copy of a styled deck is version 4", dup.deck.v === 4);
  { const p = S.ops.addElement(S.newDeck(), 0, "rect"), pd = S.ops.duplicateElement(p.deck, 0, p.id); ok("dup: an unstyled deck stays version 1 after a copy", pd.deck.v === 1 && !("b" in pd.deck.slides[0].elements[1])); }
  { const e = S.ops.addElement(S.newDeck(), 0, "rect"); const d2 = S.ops.updateElement(e.deck, 0, e.id, { x: 760, y: 420, w: 200, h: 120 }); const c = S.ops.duplicateElement(d2, 0, e.id).deck.slides[0].elements[1];
    ok("dup: next to the right or bottom edge the copy moves left and up and stays inside the slide", c.x === 740 && c.y === 400 && c.x + c.w <= 960 && c.y + c.h <= 540); }
  { const e = S.ops.addElement(S.newDeck(), 0, "rect"); const d2 = S.ops.updateElement(e.deck, 0, e.id, { x: 760, y: 420, w: 200, h: 120 }), c1 = S.ops.duplicateElement(d2, 0, e.id); const c2 = S.ops.duplicateElement(c1.deck, 0, c1.id);
    ok("dup: a copy of a copy is again one more item with another fresh id", c2.deck.slides[0].elements.length === 3 && new Set(c2.deck.slides[0].elements.map((x) => x.id)).size === 3); }
  { let w = S.newDeck(); for (let i = 0; i < 29; i++) w = S.ops.addElement(w, 0, "rect").deck; const id = w.slides[0].elements[0].id;
    const ok29 = S.ops.duplicateElement(w, 0, id); ok("dup: the 30th item is allowed", !!ok29 && ok29.deck.slides[0].elements.length === 30); ok("dup: at the cap of 30 items it returns null", S.ops.duplicateElement(ok29.deck, 0, id) === null); }
  ok("dup: bad slide, bad id or a non-string id return null", S.ops.duplicateElement(q, 9, r1.id) === null && S.ops.duplicateElement(q, 0, "nope") === null && S.ops.duplicateElement(q, 0, undefined) === null);
  { let pd = S.newDeck(); const im = S.ops.addImage(pd, 0, H64, 400, 300, "chart"); pd = im.deck; const c = S.ops.duplicateElement(pd, 0, im.id);
    const e2 = c.deck.slides[0].elements;
    ok("dup: a picture copy refers to the same stored bytes and keeps its description and size", e2.length === 2 && e2[1].type === "image" && e2[1].img === H64 && e2[1].text === "chart" && e2[1].w === e2[0].w && e2[1].h === e2[0].h && c.deck.v === 5);
    ok("dup: the picture copy survives validate", S.validate(c.deck).slides[0].elements[1].img === H64);
    let full = pd; for (let i = 0; i < 19; i++) { full = S.ops.addImage(full, 0, H64, 100, 100, "").deck; }
    const tx = S.ops.addElement(full, 0, "text"); ok("dup: with 20 pictures a text item still copies", !!S.ops.duplicateElement(tx.deck, 0, tx.id));
    ok("dup: with 20 pictures the picture copy returns null", S.ops.duplicateElement(full, 0, im.id) === null); }
  { const t = S.ops.addElement(S.newDeck(), 0, "text"); const d3 = S.ops.setAnim(S.ops.updateElement(t.deck, 0, t.id, { text: "a" }), 0, t.id, "fade"), c = S.ops.duplicateElement(d3, 0, t.id);
    ok("dup: the animation is not copied and the original keeps it", c.deck.slides[0].anim.length === 1 && c.deck.slides[0].anim[0].el === t.id && c.deck.v === 6); }
  { const m = S.ops.master_addElement(S.newDeck(), "rect"); const mid = m.deck.master.elements[0].id; const c = S.ops.master_duplicateElement(m.deck, mid);
    ok("dup: a master item can be copied", !!c && c.deck.master.elements.length === 2 && c.deck.master.elements[1].id === c.id && c.deck.v === 2);
    let mm = m.deck; for (let i = 0; i < 9; i++) mm = S.ops.master_addElement(mm, "rect").deck;
    ok("dup: at the master cap of 10 items a master copy returns null", mm.master.elements.length === 10 && S.ops.master_duplicateElement(mm, mid) === null); }
  { const Hh = new S.History(S.newDeck(), mem(), 100); const a1 = S.ops.addElement(Hh.deck, 0, "text"); Hh.change(a1.deck, "x"); const c = S.ops.duplicateElement(Hh.deck, 0, a1.id); Hh.change(c.deck, "dup"); const n1 = Hh.deck.slides[0].elements.length; Hh.undo();
    ok("dup: one undo removes the copy, redo brings it back", n1 === 2 && Hh.deck.slides[0].elements.length === 1 && (Hh.redo(), Hh.deck.slides[0].elements.length === 2)); }
}
console.log(`${n - bad}/${n} slides model rows passed`); process.exit(bad ? 1 : 0);
