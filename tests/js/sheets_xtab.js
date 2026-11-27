const F = require("../../static/sheets/formula.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const mk = (tabs, self) => {  // tabs: name -> {id, cells}
  const find = (nm) => { const k = Object.keys(tabs).find((x) => x.toLowerCase() === nm.toLowerCase()); return k ? tabs[k] : null; };
  return new F.Sheet(find(self).cells, { self: find(self).id, tab: find });
};
const S = (v) => String(v);
const T = {
  Main: { id: "t0", cells: { A1: "5", A2: "=Other!A1+1", A3: "='My ''tab'!B2", A4: "=SUM(Other!A1:A2)", A5: "=Nope!A1", A6: "=Other!A9+1", A7: "=other!a1*2", A8: "=Main!A1+10", A9: "=Bad!A1", A10: "=IFERROR(Nope!A1,\"gone\")", A11: "=Other!A3", A12: "=INDEX(Other!A1:B2,2,2)", A13: "=VLOOKUP(\"k\",Other!C1:D2,2,FALSE)", A14: "=COUNT(Other!A1:B2)" } },
  Other: { id: "t1", cells: { A1: "7", A2: "=Main!A1", A3: "=1/0", B2: "hi", C1: "k", D1: "kv", C2: "z", D2: "zv", B1: "3" } },
  "My 'tab": { id: "t2", cells: { B2: "quoted" } },
  Bad: { id: "t3", cells: null },
};
let s = mk(T, "Main");
ok("a reference reads the other tab's value", S(s.get("A2")) === "8");
ok("a quoted name with a doubled apostrophe reads its tab", S(s.get("A3")) === "quoted");
ok("a range sums across tabs, including a formula in the other tab", S(s.get("A4")) === "12");
ok("an unknown tab name is #REF!", S(s.get("A5")) === "#REF!");
ok("a blank cell on the other tab is blank, so +1 gives 1", S(s.get("A6")) === "1");
ok("the tab name is case-insensitive and so is the cell", S(s.get("A7")) === "14");
ok("a reference to its own tab by name reads the same cell", S(s.get("A8")) === "15");
ok("a tab whose stored cells cannot be read is #TAB!, not a value", S(s.get("A9")) === "#TAB!");
ok("IFERROR catches the unknown tab", S(s.get("A10")) === "gone");
ok("an error in the other tab's cell comes through", S(s.get("A11")) === "#DIV/0!");
ok("INDEX reads a range on another tab with its shape", S(s.get("A12")) === "hi");
ok("VLOOKUP reads a range on another tab", S(s.get("A13")) === "kv");
ok("COUNT counts the numbers in a range on another tab", S(s.get("A14")) === "3");
const sOther = mk(T, "Other");
ok("a loop through two tabs is #CIRC! when asked from either side", (() => { const t = JSON.parse(JSON.stringify(T)); t.Main.cells.A1 = "=Other!A2"; return S(mk(t, "Main").get("A1")) === "#CIRC!" && S(mk(t, "Other").get("A2")) === "#CIRC!" && S(mk(t, "Other").get("A1")) === "7"; })());
ok("the answer does not depend on which cell is asked first", (() => { const t = JSON.parse(JSON.stringify(T)); const a = mk(t, "Main"), v1 = [S(a.get("A2")), S(a.get("A4"))]; const b = mk(t, "Main"), v2 = [S(b.get("A4")), S(b.get("A2"))].reverse(); return v1.join() === v2.join(); })());
ok("a cell in a loop with itself through another tab is #CIRC!", (() => { const t = { P: { id: "p", cells: { A1: "=Q!A1" } }, Q: { id: "q", cells: { A1: "=P!A1+1" } } }; return S(mk(t, "P").get("A1")) === "#CIRC!"; })());
ok("a self reference by name is #CIRC!", S(new F.Sheet({ A1: "=Main!A1" }, { self: "t0", tab: (nm) => nm === "Main" ? { id: "t0", cells: { A1: "=Main!A1" } } : null }).get("A1")) === "#CIRC!");
ok("a chain of 60 hops across two tabs gives a number or a stated limit, and the same twice", (() => {
  const a = {}, b = {}; for (let i = 1; i <= 30; i++) { a["A" + i] = i === 1 ? "1" : "=B" + (i - 1) + "+1"; b["B" + i] = "=A" + i + "+1"; }
  // a hop goes A(i) -> B(i-1) -> A(i-1) ...
  const t = { Aa: { id: "a", cells: Object.assign({}, a) }, Bb: { id: "b", cells: Object.assign({}, b) } };
  for (let i = 1; i <= 30; i++) { t.Aa.cells["A" + i] = i === 1 ? "1" : "=Bb!B" + (i - 1) + "+1"; t.Bb.cells["B" + i] = "=Aa!A" + i + "+1"; }
  const r1 = S(mk(t, "Aa").get("A30")), r2 = S(mk(t, "Aa").get("A30"));
  return r1 === r2 && (r1 === "59" || r1 === "#LIMIT!");
})());
ok("a provider that throws gives #TAB!", S(new F.Sheet({ A1: "=X!A1" }, { self: "t0", tab: () => { throw new Error("storage"); } }).get("A1")) === "#TAB!");
ok("with no tab provider a tab reference is #REF! and plain formulas are unchanged", S(new F.Sheet({ A1: "=X!A1", A2: "2", A3: "=A2*3" }).get("A1")) === "#REF!" && S(new F.Sheet({ A2: "2", A3: "=A2*3" }).get("A3")) === "6");
const P = (src) => { try { return F.parse(src); } catch (e) { return e; } };
ok("a range whose second end is qualified is #REF!, as a bad range end is on one tab", S(P("'a'!A1:'a'!B2")) === "#REF!");
ok("a name with no cell is a parse error", S(P("Sheet!")) === "#PARSE!" && S(P("Sheet!+1")) === "#PARSE!");
ok("a name then a non cell word is #NAME?", S(P("Sheet!B")) === "#NAME?");
ok("a cell off the grid on another tab is #REF!", P("Sheet!ZZZ9").n === "ref_err" && S(new F.Sheet({ A1: "=T!ZZZ9" }, { self: "s", tab: () => ({ id: "t", cells: {} }) }).get("A1")) === "#REF!");
ok("an unclosed quote is a parse error", S(P("'abc!A1")) === "#PARSE!");
ok("a name that looks like a cell parses as a tab when quoted or followed by !", (() => { const t = P("AB1!C3"); return t.n === "xcell" && t.t === "AB1" && t.a.col === 3 && t.a.row === 3; })());
ok("tab names round trip through tabName and parse", ["A1", "it's", "a b", "x!y", "日本", "Sheet 1", "Q1", "a''b", "_x", "9lives"].every((nm) => { const t = P(F.tabName(nm) + "!B2"); return t.n === "xcell" && t.t === nm && t.a.col === 2 && t.a.row === 2; }));
ok("tabName leaves a plain word and quotes the rest", F.tabName("Sheet2") === "Sheet2" && F.tabName("A1") === "'A1'" && F.tabName("it's") === "'it''s'" && F.tabName("a b") === "'a b'");
ok("tabRefs lists tab names, not text in quotes or plain cells", JSON.stringify(F.tabRefs("=Other!A1+'My ''tab'!B2+A1+\"x!A1\"")) === JSON.stringify(["Other", "My 'tab"]) && F.tabRefs("=A1+2").length === 0 && F.tabRefs("not a formula").length === 0 && F.tabRefs("='x").length === 0);
ok("Fill moves a qualified reference like any other", F.shiftFormula("=Other!A1+'My ''tab'!B2:C3+A1+\"Other!A1\"", 1, 1) === "=Other!B2+'My ''tab'!C3:D4+B2+\"Other!A1\"");
ok("a pinned qualified reference stays", F.shiftFormula("=Other!$A$1+Other!A$1", 1, 1) === "=Other!$A$1+Other!B$1");
ok("insert and delete on this tab leave qualified references as written and move plain ones", F.structFormula("=Other!A1+'My ''tab'!B2+A1+B5", "row", "insert", 1) === "=Other!A1+'My ''tab'!B2+A2+B6" && F.structFormula("=Other!A1+A1", "col", "delete", 1) === "=Other!A1+#REF!");
ok("plain formulas are untouched by the qualified handling", F.shiftFormula("=SUM(A1:B2)+LOG10(A1)+1E5", 1, 1) === "=SUM(B2:C3)+LOG10(B2)+1E5");
ok("a shifted reference past the grid keeps the tab name and shows #REF!", F.shiftFormula("=Other!Z1", 1, 0) === "=Other!#REF!" && S(new F.Sheet({ A1: "=Other!#REF!" }, { self: "s", tab: () => ({ id: "t", cells: {} }) }).get("A1")) === "#REF!");
console.log(`${n - fails}/${n} cross-tab rows passed`);
process.exit(fails ? 1 : 0);
