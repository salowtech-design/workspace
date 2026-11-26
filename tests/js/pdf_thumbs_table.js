const T = require("../../static/pdf/thumbs.js");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
ok("start of a long strip wants the visible items plus a few ahead", eq(T.want(0, 300, 100, 300), [0, 5]));
ok("a scrolled strip wants items on both sides", eq(T.want(5000, 300, 100, 300), [47, 55]));
ok("the end of the strip is clamped", eq(T.want(29700, 300, 100, 300), [294, 299]));
ok("a short strip wants everything", eq(T.want(0, 1000, 100, 4), [0, 3]));
ok("no width or no items wants nothing", eq(T.want(0, 0, 100, 10), [0, -1]) && eq(T.want(0, 300, 0, 10), [0, -1]) && eq(T.want(0, 300, 100, 0), [0, -1]));
ok("an item near the range is kept and a far one is let go", !T.drop(40, [47, 55]) && T.drop(30, [47, 55]) && T.drop(70, [47, 55]) && !T.drop(63, [47, 55]));
ok("the number of drawn items is bounded for any scroll position", (() => { for (let s = 0; s < 40000; s += 137) { const r = T.want(s, 390, 92, 400); if (r[1] - r[0] + 1 > Math.ceil(390 / 92) + 2 * T.AHEAD + 1) return false; } return true; })());
console.log(`${n - bad}/${n} pdf thumbs rows passed`);
process.exit(bad ? 1 : 0);
