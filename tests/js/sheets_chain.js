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
// The work budget (500,000) is per top-level request, and a request that stopped on it is never kept. Block of 10,000 constants plus 60 whole-block SUMs.
const blk = {}; for (let r = 1; r <= 1000; r++) for (let k = 0; k < 10; k++) blk[String.fromCharCode(65 + k) + r] = "1";
for (let i = 1; i <= 60; i++) blk["M" + i] = "=SUM(A1:J1000)";
const mAll = []; for (let i = 1; i <= 60; i++) mAll.push("M" + i);
const exact60 = (list) => { const sh = sheet(blk); return list.every((a) => sh.get(a) === 10000); };
ok("60 whole-block SUMs on one Sheet are all exact: forward, reverse, and asked after every block cell", exact60(mAll) && exact60(mAll.slice().reverse()) && (() => { const sh = sheet(blk); rowMajor.slice(0, 500).forEach((a) => sh.get(a)); return mAll.every((a) => sh.get(a) === 10000); })());
const heavy = Object.assign({}, blk); heavy.X1 = "=" + mAll.map(() => "SUM(A1:J1000)").join("+"); heavy.X2 = "=IFERROR(X1,0)"; heavy.X3 = "=" + mAll.map((a) => a).join("+");
const lim = (v) => F.isErr(v) && v.code === "#LIMIT!";
ok("a cell whose own formula reads 600,000 cells is #LIMIT! cold, after the 60 SUMs, and in reverse: always, never a number", (() => { const a = sheet(heavy); const w = sheet(heavy); mAll.forEach((x) => w.get(x)); const r = sheet(heavy); return lim(a.get("X1")) && lim(w.get("X1")) && lim(r.get("X1")); })());
ok("IFERROR around a budget stop does not turn it into 0 (and 0 is not kept for later)", (() => { const sh = sheet(heavy); const v1 = sh.get("X2"); const v2 = sh.get("X2"); return lim(v1) && lim(v2); })());
ok("a stop is not kept: after #LIMIT!, an unrelated SUM on the same Sheet is still exact", (() => { const sh = sheet(heavy); sh.get("X1"); return sh.get("M7") === 10000 && sh.get("A1") === 1; })());
ok("known residual, not hidden: a sum of the 60 SUM cells is #LIMIT! cold (600,000 cells read) and exact once those cells are already known", (() => { const cold = sheet(heavy); const c1 = cold.get("X3"); const warm = sheet(heavy); mAll.forEach((x) => warm.get(x)); return lim(c1) && warm.get("X3") === 600000; })());
ok("the cold stop leaves nothing wrong behind: asking the dependents afterwards gives the exact number", (() => { const sh = sheet(heavy); sh.get("X3"); return mAll.every((a) => sh.get(a) === 10000) && sh.get("X3") === 600000; })());
// No batch history: a heavy cell is #LIMIT! or exact by its own request only (the 3M batch backstop was tried and removed: it made values depend on what was asked earlier).
const nb = {}; for (let r = 1; r <= 1000; r++) for (let k = 0; k < 10; k++) nb[String.fromCharCode(65 + k) + r] = "1";
const nearF = "=" + Array(49).fill("SUM(A1:J1000)").join("+");
for (let i = 1; i <= 40; i++) nb["N" + i] = nearF;
const nbSh = sheet(nb); const nbA = []; for (let i = 1; i <= 40; i++) nbA.push("N" + i); const nbB = nbA.slice().reverse();
const nbSh2 = sheet(nb);
ok("40 near-budget cells (490,000 units each): every one exact, in order and in reverse on a second Sheet (no history effect)", nbA.every((a) => nbSh.get(a) === 490000) && nbB.every((a) => nbSh2.get(a) === 490000));
// cross-tab: one request reading more than 500,000 cells in total over two tabs is #LIMIT!; the stop travels up through a reference and is not kept
const xt = { Main: { id: "m", cells: Object.assign({}, nb) }, Other: { id: "o", cells: Object.assign({}, nb) } };
xt.Main.cells.X1 = "=" + Array(30).fill("SUM(A1:J1000)").concat(Array(30).fill("SUM(Other!A1:J1000)")).join("+");
xt.Main.cells.X2 = "=X1+1"; xt.Main.cells.X3 = "=IFERROR(X1,0)"; xt.Main.cells.X4 = "=SUM(Other!A1:J1000)";
xt.Other.cells.W1 = "=" + Array(60).fill("SUM(A1:J1000)").join("+"); xt.Main.cells.X5 = "=Other!W1+1";
const mkx = (self) => { const f = (nm) => { const k = Object.keys(xt).find((x) => x.toLowerCase() === nm.toLowerCase()); return k ? xt[k] : null; }; return new F.Sheet(f(self).cells, { self: f(self).id, tab: f }); };
ok("cross-tab: 30 own-tab and 30 other-tab whole-block SUMs in one formula (600,000 cells in total) is #LIMIT!, not 600000", (() => { const m = mkx("Main"); return lim(m.get("X1")); })());
ok("cross-tab: the stop reaches a dependent (X1+1) and an IFERROR around it, and is not kept: X4 (a single other-tab SUM) stays exact afterwards", (() => { const m = mkx("Main"); const a = m.get("X2"), b = m.get("X3"), c = m.get("X4"); return lim(a) && lim(b) && c === 10000 && lim(m.get("X2")); })());
ok("cross-tab: a #LIMIT! made on the other tab comes up through Other!W1 into Main, is not kept, and the same cell asked again is still #LIMIT!", (() => { const m = mkx("Main"); const a = m.get("X5"); const b = m.get("X5"); return lim(a) && lim(b) && m.get("X4") === 10000; })());
// a cold 26 x 1000 chain after a heavy stop on the same Sheet
const afterHeavy = Object.assign({}, big); afterHeavy.Z1000 = "=" + Array(60).fill("SUM(A1:J1000)").join("+");
const rm2 = rowMajor.filter((a) => a !== "Z1000");
ok("a cold 26,000-cell chain after a heavy #LIMIT! on the same Sheet: the heavy cell is #LIMIT! and every other cell is exact (row order, then a fresh Sheet in reverse)", (() => { const sh = sheet(afterHeavy); const h = sh.get("Z1000"); const w = wrong(sh, rm2); const sh2 = sheet(afterHeavy); const w2 = wrong(sh2, rm2.slice().reverse()); return lim(h) && w === 0 && w2 === 0; })());
const shuf = mAll.slice(); for (let i = shuf.length - 1; i > 0; i--) { const j = (i * 7919 + 13) % (i + 1); [shuf[i], shuf[j]] = [shuf[j], shuf[i]]; }
ok("60 whole-block SUMs in a shuffled order, asked twice (repaint): all exact both times", (() => { const sh = sheet(blk); return shuf.every((a) => sh.get(a) === 10000) && shuf.every((a) => sh.get(a) === 10000); })());
// Repeated ranges and the dependency walk (the walk dedupes a range written again in the same formula; the 500k guard is in range evaluation and still charges every repeat).
const rpt = (n, rng) => Array(n).fill("SUM(" + rng + ")").join("+");
const one = (f) => sheet(Object.assign({ Z1: f }, blk)).get("Z1");
ok("repeated range: 48 and 49 repeats of a 10,000-cell SUM are exact (480000, 490000), 50 and 60 are #LIMIT! (same as before the dedupe)", one("=" + rpt(48, "A1:J1000")) === 480000 && one("=" + rpt(49, "A1:J1000")) === 490000 && lim(one("=" + rpt(50, "A1:J1000"))) && lim(one("=" + rpt(60, "A1:J1000"))));
ok("repeated range: a different second range does not hide the repeat cost: 30 of A1:J1000 plus 30 of A1:I1000 is #LIMIT!", lim(one("=" + rpt(30, "A1:J1000") + "+" + rpt(30, "A1:I1000"))));
ok("repeated range: A1:J500 and A501:J1000 30 times each read 300,000 cells in all (500 rows of 10 columns is 5,000 per half... 30 x 5,000 x 2), under the budget, so the sum is exact (300000)", one("=" + rpt(30, "A1:J500") + "+" + rpt(30, "A501:J1000")) === 300000);
ok("repeated range in an IF branch that is not taken costs nothing (IF(1,1,60 repeats) is 1); in the taken branch it is #LIMIT!", one("=IF(1,1," + rpt(60, "A1:J1000") + ")") === 1 && lim(one("=IF(0,1," + rpt(60, "A1:J1000") + ")")));
ok("documented change from the taint rule (not from the dedupe): AND(0, <60 repeats> > 0) now shows #LIMIT! because a stop happened while the arguments were read; before it showed FALSE", lim(one("=AND(0," + rpt(60, "A1:J1000") + ">0)")));
const H60 = rpt(60, "A1:J1000");
ok("memo cap: 51 DISTINCT ranges (A1:J1..A1:J51), each asked twice in one formula (more than the 50 lists the memo keeps), gives the exact sum 2 x (10 x (1+...+51)) = 26520, the same as with no memo, and the work is charged for every use (27030 with the 10,000-cell walk is checked against the pre-memo engine in the review compare)", one("=" + Array.from({ length: 51 }, (_, i) => "SUM(A1:J" + (i + 1) + ")").concat(Array.from({ length: 51 }, (_, i) => "SUM(A1:J" + (i + 1) + ")")).join("+")) === 26520);
ok("same taint rule for OR: OR(1, H60>0) shows #LIMIT! (the heavy argument is read though not needed); IFERROR is lazy (IFERROR(1, H60) = 1) and CHOOSE does not exist here (#NAME?), so neither is in the rule", lim(one("=OR(1," + H60 + ">0)")) && one("=IFERROR(1," + H60 + ")") === 1);
ok("IF is the lazy control: IF(1, 1, H60) = 1 and the taken heavy branch is #LIMIT!", one("=IF(1,1," + H60 + ")") === 1 && lim(one("=IF(0,1," + H60 + ")")));
const tabsC = { Main: { id: "m", cells: Object.assign({}, blk) }, Other: { id: "o", cells: Object.assign({}, blk) } };
tabsC.Other.cells.A1 = "5"; tabsC.Main.cells.A2 = "=Other!A1+A1"; tabsC.Main.cells.Z1 = "=SUM(A1:J10)+SUM(Other!A1:J10)"; tabsC.Main.cells.Z2 = "=SUM(Other!A1:J10)+SUM(A1:J10)"; tabsC.Main.cells.Z4 = "=SUM(A1:A2)+SUM(Other!A1:A2)"; tabsC.Other.cells.Z1 = "=SUM(A1:J10)+SUM(Main!A1:J10)";
const mkT = (self) => { const f = (nm) => { const k = Object.keys(tabsC).find((x) => x.toLowerCase() === nm.toLowerCase()); return k ? tabsC[k] : null; }; return new F.Sheet(f(self).cells, { self: f(self).id, tab: f }); };
ok("the same range text on the own tab and on another tab is never merged: own A1:J10 is 105 (A2 reads Other!A1), Other A1:J10 is 104, the sums are 209 in either order, from either tab, and A1:A2 plus Other!A1:A2 is 7 + 6 = 13", (() => { const m = mkT("Main"), o = mkT("Other"); return m.get("Z1") === 209 && m.get("Z2") === 209 && o.get("Z1") === 209 && m.get("Z4") === 13; })());
console.log(`${n - bad}/${n} chain rows passed`);
process.exit(bad ? 1 : 0);
