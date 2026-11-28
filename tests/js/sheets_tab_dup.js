// Sheets duplicate tab: model rows for M.duplicateTab (the page copies the cells; see the tour).
const M = require("../../static/sheets/model.js");
let n = 0, fails = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const base = M.loadTabs(null).meta;
const two = M.renameTab(M.addTab(base), "t1", "Budget").meta;  // Sheet 1 is t0
let d = M.duplicateTab(base, "t0");
ok("a copy is named '<name> copy', placed after the source, and becomes active", d && d.tabs.length === 2 && d.tabs[1].name === base.tabs[0].name + " copy" && d.active === d.tabs[1].id);
ok("the new id is fresh", d.tabs[1].id !== "t0" && /^t[0-9]+$/.test(d.tabs[1].id));
ok("the source meta is unchanged", base.tabs.length === 1 && base.active === "t0");
const mid = M.duplicateTab(two, "t0");
ok("a copy of the first tab goes right after it, not at the end", mid.tabs.map((t) => t.name).join("|") === two.tabs[0].name + "|" + two.tabs[0].name + " copy|Budget");
const again = M.duplicateTab(d, "t0");
ok("a second copy of the same tab gets ' copy 2'", again.tabs.some((t) => t.name === base.tabs[0].name + " copy 2"));
const third = M.duplicateTab(again, "t0");
ok("a third copy gets ' copy 3'", third.tabs.some((t) => t.name === base.tabs[0].name + " copy 3"));
ok("names stay unique ignoring case", (() => { const m = M.renameTab(M.addTab(base), "t1", "sheet 1 COPY").meta; const x = M.duplicateTab(m, "t0"); const names = x.tabs.map((t) => t.name.toLowerCase()); return new Set(names).size === names.length && names.includes("sheet 1 copy 2"); })());
ok("a long name is cut so the copy name fits 30 characters", (() => { const long = "x".repeat(30); const m = M.renameTab(M.addTab(base), "t1", long).meta; const x = M.duplicateTab(m, "t1"); const nm = x.tabs.find((t) => t.id === x.active).name; return nm.length <= 30 && nm.endsWith(" copy"); })());
ok("a name already at 30 characters still gets a copy name", (() => { const long = "y".repeat(30); const m = M.renameTab(M.addTab(base), "t1", long).meta; const x = M.duplicateTab(m, "t1"); return x !== null && x.tabs.length === 3; })());
ok("an id whose cells still exist is not reused", (() => { const x = M.duplicateTab(base, "t0", (id) => id === "t1" || id === "t2"); return x.tabs[1].id === "t3"; })());
ok("at the 10-tab cap it refuses", (() => { let m = base; while (m.tabs.length < M.MAX_TABS) m = M.addTab(m); return M.duplicateTab(m, "t0") === null; })());
ok("an unknown tab refuses", M.duplicateTab(base, "t9") === null);
ok("the version marker is kept", (() => { const m = M.markFmt(base); return M.duplicateTab(m, "t0").v === 2; })());
ok("the result reads back through loadTabs", (() => { const r = M.loadTabs(JSON.stringify(d)); return !r.unreadable && r.meta.tabs.length === 2 && r.meta.active === d.active; })());
console.log((fails ? "FAILED " : "") + (n - fails) + "/" + n + " sheets tab duplicate rows passed");
process.exit(fails ? 1 : 0);
