const { Sheet, isErr } = require("../../static/sheets/formula.js");
// XLOOKUP. [description, cells, cell, expected]; "#X!" = error code
const P = { A1: "Apple", A2: "Pear", A3: "apple", A4: "Fig", B1: "3", B2: "5", B3: "7", B4: "9", C1: "10", C2: "20", C3: "30", C4: "40" };
const R = { A1: "x", B1: "y", C1: "z", A2: "1", B2: "2", C2: "3" };
const N = { A1: "10", A2: "20", A3: "30", A4: "5", B1: "a", B2: "b", B3: "c", B4: "d" };
const T = [
  ["exact match returns the result in the same position", { ...P, E1: '=XLOOKUP("Pear",A1:A4,B1:B4)' }, "E1", 5],
  ["exact ignores case and returns the first match", { ...P, E1: '=XLOOKUP("APPLE",A1:A4,B1:B4)' }, "E1", 3],
  ["search_mode -1 returns the last match", { ...P, E1: '=XLOOKUP("apple",A1:A4,B1:B4,NA(),0,-1)' }, "E1", 7],
  ["lookup column can be right of the result column", { ...P, E1: '=XLOOKUP(40,C1:C4,A1:A4)' }, "E1", "Fig"],
  ["a row lookup works across a row", { ...R, E1: '=XLOOKUP("y",A1:C1,A2:C2)' }, "E1", 2],
  ["no match is #N/A", { ...P, E1: '=XLOOKUP("Plum",A1:A4,B1:B4)' }, "E1", "#N/A"],
  ["no match returns missing_value", { ...P, E1: '=XLOOKUP("Plum",A1:A4,B1:B4,"none")' }, "E1", "none"],
  ["exact mode has no wildcards", { ...P, E1: '=XLOOKUP("Pe*",A1:A4,B1:B4)' }, "E1", "#N/A"],
  ["match_mode 2 uses wildcards", { ...P, E1: '=XLOOKUP("Pe*",A1:A4,B1:B4,NA(),2)' }, "E1", 5],
  ["match_mode 1 takes the next larger value (unsorted data)", { ...N, E1: "=XLOOKUP(12,A1:A4,B1:B4,NA(),1)" }, "E1", "b"],
  ["match_mode -1 takes the next smaller value", { ...N, E1: "=XLOOKUP(12,A1:A4,B1:B4,NA(),-1)" }, "E1", "a"],
  ["match_mode 1 prefers an exact match", { ...N, E1: "=XLOOKUP(20,A1:A4,B1:B4,NA(),1)" }, "E1", "b"],
  ["match_mode 1 with nothing larger is #N/A", { ...N, E1: "=XLOOKUP(99,A1:A4,B1:B4,NA(),1)" }, "E1", "#N/A"],
  ["a number key never matches text cells", { ...P, E1: "=XLOOKUP(3,A1:A4,B1:B4)" }, "E1", "#N/A"],
  ["a blank key is not found", { ...P, E1: "=XLOOKUP(Z9,A1:A4,B1:B4)" }, "E1", "#N/A"],
  ["an error key is returned", { ...P, E1: "=XLOOKUP(1/0,A1:A4,B1:B4)" }, "E1", "#DIV/0!"],
  ["an error cell in the result is returned", { ...P, B2: "=1/0", E1: '=XLOOKUP("Pear",A1:A4,B1:B4)' }, "E1", "#DIV/0!"],
  ["a lookup range of two rows and columns is #VALUE!", { ...P, E1: '=XLOOKUP("Pear",A1:B4,B1:B4)' }, "E1", "#VALUE!"],
  ["result range of another length is #VALUE!", { ...P, E1: '=XLOOKUP("Pear",A1:A4,B1:B3)' }, "E1", "#VALUE!"],
  ["a result range of several columns would spill, #VALUE! here", { ...P, E1: '=XLOOKUP("Pear",A1:A4,B1:C4)' }, "E1", "#VALUE!"],
  ["binary search modes are not supported", { ...P, E1: '=XLOOKUP("Pear",A1:A4,B1:B4,NA(),0,2)' }, "E1", "#VALUE!"],
  ["unknown match_mode is #VALUE!", { ...P, E1: '=XLOOKUP("Pear",A1:A4,B1:B4,NA(),3)' }, "E1", "#VALUE!"],
  ["an empty optional argument is a #PARSE! error value, not a thrown exception", { ...N, E1: "=XLOOKUP(10,A1:A4,B1:B4,,1)" }, "E1", "#PARSE!"],
  ["match_mode TRUE is read as the number 1 (same coercion as other numeric arguments)", { ...N, E1: "=XLOOKUP(10,A1:A4,B1:B4,NA(),TRUE)" }, "E1", "a"],
  ["match_mode 1 with two equal next-larger values (20 and 20) takes the first in search order", { ...N, A3: "20", E1: "=XLOOKUP(15,A1:A4,B1:B4,NA(),1)" }, "E1", "b"],
  ["the same with search_mode -1 takes the last", { ...N, A3: "20", E1: "=XLOOKUP(15,A1:A4,B1:B4,NA(),1,-1)" }, "E1", "c"],
  ["match_mode 1 on text takes the smallest larger text", { A1: "pear", A2: "apple", A3: "fig", B1: "1", B2: "2", B3: "3", E1: '=XLOOKUP("b",A1:A3,B1:B3,NA(),1)' }, "E1", 3],
  ["match_mode -1 on text takes the largest smaller text", { A1: "pear", A2: "apple", A3: "fig", B1: "1", B2: "2", B3: "3", E1: '=XLOOKUP("b",A1:A3,B1:B3,NA(),-1)' }, "E1", 2],
  ["a true/false key matches true/false cells only", { A1: "=TRUE", A2: "=FALSE", B1: "1", B2: "2", E1: "=XLOOKUP(FALSE,A1:A2,B1:B2)" }, "E1", 2],
  ["NA() gives #N/A", { E1: "=NA()" }, "E1", "#N/A"],
  ["NA() inside IFERROR is caught", { E1: '=IFERROR(NA(),"caught")' }, "E1", "caught"],
  ["too few arguments is an error", { ...P, E1: '=XLOOKUP("Pear",A1:A4)' }, "E1", "#VALUE!"],
];
let bad = 0;
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length - bad}/${T.length} rows passed`);
process.exit(bad ? 1 : 0);
