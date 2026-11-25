// Scope: the previous 8 x 20 model.js and formula.js (byte-identical copies of static/sheets/{model,formula}.js at commit 3d0df75,
// kept in fixtures/) are asked to read, save and undo a sheet that uses the 26 x 100 corner. This tests the old model/history
// code path only (clean, History save, undo). It does not run the old grid.js page, which hides cells outside A1:H20.
const Old = require("./fixtures/sheets_model_grid8x20.js");
const OldF = require("./fixtures/sheets_formula_grid8x20.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const mk = (o) => Object.assign(Object.create(null), o);
const stored = { A1: "1", H20: "h", I1: "wide", Z100: "far", A100: "deep", Z1: "z" };
const c = Old.clean(JSON.parse(JSON.stringify(stored)), OldF.parseAddr, 2000);
ok("previous build reads a 26 x 100 sheet without refusing it", c && c.count === 6 && c.skipped === 0);
const saved = [];
const h = new Old.History(c.cells, { save(x) { saved.push(Object.assign({}, x)); return true; } }, 100);
const next = Old.copy ? Old.copy(h.cells) : Object.assign(mk({}), h.cells);
next.A1 = "2";
h.change(next);
const last = saved[saved.length - 1];
ok("previous build's own save keeps cells outside A1:H20", last && last.Z100 === "far" && last.A100 === "deep" && last.I1 === "wide" && last.Z1 === "z" && last.A1 === "2");
ok("previous build: undo also keeps them", h.undo() && h.cells.Z100 === "far" && h.cells.A1 === "1");
ok("old parseAddr accepts the new corner", OldF.parseAddr("Z100") && OldF.parseAddr("Z100").col === 26);
console.log(n - fails + "/" + n + " grid compatibility rows passed");
process.exit(fails ? 1 : 0);
