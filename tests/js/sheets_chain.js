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
// One Sheet instance asked for every cell, as the grid does when it draws, in three orders, with SUM ranges on top.
const NC = 26, big = {};
for (let k = 0; k < NC; k++) col(big, String.fromCharCode(65 + k), 1000, k === 0 ? "1" : "=" + String.fromCharCode(64 + k) + "1000+1");
const expect = (a) => (a.charCodeAt(0) - 65) * 1000 + Number(a.slice(1));
const rowMajor = [], colMajor = [];
for (let r = 1; r <= 1000; r++) for (let k = 0; k < NC; k++) rowMajor.push(String.fromCharCode(65 + k) + r);
for (let k = 0; k < NC; k++) for (let r = 1; r <= 1000; r++) colMajor.push(String.fromCharCode(65 + k) + r);
const wrong = (sh, list) => list.filter((a) => sh.get(a) !== expect(a)).length;
ok("the full 26 x 1000 sheet (26,000 formulas, chained down and across): row by row on ONE Sheet, every cell exactly right", wrong(sheet(big), rowMajor) === 0);
ok("same, column by column, every cell exactly right", wrong(sheet(big), colMajor) === 0);
ok("same, last cell first (reverse), every cell exactly right", wrong(sheet(big), colMajor.slice().reverse()) === 0);
const sums = Object.assign({}, c); const sumCol = (k) => { let q = 0; for (let r = 1; r <= 1000; r++) q += k * 1000 + r; return q; };
let tot = 0; for (let k = 0; k < 10; k++) { sums["L" + (k + 1)] = "=SUM(" + String.fromCharCode(65 + k) + "1:" + String.fromCharCode(65 + k) + "1000)"; tot += sumCol(k); }
sums.L11 = "=SUM(A1:J1000)";
const tenRow = rowMajor.filter((a) => a.charCodeAt(0) < 75);
const sumsOk = (sh) => { for (let k = 0; k < 10; k++) if (sh.get("L" + (k + 1)) !== sumCol(k)) return false; return sh.get("L11") === tot; };
ok("SUM over each 1000-cell column and over the whole 10,000-cell block, asked first, on the same Sheet as the cells: all 11 sums exact", (() => { const sh = sheet(sums); const f = sumsOk(sh); return f && wrong(sh, tenRow) === 0; })());
ok("same SUMs asked after every cell, and after every cell in reverse order", (() => { const a = sheet(sums); const w1 = wrong(a, tenRow) === 0 && sumsOk(a); const b = sheet(sums); const w2 = wrong(b, tenRow.slice().reverse()) === 0 && sumsOk(b); return w1 && w2; })());
ok("negative control: the same comparison does report cells when one formula in the chain is changed (so the checks above can fail)", (() => { const t = Object.assign({}, big); t.C500 = "=C499+2"; return wrong(sheet(t), colMajor) > 400; })());
// Known limit, recorded and not changed: the work budget (500,000) is shared by everything one Sheet is asked for, and each SUM adds the size of its range.
ok("known limit: on one Sheet, 49 whole-block SUMs over 10,000 cells are exact and the 50th gives #LIMIT! (the shared work budget), never a wrong number", (() => {
  const d = {}; for (let r = 1; r <= 1000; r++) for (let k = 0; k < 10; k++) d[String.fromCharCode(65 + k) + r] = r === 1 && k === 0 ? "1" : "=1";
  for (let i = 1; i <= 60; i++) d["M" + i] = "=SUM(A1:J1000)";
  const sh = sheet(d); let good = 0, lim = 0, other = 0;
  for (let i = 1; i <= 60; i++) { const v = sh.get("M" + i); if (v === 10000) good++; else if (F.isErr(v) && v.code === "#LIMIT!") lim++; else other++; }
  return good === 49 && lim === 11 && other === 0;
})());
console.log(`${n - bad}/${n} chain rows passed`);
process.exit(bad ? 1 : 0);
