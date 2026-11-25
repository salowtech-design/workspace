const M = require("../../static/sheets/model.js");
const { parseAddr } = require("../../static/sheets/formula.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const mk = (o) => Object.assign(Object.create(null), o);
const mem = (fail) => ({ saved: [], save(c) { if (fail && fail.on) return false; this.saved.push(Object.assign({}, c)); return true; } });

let st = mem(), h = new M.History(mk({}), st, 3);
h.change(mk({ A1: "1" })); h.change(mk({ A1: "2" })); h.change(mk({ A1: "3" })); h.change(mk({ A1: "4" }));
ok("history is bounded", h.past.length === 3);
ok("undo steps back one state at a time", h.undo() && h.cells.A1 === "3" && h.undo() && h.cells.A1 === "2");
ok("redo restores", h.redo() && h.cells.A1 === "3");
ok("new edit clears redo", (h.change(mk({ A1: "9" })), h.future.length === 0));
ok("unchanged edit is not a step", h.change(mk({ A1: "9" })) === false);
ok("undo after clear brings cells back", (h.change(mk({})), h.undo() && h.cells.A1 === "9"));
ok("undo of an import does not touch earlier steps", (() => { const g = new M.History(mk({ A1: "1" }), mem(), 100); g.change(mk({ A1: "1", B1: "later" })); g.change(mk({ A1: "7" })); g.undo(); return g.cells.A1 === "1" && g.cells.B1 === "later" && (g.undo(), g.cells.B1 === undefined); })());
ok("second import keeps the first state as a step", (() => { const g = new M.History(mk({ A1: "orig" }), mem(), 100); g.change(mk({ A1: "i1" })); g.change(mk({ A1: "i2" })); g.undo(); g.undo(); return g.cells.A1 === "orig"; })());
ok("empty undo and redo do nothing", (() => { const g = new M.History(mk({}), mem(), 100); return !g.undo() && !g.redo(); })());

const fail = { on: true }; st = mem(fail); h = new M.History(mk({}), st, 100);
h.change(mk({ A1: "1" }), "Imported 1 cells.");
ok("blocked storage keeps the failure visible next to the note", h.saveFailed && /would not store/.test(h.note) && /Imported 1 cells/.test(h.note));
ok("failure persists through undo", (h.undo(), h.saveFailed && /would not store/.test(h.note) && /Undone/.test(h.note)));
ok("failure persists through redo", (h.redo(), h.saveFailed && /would not store/.test(h.note)));
ok("failure persists through clear", (h.change(mk({}), h.saveFailed && /would not store/.test(h.note))));
fail.on = false; h.change(mk({ A1: "2" }));
ok("a later successful save clears the failure", !h.saveFailed && h.note === "Saved on this device.");
ok("a throwing store counts as failure", (() => { const g = new M.History(mk({}), { save() { throw new Error("quota"); } }, 100); g.change(mk({ A1: "1" })); return g.saveFailed; })());

ok("import: empty refused", M.clean({}, parseAddr, 2000).count === 0);
ok("import: arrays and non-objects refused", M.clean([1], parseAddr, 2000) === null && M.clean("x", parseAddr, 2000) === null && M.clean(null, parseAddr, 2000) === null);
ok("import: prototype and bad keys skipped", (() => { const r = M.clean(JSON.parse('{"__proto__":"x","constructor":"y","A1":"1","ZZ99":"2","A2":5}'), parseAddr, 2000); return r.count === 1 && r.skipped === 4 && Object.getPrototypeOf(r.cells) === null; })());
ok("import: over-long text skipped", M.clean({ A1: "x".repeat(2001) }, parseAddr, 2000).count === 0);

let c = M.chartPoints([{ a: "A1", v: 5 }, { a: "A2", v: -8 }, { a: "A3", v: 0 }, { a: "A4", v: "x" }, { a: "A5", v: NaN }]);
ok("chart: lo, hi and skips", c.lo === -8 && c.hi === 5 && c.skipped === 2);
ok("chart: zero value has no bar", c.bars.find((b) => b.a === "A3").bar === false && c.bars.find((b) => b.a === "A1").bar === true);
ok("chart: all zero still has a range", (() => { const z = M.chartPoints([{ a: "A1", v: 0 }]); return z.hi > z.lo; })());
ok("chart: nothing numeric gives null", M.chartPoints([{ a: "A1", v: "x" }]) === null && M.chartPoints([]) === null);
ok("chart: all negative keeps zero on the axis", (() => { const z = M.chartPoints([{ a: "A1", v: -3 }]); return z.hi === 0 && z.lo === -3; })());

ok("ime: composing Enter flagged", M.isComposing({ key: "Enter", isComposing: true }) && M.isComposing({ key: "Enter", keyCode: 229 }) && !M.isComposing({ key: "Enter", keyCode: 13 }));
ok("keys: Enter and Shift+Enter", M.keyMove("Enter", false)[1] === 1 && M.keyMove("Enter", true)[1] === -1);
ok("keys: up and down always move", M.keyMove("ArrowUp", false)[1] === -1 && M.keyMove("ArrowDown", false, false, false)[1] === 1);
ok("keys: left and right only at the edge", M.keyMove("ArrowLeft", false, false, false) === null && M.keyMove("ArrowLeft", false, true, false)[0] === -1 && M.keyMove("ArrowRight", false, false, false) === null && M.keyMove("ArrowRight", false, false, true)[0] === 1);
ok("keys: other keys do nothing", M.keyMove("a", false, true, true) === null);
console.log(`${n - fails}/${n} model rows passed`);
process.exit(fails ? 1 : 0);
