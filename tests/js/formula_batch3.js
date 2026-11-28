const { Sheet, isErr } = require("../../static/sheets/formula.js");
// IFS, SWITCH and HLOOKUP. [description, cells, cell, expected]; "#X!" = error code
const H = { A1: "id", B1: "apple", C1: "Banana", D1: "cherry", A2: "price", B2: "3", C2: "5", D2: "7", A3: "n", B3: "1", C3: "2", D3: "3" };
const N = { A1: "1", B1: "3", C1: "5", D1: "7", A2: "a", B2: "b", C2: "c", D2: "d" };
const T = [
  ["IFS first true test wins", { A1: "5", B1: '=IFS(A1<3,"low",A1<10,"mid",TRUE,"high")' }, "B1", "mid"],
  ["IFS last test", { A1: "50", B1: '=IFS(A1<3,"low",A1<10,"mid",TRUE,"high")' }, "B1", "high"],
  ["IFS none true is #N/A", { A1: "50", B1: '=IFS(A1<3,"low",A1<10,"mid")' }, "B1", "#N/A"],
  ["IFS odd argument count is #N/A", { A1: "1", B1: "=IFS(A1>0,1,A1<0)" }, "B1", "#N/A"],
  ["IFS a test error is returned", { A1: "1", B1: "=IFS(1/0,1,TRUE,2)" }, "B1", "#DIV/0!"],
  ["IFS skips the value of a test that is false (an unchosen error is not reported)", { A1: "1", B1: "=IFS(A1<0,1/0,TRUE,2)" }, "B1", 2],
  ["IFS stops at the first true test (a later error test is not run)", { A1: "1", B1: "=IFS(A1>0,\"ok\",1/0,2)" }, "B1", "ok"],
  ["IFS needs at least one pair", { B1: "=IFS(TRUE)" }, "B1", "#VALUE!"],
  ["SWITCH picks the matching case", { A1: "2", B1: '=SWITCH(A1,1,"one",2,"two",3,"three")' }, "B1", "two"],
  ["SWITCH text is compared without case", { A1: "Red", B1: '=SWITCH(A1,"red",1,"blue",2)' }, "B1", 1],
  ["SWITCH text has no wildcards", { A1: "red", B1: '=SWITCH(A1,"r*",1,2)' }, "B1", 2],
  ["SWITCH default when nothing matches", { A1: "9", B1: '=SWITCH(A1,1,"one",2,"two","other")' }, "B1", "other"],
  ["SWITCH no match and no default is #N/A", { A1: "9", B1: '=SWITCH(A1,1,"one",2,"two")' }, "B1", "#N/A"],
  ["SWITCH number does not match the same digits as text", { A1: "x", A2: "=SWITCH(1,\"1\",\"text\",1,\"num\")" }, "A2", "num"],
  ["SWITCH expression error is returned", { B1: '=SWITCH(1/0,1,"one",2)' }, "B1", "#DIV/0!"],
  ["SWITCH only runs the chosen value", { A1: "1", B1: "=SWITCH(A1,1,2,2,1/0)" }, "B1", 2],
  ["SWITCH needs expression plus one pair", { B1: "=SWITCH(1,2)" }, "B1", "#VALUE!"],
  ["HLOOKUP exact finds the column and returns the row", { ...H, E1: '=HLOOKUP("cherry",A1:D3,2,FALSE)' }, "E1", 7],
  ["HLOOKUP exact is case-insensitive", { ...H, E1: '=HLOOKUP("banana",A1:D3,3,FALSE)' }, "E1", 2],
  ["HLOOKUP exact miss is #N/A", { ...H, E1: '=HLOOKUP("fig",A1:D3,2,FALSE)' }, "E1", "#N/A"],
  ["HLOOKUP row past the range is #REF!", { ...H, E1: '=HLOOKUP("apple",A1:D3,4,FALSE)' }, "E1", "#REF!"],
  ["HLOOKUP row below 1 is #VALUE!", { ...H, E1: '=HLOOKUP("apple",A1:D3,0,FALSE)' }, "E1", "#VALUE!"],
  ["HLOOKUP row 1 returns the header itself", { ...H, E1: '=HLOOKUP("apple",A1:D3,1,FALSE)' }, "E1", "apple"],
  ["HLOOKUP approximate on a sorted first row takes the largest key not above", { ...N, E1: "=HLOOKUP(6,A1:D2,2)" }, "E1", "c"],
  ["HLOOKUP approximate below the first key is #N/A", { ...N, E1: "=HLOOKUP(0,A1:D2,2)" }, "E1", "#N/A"],
  ["HLOOKUP matches VLOOKUP on the transposed data", { A1: "k1", A2: "k2", A3: "k3", B1: "10", B2: "20", B3: "30", D1: "k1", E1: "k2", F1: "k3", D2: "10", E2: "20", F2: "30", G1: '=VLOOKUP("k2",A1:B3,2,FALSE)', G2: '=HLOOKUP("k2",D1:F2,2,FALSE)' }, "G2", 20],
  ["HLOOKUP blank key is #N/A", { ...H, E1: "=HLOOKUP(Z9,A1:D3,2,FALSE)" }, "E1", "#N/A"],
  ["HLOOKUP error key is returned", { ...H, E1: "=HLOOKUP(1/0,A1:D3,2,FALSE)" }, "E1", "#DIV/0!"],
  ["HLOOKUP an error in the returned cell is returned", { ...H, C2: "=1/0", E1: '=HLOOKUP("Banana",A1:D3,2,FALSE)' }, "E1", "#DIV/0!"],
  ["HLOOKUP needs 3 or 4 arguments", { ...H, E1: '=HLOOKUP("a",A1:D3)' }, "E1", "#VALUE!"],
];
// Work budget (counted, not timed): a range read costs its cell count and a request has 500000, so 55 reads of a 10000-cell range stop with #LIMIT!.
const big = { A1: "k1", B1: "k2", C1: "k3", A2: "5", B2: "6", C2: "7" };
const heavy = Array.from({ length: 55 }, () => 'HLOOKUP("k2",A1:J1000,2,0)').join("+");  // 55 reads of 10000 cells is over the 500000 budget; the formula is under the 2000 character cap
const lim = (cells, at) => { const v = new Sheet(cells).get(at); return isErr(v) && v.code === "#LIMIT!"; };
const budget = [
  ["HLOOKUP over a 10000-cell range (A1:J1000) works once", new Sheet({ ...big, Z1: '=HLOOKUP("k2",A1:J1000,2,FALSE)' }).get("Z1") === 6],
  ["HLOOKUP exact miss over A1:J1000 is #N/A, not a hang", (() => { const v = new Sheet({ ...big, Z1: '=HLOOKUP("none",A1:J1000,2,FALSE)' }).get("Z1"); return isErr(v) && v.code === "#N/A"; })()],
  ["55 HLOOKUPs over A1:J1000 in one cell stop with #LIMIT! (work counted)", lim({ ...big, Z1: "=" + heavy }, "Z1")],
  ["IFS: the same 55 reads in an unchosen branch cost nothing, the chosen value is returned", new Sheet({ ...big, Z1: '=IFS(TRUE,"ok",TRUE,' + heavy + ")" }).get("Z1") === "ok"],
  ["SWITCH: the same 55 reads in an unchosen branch cost nothing, the chosen value is returned", new Sheet({ ...big, Z1: '=SWITCH(1,1,"ok",2,' + heavy + ")" }).get("Z1") === "ok"],
  ["IFS: the same 55 reads in the chosen branch do stop with #LIMIT!", lim({ ...big, Z1: "=IFS(TRUE," + heavy + ")" }, "Z1")],
  ["SWITCH: the same 55 reads in the chosen branch do stop with #LIMIT!", lim({ ...big, Z1: "=SWITCH(1,1," + heavy + ")" }, "Z1")],
  ["45 reads of the same range are under the budget and add up (45 times 6 is 270), so the stop above is the counted limit and not an early cut", new Sheet({ A1: "k1", B1: "k2", B2: "6", Z1: "=" + Array.from({ length: 45 }, () => 'HLOOKUP("k2",A1:J1000,2,0)').join("+") }).get("Z1") === 270],
  ["IFS: an unchosen branch that is a #DIV/0! error is not reported", new Sheet({ Z1: '=IFS(TRUE,"ok",TRUE,1/0)' }).get("Z1") === "ok"],
];
const args = (n) => Array.from({ length: n }, (_, i) => String(i + 1)).join(",");
const ifsN = (n) => "=IFS(" + Array.from({ length: n }, (_, i) => (i % 2 ? '"v"' : "FALSE")).join(",") + ")";
const swN = (n) => "=SWITCH(1," + Array.from({ length: n - 1 }, (_, i) => String(i + 2)).join(",") + ")";
const code = (f) => { const v = new Sheet({ Z1: f }).get("Z1"); return isErr(v) ? v.code : v; };
const more = [
  ["HLOOKUP exact accepts * like VLOOKUP (shared find): ap* finds apple", new Sheet({ ...H, E1: '=HLOOKUP("ap*",A1:D3,2,FALSE)' }).get("E1") === 3],
  ["HLOOKUP exact accepts ? for one character: b?nana finds Banana", new Sheet({ ...H, E1: '=HLOOKUP("b?nana",A1:D3,2,FALSE)' }).get("E1") === 5],
  ["HLOOKUP ? is exactly one character: bana? (five characters) does not match the six-letter Banana", new Sheet({ ...H, E1: '=HLOOKUP("bana?",A1:D3,2,FALSE)' }).get("E1") !== 5],
  ["HLOOKUP ~* matches only a literal star", (() => { const v = new Sheet({ A1: "a*", B1: "ab", A2: "1", B2: "2", E1: '=HLOOKUP("a~*",A1:B2,2,FALSE)' }).get("E1"); return v === 1; })()],
  ["HLOOKUP a text key over 255 characters is #VALUE! when the first row has text to compare (same shared find as VLOOKUP)", (() => { const v = new Sheet({ A1: "x", A2: "1", Z1: '=HLOOKUP("' + "a".repeat(256) + '",A1:B2,2,FALSE)' }).get("Z1"); return isErr(v) && v.code === "#VALUE!"; })()],
  ["SWITCH blank expression matches an empty-text case", code('=SWITCH(Y9,"","empty","other")') === "empty"],
  ["SWITCH blank expression does not match the number 0 (documented: blank is not 0 here)", code('=SWITCH(Y9,0,"zero","other")') === "other"],
  ["SWITCH boolean matches boolean", code('=SWITCH(TRUE,FALSE,"n",TRUE,"y")') === "y"],
  ["SWITCH boolean does not match the number 1", code('=SWITCH(TRUE,1,"num","other")') === "other"],
  ["SWITCH number does not match a boolean", code('=SWITCH(1,TRUE,"b","other")') === "other"],
  ["IFS accepts 40 arguments (20 pairs)", code(ifsN(40)) === "#N/A"],
  ["IFS with 41 arguments is #VALUE! (local cap of 40)", code(ifsN(41)) === "#VALUE!"],
  ["IFS with an odd count of 3 is #N/A", code('=IFS(FALSE,1,TRUE)') === "#N/A"],
  ["SWITCH accepts 40 arguments (19 cases then the last argument is the default, here 40)", code(swN(40)) === 40],
  ["SWITCH with 41 arguments is #VALUE! (local cap of 40)", code(swN(41)) === "#VALUE!"],
];
let bad = 0;
for (const [d, ok] of more) if (!ok) { bad++; console.log("FAIL", d); }
for (const [d, ok] of budget) if (!ok) { bad++; console.log("FAIL", d); }
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length + budget.length + more.length - bad}/${T.length + budget.length + more.length} rows passed`);
process.exit(bad ? 1 : 0);
