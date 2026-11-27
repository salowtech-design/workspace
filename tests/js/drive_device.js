// Drive device client logic. Uses a small in-memory IndexedDB stand-in (NOT a browser); real IndexedDB is exercised by browser tour 107.
// Quota here is INJECTED by the stand-in, not a real quota.
const D = require("../../static/drive/device.js");
let n = 0, bad = 0;
const ok = (d, c) => { n++; if (!c) { bad++; console.log("FAIL", d); } };
function fakeIdb(opts) {
  opts = opts || {}; const store = new Map(); let dbo;
  return {
    store, get dbo() { return dbo; },
    open() {
      const r = {}; Promise.resolve().then(() => {
        r.result = dbo = { objectStoreNames: { contains: () => false }, createObjectStore() {}, close() { dbo.closed = true; }, transaction() {
          if (dbo.closed) throw new Error("InvalidStateError");
          const t = { error: null }; const done = () => Promise.resolve().then(() => Promise.resolve()).then(() => { if (t.error) t.onabort && t.onabort(); else t.oncomplete && t.oncomplete(); });
          t.objectStore = () => ({
            get: (k) => { const q = { result: store.get(k) }; done(); return q; },
            count: (k) => { const q = { result: store.has(k) ? 1 : 0 }; done(); return q; },
            put: (rec) => { if (opts.quota) t.error = { name: "QuotaExceededError" }; else if (opts.corruptNext) { opts.corruptNext = false; store.set(rec.sha256, { sha256: rec.sha256, size: 3, bytes: new Uint8Array([9, 9, 9]).buffer }); } else store.set(rec.sha256, rec); done(); return { result: undefined }; },
          }); return t; } };
        r.onupgradeneeded && r.onupgradeneeded(); r.onsuccess && r.onsuccess();
      }); return r;
    },
  };
}
const file = (name, type, bytes) => ({ name, type, arrayBuffer: () => Promise.resolve(Uint8Array.from(bytes).buffer) });
const json = (status, body) => Promise.resolve({ status, redirected: false, headers: { get: () => "application/json" }, json: () => Promise.resolve(body) });
const page = (status, redirected) => Promise.resolve({ status, redirected: !!redirected, headers: { get: () => "text/html" }, json: () => Promise.reject(new Error("no")) });
const urls = { fileNew: "/drive/file/new/", version: (id) => "/drive/file/" + id + "/version/" };
const mk = (o, fetchFn) => ({ store: new D.Store("t", fakeIdb(o)), fetch: fetchFn, csrf: "tok", urls });
const created = (u, id) => u.endsWith("new/") ? json(201, { ok: true, file: { id } }) : json(200, { ok: true, file: { id, version: 1, size: 3 } });
(async () => {
  const h = await D.sha256(Uint8Array.from([1, 2, 3]));
  ok("hash: an ArrayBuffer and a Uint8Array of the same bytes hash the same", (await D.sha256(Uint8Array.from([1, 2, 3]).buffer)) === h);
  let calls = [], dep = mk({}, (u, init) => { calls.push({ u, body: init.body, tok: init.headers["X-CSRFToken"] }); return created(u, "f1"); });
  let r = await D.addFile(dep, file("a.pdf", "application/pdf", [1, 2, 3]), "fold");
  ok("add: ok, version 1, hash is the SHA-256, read back healthy", r.ok && r.onDevice && r.version === 1 && r.sha256 === h && (await dep.store.state(h)).state === "on-device");
  ok("add: only name, type, folder, size, hash and the CSRF token are sent, never bytes", calls.length === 2 && calls[0].tok === "tok" && /name=a.pdf/.test(calls[0].body) && /folder=fold/.test(calls[0].body) && /base_version=0/.test(calls[1].body) && /size=3/.test(calls[1].body) && calls[1].body.includes("sha256=" + h) && !/%01%02%03/.test(calls[0].body + calls[1].body));
  const EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
  calls = []; r = await D.addFile(mk({}, (u, init) => { calls.push({ u, body: init.body }); return created(u, "e"); }), file("empty.txt", "text/plain", []), "");
  ok("empty: the version request itself carries size 0, base 0 and the empty-input hash (not the stub reply)", r.ok && r.sha256 === EMPTY && r.size === 3 /* stub reply, not asserted as the request */ && calls.length === 2 && /size=0(&|$)/.test(calls[1].body) && /base_version=0(&|$)/.test(calls[1].body) && calls[1].body.includes("sha256=" + EMPTY));
  calls = []; r = await D.addFile(mk({ quota: true }, (u) => { calls.push(u); return json(201, {}); }), file("big.bin", "", [1, 2]), "");
  ok("injected quota: not stored, nothing sent to Drive, no on-device claim", !r.ok && !r.onDevice && /out of room/.test(r.message) && calls.length === 0);
  // damaged copy: a healthy copy is kept; a damaged one is repaired; a bad write is caught by the read back
  const s1 = new D.Store("d", fakeIdb({})); const b = Uint8Array.from([1, 2, 3]);
  ok("save: first save adds", (await s1.save(b)).how === "added" && (await s1.save(b)).how === "kept");
  s1.idb.store.set(h, { sha256: h, size: 3, bytes: Uint8Array.from([1, 2, 4]).buffer });
  ok("damaged: the record is reported damaged, then repaired by saving the same bytes, then healthy", (await s1.state(h)).state === "damaged" && (await s1.save(b)).how === "repaired" && (await s1.state(h)).state === "on-device");
  const w = mk({ corruptNext: true }, () => json(201, {}));
  calls = []; w.fetch = (u) => { calls.push(u); return json(201, {}); }; r = await D.addFile(w, file("c.bin", "", [5, 6, 7]), "");
  ok("readback: bytes that do not hash to the name are refused, Drive not called", !r.ok && r.stage === "readback" && !r.onDevice && calls.length === 0);
  // concurrent adds of the same bytes
  const cs = new D.Store("c", fakeIdb({})); const both = await Promise.all([cs.save(b), cs.save(b), cs.save(b)]);
  ok("concurrent: three saves of the same bytes at once all succeed and the stored copy is healthy", both.every((x) => x.sha256 === h) && (await cs.state(h)).state === "on-device");
  // replies: signed out vs unexpected vs network on create
  r = await D.addFile(mk({}, () => page(200, true)), file("s.txt", "", [1]), "");
  ok("signin: a redirect is a sign-in need, kept on this device, not in Drive", r.stage === "signin" && r.onDevice && /not in Drive/.test(r.message));
  r = await D.addFile(mk({}, () => json(401, {})), file("s.txt", "", [1]), "");
  ok("signin: a 401 is a sign-in need", r.stage === "signin");
  r = await D.addFile(mk({}, () => page(403, false)), file("s.txt", "", [1]), "");
  ok("unexpected: a non-JSON 403 is NOT called a sign-in need", r.stage === "unexpected" && /403/.test(r.message) && !/signing in/.test(r.message));
  r = await D.addFile(mk({}, () => page(500, false)), file("s.txt", "", [1]), "");
  ok("unexpected: a 500 page is reported as unexpected", r.stage === "unexpected" && /500/.test(r.message));
  r = await D.addFile(mk({}, () => Promise.reject(new Error("x"))), file("n.txt", "", [1]), "");
  ok("ambiguous: a failed create call does not guess; it says Drive may or may not have made the name", r.stage === "ambiguous" && r.onDevice && /may or may not/.test(r.message) && !r.fileId);
  // partial failure then the explicit existing-file path
  calls = []; r = await D.addFile(mk({}, (u) => u.endsWith("new/") ? json(201, { ok: true, file: { id: "z" } }) : Promise.reject(new Error("x"))), file("v.txt", "", [1, 2, 3]), "");
  ok("version call lost: the file id and base 0 come back, resumable, and the outcome is stated as not known", !r.ok && r.stage === "network" && r.fileId === "z" && r.baseVersion === 0 && r.resumable && /not known/.test(r.message));
  const rd = mk({}, (u, init) => { calls.push({ u, body: init.body }); return json(200, { ok: true, file: { id: "z", version: 1, size: 3 } }); });
  r = await D.addVersion(rd, "z", 0, file("v.txt", "", [1, 2, 3]));
  ok("resume: addVersion to the known file with base 0 records version 1 and creates no new record", r.ok && r.version === 1 && calls.length === 1 && calls[0].u === "/drive/file/z/version/" && /base_version=0/.test(calls[0].body) && !calls.some((c) => c.u.endsWith("new/")));
  r = await D.addVersion(mk({}, () => json(409, { ok: false, error: "This file changed" })), "z", 0, file("v.txt", "", [1]));
  ok("conflict: a stale base is reported and is not marked resumable", !r.ok && r.stage === "version" && r.resumable === false && /This file changed/.test(r.message));
  // invalid type claim retried once; other 400s are not retried blindly
  let seen = []; r = await D.addFile(mk({}, (u, init) => { seen.push(init.body); return u.endsWith("new/") ? (seen.length === 1 ? json(400, { ok: false, error: "That file type is not valid." }) : json(201, { ok: true, file: { id: "q" } })) : json(200, { ok: true, file: { id: "q", version: 1, size: 1 } }); }), file("t.x", "bad type", [1]), "");
  ok("type claim: a rejected claim is retried once with none", r.ok && /content_type=bad\+type/.test(seen[0]) && /content_type=(&|$)/.test(seen[1]));
  seen = []; r = await D.addFile(mk({}, (u, init) => { seen.push(1); return json(400, { ok: false, error: "That name is already used here." }); }), file("dup.x", "text/plain", [1]), "");
  ok("duplicate name: not retried, said plainly, still only on this device", !r.ok && seen.length === 1 && /already used/.test(r.message) && /not in Drive/.test(r.message));
  // cheap presence badge, and a closed handle is reopened
  const hs = new D.Store("h", fakeIdb({})); await hs.save(b);
  ok("has: presence without reading bytes", (await hs.has(h)) === true && (await hs.has("0".repeat(64))) === false);
  hs.db.onversionchange(); ok("versionchange: the handle is closed and dropped", hs.db === null && hs.idb.dbo.closed === true);
  ok("versionchange: the next call opens a fresh handle and still finds the copy", (await hs.has(h)) === true && hs.db !== null);
  // namespaces
  const A = new D.Store("salow-drive-1", fakeIdb({})), B = new D.Store("salow-drive-2", fakeIdb({})); await A.save(b);
  ok("namespace: a copy saved under one name is not present under another", (await A.has(h)) === true && (await B.has(h)) === false);
  // reattach: local only, hash must equal the row's current checksum; never talks to Drive
  calls = []; const rs = new D.Store("ra", fakeIdb({})); const rdep = { store: rs, fetch: (u) => { calls.push(u); return json(200, {}); }, csrf: "t", urls };
  r = await D.reattach(rdep, h, file("anything.bin", "", [1, 2, 3]));
  ok("reattach: matching bytes are stored and read back with no request to Drive", r.ok && r.how === "added" && calls.length === 0 && (await rs.state(h)).state === "on-device");
  rs.idb.store.set(h, { sha256: h, size: 3, bytes: Uint8Array.from([1, 2, 4]).buffer });
  r = await D.reattach(rdep, h, file("a", "", [1, 2, 3]));
  ok("reattach: a damaged copy is repaired the same way, still no request", r.ok && r.how === "repaired" && calls.length === 0 && (await rs.state(h)).state === "on-device");
  const before = rs.idb.store.size; r = await D.reattach(rdep, h, file("other", "", [9, 9, 9]));
  ok("reattach: different bytes are refused before anything is stored", !r.ok && r.stage === "mismatch" && rs.idb.store.size === before && calls.length === 0);
  // fallback hash with crypto.subtle absent: run the real files in a context that has no crypto and no require
  {
    const vm = require("vm"), fs = require("fs"), crypto = require("crypto");
    const box = { Uint8Array, ArrayBuffer, Promise, URLSearchParams, Object, Array, String, Math, Error, setTimeout }; box.self = box; vm.createContext(box);
    vm.runInContext(fs.readFileSync(__dirname + "/../../static/slides/images.js", "utf8"), box);
    vm.runInContext(fs.readFileSync(__dirname + "/../../static/drive/device.js", "utf8"), box);
    const F = box.SalowDriveDevice, vals = [[], [1, 2, 3], new Array(55).fill(7), new Array(56).fill(8), new Array(1000).fill(3)];
    const want = (a) => crypto.createHash("sha256").update(Buffer.from(a)).digest("hex");
    let all = true;
    for (const v of vals) { const u8 = Uint8Array.from(v); const a = await F.sha256(u8), b = await F.sha256(u8.buffer.slice(0)); all = all && a === want(v) && b === want(v) && typeof a === "string"; }
    ok("fallback: with no crypto.subtle and no require, ArrayBuffer and Uint8Array inputs of 0, 3, 55, 56 and 1000 bytes give node's digest as a string", all && box.crypto === undefined);
    const fs2 = new F.Store("fb", fakeIdb({})); const sv = await fs2.save(Uint8Array.from([1, 2, 3]));
    ok("fallback: a save and read back works end to end on the fallback hash", sv.sha256 === want([1, 2, 3]) && (await fs2.state(sv.sha256)).state === "on-device");
  }
  // open: attachment only, revoked afterwards, name sanitised
  let dl = [], blobs = [], revoked = [], laters = [];
  const odep = (st) => ({ store: st, Blob: function (parts, o) { blobs.push(o); }, URL: { createObjectURL: () => "blob:x", revokeObjectURL: (u) => revoked.push(u) }, download: (u, name) => dl.push([u, name]), later: (f) => laters.push(f) });
  r = await D.openLocal(odep(A), "x\u202Egpj.exe\n/../a.html", h);
  ok("open: octet-stream attachment, safe name", r.ok && blobs[0].type === "application/octet-stream" && dl.length === 1 && !/[\u202e\n\/]/.test(dl[0][1]) && D.safeName("\u0085\u200b\u2066.\u0000") === "_._");
  ok("open: the object URL is revoked after the download", revoked.length === 0 && laters.length === 1 && (laters[0](), revoked.join() === "blob:x"));
  r = await D.openLocal(odep(B), "a", h);
  ok("open: a file that is not on this device says so", !r.ok && r.state === "not-on-device" && dl.length === 1);
  s1.idb.store.set(h, { sha256: h, size: 3, bytes: Uint8Array.from([1, 2, 4]).buffer });
  r = await D.openLocal(odep(s1), "a", h);
  ok("open: a damaged copy is reported and not downloaded", !r.ok && r.state === "damaged" && dl.length === 1);
  ok("name: control, path and long names are bounded", D.safeName("a/b\\c") === "a_b_c" && D.safeName("") === "file" && D.safeName("x".repeat(500)).length === 120 && D.safeName("..hidden") === "hidden");
  console.log(n - bad + "/" + n + " passed"); process.exit(bad ? 1 : 0);
})();
