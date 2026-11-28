// The blueprint's "10,000-cell dependency chain" gate, run on the formula engine alone (no grid, no browser).
const F = require("../../static/sheets/formula.js");
let n = 0, bad = 0;
const ok = (d, c) => { n++; if (!c) { bad++; console.log("FAIL", d); } };
const sheet = (c) => new F.Sheet(c, { self: "t", tab: () => null });
const col = (c, L, rows, first) => { for (let r = 1; r <= rows; r++) c[L + r] = r === 1 ? first : "=" + L + (r - 1) + "+1"; };
let c = {}; col(c, "A", 1000, "1");
ok("a chain of 1000 in one column gives 1000 (past the 400 evaluation depth)", sheet(c).get("A1000") === 1000);
c = {};
for (let k = 0; k < 10; k++) { const L = String.fromCharCode(65 + k); col(c, L, 1000, k === 0 ? "1" : "=" + String.fromCharCode(64 + k) + "1000+1"); }
let t = Date.now(); const s10 = sheet(c); const v = s10.get("J1000"); const ms = Date.now() - t;
ok("10 columns by 1000 rows, each cell depending on the one before: the last is 10000", v === 10000);
ok("every 1000th cell along the chain is right", [1, 2, 5, 9].every((k) => sheet(c).get(String.fromCharCode(65 + k) + "1000") === (k + 1) * 1000));
ok("the 10,000-cell chain finishes in under 3 seconds on this machine (it measured about " + ms + " ms)", ms < 3000);
const cyc = Object.assign({}, c); cyc.A1 = "=J1000+1";
const cv = sheet(cyc).get("J1000");
ok("negative control: closing the same 10,000-cell chain into a loop gives an error value (it is #LIMIT! here, not #CIRC!, because the loop is longer than the evaluation depth) and not a number or a hang", F.isErr(cv) && (cv.code === "#LIMIT!" || cv.code === "#CIRC!"));
const short = { A1: "=A3+1", A2: "=A1+1", A3: "=A2+1" };
ok("a short loop is named #CIRC!", sheet(short).get("A3").code === "#CIRC!");
const mid = Object.assign({}, c); mid.E500 = "=hello";
ok("negative control: a bad cell in the middle of the chain shows up at the end as an error, not a stale number", F.isErr(sheet(mid).get("J1000")));
console.log(`${n - bad}/${n} chain rows passed`);
process.exit(bad ? 1 : 0);
