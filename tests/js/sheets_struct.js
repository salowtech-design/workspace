// Insert and delete a row or column: the pure model (formula rewriting, cells, and the three rule lists).
const M = require("../../static/sheets/model.js");
const F = require("../../static/sheets/formula.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const mk = (o) => Object.assign(Object.create(null), o);
const sf = F.structFormula, G = { rows: 100, cols: 26 };
const edit = (cells, op, rules, cf, fmt) => M.structEdit({ cells: mk(cells), rules: rules || [], cf: cf || [], fmt: fmt || [] }, op, F.parseAddr, sf, G, 2000);
const row = (kind, at) => ({ axis: "row", kind, at }), col = (kind, at) => ({ axis: "col", kind, at });
// ---- formula rewriting
const T = [
  ["=A5", "row", "insert", 5, "=A6"], ["=A4", "row", "insert", 5, "=A4"], ["=A5", "row", "delete", 5, "=#REF!"], ["=A6", "row", "delete", 5, "=A5"], ["=A4", "row", "delete", 5, "=A4"],
  ["=SUM(A1:A5)", "row", "insert", 3, "=SUM(A1:A6)"], ["=SUM(A1:A5)", "row", "insert", 6, "=SUM(A1:A5)"], ["=SUM(A1:A5)", "row", "insert", 1, "=SUM(A2:A6)"],
  ["=SUM(A1:A5)", "row", "delete", 3, "=SUM(A1:A4)"], ["=SUM(A3:A5)", "row", "delete", 3, "=SUM(A3:A4)"], ["=SUM(A3:A5)", "row", "delete", 5, "=SUM(A3:A4)"],
  ["=SUM(A3:A3)", "row", "delete", 3, "=SUM(#REF!)"], ["=SUM(A3:B3)", "row", "delete", 3, "=SUM(#REF!)"], ["=SUM(A1:A2)", "row", "delete", 9, "=SUM(A1:A2)"],
  ["=SUM(A4:A9)", "row", "delete", 1, "=SUM(A3:A8)"],
  ["=C1", "col", "insert", 2, "=D1"], ["=B1", "col", "insert", 2, "=C1"], ["=A1", "col", "insert", 2, "=A1"], ["=B1", "col", "delete", 2, "=#REF!"], ["=C1", "col", "delete", 2, "=B1"],
  ["=SUM(A1:C1)", "col", "insert", 2, "=SUM(A1:D1)"], ["=SUM(A1:C1)", "col", "delete", 2, "=SUM(A1:B1)"], ["=SUM(B1:C1)", "col", "delete", 2, "=SUM(B1:B1)"],
  ["=$A$5+A$5+$A5", "row", "insert", 2, "=$A$6+A$6+$A6"], ["=$C1+$C$1", "col", "delete", 1, "=$B1+$B$1"],
  ['=A5&"A5 ""B5"""', "row", "insert", 2, '=A6&"A5 ""B5"""'], ["=LOG10(A5)+1E5", "row", "insert", 2, "=LOG10(A6)+1E5"],
  ["=A1000", "row", "insert", 1, "=#REF!"], ["=Z1", "col", "insert", 1, "=#REF!"], ["=SUM(A1:A1000)", "row", "insert", 1, "=SUM(#REF!)"],
  ["=SUM(A5:A1)", "row", "insert", 3, "=SUM(A6:A1)".replace("A6:A1", "A6:A1")],
  ["=ZZ9+A0", "row", "insert", 1, "=ZZ9+A0"], ["plain A5", "row", "insert", 1, "plain A5"], ["=#REF!+A5", "row", "insert", 1, "=#REF!+A6"],
];
T.forEach(([src, ax, k, at, want]) => { const got = sf(src, ax, k, at); ok("formula " + ax + " " + k + " " + at + ": " + src + " -> " + want + " (got " + got + ")", got === want); });
// ---- cells
let r = edit({ A1: "x", A2: "y", A3: "z", B2: "=A2+A3", C1: "=SUM(A1:A3)" }, row("insert", 2));
ok("insert row 2: cells at and below move down", r.cells.A1 === "x" && r.cells.A3 === "y" && r.cells.A4 === "z" && !("A2" in r.cells) && r.cells.B3 === "=A3+A4" && r.cells.C1 === "=SUM(A1:A4)");
r = edit({ A1: "x", A2: "y", A3: "z", B2: "=A2+A3", C1: "=SUM(A1:A3)", D1: "=A2" }, row("delete", 2));
ok("delete row 2: its cells go, later ones move up, references follow", r.cells.A1 === "x" && r.cells.A2 === "z" && !("B2" in r.cells) && r.cells.C1 === "=SUM(A1:A2)" && r.cells.D1 === "=#REF!" && r.stats.removed === 2 && r.stats.refErrs === 1);
ok("delete counts only filled cells removed", edit({ A1: "x", A2: "", B2: "" }, row("delete", 2)).stats.removed === 0);
r = edit({ A1: "x", B1: "y", C1: "=A1+B1" }, col("insert", 2));
ok("insert column B: B moves to C, formula follows", r.cells.A1 === "x" && r.cells.C1 === "y" && r.cells.D1 === "=A1+C1" && !("B1" in r.cells));
r = edit({ A1: "x", B1: "y", C1: "=A1+B1", D1: "=C1" }, col("delete", 2));
ok("delete column B", r.cells.A1 === "x" && r.cells.B1 === "=A1+#REF!" && r.cells.C1 === "=B1" && !("D1" in r.cells));
// round trip
const base = { A1: "1", A2: "2", A3: "3", B1: "=A1+A3", B3: "=SUM(A1:A3)", C2: "=$A$2*B$1" };
const there = edit(base, row("insert", 2)), back = edit(there.cells, row("delete", 2));
ok("insert then delete the same new row restores every cell and formula", JSON.stringify(Object.entries(back.cells).sort()) === JSON.stringify(Object.entries(mk(base)).sort()));
// boundaries
ok("insert row refused when the last row has content", edit({ A100: "x" }, row("insert", 1)).error && /pushed off/.test(edit({ A100: "x" }, row("insert", 1)).error));
ok("insert row allowed when only an empty string is in the last row", !edit({ A100: "" }, row("insert", 1)).error);
ok("insert column refused when column Z has content", !!edit({ Z1: "x" }, col("insert", 1)).error);
ok("delete in the last row is fine", !edit({ A100: "x", A1: "y" }, row("delete", 100)).error && edit({ A100: "x" }, row("delete", 100)).stats.removed === 1);
ok("row 0 and 101, column 27 are refused", !!edit({}, row("insert", 0)).error && !!edit({}, row("insert", 101)).error && !!edit({}, col("delete", 27)).error && !!edit({}, row("insert", 1.5)).error);
ok("bad op refused", !!edit({}, { axis: "x", kind: "insert", at: 1 }).error && !!edit({}, { axis: "row", kind: "move", at: 1 }).error);
ok("input cells are not modified", (() => { const c = mk({ A1: "=A2", A2: "q" }); M.structEdit({ cells: c, rules: [], cf: [], fmt: [] }, row("insert", 1), F.parseAddr, sf, G, 2000); return c.A1 === "=A2" && c.A2 === "q"; })());
ok("a formula growing past the cell limit refuses the whole change", (() => { const long = "=A9" + "+A9".repeat(5); const x = M.structEdit({ cells: mk({ A1: long }), rules: [], cf: [], fmt: [] }, row("insert", 1), F.parseAddr, sf, G, long.length); return !!x.error && !x.cells; })());
// ---- rules
const VR = { range: "A2:A5", kind: "list", items: ["a", "b"] }, CF = { range: "B1:B3", kind: "greater", color: "red", value: 5 }, FM = { range: "C3:D4", bold: true };
r = edit({}, row("insert", 3), [VR], [CF], [FM]);
ok("insert inside ranges grows them, a range below moves", r.rules[0].range === "A2:A6" && r.cf[0].range === "B1:B4" && r.fmt[0].range === "C4:D5" && r.stats.resized === 3);
ok("other rule fields are kept", r.rules[0].items.join() === "a,b" && r.cf[0].value === 5 && r.cf[0].color === "red" && r.fmt[0].bold === true);
r = edit({}, row("delete", 3), [VR], [CF], [FM]);
ok("delete inside shrinks, below moves up", r.rules[0].range === "A2:A4" && r.cf[0].range === "B1:B2" && r.fmt[0].range === "C3:D3");
r = edit({}, row("delete", 3), [{ range: "A3:A3", kind: "number", min: 0, max: 1 }], [], []);
ok("a rule whose only row is deleted is dropped and counted", r.rules.length === 0 && r.stats.dropped === 1);
r = edit({}, col("delete", 2), [{ range: "B1:B9", kind: "number", min: 0, max: 1 }], [{ range: "A1:C1", kind: "equals", color: "red", value: "x" }], []);
ok("column delete: one-column rule dropped, wider range shrinks", r.rules.length === 0 && r.cf[0].range === "A1:B1");
ok("insert refused when a rule reaches the last row", !!edit({}, row("insert", 1), [{ range: "A50:A100", kind: "number", min: 0, max: 1 }]).error);
ok("insert at a later row than a rule that reaches the end of the grid is fine", !edit({}, row("insert", 1), [{ range: "A1:A99", kind: "number", min: 0, max: 1 }]).error);
ok("insert at the row of a one-row rule on the last row is refused", !!edit({}, row("insert", 100), [{ range: "A100:A100", kind: "number", min: 0, max: 1 }]).error);
ok("insert column refused when a colour rule reaches column Z", !!edit({}, col("insert", 1), [], [{ range: "X1:Z2", kind: "equals", color: "red", value: "x" }]).error);
ok("an unreadable range in a list refuses", !!edit({}, row("insert", 1), [{ range: "nope", kind: "list", items: [] }]).error);
// results must still be loadable by the loaders (validation rules must not overlap, ranges must be canonical)
r = edit({}, row("insert", 4), [{ range: "A1:A3", kind: "number", min: 0, max: 1 }, { range: "A4:A6", kind: "number", min: 0, max: 1 }, { range: "B1:B9", kind: "list", items: ["z"] }]);
ok("rewritten rules load back and stay disjoint", (() => { const l = M.loadRules(M.rulesJson(r.rules), F.parseAddr); return !l.unreadable && l.rules.length === 3 && l.rules[1].range === "A5:A7"; })());
r = edit({}, row("delete", 3), [], [CF, { range: "B2:B6", kind: "less", color: "blue", value: 1 }], [FM]);
ok("rewritten colour and format rules load back", !M.loadCf(M.cfJson(r.cf), F.parseAddr).unreadable && !M.loadFmt(M.fmtJson(r.fmt), F.parseAddr).unreadable);
// the real grid is 1000 rows: the same checks at the real bound
const G1 = { rows: 1000, cols: 26 }, edit1 = (cells, op) => M.structEdit({ cells: mk(cells), rules: [], cf: [], fmt: [] }, op, F.parseAddr, sf, G1, 2000);
ok("1000-row grid: insert above row 500 with row 1000 empty works and moves A999 to A1000", (() => { const x = edit1({ A999: "k", A500: "m" }, row("insert", 500)); return !x.error && x.cells.A1000 === "k" && x.cells.A501 === "m" && !x.cells.A500; })());
ok("1000-row grid: insert refused when row 1000 holds a value (it would be pushed out)", (() => { const x = edit1({ A1000: "k" }, row("insert", 500)); return !!x.error; })());
ok("1000-row grid: insert refused when a formula cell sits on row 1000", !!edit1({ Z1000: "=1" }, row("insert", 1)).error);
ok("1000-row grid: insert at row 1000 with row 1000 empty works", !edit1({ A5: "k" }, row("insert", 1000)).error);
ok("1000-row grid: a value on row 100 is NOT treated as the edge (moves to row 101)", (() => { const x = edit1({ A100: "k" }, row("insert", 1)); return !x.error && x.cells.A101 === "k"; })());
console.log(n - fails + "/" + n + " structure rows passed");
process.exit(fails ? 1 : 0);
