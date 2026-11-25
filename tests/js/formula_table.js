const { Sheet, isErr } = require("../../static/sheets/formula.js");
// [description, cells, cell to read, expected] ; expected "#X!" means an error value
const T = [
  ["number literal", { A1: "5" }, "A1", 5],
  ["add", { A1: "2", A2: "=A1+3" }, "A2", 5],
  ["precedence", { A1: "=2+3*4" }, "A1", 14],
  ["power", { A1: "=2^3^2" }, "A1", 64],  // left associative here, documented
  ["unary minus", { A1: "=-3+5" }, "A1", 2],
  ["percent", { A1: "=50%" }, "A1", 0.5],
  ["parens", { A1: "=(2+3)*4" }, "A1", 20],
  ["div by zero", { A1: "=1/0" }, "A1", "#DIV/0!"],
  ["div by blank", { A1: "=1/B1" }, "A1", "#DIV/0!"],
  ["text in arithmetic", { A1: "abc", A2: "=A1+1" }, "A2", "#VALUE!"],
  ["numeric text coerces", { A1: " 7 ", A2: "=A1+1" }, "A2", 8],
  ["blank is zero", { A1: "=B1+1" }, "A1", 1],
  ["unknown function", { A1: "=FOO(1)" }, "A1", "#NAME?"],
  ["unknown name", { A1: "=foo+1" }, "A1", "#NAME?"],
  ["out of range cell", { A1: "=ZZ9999" }, "A1", "#REF!"],
  ["bad range end", { A1: "=SUM(A2:B)" }, "A1", "#REF!"],
  ["huge range refused", { A1: "=SUM(A1:Z1000)" }, "A1", "#REF!"],
  ["self reference", { A1: "=A1+1" }, "A1", "#CIRC!"],
  ["two cell cycle A", { A1: "=A2", A2: "=A1" }, "A1", "#CIRC!"],
  ["two cell cycle B", { A1: "=A2", A2: "=A1" }, "A2", "#CIRC!"],
  ["cell feeding a cycle", { A1: "=A2", A2: "=A1", A3: "=A1+1" }, "A3", "#CIRC!"],
  ["cycle inside range", { A1: "=SUM(A2:A3)", A3: "=A1" }, "A1", "#CIRC!"],
  ["error propagates", { A1: "=1/0", A2: "=A1+1" }, "A2", "#DIV/0!"],
  ["error in range", { A1: "=1/0", A2: "=SUM(A1:A1)" }, "A2", "#DIV/0!"],
  ["parse error", { A1: "=1+" }, "A1", "#PARSE!"],
  ["unbalanced paren", { A1: "=(1+2" }, "A1", "#PARSE!"],
  ["unterminated string", { A1: '="abc' }, "A1", "#PARSE!"],
  ["stray character", { A1: "=1 $ 2" }, "A1", "#PARSE!"],
  ["trailing tokens", { A1: "=1 2" }, "A1", "#PARSE!"],
  ["too long formula", { A1: "=" + "1+".repeat(1500) + "1" }, "A1", "#PARSE!"],
  ["deep nesting refused", { A1: "=" + "(".repeat(80) + "1" + ")".repeat(80) }, "A1", "#PARSE!"],
  ["SUM range skips text and blanks", { A1: "1", A2: "x", A3: "", A4: "4", B1: "=SUM(A1:A4)" }, "B1", 5],
  ["SUM direct text arg errors", { B1: '=SUM(1,"x")' }, "B1", "#VALUE!"],
  ["SUM reversed range", { A1: "1", A2: "2", B1: "=SUM(A2:A1)" }, "B1", 3],
  ["SUM 2d range", { A1: "1", B1: "2", A2: "3", B2: "4", C1: "=SUM(A1:B2)" }, "C1", 10],
  ["AVERAGE", { A1: "2", A2: "4", B1: "=AVERAGE(A1:A2)" }, "B1", 3],
  ["AVERAGE empty", { B1: "=AVERAGE(A1:A2)" }, "B1", "#DIV/0!"],
  ["MIN MAX", { A1: "3", A2: "9", B1: "=MIN(A1:A2)", B2: "=MAX(A1:A2)" }, "B2", 9],
  ["MIN of nothing", { B1: "=MIN(A1:A2)" }, "B1", 0],
  ["COUNT counts numbers only", { A1: "1", A2: "x", A3: "3", B1: "=COUNT(A1:A3)" }, "B1", 2],
  ["ABS", { A1: "=ABS(-4)" }, "A1", 4],
  ["ROUND half away from zero", { A1: "=ROUND(2.5)", A2: "=ROUND(-2.5)", A3: "=ROUND(1.005,2)" }, "A2", -3],
  ["ROUND 2 places", { A3: "=ROUND(1.005,2)" }, "A3", 1.01],
  ["wrong arity", { A1: "=ABS()" }, "A1", "#VALUE!"],
  ["wrong arity high", { A1: "=ABS(1,2)" }, "A1", "#VALUE!"],
  ["LEN UPPER LOWER", { A1: "Hello", B1: "=LEN(A1)", B2: "=UPPER(A1)" }, "B2", "HELLO"],
  ["concat operator", { A1: '="a"&1&TRUE' }, "A1", "a1TRUE"],
  ["CONCAT", { A1: '=CONCAT("x",2)' }, "A1", "x2"],
  ["quote escape", { A1: '="say ""hi"""' }, "A1", 'say "hi"'],
  ["compare numbers", { A1: "=3>2" }, "A1", true],
  ["compare text case-insensitive", { A1: '="A"="a"' }, "A1", true],
  ["compare mixed", { A1: '=1="1"' }, "A1", false],
  ["IF true", { A1: "=IF(1,10,20)" }, "A1", 10],
  ["IF false", { A1: "=IF(0,10,20)" }, "A1", 20],
  ["IF two args false", { A1: "=IF(0,10)" }, "A1", false],
  ["IF skips unchosen error", { A1: "=IF(1,5,1/0)" }, "A1", 5],
  ["IF condition error", { A1: "=IF(1/0,1,2)" }, "A1", "#DIV/0!"],
  ["IF condition text", { A1: '=IF("maybe",1,2)' }, "A1", "#VALUE!"],
  ["AND OR NOT", { A1: "=AND(1,TRUE)", A2: "=OR(0,0)", A3: "=NOT(0)" }, "A2", false],
  ["range as a scalar", { A1: "1", A2: "2", B1: "=A1:A2+1" }, "B1", "#VALUE!"],
  ["overflow", { A1: "=10^400" }, "A1", "#VALUE!"],
  ["proto-name is not a cell", { A1: "=constructor+1" }, "A1", "#NAME?"],
  ["lowercase refs", { A1: "2", A2: "=a1*2" }, "A2", 4],
  ["no eval of code text", { A1: "=process.exit(1)" }, "A1", "#NAME?"],
  ["plain text cell", { A1: "hello" }, "A1", "hello"],
  ["empty cell reads empty", {}, "A1", ""],
  ["unary minus binds before power (this engine's rule, matches common spreadsheets but not claimed as compatibility)", { A1: "=-2^2" }, "A1", 4],
  ["SUM of a text cell ref ignores it", { A1: "x", B1: "=SUM(A1)" }, "B1", 0],
  ["SUM of a blank cell ref", { B1: "=SUM(A1)" }, "B1", 0],
  ["SUM of text literal errors", { B1: '=SUM("x")' }, "B1", "#VALUE!"],
  ["COUNT of a text cell ref", { A1: "x", B1: "=COUNT(A1)" }, "B1", 0],
  ["AVERAGE of a text cell ref is div0", { A1: "x", B1: "=AVERAGE(A1)" }, "B1", "#DIV/0!"],
  ["one-cell range and ref agree", { A1: "x", B1: "=SUM(A1:A1)", B2: "=SUM(A1)" }, "B2", 0],
  ["nonfinite literal", { A1: "=1e999" }, "A1", "#VALUE!"],
  ["nonfinite numeric text stays text", { A1: "1e999", B1: "=A1+1" }, "B1", "#VALUE!"],
  ["ROUND 2.4999999995", { A1: "=ROUND(2.4999999995)" }, "A1", 2],
  ["ROUND 1.4999999999", { A1: "=ROUND(1.4999999999)" }, "A1", 1],
  ["ROUND negative places", { A1: "=ROUND(1250,-2)" }, "A1", 1300],
  ["ROUND -1.005", { A1: "=ROUND(-1.005,2)" }, "A1", -1.01],
  ["ROUND tiny exponent", { A1: "=ROUND(0.0000001,3)" }, "A1", 0],
  ["ROUND 1e10 at 15 places", { A1: "=ROUND(1e10,15)" }, "A1", 1e10],
  ["ROUND 15-digit int at 8 places", { A1: "=ROUND(123456789012345,8)" }, "A1", 123456789012345],
  ["ROUND 1e21 at 0", { A1: "=ROUND(1e21)" }, "A1", 1e21],
  ["ROUND 1e-7 at 7", { A1: "=ROUND(0.00000015,7)" }, "A1", 0.0000002],
  ["ROUND large negative places", { A1: "=ROUND(123456,-15)" }, "A1", 0],
  ["bad address read", { A1: "1" }, "constructor", "#REF!"],
  ["prototype address read", { A1: "1" }, "__proto__", "#REF!"],
];
const chain = {}; for (let i = 1; i <= 3000; i++) chain["A" + i] = i === 1000 ? "=1" : "=A" + (i + 1) + "+1";
// long dependency chain: rows are capped at 1000 so only A1..A1000 exist; cells past A1000 are #REF!
const c2 = {}; for (let i = 1; i <= 1000; i++) c2["A" + i] = i === 1000 ? "1" : "=A" + (i + 1) + "+1";
const asc = new Sheet(c2).get("A1");
const sh = new Sheet(c2); const mid = sh.get("A500"); const top = sh.get("A1");
const sh2 = new Sheet(c2); const top2 = sh2.get("A1");
const cyc = {}; for (let i = 1; i <= 600; i++) cyc["A" + i] = "=A" + (i % 600 + 1);
const cy = new Sheet(cyc).get("A1");
const dbl = {A1: "=\"abcdefghij\""}; for (let i = 2; i <= 40; i++) dbl["A" + i] = "=A" + (i - 1) + "&A" + (i - 1);
const big = {}; for (let r = 1; r <= 60; r++) big["L" + r] = "=SUM(A1:J1000)+1";
const extra = [
  ["1000-cell chain, order independent", asc === 1000 && top === 1000 && mid === 501 && top2 === 1000],
  ["600-cell cycle reports #CIRC! or the depth limit, never a crash", isErr(cy) && (cy.code === "#CIRC!" || cy.code === "#LIMIT!")],
  ["doubling strings fail predictably", isErr(new Sheet(dbl).get("A40")) && new Sheet(dbl).get("A40").code === "#VALUE!"],
  ["many big ranges hit the budget, not a freeze", (() => { const t0 = Date.now(); const s = new Sheet(big); let lim = 0; for (let r = 1; r <= 60; r++) { const v = s.get("L" + r); if (isErr(v) && v.code === "#LIMIT!") lim++; } return lim > 0 && Date.now() - t0 < 20000; })()],
  ["imported keys cannot reach prototypes", new Sheet({ constructor: "=1", __proto__: "x", A1: "2" }).get("A1") === 2 && new Sheet({ constructor: "5" }).get("constructor").code === "#REF!"],
  ["non-scalar cell values are dropped", new Sheet({ A1: { x: 1 }, A2: [1], A3: NaN, A4: "ok" }).get("A4") === "ok" && new Sheet({ A1: { x: 1 } }).get("A1") === ""],
];
let bad = 0;
for (const [d, ok] of extra) if (!ok) { bad++; console.log("FAIL", d); }
for (const [d, cells, at, want] of T) {
  const got = new Sheet(cells).get(at);
  const g = isErr(got) ? got.code : got;
  if (g !== want) { bad++; console.log("FAIL", d, "got", g, "want", want); }
}
console.log(`${T.length + extra.length - bad}/${T.length + extra.length} rows passed`);
process.exit(bad ? 1 : 0);
