const { Sheet, isErr } = require("../../static/sheets/formula.js");
// SUMPRODUCT, COUNTUNIQUE, TEXTJOIN. [description, cells, cell, expected]; "#X!" = error code
const D = { A1: "1", A2: "2", A3: "3", B1: "4", B2: "5", B3: "6", C1: "x", C2: "", C3: "y", D1: "Apple", D2: "apple", D3: "pear" };
const f = (e, extra) => ({ ...D, ...(extra || {}), E1: e });
const T = [
  ["SUMPRODUCT of two columns", f("=SUMPRODUCT(A1:A3,B1:B3)"), "E1", 32],
  ["SUMPRODUCT of three columns", f("=SUMPRODUCT(A1:A3,B1:B3,A1:A3)"), "E1", 4 + 20 + 54],
  ["SUMPRODUCT of one range is its sum", f("=SUMPRODUCT(A1:A3)"), "E1", 6],
  ["SUMPRODUCT of two-by-two blocks", f("=SUMPRODUCT(A1:B2,A2:B3)"), "E1", 1 * 2 + 4 * 5 + 2 * 3 + 5 * 6],
  ["SUMPRODUCT of different sizes is #VALUE!", f("=SUMPRODUCT(A1:A3,B1:B2)"), "E1", "#VALUE!"],
  ["SUMPRODUCT of a column and a row of the same count is #VALUE!", f("=SUMPRODUCT(A1:A3,A1:C1)"), "E1", "#VALUE!"],
  ["SUMPRODUCT counts text and blanks as 0", f("=SUMPRODUCT(A1:A3,C1:C3)"), "E1", 0],
  ["SUMPRODUCT returns an error entry", f("=SUMPRODUCT(A1:A3,B1:B3)", { B2: "=1/0" }), "E1", "#DIV/0!"],
  ["SUMPRODUCT of single values", f("=SUMPRODUCT(3,4)"), "E1", 12],
  ["SUMPRODUCT a single value against a range is #VALUE!", f("=SUMPRODUCT(3,A1:A3)"), "E1", "#VALUE!"],
  ["COUNTUNIQUE of numbers", f("=COUNTUNIQUE(1,1,2,3)"), "E1", 3],
  ["COUNTUNIQUE ignores blanks and ignores case in text", f("=COUNTUNIQUE(C1:D3)"), "E1", 4],
  ["COUNTUNIQUE across a value and a range", f("=COUNTUNIQUE(1,A1:A3)"), "E1", 3],
  ["COUNTUNIQUE: the number 1 and the text 1 differ", f('=COUNTUNIQUE(1,"1")'), "E1", 2],
  ["COUNTUNIQUE: true and false are their own values", f("=COUNTUNIQUE(TRUE,FALSE,TRUE)"), "E1", 2],
  ["COUNTUNIQUE of only blanks is 0", f("=COUNTUNIQUE(Y1:Y3)"), "E1", 0],
  ["COUNTUNIQUE returns an error in a range", f("=COUNTUNIQUE(A1:A3)", { A2: "=1/0" }), "E1", "#DIV/0!"],
  ["TEXTJOIN with ignore_empty true skips blanks", f('=TEXTJOIN("-",TRUE,C1:C3)'), "E1", "x-y"],
  ["TEXTJOIN with ignore_empty false keeps blanks as empty pieces", f('=TEXTJOIN("-",FALSE,C1:C3)'), "E1", "x--y"],
  ["TEXTJOIN joins values and a range", f('=TEXTJOIN(", ",TRUE,"a",D1:D2)'), "E1", "a, Apple, apple"],
  ["TEXTJOIN reads a block row by row", f('=TEXTJOIN(",",TRUE,A1:B2)'), "E1", "1,4,2,5"],
  ["TEXTJOIN with an empty delimiter concatenates", f('=TEXTJOIN("",TRUE,A1:A3)'), "E1", "123"],
  ["TEXTJOIN joins booleans as the engine writes them (TRUE, FALSE, as CONCAT does)", f('=TEXTJOIN(" ",TRUE,TRUE,FALSE)'), "E1", "TRUE FALSE"],
  ["TEXTJOIN returns an error value", f('=TEXTJOIN("-",TRUE,A1:A3)', { A2: "=1/0" }), "E1", "#DIV/0!"],
  ["TEXTJOIN over 10,000 characters is #VALUE!", f('=TEXTJOIN("",TRUE,REPT("a",6000),REPT("b",6000))'), "E1", "#VALUE!"],
  ["TEXTJOIN with exactly 10,000 characters is allowed", f('=LEN(TEXTJOIN("",TRUE,REPT("a",5000),REPT("b",5000)))'), "E1", 10000],
  ["TEXTJOIN needs delimiter, ignore_empty and one text", f('=TEXTJOIN("-",TRUE)'), "E1", "#VALUE!"],
];
let bad = 0;
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length - bad}/${T.length} rows passed`);
process.exit(bad ? 1 : 0);
