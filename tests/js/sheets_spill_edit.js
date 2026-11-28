// SORT block under real structural edits (M.structEdit then a new Sheet) and fill (M.fillRange). Node only.
const M = require("../../static/sheets/model.js"), F = require("../../static/sheets/formula.js");
let fails = 0, n = 0;
const mk = (o) => Object.assign(Object.create(null), o);
const G = { rows: 100, cols: 26 };
const edit = (cells, op) => M.structEdit({ cells: mk(cells), rules: [], cf: [], fmt: [] }, op, F.parseAddr, F.structFormula, G, 2000);
const val = (sh, a) => { const v = sh.get(a); return F.isErr(v) ? v.code : v; };
const eq = (d, got, want) => { n++; if (JSON.stringify(got) !== JSON.stringify(want)) { fails++; console.log("FAIL", d, "got", JSON.stringify(got), "want", JSON.stringify(want)); } };
const col = (sh, c, r1, r2) => { const o = []; for (let r = r1; r <= r2; r++) o.push(val(sh, c + r)); return o; };
const base = { B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)" };
eq("before", col(new F.Sheet(mk(base)), "C", 1, 4), [1, 2, 3, ""]);
let r = edit(base, { axis: "row", kind: "insert", at: 2 });  // inside the source and the block
eq("insert inside: formula", r.cells.C1, "=SORT(B1:B4)");
eq("insert inside: block is 4 tall, the new blank row sorts last", col(new F.Sheet(r.cells), "C", 1, 5), [1, 2, 3, "", ""]);
r = edit(base, { axis: "row", kind: "insert", at: 1 });  // above the anchor
eq("insert above: anchor moves with its source", [r.cells.C2, r.cells.C1], ["=SORT(B2:B4)", undefined]);
eq("insert above: block moves", col(new F.Sheet(r.cells), "C", 1, 5), ["", 1, 2, 3, ""]);
r = edit(base, { axis: "row", kind: "delete", at: 2 });  // a source row
eq("delete source row: range shrinks", r.cells.C1, "=SORT(B1:B2)");
eq("delete source row: block shrinks to 2", col(new F.Sheet(r.cells), "C", 1, 3), [2, 3, ""]);
r = edit(base, { axis: "row", kind: "delete", at: 1 });  // the anchor row
eq("delete anchor row: no anchor, no block", [r.cells.C1, col(new F.Sheet(r.cells), "C", 1, 3)], [undefined, ["", "", ""]]);
// a stored cell shifts INTO the block: source elsewhere, so deleting row 2 keeps the block 3 tall while C4 moves up to C3
const far = { B10: "3", B11: "1", B12: "2", C1: "=SORT(B10:B12)", C4: "typed" };
eq("far before: anchor works", val(new F.Sheet(mk(far)), "C1"), 1);
r = edit(far, { axis: "row", kind: "delete", at: 2 });
eq("stored cell shifted into the block: C3 holds it", r.cells.C3, "typed");
eq("stored cell shifted into the block: anchor is #REF!, typed value stays", [val(new F.Sheet(r.cells), "C1"), val(new F.Sheet(r.cells), "C3")], ["#REF!", "typed"]);
// column insert before the anchor column moves the whole thing right
r = edit(base, { axis: "col", kind: "insert", at: 1 });
eq("column insert: the anchor moves to D1 and the source to column C", [r.cells.D1, r.cells.C1, r.cells.B1], ["=SORT(C1:C3)", "3", undefined]);
eq("column insert: block is under D", col(new F.Sheet(r.cells), "D", 1, 3), [1, 2, 3]);
// fill
const addrOf = (c, rr) => String.fromCharCode(64 + c) + rr, R = (a, b, c2, d) => ({ c1: a, r1: b, c2: c2, r2: d });
let f = M.fillRange(mk({ B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)" }), R(3, 1, 3, 3), "down", null, addrOf, 2000, F.shiftFormula);
eq("fill the anchor down: formulas are stored in the block cells", [f.cells.C2, f.cells.C3], ["=SORT(B2:B4)", "=SORT(B3:B5)"]);
eq("fill the anchor down: the first anchor is then blocked", val(new F.Sheet(f.cells), "C1"), "#REF!");
f = M.fillRange(mk({ B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)", C3: "typed" }), R(3, 2, 3, 3), "down", null, addrOf, 2000, F.shiftFormula);
eq("fill down from a virtual empty cell (C2) copies empty: the typed C3 is cleared, nothing spilled is copied", [f.cells.C3, f.cleared], [undefined, 1]);
eq("after that clear the block is back", col(new F.Sheet(f.cells), "C", 1, 3), [1, 2, 3]);
f = M.fillRange(mk({ B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)", D2: "hello" }), R(3, 2, 4, 2), "right", null, addrOf, 2000, F.shiftFormula);
eq("fill right from a virtual empty cell: D2 is cleared, C2 is not stored", [f.cells.D2, f.cells.C2], [undefined, undefined]);
console.log(n + " rows, " + fails + " failed"); process.exit(fails ? 1 : 0);
