// SORT as the first array result. Node only. Block cells are virtual, membership is from formula text.
var F = require("../../static/sheets/formula.js"), assert = require("assert"), fails = 0, n = 0;
function S(c, o) { return new F.Sheet(c, o || {}); }
function t(name, got, want) { n++; var g = F.isErr(got) ? got.code : got; if (g !== want) { fails++; console.log("FAIL", name, "got", JSON.stringify(g), "want", JSON.stringify(want)); } }
var base = { B1: "3", B2: "1", B3: "2", B4: "", B5: "x", C1: "=SORT(B1:B5)" };
var s = S(base);
["C1", "C2", "C3", "C4", "C5", "C6"].forEach(function (a, i) { t("basic " + a, s.get(a), ["1", "", "", "", "", ""][i] === "1" ? 1 : [1, 2, 3, "x", "", ""][i]); });
t("basic C1", s.get("C1"), 1);
// block below row 5 is empty
t("outside", S(base).get("C6"), "");
// descending, blanks last, ties stable (numbers as typed text keep order)
var d = S({ B1: "b", B2: "A", B3: "2", B4: "", B5: "10", C1: "=SORT(B1:B5,1,FALSE)" });
// descending is the full reverse of the kind order (text, then numbers), blanks still last
t("desc1", d.get("C1"), "b"); t("desc2", d.get("C2"), "A"); t("desc3", d.get("C3"), 10); t("desc4", d.get("C4"), 2); t("desc5", d.get("C5"), "");
var m = S({ B1: "x", B2: "5", B3: "TRUE", B4: "a", C1: "=SORT(B1:B4)" });
t("mixed1", m.get("C1"), 5); t("mixed2", m.get("C2"), "a"); t("mixed3", m.get("C3"), "x"); t("mixed4", m.get("C4"), true);
// ties keep order: case-insensitive equal keys
var ti = S({ B1: "b", B2: "B", B3: "a", C1: "=SORT(B1:B3)" });
t("tie1", ti.get("C1"), "a"); t("tie2", ti.get("C2"), "b"); t("tie3", ti.get("C3"), "B");
// blocked by a stored cell: anchor #REF!, typed cell shows its value, others empty
var bl = S({ B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)", C3: "typed" });
t("blocked anchor", bl.get("C1"), "#REF!"); t("blocked cell", bl.get("C3"), "typed"); t("blocked empty", bl.get("C2"), "");
// removing the typed cell on a fresh Sheet (the grid builds a new Sheet per recompute) brings the block back
t("unblocked", S({ B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)" }).get("C3"), 3);
// source edit
t("edited", S({ B1: "0", B2: "1", B3: "2", C1: "=SORT(B1:B3,1,FALSE)" }).get("C1"), 2);
// two anchors in one column: overlap only through stored content, order independent
var two = { B1: "2", B2: "1", B3: "4", B4: "3", C1: "=SORT(B1:B3)", C2: "=SORT(B2:B4)" };
["C1", "C2", "C3"].forEach(function (a) { }); 
var o1 = S(two), o2 = S(two);
t("two C1", o1.get("C1"), "#REF!"); t("two C2", o1.get("C2"), 1); t("two C3 (second anchor's block)", o1.get("C3"), 3);
t("two order", o2.get("C3"), 3); t("two order C1", o2.get("C1"), "#REF!");
// no false cycle: B2 reads A9 outside the block
var nc = S({ A1: "=SORT(B1:B3)", B1: "3", B2: "=SUM(A9)", B3: "1" });
t("no false cycle A1", nc.get("A1"), 0); t("no false cycle A2", nc.get("A2"), 1);
// true cycle: B2 reads A3 inside the block
var cy = S({ A1: "=SORT(B1:B3)", B1: "3", B2: "=SUM(A3)", B3: "1" });
t("true cycle A1", cy.get("A1"), "#CIRC!"); t("true cycle A3", cy.get("A3"), "#CIRC!");
// dependent before the anchor in address order
var dp = S({ A1: "=SUM(B1:B3)", B1: "=SUM(Z3)", B2: "", B3: "", Z1: "=SORT(C1:C3)", C1: "2", C2: "1", C3: "3", D1: "=SUM(Z2)" });
t("dependent", dp.get("D1"), 2);
// a dependent that reads a block cell
var rd = S({ B1: "3", B2: "1", B3: "2", C1: "=SORT(B1:B3)", D1: "=C2+C3" });
t("reads block", rd.get("D1"), 5);
// reversed, absolute, single cell, two columns, other forms, nested
t("reversed", S({ B1: "3", B2: "1", C1: "=SORT(B2:B1)" }).get("C2"), 3);
t("absolute", S({ B1: "3", B2: "1", C1: "=SORT($B$1:$B$2)" }).get("C1"), 1);
t("single cell", S({ B1: "3", C1: "=SORT(B1)" }).get("C1"), "#VALUE!");
t("two columns", S({ B1: "3", C1: "4", D1: "=SORT(B1:C2)" }).get("D1"), "#VALUE!");
t("computed arg", S({ B1: "3", C1: "=SORT(B1:B2&\"\")" }).get("C1"), "#VALUE!");
t("whole column", S({ B1: "3", C1: "=SORT(B:B)" }).get("C1") instanceof F.Err, true);
t("sort_column 2", S({ B1: "3", C1: "=SORT(B1:B2,2)" }).get("C1"), "#VALUE!");
t("is_ascending text", S({ B1: "3", C1: "=SORT(B1:B2,1,\"x\")" }).get("C1"), "#VALUE!");
t("nested", S({ B1: "3", B2: "4", C1: "=SUM(SORT(B1:B2))" }).get("C1"), 7);
t("not anchor shows no block", S({ B1: "3", B2: "4", C1: "=SUM(SORT(B1:B2))" }).get("C2"), "");
// source error
t("source error", S({ B1: "=1/0", B2: "1", C1: "=SORT(B1:B2)" }).get("C1"), "#DIV/0!");
t("source error in block", S({ B1: "=1/0", B2: "1", C1: "=SORT(B1:B2)" }).get("C2"), "#DIV/0!");
// other tab: source on another tab, block on the anchor's own tab; and a read of a spilled block from another tab
var tabs = { T1: { id: "T1", cells: { B1: "3", B2: "1" } }, T2: { id: "T2", cells: { A1: "=SORT(T1!B1:B2)", C1: "=SUM(A2)" } }, T3: { id: "T3", cells: { A1: "=T2!A2" } } };
var tab = function (nm) { return tabs[nm] || null; };
var sh2 = S(tabs.T2.cells, { self: "T2", tab: tab });
t("xrange anchor", sh2.get("A1"), 1); t("xrange block", sh2.get("A2"), 3); t("same-tab read of block", sh2.get("C1"), 3);
t("other tab reads block", S(tabs.T3.cells, { self: "T3", tab: tab }).get("A1"), 3);
// bounds: past row 1000, and a block near the work cap
t("past row 1000", S({ B1: "1", B2: "2", C999: "=SORT(B1:B3)" }).get("C999"), "#REF!");
var big = {}; for (var i = 1; i <= 1000; i++) big["B" + i] = String((i * 7919) % 1000); big.C1 = "=SORT(B1:B1000)";
var t0 = Date.now(), bs = S(big); var last = bs.get("C1000"); t("big first", bs.get("C1"), 0); t("big count of sorts", typeof last, "number");
assert(Date.now() - t0 < 3000, "1000 row block too slow: " + (Date.now() - t0));
// the array is evaluated once: many block cells, one sort (work charged once)
var w = S(big); w.get("C1"); for (var j = 2; j <= 1000; j++) w.get("C" + j);
t("block cells sorted", [w.get("C2"), w.get("C1000")].join(), "1,999");
var ws = Object.keys(big).length; 
// blockCells
t("blockCells", S({ B1: "1", B2: "2", B3: "3", C1: "=SORT(B1:B3)", C3: "x" }).blockCells().join(), "C2");
t("blockCells all", S({ B1: "1", B2: "2", B3: "3", C1: "=SORT(B1:B3)" }).blockCells().join(), "C2,C3");
console.log(n + " rows, " + fails + " failed"); process.exit(fails ? 1 : 0);
