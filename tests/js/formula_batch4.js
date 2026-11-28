const { Sheet, isErr } = require("../../static/sheets/formula.js");
// MAXIFS and MINIFS. [description, cells, cell, expected]; "#X!" = error code
const D = { A1: "North", A2: "South", A3: "North", A4: "North", A5: "north", B1: "10", B2: "20", B3: "30", B4: "abc", B5: "-5", C1: "1", C2: "1", C3: "2", C4: "2", C5: "2" };
const T = [
  ["MAXIFS one pair (text in the range is skipped, criteria ignore case)", { ...D, E1: '=MAXIFS(B1:B5,A1:A5,"North")' }, "E1", 30],
  ["MINIFS one pair", { ...D, E1: '=MINIFS(B1:B5,A1:A5,"North")' }, "E1", -5],
  ["MAXIFS two pairs", { ...D, E1: '=MAXIFS(B1:B5,A1:A5,"North",C1:C5,1)' }, "E1", 10],
  ["MINIFS operator criterion", { ...D, E1: '=MINIFS(B1:B5,B1:B5,">=10")' }, "E1", 10],
  ["MAXIFS wildcard", { ...D, E1: '=MAXIFS(B1:B5,A1:A5,"N*")' }, "E1", 30],
  ["MAXIFS nothing matches gives 0", { ...D, E1: '=MAXIFS(B1:B5,A1:A5,"West")' }, "E1", 0],
  ["MINIFS nothing matches gives 0", { ...D, E1: '=MINIFS(B1:B5,A1:A5,"West")' }, "E1", 0],
  ["MAXIFS matches only text cells gives 0", { ...D, E1: '=MAXIFS(B1:B5,B1:B5,"abc")' }, "E1", 0],
  ["MAXIFS all negative returns the largest negative", { A1: "x", A2: "x", B1: "-3", B2: "-9", E1: '=MAXIFS(B1:B2,A1:A2,"x")' }, "E1", -3],
  ["MAXIFS size mismatch", { ...D, E1: '=MAXIFS(B1:B5,A1:A4,"North")' }, "E1", "#VALUE!"],
  ["MINIFS an incomplete pair (four arguments) is #VALUE!", { ...D, E1: '=MINIFS(B1:B5,A1:A5,"North",C1:C5)' }, "E1", "#VALUE!"],
  ["MAXIFS error in a matching value is returned", { ...D, B3: "=1/0", E1: '=MAXIFS(B1:B5,A1:A5,"North")' }, "E1", "#DIV/0!"],
  ["MINIFS error in a non-matching value is not reported", { ...D, B2: "=1/0", E1: '=MINIFS(B1:B5,A1:A5,"North")' }, "E1", -5],
  ["MAXIFS with too few arguments is #VALUE!", { ...D, E1: '=MAXIFS(B1:B5,A1:A5)' }, "E1", "#VALUE!"],
];
let bad = 0;
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length - bad}/${T.length} rows passed`);
process.exit(bad ? 1 : 0);
