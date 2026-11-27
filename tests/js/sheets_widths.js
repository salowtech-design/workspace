const M = require("../../static/sheets/model.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const keys = (o) => Object.keys(o).join(",");
ok("no stored value is readable with no custom widths", (() => { const l = M.loadWidths(null); return !l.unreadable && keys(l.w) === ""; })());
ok("a stored width is read back", (() => { const l = M.loadWidths('{"v":1,"w":{"B":200,"A":60}}'); return !l.unreadable && l.w.B === 200 && l.w.A === 60; })());
ok("a stored standard width is dropped", (() => { const l = M.loadWidths('{"v":1,"w":{"B":144}}'); return !l.unreadable && keys(l.w) === ""; })());
for (const [d, raw] of [["broken JSON", "{bad"], ["wrong version", '{"v":2,"w":{}}'], ["no w", '{"v":1}'], ["w is an array", '{"v":1,"w":[]}'], ["lowercase column", '{"v":1,"w":{"a":100}}'], ["column past Z", '{"v":1,"w":{"AA":100}}'], ["width 39", '{"v":1,"w":{"A":39}}'], ["width 401", '{"v":1,"w":{"A":401}}'], ["fractional width", '{"v":1,"w":{"A":100.5}}'], ["string width", '{"v":1,"w":{"A":"100"}}']])
  ok("unreadable: " + d, M.loadWidths(raw).unreadable === true && keys(M.loadWidths(raw).w) === "");
ok("one bad entry makes the whole entry unreadable (nothing half applied)", M.loadWidths('{"v":1,"w":{"A":100,"B":9}}').unreadable === true);
ok("widthsJson of nothing is null and of standard widths is null", M.widthsJson({}) === null && M.widthsJson({ A: 144 }) === null);
ok("widthsJson sorts columns and round-trips", (() => { const j = M.widthsJson({ C: 90, A: 200 }); return j === '{"v":1,"w":{"A":200,"C":90}}' && M.loadWidths(j).w.C === 90; })());
ok("parseWidth accepts 40 and 400 and trims", (() => { const a = M.parseWidth(" b ", " 40 "), b = M.parseWidth("Z", "400"); return a.col === "B" && a.px === 40 && b.col === "Z" && b.px === 400; })());
for (const t of ["39", "401", "", "abc", "12.5", "-50", "1e2", "00100x", "10000"]) ok("parseWidth refuses width " + JSON.stringify(t), !!M.parseWidth("A", t).error);
for (const c of ["", "AA", "1", "@", "[" ]) ok("parseWidth refuses column " + JSON.stringify(c), !!M.parseWidth(c, "100").error);
ok("parseWidth accepts a zero-padded number as its value", M.parseWidth("A", "0100").px === 100);
ok("constants", M.MIN_CW === 40 && M.MAX_CW === 400 && M.DEFAULT_CW === 144);
console.log(`${n - fails}/${n} width rows passed`);
process.exit(fails ? 1 : 0);
