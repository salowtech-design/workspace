// Sheets move tab: model rows for M.moveTab (order only).
const M = require("../../static/sheets/model.js");
let n = 0, fails = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
let m = M.loadTabs(null).meta; m = M.addTab(m); m = M.addTab(m);  // t0 t1 t2, t2 active
const ids = (x) => x.tabs.map((t) => t.id).join(",");
ok("setup has three tabs", ids(m) === "t0,t1,t2");
const l = M.moveTab(m, "t2", -1);
ok("left swaps with the previous tab", ids(l) === "t0,t2,t1");
ok("the source meta is unchanged", ids(m) === "t0,t1,t2");
ok("active tab and names are kept", l.active === m.active && l.tabs.map((t) => t.name).sort().join() === m.tabs.map((t) => t.name).sort().join());
ok("right swaps with the next tab", ids(M.moveTab(m, "t0", 1)) === "t1,t0,t2");
ok("the first tab cannot move left", M.moveTab(m, "t0", -1) === null);
ok("the last tab cannot move right", M.moveTab(m, "t2", 1) === null);
ok("an unknown tab or direction is refused", M.moveTab(m, "t9", 1) === null && M.moveTab(m, "t1", 0) === null && M.moveTab(m, "t1", 2) === null);
ok("a single tab cannot move", M.moveTab(M.loadTabs(null).meta, "t0", 1) === null);
ok("the version marker is kept", M.moveTab(M.markFmt(m), "t1", 1).v === 2);
ok("result reads back through loadTabs", (() => { const r = M.loadTabs(JSON.stringify(l)); return !r.unreadable && ids(r.meta) === "t0,t2,t1"; })());
console.log((fails ? "FAILED " : "") + (n - fails) + "/" + n + " sheets tab move rows passed");
process.exit(fails ? 1 : 0);
