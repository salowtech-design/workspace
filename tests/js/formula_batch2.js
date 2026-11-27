const { Sheet, isErr } = require("../../static/sheets/formula.js");
// [description, cells, cell, expected]; "#X!" = error code
const D = { A1: "North", A2: "South", A3: "North", A4: "North", B1: "10", B2: "20", B3: "30", B4: "abc", C1: "1", C2: "1", C3: "2", C4: "2" };
const T = [
  ["SUMIFS one pair", { ...D, E1: '=SUMIFS(B1:B4,A1:A4,"North")' }, "E1", 40],
  ["SUMIFS two pairs", { ...D, E1: '=SUMIFS(B1:B4,A1:A4,"North",C1:C4,2)' }, "E1", 30],
  ["SUMIFS operator criterion", { ...D, E1: '=SUMIFS(B1:B4,B1:B4,">15")' }, "E1", 50],
  ["SUMIFS ignores text in sum range", { ...D, E1: '=SUMIFS(B1:B4,C1:C4,2)' }, "E1", 30],
  ["SUMIFS size mismatch", { ...D, E1: '=SUMIFS(B1:B4,A1:A3,"North")' }, "E1", "#VALUE!"],
  ["COUNTIFS criteria ranges of different size", { ...D, E1: '=COUNTIFS(A1:A4,"North",C1:C3,2)' }, "E1", "#VALUE!"],
  ["SUMIFS odd criteria count", { ...D, E1: '=SUMIFS(B1:B4,A1:A4)' }, "E1", "#VALUE!"],
  ["COUNTIFS two pairs", { ...D, E1: '=COUNTIFS(A1:A4,"North",C1:C4,2)' }, "E1", 2],
  ["COUNTIFS wildcard", { ...D, E1: '=COUNTIFS(A1:A4,"N*")' }, "E1", 3],
  ["COUNTIFS none", { ...D, E1: '=COUNTIFS(A1:A4,"West")' }, "E1", 0],
  ["AVERAGEIFS", { ...D, E1: '=AVERAGEIFS(B1:B4,A1:A4,"North")' }, "E1", 20],
  ["AVERAGEIFS no match", { ...D, E1: '=AVERAGEIFS(B1:B4,A1:A4,"West")' }, "E1", "#DIV/0!"],
  ["ROUNDUP away from 0", { A1: "=ROUNDUP(2.11,1)" }, "A1", 2.2],
  ["ROUNDUP negative", { A1: "=ROUNDUP(-2.11,1)" }, "A1", -2.2],
  ["ROUNDUP exact stays", { A1: "=ROUNDUP(1.1,1)" }, "A1", 1.1],
  ["ROUNDUP tens", { A1: "=ROUNDUP(121,-1)" }, "A1", 130],
  ["ROUNDDOWN toward 0", { A1: "=ROUNDDOWN(2.99,1)" }, "A1", 2.9],
  ["ROUNDDOWN negative", { A1: "=ROUNDDOWN(-2.99,0)" }, "A1", -2],
  ["TRUNC default", { A1: "=TRUNC(-7.9)" }, "A1", -7],
  ["TRUNC digits", { A1: "=TRUNC(3.14159,2)" }, "A1", 3.14],
  ["SIGN", { A1: "=SIGN(-4)+SIGN(0)*10+SIGN(9)*100" }, "A1", 99],
  ["PRODUCT range ignores text", { ...D, E1: "=PRODUCT(B1:B4)" }, "E1", 6000],
  ["PRODUCT no numbers", { A1: "x", E1: "=PRODUCT(A1:A1)" }, "E1", 0],
  ["LARGE 2", { ...D, E1: "=LARGE(B1:B4,2)" }, "E1", 20],
  ["SMALL 1", { ...D, E1: "=SMALL(B1:B4,1)" }, "E1", 10],
  ["LARGE k past count", { ...D, E1: "=LARGE(B1:B4,4)" }, "E1", "#NUM!"],
  ["LARGE k zero", { ...D, E1: "=LARGE(B1:B4,0)" }, "E1", "#VALUE!"],
  ["ISNUMBER number", { A1: "5", E1: "=ISNUMBER(A1)" }, "E1", true],
  ["ISNUMBER text", { A1: "hi", E1: "=ISNUMBER(A1)" }, "E1", false],
  ["ISTEXT text", { A1: "hi", E1: "=ISTEXT(A1)" }, "E1", true],
  ["ISTEXT number", { A1: "5", E1: "=ISTEXT(A1)" }, "E1", false],
  ["ISBLANK empty", { E1: "=ISBLANK(A1)" }, "E1", true],
  ["ISBLANK filled", { A1: "0", E1: "=ISBLANK(A1)" }, "E1", false],
  ["ISERROR error", { A1: "=1/0", E1: "=ISERROR(A1)" }, "E1", true],
  ["ISERROR fine", { A1: "1", E1: "=ISERROR(A1)" }, "E1", false],
  ["ISNUMBER on range is #VALUE!", { A1: "1", A2: "2", E1: "=ISNUMBER(A1:A2)" }, "E1", "#VALUE!"],
  ["IFERROR passes value", { E1: "=IFERROR(5,9)" }, "E1", 5],
  ["IFERROR catches div0", { E1: "=IFERROR(1/0,9)" }, "E1", 9],
  ["IFERROR default empty text", { E1: "=IFERROR(1/0)" }, "E1", ""],
  ["IFERROR catches #N/A from lookup", { A1: "a", E1: '=IFERROR(MATCH("zz",A1:A1,0),"none")' }, "E1", "none"],
  ["IFERROR lazy: fallback error not reached when no error", { E1: "=IFERROR(5,1/0)" }, "E1", 5],
  ["IFERROR fallback error is returned", { E1: "=IFERROR(1/0,1/0)" }, "E1", "#DIV/0!"],
  ["IFERROR wrong arity", { E1: "=IFERROR()" }, "E1", "#VALUE!"],
  ["error in sum range is returned", { A1: "x", B1: "=1/0", E1: '=SUMIFS(B1:B1,A1:A1,"x")' }, "E1", "#DIV/0!"],
  ["error cell in criteria range never matches", { A1: "=1/0", B1: "5", E1: '=COUNTIFS(A1:A1,"<>x")' }, "E1", 0],
];
let bad = 0;
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length - bad}/${T.length} rows passed`);
process.exit(bad ? 1 : 0);
