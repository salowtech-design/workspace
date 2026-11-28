const { Sheet, isErr } = require("../../static/sheets/formula.js");
// IFNA, EXACT, PROPER, REPT, CHOOSE. [description, cells, cell, expected]; "#X!" = error code
const f = (e, extra) => ({ ...(extra || {}), E1: e });
const T = [
  ["IFNA returns the value when it is not an error", f('=IFNA(5,"x")'), "E1", 5],
  ["IFNA replaces #N/A", f('=IFNA(NA(),"gone")'), "E1", "gone"],
  ["IFNA replaces a failed lookup", f('=IFNA(MATCH("q",A1:A2,0),"none")', { A1: "a", A2: "b" }), "E1", "none"],
  ["IFNA returns another error unchanged (#DIV/0!)", f('=IFNA(1/0,"gone")'), "E1", "#DIV/0!"],
  ["IFNA does not run the second argument when the first is fine (an error there is not reported)", f("=IFNA(3,1/0)"), "E1", 3],
  ["IFNA with the second argument an error returns it when the first is #N/A", f("=IFNA(NA(),1/0)"), "E1", "#DIV/0!"],
  ["IFNA with one argument is an error", f("=IFNA(1)"), "E1", "#VALUE!"],
  ["EXACT is case sensitive: same text", f('=EXACT("Abc","Abc")'), "E1", true],
  ["EXACT is case sensitive: different case", f('=EXACT("Abc","abc")'), "E1", false],
  ["EXACT compares a number with the same digits as text", f('=EXACT(12,"12")'), "E1", true],
  ["EXACT returns an error argument", f('=EXACT(1/0,"a")'), "E1", "#DIV/0!"],
  ["PROPER capitalises each word", f('=PROPER("hello wORLD of sheets")'), "E1", "Hello World Of Sheets"],
  ["PROPER capitalises after a non-letter (local choice: it's gives It'S)", f('=PROPER("it\'s 3rd-rate")'), "E1", "It'S 3Rd-Rate"],
  ["PROPER of empty text is empty", f('=PROPER("")'), "E1", ""],
  ["PROPER handles accented letters", f('=PROPER("élan vital")'), "E1", "Élan Vital"],
  ["REPT repeats text", f('=REPT("ab",3)'), "E1", "ababab"],
  ["REPT zero times is empty", f('=REPT("ab",0)'), "E1", ""],
  ["REPT cuts a fraction to a whole number", f('=REPT("ab",2.9)'), "E1", "abab"],
  ["REPT negative is #VALUE!", f('=REPT("ab",-1)'), "E1", "#VALUE!"],
  ["REPT over 10,000 characters is #VALUE!", f('=REPT("abc",3334)'), "E1", "#VALUE!"],
  ["REPT exactly 10,000 characters is allowed", f('=LEN(REPT("abcd",2500))'), "E1", 10000],
  ["REPT of empty text with a huge count is empty (no work done)", f('=REPT("",1000000)'), "E1", ""],
  ["CHOOSE picks by position", f('=CHOOSE(2,"a","b","c")'), "E1", "b"],
  ["CHOOSE cuts the index to a whole number", f('=CHOOSE(2.9,"a","b","c")'), "E1", "b"],
  ["CHOOSE index 0 is #VALUE!", f('=CHOOSE(0,"a","b")'), "E1", "#VALUE!"],
  ["CHOOSE index past the values is #VALUE!", f('=CHOOSE(3,"a","b")'), "E1", "#VALUE!"],
  ["CHOOSE with an error index returns it", f('=CHOOSE(1/0,"a","b")'), "E1", "#DIV/0!"],
  ["CHOOSE an unchosen error value is not reported", f("=CHOOSE(1,5,1/0)"), "E1", 5],
  ["CHOOSE a chosen error value is returned", f("=CHOOSE(2,5,1/0)"), "E1", "#DIV/0!"],
  ["CHOOSE can choose a cell value", f("=CHOOSE(2,A1,B1)", { A1: "left", B1: "right" }), "E1", "right"],
  ["CHOOSE a multi-cell range as the chosen value is #VALUE!", f("=CHOOSE(1,A1:A2,5)", { A1: "1", A2: "2" }), "E1", "#VALUE!"],
  ["CHOOSE needs an index and one value", f("=CHOOSE(1)"), "E1", "#VALUE!"],
];
let bad = 0;
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length - bad}/${T.length} rows passed`);
process.exit(bad ? 1 : 0);
