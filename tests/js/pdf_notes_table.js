const N = require("../../static/pdf/notes.js");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const r = () => 0.5;
ok("nothing stored is an empty readable list", (() => { const l = N.load(null); return l.notes.length === 0 && !l.unreadable; })());
ok("add then dump then load round trips exactly", (() => { const a = N.add([], 3, 0.25, 0.5, "  hello   there ", r); const l = N.load(N.dump(a.notes)); return !a.error && l.notes.length === 1 && l.notes[0].text === "hello there" && l.notes[0].page === 3 && !l.unreadable; })());
ok("empty, overlong and over-cap notes are refused with a message", (() => { const e = N.add([], 1, 0, 0, "   ", r).error, o = N.add([], 1, 0, 0, "x".repeat(501), r).error; let l = []; for (let i = 0; i < 200; i++) l = N.add(l, 1, 0, 0, "n", () => (i + 1) / 300).notes || l; return !!e && !!o && l.length <= 200 && !!N.add(Array.from({ length: 200 }, (_, i) => ({ id: "id" + String(i).padStart(6, "0"), page: 1, x: 0, y: 0, text: "t" })), 1, 0, 0, "more", r).error; })());
ok("position is clamped into the page", (() => { const a = N.add([], 1, -3, 9, "t", r); return a.notes[0].x === 0 && a.notes[0].y === 1; })());
ok("damaged or foreign stored data is unreadable, not silently dropped", ["{", "[]", '{"v":2,"notes":[]}', '{"v":1,"notes":[{"id":"a"}]}', '{"v":1,"notes":"x"}', "null", '{"v":1,"notes":[{"id":"abcdef1","page":0,"x":0,"y":0,"text":"t"}]}'].every((s) => N.load(s).unreadable));
ok("duplicate ids are unreadable", (() => { const a = { id: "abcdef12", page: 1, x: 0, y: 0, text: "t" }; return N.load(N.dump([a, a])).unreadable; })());
ok("remove and forPage", (() => { let l = N.add([], 1, 0, 0, "a", () => 0.1).notes; l = N.add(l, 2, 0, 0, "b", () => 0.7).notes; return N.forPage(l, 2).length === 1 && N.remove(l, l[0].id).length === 1 && N.remove(l, "nope").length === 2; })());
ok("text export is sorted by page and says it is not inside the PDF", (() => { let l = N.add([], 5, 0, 0, "late", () => 0.1).notes; l = N.add(l, 2, 0, 0, "early", () => 0.7).notes; const t = N.toText(l, "a.pdf"); return t.indexOf("Page 2: early") < t.indexOf("Page 5: late") && /not inside the PDF/.test(t); })());
ok("markup in note text stays plain text", N.add([], 1, 0, 0, "<img src=x onerror=1>", r).notes[0].text === "<img src=x onerror=1>");
console.log(`${n - bad}/${n} pdf notes rows passed`);
process.exit(bad ? 1 : 0);
