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
const virt = (c) => { const sh = new F.Sheet(mk(c)); return sh.isVirtual; };
let c1 = { B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)", D2: "hello", D3: "keep" };
f = M.fillRange(mk(c1), R(3, 2, 4, 3), "down", null, addrOf, 2000, F.shiftFormula, virt(c1));
eq("fill down whose first row has a virtual SORT cell (C2): refused, message says why", [!!f.error, /only shows a SORT result/.test(f.error || ""), f.cells], [true, true, undefined]);
f = M.fillRange(mk(c1), R(3, 2, 4, 2), "right", null, addrOf, 2000, F.shiftFormula, virt(c1));
eq("fill right whose first column has a virtual cell: refused", !!f.error, true);
f = M.fillRange(mk(c1), R(3, 1, 3, 3), "down", null, addrOf, 2000, F.shiftFormula, virt(c1));
eq("a first row that is the stored anchor itself is not virtual: that fill still runs (stores formulas in the block, as typing would)", [!!f.error, f.cells.C2], [false, "=SORT(B2:B4)"]);
f = M.fillRange(mk({ A1: "x", A2: "", A3: "", D1: "k" }), R(1, 1, 1, 3), "down", null, addrOf, 2000, F.shiftFormula, virt({ A1: "x" }));
eq("a sheet with no SORT fills as before", f.cells.A2, "x");
// the refused fill leaves stored targets unchanged
eq("refused fill: the stored cells are as they were", c1.D2 + c1.D3, "hellokeep");
// stored empty text inside a block counts as empty (the grid never stores it; an import or an old save could)
const se = new F.Sheet(mk({ B1: "3", B2: "1", C1: "=SORT(B1:B2)", C2: "" }));
eq("stored empty text in the block: not content, the block shows", [val(se, "C1"), val(se, "C2")], [1, 3]);
eq("stored empty text counts as virtual for fill and CSV", [se.isVirtual("C2"), se.blockCells().join()], [true, "C2"]);
// true cycles of the SORT itself, in both asking orders
let cy1 = { A1: "=SORT(A1:A3)" }, cy2 = { A1: "=SORT(A2:A4)", A2: "5" };
eq("SORT over a range that includes its own cell: #CIRC!, either order", [val(new F.Sheet(mk(cy1)), "A1"), val(new F.Sheet(mk(cy1)), "A3")], ["#CIRC!", "#CIRC!"]);
eq("SORT whose source is its own future block (A2:A4 in A1): A2 holds a typed value so the anchor is blocked", val(new F.Sheet(mk(cy2)), "A1"), "#REF!");
cy2 = { A1: "=SORT(A2:A4)" };
eq("SORT whose source is its own future block, nothing typed: #CIRC! from A1 or from A3, either order", [val(new F.Sheet(mk(cy2)), "A1"), val(new F.Sheet(mk(cy2)), "A3")], ["#CIRC!", "#CIRC!"]);
console.log(n + " rows, " + fails + " failed"); process.exit(fails ? 1 : 0);
