const P = require("../../static/pdf/check.js");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
ok("a PDF header passes", P.check("a.pdf", 100, "%PDF-1.7\n").ok);
ok("junk before the header up to 1024 bytes passes", P.check("a.pdf", 2000, "x".repeat(500) + "%PDF-1.4").ok);
ok("header after 1024 bytes fails", !P.check("a.pdf", 2000, "x".repeat(1100) + "%PDF-1.4").ok);
ok("the name is not trusted", !P.check("a.pdf", 100, "PK\x03\x04").ok && P.check("a.txt", 100, "%PDF-1.7").ok);
ok("empty and oversize files fail with a reason", !P.check("a", 0, "%PDF-").ok && !P.check("a", P.MAX_BYTES + 1, "%PDF-").ok && /MB/.test(P.check("a", P.MAX_BYTES + 1, "%PDF-").why));
ok("the limit itself passes", P.check("a", P.MAX_BYTES, "%PDF-").ok);
ok("bad inputs fail without throwing", !P.check("a", "9", "%PDF-").ok && !P.check("a", 9, null).ok);
ok("page index is clamped", P.pageIndex(1, -1, 5) === 1 && P.pageIndex(5, 1, 5) === 5 && P.pageIndex(2, 1, 5) === 3);
ok("zoom steps are bounded", P.zoomStep(0.5, -1) === 0.5 && P.zoomStep(3, 1) === 3 && P.zoomStep(1, 1) === 1.25 && P.zoomStep(7, 1) === 1.25);
console.log(`${n - bad}/${n} pdf check rows passed`);
process.exit(bad ? 1 : 0);
