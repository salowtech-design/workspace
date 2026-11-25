const M = require("../../static/sheets/model.js");
const { parseAddr } = require("../../static/sheets/formula.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const mk = (o) => Object.assign(Object.create(null), o);
const mem = (fail) => ({ saved: [], save(c) { if (fail && fail.on) return false; this.saved.push(Object.assign({}, c)); return true; } });

let st = mem(), h = new M.History(mk({}), st, 3);
h.change(mk({ A1: "1" })); h.change(mk({ A1: "2" })); h.change(mk({ A1: "3" })); h.change(mk({ A1: "4" }));
ok("history is bounded", h.past.length === 3);
ok("undo steps back one state at a time", h.undo() && h.cells.A1 === "3" && h.undo() && h.cells.A1 === "2");
ok("redo restores", h.redo() && h.cells.A1 === "3");
ok("new edit clears redo", (h.change(mk({ A1: "9" })), h.future.length === 0));
ok("unchanged edit is not a step", h.change(mk({ A1: "9" })) === false);
ok("undo after clear brings cells back", (h.change(mk({})), h.undo() && h.cells.A1 === "9"));
ok("undo of an import does not touch earlier steps", (() => { const g = new M.History(mk({ A1: "1" }), mem(), 100); g.change(mk({ A1: "1", B1: "later" })); g.change(mk({ A1: "7" })); g.undo(); return g.cells.A1 === "1" && g.cells.B1 === "later" && (g.undo(), g.cells.B1 === undefined); })());
ok("second import keeps the first state as a step", (() => { const g = new M.History(mk({ A1: "orig" }), mem(), 100); g.change(mk({ A1: "i1" })); g.change(mk({ A1: "i2" })); g.undo(); g.undo(); return g.cells.A1 === "orig"; })());
ok("empty undo and redo do nothing", (() => { const g = new M.History(mk({}), mem(), 100); return !g.undo() && !g.redo(); })());

const fail = { on: true }; st = mem(fail); h = new M.History(mk({}), st, 100);
h.change(mk({ A1: "1" }), "Imported 1 cells.");
ok("blocked storage keeps the failure visible next to the note", h.saveFailed && /would not store/.test(h.note) && /Imported 1 cells/.test(h.note));
ok("failure persists through undo", (h.undo(), h.saveFailed && /would not store/.test(h.note) && /Undone/.test(h.note)));
ok("failure persists through redo", (h.redo(), h.saveFailed && /would not store/.test(h.note)));
ok("failure persists through clear", (h.change(mk({}), h.saveFailed && /would not store/.test(h.note))));
fail.on = false; h.change(mk({ A1: "2" }));
ok("a later successful save clears the failure", !h.saveFailed && h.note === "Saved on this device.");
ok("a throwing store counts as failure", (() => { const g = new M.History(mk({}), { save() { throw new Error("quota"); } }, 100); g.change(mk({ A1: "1" })); return g.saveFailed; })());

ok("import: empty refused", M.clean({}, parseAddr, 2000).count === 0);
ok("import: arrays and non-objects refused", M.clean([1], parseAddr, 2000) === null && M.clean("x", parseAddr, 2000) === null && M.clean(null, parseAddr, 2000) === null);
ok("import: prototype and bad keys skipped", (() => { const r = M.clean(JSON.parse('{"__proto__":"x","constructor":"y","A1":"1","ZZ99":"2","A2":5}'), parseAddr, 2000); return r.count === 1 && r.skipped === 4 && Object.getPrototypeOf(r.cells) === null; })());
ok("import: over-long text skipped", M.clean({ A1: "x".repeat(2001) }, parseAddr, 2000).count === 0);

let c = M.chartPoints([{ a: "A1", v: 5 }, { a: "A2", v: -8 }, { a: "A3", v: 0 }, { a: "A4", v: "x" }, { a: "A5", v: NaN }]);
ok("chart: lo, hi and skips", c.lo === -8 && c.hi === 5 && c.skipped === 2);
ok("chart: zero value has no bar", c.bars.find((b) => b.a === "A3").bar === false && c.bars.find((b) => b.a === "A1").bar === true);
ok("chart: all zero still has a range", (() => { const z = M.chartPoints([{ a: "A1", v: 0 }]); return z.hi > z.lo; })());
ok("chart: nothing numeric gives null", M.chartPoints([{ a: "A1", v: "x" }]) === null && M.chartPoints([]) === null);
ok("chart: all negative keeps zero on the axis", (() => { const z = M.chartPoints([{ a: "A1", v: -3 }]); return z.hi === 0 && z.lo === -3; })());

ok("ime: composing Enter flagged", M.isComposing({ key: "Enter", isComposing: true }) && M.isComposing({ key: "Enter", keyCode: 229 }) && !M.isComposing({ key: "Enter", keyCode: 13 }));
ok("keys: Enter and Shift+Enter", M.keyMove("Enter", false)[1] === 1 && M.keyMove("Enter", true)[1] === -1);
ok("keys: up and down always move", M.keyMove("ArrowUp", false)[1] === -1 && M.keyMove("ArrowDown", false, false, false)[1] === 1);
ok("keys: left and right only at the edge", M.keyMove("ArrowLeft", false, false, false) === null && M.keyMove("ArrowLeft", false, true, false)[0] === -1 && M.keyMove("ArrowRight", false, false, false) === null && M.keyMove("ArrowRight", false, false, true)[0] === 1);
ok("keys: other keys do nothing", M.keyMove("a", false, true, true) === null);
{
  const F = require("../../static/sheets/formula.js");
  const csv = (obj) => { const c = Object.create(null); Object.keys(obj).forEach((k) => { c[k] = obj[k]; }); const sh = new F.Sheet(c); return M.toCsv(c, F.parseAddr, (a) => sh.get(a), F.isErr); };
  ok("csv: empty sheet gives nothing", csv({}) === null);
  ok("csv: values not formulas, over the box that holds the cells, CRLF lines", (() => { const r = csv({ A1: "1", B1: "2", A2: "=A1+B1", C2: "x" }); return r && r.text === "1,2,\r\n3,,x\r\n" && r.rows === 2 && r.cols === 3; })());
  ok("csv: numbers that are negative are not touched", csv({ A1: "-5", B1: "=0-2" }).text === "-5,-2\r\n");
  ok("csv: text starting with = + - @ tab or CR gets an apostrophe, and is counted", (() => { const r = csv({ A1: "-note", B1: "+1x", C1: "@sum", D1: "\tx", E1: '=CONCAT("=","HYPERLINK")' }); return r.text === "'-note,'+1x,'@sum,'\tx,'=HYPERLINK\r\n" && r.guarded === 5; })());
  ok("csv: blanks, no-break and zero-width characters before the trigger do not hide it, fullwidth look-alikes are guarded", (() => { const r = csv({ A1: "  =1+1", B1: "\u00a0@x", C1: "\u200b-x", D1: "\uff1dx", E1: "\ufeff+x", F1: "\u0001=x" }); return r.guarded === 6 && r.text.split(",").every((f) => f.startsWith("'") || f.startsWith("\"'")); })());
  ok("csv: text that only contains a trigger character later, or other Unicode, is left alone", (() => { const r = csv({ A1: "a=b", B1: "caf\u00e9 \u2013 x", C1: "\u00a0 plain" }); return r.guarded === 0 && r.text === "a=b,caf\u00e9 \u2013 x,\u00a0 plain\r\n"; })());
  ok("csv: the engine rejects wider addresses, so none can be written under a wrong letter", F.parseAddr("AA1") === null && F.parseAddr("A1001") === null && csv({ AA1: "x", A1001: "y" }) === null);
  ok("csv: the largest sheet (A1 to Z1000) is written in full with the right row and column counts", (() => { const c = {}; for (let r = 1; r <= 1000; r++) for (let k = 1; k <= 26; k++) c[String.fromCharCode(64 + k) + r] = "v"; const t0 = Date.now(), r = csv(c); return r.rows === 1000 && r.cols === 26 && r.text.split("\r\n").length === 1001 && Date.now() - t0 < 5000; })());
  ok("csv: commas, quotes and newlines are quoted and escaped", csv({ A1: 'a,b', B1: 'say "hi"', C1: "l1\nl2" }).text === '"a,b","say ""hi""","l1\nl2"\r\n');
  ok("csv: an error shows its code and is not guarded", (() => { const r = csv({ A1: "=1/0" }); return /^#[A-Z/0!]+\r\n$/.test(r.text) && r.guarded === 0; })());
  ok("csv: ordinary text is unchanged and booleans are TRUE or FALSE", csv({ A1: "hello", B1: "TRUE", C1: "=1=2" }).text === "hello,TRUE,FALSE\r\n");
}
const F = require("../../static/sheets/formula.js");
const col = (c, k) => Object.keys(c).filter((a) => a[0] === k).sort((x, y) => parseInt(x.slice(1)) - parseInt(y.slice(1))).map((a) => c[a]);
const fruit = mk({ A1: "Name", B1: "Qty", A2: "pear", B2: "3", A3: "Apple", B3: "10", B4: "7", A5: "fig", B5: "abc", A6: "x", B6: "1" });
{
  const r = M.sortRange(fruit, parseAddr, { c1: 1, c2: 2, r1: 1, r2: 6 }, 1, false, true);
  ok("sort A to Z keeps the header, ignores case, puts blanks last and moves whole rows", col(r.cells, "A").join("|") === "Name|Apple|fig|pear|x" && r.cells.B6 === "7" && !("A6" in r.cells));
  ok("rows stay together after a sort", r.cells.A2 === "Apple" && r.cells.B2 === "10" && r.cells.A4 === "pear" && r.cells.B4 === "3" && r.cells.B6 === "7" && !("A6" in r.cells));
  ok("sort does not change its input", fruit.A2 === "pear" && fruit.B6 === "1");
  const q = M.sortRange(fruit, parseAddr, { c1: 1, c2: 2, r1: 1, r2: 6 }, 2, false, true);
  ok("numbers sort before text, by number not by text", [q.cells.B2, q.cells.B3, q.cells.B4, q.cells.B5, q.cells.B6].join(",") === "1,3,7,10,abc");
  const d = M.sortRange(fruit, parseAddr, { c1: 1, c2: 2, r1: 1, r2: 6 }, 2, true, true);
  ok("descending reverses the order of keys", [d.cells.B2, d.cells.B3, d.cells.B4, d.cells.B5, d.cells.B6].join(",") === "abc,10,7,3,1");
ok("descending still puts a blank key last", (() => { const x = M.sortRange(fruit, parseAddr, { c1: 1, c2: 2, r1: 1, r2: 6 }, 1, true, true); return x.cells.A2 === "x" && x.cells.A3 === "pear" && x.cells.B6 === "7" && !x.cells.A6; })());
  const t = M.sortRange(mk({ A1: "b", B1: "1", A2: "a", B2: "2", A3: "b", B3: "3", A4: "a", B4: "4" }), parseAddr, { c1: 1, c2: 2, r1: 1, r2: 4 }, 1, false, false);
  ok("equal keys keep their order (stable)", col(t.cells, "B").join(",") === "2,4,1,3");
  const nh = M.sortRange(fruit, parseAddr, { c1: 1, c2: 2, r1: 1, r2: 6 }, 1, false, false);
  ok("without a header the first row sorts too", nh.cells.A1 === "Apple" || nh.cells.A1 === "fig");
}
ok("sort refuses a range holding a formula and says how many", (() => { const r = M.sortRange(mk({ A1: "2", A2: "=A1+1", A3: "1", B1: "=1" }), parseAddr, { c1: 1, c2: 2, r1: 1, r2: 3 }, 1, false, false); return !r.cells && /2 formulas/.test(r.error); })());
ok("sort refuses a key outside the range and fewer than two rows", M.sortRange(mk({}), parseAddr, { c1: 1, c2: 2, r1: 1, r2: 5 }, 3, false, false).error !== undefined && M.sortRange(mk({ A1: "1" }), parseAddr, { c1: 1, c2: 1, r1: 1, r2: 1 }, 1, false, false).error !== undefined && M.sortRange(mk({ A1: "h", A2: "1" }), parseAddr, { c1: 1, c2: 1, r1: 1, r2: 2 }, 1, false, true).error !== undefined);
ok("sort only touches cells inside the range", (() => { const c = mk({ A1: "3", A2: "1", C1: "keep", D9: "keep2", A3: "2" }); const r = M.sortRange(c, parseAddr, { c1: 1, c2: 1, r1: 1, r2: 3 }, 1, false, false); return r.cells.C1 === "keep" && r.cells.D9 === "keep2" && col(r.cells, "A").join(",") === "1,2,3"; })());
ok("sort of sorted data moves nothing", M.sortRange(mk({ A1: "1", A2: "2", A3: "3" }), parseAddr, { c1: 1, c2: 1, r1: 1, r2: 3 }, 1, false, false).moved === 0);
ok("sort treats numbers like 1e3, -2 and .5 as numbers but not 12abc or Infinity", M.toNum("1e3") === 1000 && M.toNum("-2") === -2 && M.toNum(".5") === 0.5 && M.toNum("12abc") === null && M.toNum("Infinity") === null && M.toNum("  ") === null);
{
  const sh = new F.Sheet({ A1: "Item", A2: "Apple", A3: "Banana", A4: "apricot", A5: "=1+1", A6: "", B1: "n", B2: "5", B3: "12", B4: "abc", B5: "=B2*2", B6: "-1" });
  const g = (a) => sh.get(a);
  const fr = (col, op, val, header) => M.filterRows(g, F.isErr, 1, 6, col, op, val, header);
  ok("filter contains ignores case and keeps the header row", (() => { const r = fr(1, "contains", "AP", true); return r.vis[1] && r.vis[2] && r.vis[4] && !r.vis[3] && r.shown === 2 && r.total === 5; })());
  ok("filter uses displayed formula results", (() => { const r = fr(1, "equals", "2", true); return r.vis[5] && r.shown === 1; })());
  ok("filter greater and less compare numbers only, text never matches", (() => { const g1 = fr(2, "gt", "4", true), l1 = fr(2, "lt", "6", true); return [2, 3, 5].every((r) => g1.vis[r]) && !g1.vis[4] && !g1.vis[6] && l1.vis[2] && l1.vis[6] && !l1.vis[3] && !l1.vis[4]; })());
  ok("filter blank and not blank", fr(1, "blank", "", true).shown === 1 && fr(1, "nonblank", "", true).shown === 4);
  ok("filter without a header counts row 1 as data", fr(1, "contains", "item", false).vis[1] === true && fr(1, "contains", "item", false).total === 6);
  ok("filter refuses a bad operator, an empty value and a non-number", fr(1, "xx", "a", true).error !== undefined && fr(1, "contains", "  ", true).error !== undefined && fr(2, "gt", "abc", true).error !== undefined);
  ok("filter with no match shows zero rows without error", (() => { const r = fr(1, "contains", "zzz", true); return r.shown === 0 && !r.error && r.vis[1]; })());
}
ok("sort treats a cell as a formula exactly when the engine does (first character =, no trimming)", (() => { const c = mk({ A1: " =1+1", A2: "b", A3: "a" }); const r = M.sortRange(c, parseAddr, { c1: 1, c2: 1, r1: 1, r2: 3 }, 1, false, false); const sh = new F.Sheet({ A1: " =1+1" }); return !r.error && typeof sh.get("A1") === "string" && M.sortRange(mk({ A1: "=1", A2: "b" }), parseAddr, { c1: 1, c2: 1, r1: 1, r2: 2 }, 1, false, false).error !== undefined; })());
ok("sort and the engine agree on which typed values are numbers", ["1", " 2 ", "-3", "+4", ".5", "6.", "1e3", "1E3", "1e999", "12abc", "Infinity", "NaN", "0x10", "1,5", "TRUE", "", " "].every((t) => { const v = new F.Sheet({ A1: t }).get("A1"); return (M.toNum(t) !== null) === (typeof v === "number"); }));
{
  const L = (m) => JSON.stringify(m.tabs.map((t) => t.name));
  const d0 = M.loadTabs(null);
  ok("tabs: no stored value gives one default tab, readable", !d0.unreadable && d0.meta.tabs.length === 1 && d0.meta.active === "t0" && d0.meta.tabs[0].name === "Sheet 1");
  ok("tabs: damaged or wrong-shape stored values are reported as unreadable, never empty", ["{", "[]", "{\"v\":2,\"tabs\":[]}", "{\"v\":1,\"tabs\":[]}", JSON.stringify({ v: 1, tabs: [{ id: "x", name: "a" }] }), JSON.stringify({ v: 1, tabs: [{ id: "t1", name: "A" }, { id: "t2", name: "a" }] }), JSON.stringify({ v: 1, tabs: [{ id: "t1", name: "A" }, { id: "t1", name: "B" }] }), JSON.stringify({ v: 1, tabs: [{ id: "t1", name: "  " }] })].every((r) => M.loadTabs(r).unreadable === true));
  ok("tabs: a stored active id that does not exist falls back to the first tab", M.loadTabs(JSON.stringify({ v: 1, active: "t9", tabs: [{ id: "t1", name: "A" }, { id: "t2", name: "B" }] })).meta.active === "t1");
  const a1 = M.addTab(d0.meta);
  ok("tabs: add makes a new active tab with a free name and does not change the input", a1.tabs.length === 2 && a1.active === "t1" && a1.tabs[1].name === "Sheet 2" && d0.meta.tabs.length === 1);
  let m10 = d0.meta; for (let i = 0; i < 9; i++) m10 = M.addTab(m10);
  ok("tabs: ten tabs at most, unique ids and names", m10.tabs.length === 10 && M.addTab(m10) === null && new Set(m10.tabs.map((t) => t.id)).size === 10 && new Set(m10.tabs.map((t) => t.name.toLowerCase())).size === 10);
  ok("tabs: add skips a name that is taken", (() => { const m = M.renameTab(a1, "t1", "Sheet 3").meta; const n = M.addTab(m); return n.tabs[2].name !== "Sheet 3" && new Set(n.tabs.map((t) => t.name)).size === 3; })());
  ok("tabs: rename trims, collapses blanks, refuses empty and duplicates (ignoring case)", (() => { const r = M.renameTab(a1, "t1", "  Q1   Totals "); return r.name === "Q1 Totals" && M.renameTab(a1, "t1", "  ").error !== undefined && M.renameTab(a1, "t1", "sheet 1").error !== undefined && M.renameTab(a1, "t1", "SHEET 2").meta.tabs[1].name === "SHEET 2" && M.renameTab(a1, "zz", "x").error !== undefined; })());
  ok("tabs: rename cuts a long name by code point and says it was cut", (() => { const r = M.renameTab(a1, "t1", "\u{1F600}".repeat(40)); return Array.from(r.name).length === 30 && r.cut === true && !/[\ud800-\udbff]$/.test(r.name); })());
  ok("tabs: delete refuses the last tab, returns the removed tab, moves active to a neighbour", (() => { const r = M.deleteTab(a1, "t1"); return M.deleteTab(d0.meta, "t0").error !== undefined && r.removed.id === "t1" && r.meta.active === "t0" && r.meta.tabs.length === 1 && M.deleteTab(a1, "t5").error !== undefined; })());
  ok("tabs: deleting the first tab keeps the rest and a valid active tab", (() => { const r = M.deleteTab(a1, "t0"); return r.meta.active === "t1" && L(r.meta) === JSON.stringify(["Sheet 2"]); })());
  ok("tabs: a new tab never reuses the id of a deleted first tab", (() => { const r = M.deleteTab(a1, "t0").meta; const n = M.addTab(r); return n.tabs.every((t) => t.id !== "t0") && new Set(n.tabs.map((t) => t.id)).size === 2; })());
  ok("tabs: setActive switches only to an existing, different tab", M.setActive(a1, "t0").active === "t0" && M.setActive(a1, "t1") === null && M.setActive(a1, "nope") === null);
  ok("tabs: saved meta round-trips through loadTabs", (() => { const m = M.renameTab(a1, "t0", "Budget").meta; const r = M.loadTabs(JSON.stringify(m)); return !r.unreadable && JSON.stringify(r.meta) === JSON.stringify(m); })());
  ok("tabs: addTab skips ids whose stored cells still exist", (() => { const n = M.addTab(d0.meta, (id) => id === "t1" || id === "t2"); return n.active === "t3" && n.tabs.length === 2; })());
  ok("tabs: addTab without a callback behaves as before", M.addTab(d0.meta).active === "t1");
  ok("chart: pie keeps positive values only and the shares add up to 1", (() => { const r = M.pieSlices([{ a: "A1", v: 3 }, { a: "A2", v: 1 }, { a: "A3", v: 0 }, { a: "A4", v: -2 }]); return r.slices.length === 2 && r.left === 2 && Math.abs(r.slices[0].frac - 0.75) < 1e-12 && Math.abs(r.slices.reduce((a, q) => a + q.frac, 0) - 1) < 1e-12; })());
  ok("chart: pie of nothing positive is null", M.pieSlices([{ a: "A1", v: 0 }, { a: "A2", v: -1 }]) === null && M.pieSlices([]) === null);
  ok("chart: pie with one value is a full share", M.pieSlices([{ a: "A1", v: 5 }]).slices[0].frac === 1);
}
console.log(`${n - fails}/${n} model rows passed`);
process.exit(fails ? 1 : 0);
