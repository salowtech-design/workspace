const { Sheet, isErr } = require("../../static/sheets/formula.js");
// Batch 9: VAR, VARP, STDEVP, MODE, RANK, CORREL, SLOPE, INTERCEPT. Argument order and the #DIV/0! / #N/A rules are from Google's help pages; the rest are this
// engine's own choices (see the comment in formula.js). Expected numbers were computed with Python's statistics module. [description, cells, expected]
const D = { A1: "2", A2: "4", A3: "4", A4: "4", A5: "5", A6: "5", A7: "7", A8: "9", B1: "1", B2: "2", B3: "3", B4: "4", B5: "5", B6: "6", B7: "7", B8: "8", C1: "x", C2: "", C3: "y" };
const f = (e, extra) => ({ ...D, ...(extra || {}), E1: e });
const near = (v) => typeof v === "number" ? Math.round(v * 1e12) / 1e12 : v;
const T = [
  ["VAR of a range (sample, n-1)", f("=VAR(A1:A8)"), 4.571428571429],
  ["VARP of a range (population, n)", f("=VARP(A1:A8)"), 4],
  ["STDEVP is the square root of VARP", f("=STDEVP(A1:A8)"), 2],
  ["VAR of direct values", f("=VAR(1,2,3,4,5,6,7,8,9,10)"), 9.166666666667],
  ["VAR ignores text and blanks in a range", f("=VAR(C1:C3,A1:A8)"), 4.571428571429],
  ["VAR with one value is #DIV/0!", f("=VAR(5)"), "#DIV/0!"],
  ["VAR with only text in a range is #DIV/0!", f("=VAR(C1:C3)"), "#DIV/0!"],
  ["VARP with one value is #DIV/0!", f("=VARP(5)"), "#DIV/0!"],
  ["STDEVP with one value is #DIV/0!", f("=STDEVP(5)"), "#DIV/0!"],
  ["VAR of equal values is 0, not rounding noise", f("=VAR(0.1,0.1,0.1)"), 0],
  ["VAR direct text is #VALUE!", f('=VAR("x",1,2)'), "#VALUE!"],
  ["VAR returns an error cell", f("=VAR(A1:A3)", { A2: "=1/0" }), "#DIV/0!"],
  ["VAR overflow is #NUM!", f("=VAR(1E200,-1E200)"), "#NUM!"],
  ["MODE of a range", f("=MODE(A1:A8)"), 4],
  ["MODE tie gives the first value reaching the top count", f("=MODE(3,1,1,3)"), 3],
  ["MODE with no repeat is #N/A", f("=MODE(B1:B8)"), "#N/A"],
  ["MODE tie of 5 and 4 (three each) gives the one that comes first in reading order, the direct 5", f("=MODE(5,A1:A8)"), 5],
  ["MODE across values and a range, no tie", f("=MODE(4,A1:A8)"), 4],
  ["MODE of nothing numeric is #N/A", f("=MODE(C1:C3)"), "#N/A"],
  ["RANK descending by default (Google: greatest is 1)", f("=RANK(9,A1:A8)"), 1],
  ["RANK of a tied value shares the best rank", f("=RANK(5,A1:A8)"), 3],
  ["RANK ascending", f("=RANK(5,A1:A8,1)"), 5],
  ["RANK ascending of the least", f("=RANK(2,A1:A8,1)"), 1],
  ["RANK of a value not in the data is #N/A", f("=RANK(6,A1:A8)"), "#N/A"],
  ["RANK of text is #VALUE!", f('=RANK("x",A1:A8)'), "#VALUE!"],
  ["RANK ignores text in the data", f("=RANK(2,C1:C3)"), "#N/A"],
  ["RANK passes an error cell", f("=RANK(2,A1:A3)", { A2: "=1/0" }), "#DIV/0!"],
  ["CORREL of two columns", f("=CORREL(A1:A8,B1:B8)"), 0.927426033503],
  ["SLOPE of two columns (y first, then x)", f("=SLOPE(A1:A8,B1:B8)"), 0.809523809524],
  ["INTERCEPT of two columns", f("=INTERCEPT(A1:A8,B1:B8)"), 1.357142857143],
  ["SLOPE with swapped arguments differs (x first)", f("=SLOPE(B1:B8,A1:A8)"), 1.0625],
  ["SLOPE of sizes that differ is #VALUE!", f("=SLOPE(A1:A8,B1:B7)"), "#VALUE!"],
  ["SLOPE skips a pair with text in either cell", f("=SLOPE(A1:A4,B1:B4)", { B2: "x" }), near((() => { const x = [1, 3, 4], y = [2, 4, 4]; const mx = 8 / 3, my = 10 / 3; let n = 0, d = 0; for (let i = 0; i < 3; i++) { n += (x[i] - mx) * (y[i] - my); d += (x[i] - mx) ** 2; } return n / d; })())],
  ["SLOPE with no spread in x is #DIV/0!", f("=SLOPE(A1:A3,B1:B3)", { B1: "1", B2: "1", B3: "1" }), "#DIV/0!"],
  ["CORREL with no spread in y is #DIV/0!", f("=CORREL(A1:A3,B1:B3)", { A1: "2", A2: "2", A3: "2" }), "#DIV/0!"],
  ["CORREL with one pair is #DIV/0!", f("=CORREL(A1:A1,B1:B1)"), "#DIV/0!"],
  ["CORREL of an exact line is 1", f("=CORREL(B1:B8,B1:B8)"), 1],
  ["INTERCEPT of an exact line y=2x+1", f("=INTERCEPT(A1:A4,B1:B4)", { A1: "3", A2: "5", A3: "7", A4: "9" }), 1],
  ["CORREL returns an error cell", f("=CORREL(A1:A3,B1:B3)", { B2: "=1/0" }), "#DIV/0!"],
  ["SLOPE with constant decimal x (0.1 three times) is #DIV/0!, not 0", f("=SLOPE(A1:A3,B1:B3)", { B1: "0.1", B2: "0.1", B3: "0.1" }), "#DIV/0!"],
  ["INTERCEPT with constant decimal x is #DIV/0!", f("=INTERCEPT(A1:A3,B1:B3)", { B1: "0.1", B2: "0.1", B3: "0.1" }), "#DIV/0!"],
  ["SLOPE with constant 0.7 seven times is #DIV/0!", { A1: "1", A2: "2", A3: "3", A4: "4", A5: "5", A6: "6", A7: "7", B1: "0.7", B2: "0.7", B3: "0.7", B4: "0.7", B5: "0.7", B6: "0.7", B7: "0.7", E1: "=SLOPE(A1:A7,B1:B7)" }, "#DIV/0!"],
  ["CORREL with constant decimal y is #DIV/0!, not 0", f("=CORREL(A1:A3,B1:B3)", { A1: "0.1", A2: "0.1", A3: "0.1" }), "#DIV/0!"],
  ["CORREL with constant decimal x is #DIV/0!", f("=CORREL(A1:A3,B1:B3)", { B1: "0.7", B2: "0.7", B3: "0.7" }), "#DIV/0!"],
  ["SLOPE with x values that differ by 1e-9 still gives a number (1000000028.282 is the exact slope of the three stored doubles, computed with Python Fractions; not 1e9 because 1.000000001 is not stored exactly)", f("=ROUND(SLOPE(A1:A3,B1:B3),3)", { A1: "1", A2: "2", A3: "3", B1: "1", B2: "1.000000001", B3: "1.000000002" }), 1000000028.282],
  ["CORREL of nearby but different x is a number (1 for an exact line)", f("=CORREL(A1:A3,B1:B3)", { A1: "1", A2: "2", A3: "3", B1: "1", B2: "1.000000001", B3: "1.000000002" }), 1],
  ["INTERCEPT for nearby x (exact value for the stored doubles, Fractions)", f("=ROUND(INTERCEPT(A1:A3,B1:B3),3)", { A1: "1", A2: "2", A3: "3", B1: "1", B2: "1.000000001", B3: "1.000000002" }), -1000000027.282],
  ["SLOPE pairs a 2 by 2 block with a column by cell count (reading order)", f("=SLOPE(A1:B2,D1:D4)", { A1: "2", B1: "4", A2: "6", B2: "8", D1: "1", D2: "2", D3: "3", D4: "4" }), 2],
  ["VAR needs at least one argument", f("=VAR()"), "#VALUE!"],
  ["RANK needs value and data", f("=RANK(1)"), "#VALUE!"],
];
let bad = 0;
for (const [d, cells, want] of T) {
  const got = new Sheet(cells).get("E1");
  const g = isErr(got) ? got.code : near(got);
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length - bad}/${T.length} rows passed`);
process.exit(bad ? 1 : 0);
