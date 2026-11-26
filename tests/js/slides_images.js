// Picture checks that need no browser: signature, header and size rules, and refusal of crafted files.
const zlib = require("zlib"), crypto = require("crypto"), fs = require("fs");
const I = require("../../static/slides/images.js");
let n = 0, bad = 0;
const ok = (d, c) => { n++; if (!c) { bad++; console.log("FAIL", d); } };
function crc(buf) { let c, t = []; for (let k = 0; k < 256; k++) { c = k; for (let j = 0; j < 8; j++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[k] = c >>> 0; } let r = 0xffffffff; for (const x of buf) r = t[(r ^ x) & 255] ^ (r >>> 8); return (r ^ 0xffffffff) >>> 0; }
function chunk(name, data) { const b = Buffer.alloc(12 + data.length); b.writeUInt32BE(data.length, 0); b.write(name, 4, "latin1"); data.copy(b, 8); b.writeUInt32BE(crc(b.subarray(4, 8 + data.length)), 8 + data.length); return b; }
function png(w, h, o = {}) {
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = o.depth || 8; ih[9] = o.ct === undefined ? 2 : o.ct; 
  const idat = zlib.deflateSync(Buffer.alloc(Math.min(h, 4) * (1 + Math.min(w, 8) * 3)));
  const parts = [Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ih)]; if (!o.noIdat) parts.push(chunk("IDAT", idat)); if (!o.noEnd) parts.push(chunk("IEND", Buffer.alloc(0)));
  return new Uint8Array(Buffer.concat(parts));
}
const hexb = (f) => new Uint8Array(Buffer.from(fs.readFileSync(f, "utf8"), "hex"));
const J = hexb(__dirname + "/fixtures/images/tiny.jpg.hex"), JP = hexb(__dirname + "/fixtures/images/tiny_progressive.jpg.hex"), JC = hexb(__dirname + "/fixtures/images/cmyk.jpg.hex");
let r = I.sniff(png(8, 4)); ok("png: a well formed PNG gives its type and size", r.ok && r.type === "image/png" && r.w === 8 && r.h === 4);
r = I.sniff(J); ok("jpeg: a baseline JPEG gives its type and size", r.ok && r.type === "image/jpeg" && r.w === 8 && r.h === 4);
r = I.sniff(JP); ok("jpeg: a progressive JPEG is accepted", r.ok && r.w === 8 && r.h === 4);
ok("jpeg: a CMYK JPEG is refused", !I.sniff(JC).ok);
ok("png: a 60000 by 60000 header is refused from the header alone, before any decode", !I.sniff(png(60000, 60000)).ok && /limit/.test(I.sniff(png(60000, 60000)).reason));
ok("png: 4096 by 4096 is over the pixel limit, 3000 by 4000 (12 million) is allowed, 4097 wide is refused", !I.sniff(png(4096, 4096)).ok && I.sniff(png(3000, 4000)).ok && !I.sniff(png(4097, 1)).ok);
ok("png: zero width or height is refused", !I.sniff(png(0, 5)).ok && !I.sniff(png(5, 0)).ok);
ok("png: a missing IEND, a missing IDAT, an invalid colour type or bit depth are refused", !I.sniff(png(8, 4, { noEnd: true })).ok && !I.sniff(png(8, 4, { noIdat: true })).ok && !I.sniff(png(8, 4, { ct: 5 })).ok && !I.sniff(png(8, 4, { ct: 3, depth: 16 })).ok);
const p = png(8, 4); const cut = p.slice(0, p.length - 20);
ok("png: a truncated file is refused", !I.sniff(cut).ok);
const extra = new Uint8Array(p.length + 4); extra.set(p); ok("png: bytes after IEND are refused", !I.sniff(extra).ok);
const lie = png(8, 4); const dv = Buffer.from(lie); dv.writeUInt32BE(0xffffff00, 33); ok("png: a chunk length that runs past the end of the file is refused", !I.sniff(new Uint8Array(dv)).ok);
ok("jpeg: truncated before the end marker, and a frame header lying past the file, are refused", !I.sniff(J.slice(0, J.length - 2)).ok && !I.sniff(J.slice(0, 60)).ok);
const big = new Uint8Array(I.LIMITS.MAX_BYTES + 1); big.set(png(8, 4)); ok("size: a file over 2 MB is refused", !I.sniff(big).ok && I.LIMITS.MAX_BYTES === 2000000);
ok("type: GIF, SVG, WebP, HTML and an empty file are refused by signature, whatever the name", ["GIF89a" + "x".repeat(60), "<svg xmlns='http://www.w3.org/2000/svg'></svg>", "RIFF\0\0\0\0WEBPVP8 " + "x".repeat(40), "<html><script>alert(1)</script></html>", ""].every((t) => !I.sniff(new Uint8Array(Buffer.from(t, "latin1"))).ok));
ok("type: a PNG renamed .jpg is still read as a PNG and a JPEG signature with PNG body is refused", I.sniff(png(8, 4)).type === "image/png" && !I.sniff(new Uint8Array([255, 216, 255, 0, 0, 0, 0, 0])).ok);
const samples = [new Uint8Array(0), new Uint8Array([1, 2, 3]), new Uint8Array(55), new Uint8Array(56), new Uint8Array(64), new Uint8Array(1000).map((_, i) => i * 7), new Uint8Array(100000).map((_, i) => i * 31)];
Promise.all(samples.map((x) => Promise.all([I.sha256(x, true), I.sha256(x)]))).then((rs) => {
  ok("sha256: the plain-code fallback equals the browser digest and node's for 0, 3, 55, 56, 64, 1000 and 100000 bytes", rs.every((p, k) => p[0] === p[1] && p[0] === crypto.createHash("sha256").update(Buffer.from(samples[k])).digest("hex")));
});
I.sha256(new Uint8Array([1, 2, 3])).then((h) => {
  ok("sha256: matches the reference value", h === crypto.createHash("sha256").update(Buffer.from([1, 2, 3])).digest("hex") && /^[0-9a-f]{64}$/.test(h));
  console.log(`${n - bad}/${n} slides image rows passed`); process.exit(bad ? 1 : 0);
});
