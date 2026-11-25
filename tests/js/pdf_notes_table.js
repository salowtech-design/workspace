const N = require("../../static/pdf/notes.js");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const pend = [];
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
const crypto = require("crypto");
ok("plain sha256 matches node for many lengths, including block edges", [0, 1, 3, 55, 56, 57, 63, 64, 65, 119, 120, 1000, 100000].every((len) => { const b = new Uint8Array(len).map((_, i) => (i * 7 + len) & 255); return N.sha256Plain(b) === crypto.createHash("sha256").update(b).digest("hex"); }));
ok("two files with the same first bytes get different keys", (() => { const a = new Uint8Array(2000).fill(65), b = new Uint8Array(2000).fill(65); b[1999] = 66; return N.sha256Plain(a) !== N.sha256Plain(b); })());
pend.push(N.sha256Hex(new Uint8Array([1, 2, 3])).then((h) => ok("sha256Hex resolves to the same value as node", h === crypto.createHash("sha256").update(Buffer.from([1, 2, 3])).digest("hex"))));
pend.push(N.sha256Hex(new Uint8Array(0)).then((h) => ok("sha256 of empty input is the known vector", h === "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")));
ok("sha256 known vector for abc (plain path)", N.sha256Plain(new Uint8Array([97, 98, 99])) === "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
ok("save plan: stale, none, remove, limit, set", N.plan("a", "b", 1, 0) === "stale" && N.plan(null, null, 0, 0) === "none" && N.plan("a", "a", 0, 5) === "remove" && N.plan(null, null, 1, N.MAX_FILES) === "limit" && N.plan("a", "a", 1, N.MAX_FILES) === "set" && N.plan(null, null, 1, N.MAX_FILES - 1) === "set");
ok("page-level notes have no position and still round trip", (() => { const a = N.add([], 4, null, null, "whole page", () => 0.3); const l = N.load(N.dump(a.notes)); return !a.error && l.notes[0].x === null && l.notes[0].y === null && !l.unreadable && N.load(N.dump([{ id: "abcdef12", page: 1, x: null, y: 0.5, text: "t" }])).unreadable; })());
const mem = () => { const m = {}; return { m, getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = v; }, removeItem: (k) => { delete m[k]; } }; };
ok("interleaving: a queued save keeps the baseline it was made against, so a newer value from another tab is not overwritten", (() => {
  const st = mem(); const a = N.add([], 1, 0.1, 0.1, "first", r).notes; st.setItem("k", N.dump(a));
  let tabLast = st.getItem("k"); const base = tabLast; // tab one loaded this, then a save was queued with baseline base
  const queued = N.add(a, 1, 0.2, 0.2, "from tab one", r).notes;
  const other = N.add(a, 2, 0.3, 0.3, "from tab two", r).notes; st.setItem("k", N.dump(other)); // tab two writes first
  tabLast = st.getItem("k"); // the storage event makes tab one adopt the new value while its save waits for the lock
  const res = N.applySave(st, "k", base, queued, () => 1);
  return res.stale === true && st.getItem("k") === N.dump(other) && tabLast === N.dump(other);
})());
ok("interleaving: with the current baseline the same save is applied", (() => { const st = mem(); const a = N.add([], 1, 0.1, 0.1, "x", r).notes; st.setItem("k", N.dump(a)); const res = N.applySave(st, "k", st.getItem("k"), N.add(a, 1, 0.2, 0.2, "y", r).notes, () => 1); return res.ok && JSON.parse(st.getItem("k")).notes.length === 2; })());
ok("delete-all and last-note removal go through the same guard", (() => { const st = mem(); const a = N.add([], 1, 0.1, 0.1, "x", r).notes; st.setItem("k", N.dump(a)); const stale = N.applySave(st, "k", "other", [], () => 1); const good = N.applySave(st, "k", st.getItem("k"), [], () => 1); return stale.stale && good.ok && st.getItem("k") === null; })());
ok("file limit is applied inside the guarded write", N.applySave(mem(), "k", null, N.add([], 1, 0.1, 0.1, "x", r).notes, () => N.MAX_FILES).limit === true);
Promise.all(pend).then(() => { console.log(`${n - bad}/${n} pdf notes rows passed`); process.exit(bad ? 1 : 0); });
