// Drive device client: store, read back, honest states, safe open. Uses a small in-memory IndexedDB stand-in (not a browser).
const D = require("../../static/drive/device.js");
let n = 0, bad = 0;
const ok = (d, c) => { n++; if (!c) { bad++; console.log("FAIL", d); } };
function fakeIdb(opts) {
  opts = opts || {}; const data = {};
  const req = (fn) => { const r = { result: undefined }; Promise.resolve().then(() => { r.result = fn(); r.onsuccess && r.onsuccess(); }); return r; };
  return {
    data,
    open(name) {
      const r = {}; Promise.resolve().then(() => {
        r.result = { createObjectStore() { data.s = data.s || new Map(); }, transaction() {
          const t = { error: null }; let pending = 0;
          const done = () => Promise.resolve().then(() => Promise.resolve()).then(() => { if (t.error) t.onabort && t.onabort(); else t.oncomplete && t.oncomplete(); });
          t.objectStore = () => ({
            get: (k) => { const q = { result: data.s.get(k) }; done(); return q; },
            add: (rec) => { const q = { result: undefined }; if (opts.quota) t.error = { name: "QuotaExceededError" }; else if (opts.corrupt) data.s.set(rec.sha256, Object.assign({}, rec, { bytes: new Uint8Array([9, 9, 9]).buffer, size: 3 })); else data.s.set(rec.sha256, rec); done(); return q; },
          }); return t; } };
        r.onupgradeneeded && r.onupgradeneeded(); r.onsuccess && r.onsuccess();
      }); return r;
    },
  };
}
const file = (name, type, bytes) => ({ name, type, arrayBuffer: () => Promise.resolve(Uint8Array.from(bytes).buffer) });
const json = (status, body) => Promise.resolve({ status, redirected: false, headers: { get: () => "application/json" }, json: () => Promise.resolve(body) });
const html = () => Promise.resolve({ status: 200, redirected: true, headers: { get: () => "text/html" }, json: () => Promise.reject(new Error("no")) });
const urls = { fileNew: "/drive/file/new/", version: (id) => "/drive/file/" + id + "/version/" };
const mk = (o, fetchFn) => ({ store: new D.Store("t", fakeIdb(o)), fetch: fetchFn, csrf: "tok", urls });
(async () => {
  // happy path: bytes first, then the record, then the version; the order and the fields are what the server sees
  let calls = [], dep = mk({}, (u, init) => { calls.push({ u, body: init.body, tok: init.headers["X-CSRFToken"] }); return u.endsWith("new/") ? json(201, { ok: true, file: { id: "f1" } }) : json(200, { ok: true, file: { id: "f1", version: 1, size: 3 } }); });
  let r = await D.addFile(dep, file("a.pdf", "application/pdf", [1, 2, 3]), "fold");
  const h = await D.sha256(Uint8Array.from([1, 2, 3]));
  ok("add: ok, on this device, version 1, hash is the SHA-256 of the bytes", r.ok && r.onDevice && r.version === 1 && r.sha256 === h && (await dep.store.state(h)).state === "on-device");
  ok("add: only name, type, folder, size and hash are sent, with the CSRF token, never the bytes", calls.length === 2 && calls[0].tok === "tok" && /name=a.pdf/.test(calls[0].body) && /folder=fold/.test(calls[0].body) && /base_version=0/.test(calls[1].body) && /size=3/.test(calls[1].body) && calls[1].body.includes("sha256=" + h) && !/%01%02%03|\u0001/.test(calls[0].body + calls[1].body));
  // a real empty file is a real version of size 0
  calls = []; r = await D.addFile(mk({}, (u) => u.endsWith("new/") ? json(201, { ok: true, file: { id: "e" } }) : json(200, { ok: true, file: { id: "e", version: 1, size: 0 } })), file("empty.txt", "text/plain", []), "");
  ok("empty: a zero byte file is stored and read back and recorded as version 1 size 0", r.ok && r.size === 0 && r.version === 1 && r.sha256 === "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  // quota: nothing is sent to Drive, the message says nothing was saved
  calls = []; r = await D.addFile(mk({ quota: true }, (u) => { calls.push(u); return json(201, {}); }), file("big.bin", "", [1, 2]), "");
  ok("quota: not stored, nothing sent to Drive, no on-device claim", !r.ok && !r.onDevice && /out of room/.test(r.message) && calls.length === 0);
  // a store whose read back does not match is refused before Drive hears anything
  calls = []; r = await D.addFile(mk({ corrupt: true }, (u) => { calls.push(u); return json(201, {}); }), file("c.bin", "", [5, 6, 7]), "");
  ok("readback: stored bytes that do not hash to the name are refused, Drive not called", !r.ok && r.stage === "readback" && !r.onDevice && calls.length === 0);
  // login redirect and network failure: on this device, not in Drive, said plainly
  r = await D.addFile(mk({}, () => html()), file("s.txt", "", [1]), "");
  ok("signin: HTML after a redirect is read as a sign-in need, with the file kept on this device and not in Drive", !r.ok && r.stage === "signin" && r.onDevice && /not in Drive/.test(r.message));
  r = await D.addFile(mk({}, () => Promise.reject(new Error("x"))), file("n.txt", "", [1]), "");
  ok("network: Drive unreachable is reported, still on this device only", !r.ok && r.stage === "network" && r.onDevice && /not in Drive/.test(r.message));
  // record created but the version call fails: the message says the name exists without a saved version
  r = await D.addFile(mk({}, (u) => u.endsWith("new/") ? json(201, { ok: true, file: { id: "z" } }) : json(409, { ok: false, error: "This file changed" })), file("v.txt", "", [1]), "");
  ok("version failure: Drive has the name but no saved version, and the file id is returned", !r.ok && r.stage === "version" && r.fileId === "z" && /no saved version/.test(r.message));
  // an invalid claimed type is retried once with no type
  let seen = []; r = await D.addFile(mk({}, (u, init) => { seen.push(init.body); return u.endsWith("new/") ? (seen.length === 1 ? json(400, { ok: false, error: "That file type is not valid." }) : json(201, { ok: true, file: { id: "q" } })) : json(200, { ok: true, file: { id: "q", version: 1, size: 1 } }); }), file("t.x", "bad type", [1]), "");
  ok("type claim: a rejected claim is retried once with none and the file is still added", r.ok && /content_type=bad\+type/.test(seen[0]) && /content_type=(&|$)/.test(seen[1]));
  // open: verified copy only, always an opaque attachment, name sanitised, damaged copies refused
  let dl = [], blobs = [];
  const odep = (store) => ({ store, Blob: function (parts, o) { blobs.push(o); this.parts = parts; }, URL: { createObjectURL: () => "blob:x" }, download: (u, name) => dl.push([u, name]) });
  const good = new D.Store("o", fakeIdb({})); await good.put(h, Uint8Array.from([1, 2, 3]));
  r = await D.openLocal(odep(good), "x\u202Egpj.exe\n/../a.html", h);
  ok("open: opaque octet-stream attachment, name has no path, bidi or control characters", r.ok && blobs[0].type === "application/octet-stream" && dl.length === 1 && dl[0][0] === "blob:x" && !/[\u202e\n\/]/.test(dl[0][1]) && D.safeName("\u0085\u200b\u2066.\u0000") === "_._");
  r = await D.openLocal(odep(new D.Store("o2", fakeIdb({}))), "a", h);
  ok("open: a file that is not on this device says so and downloads nothing", !r.ok && r.state === "not-on-device" && dl.length === 1);
  const bad2 = new D.Store("o3", fakeIdb({})); await bad2.open(); const badIdb = bad2.idb; badIdb.data.s.set(h, { sha256: h, size: 3, bytes: Uint8Array.from([1, 2, 4]).buffer });
  r = await D.openLocal(odep(bad2), "a", h);
  ok("open: stored bytes whose hash differs are reported damaged and not downloaded", !r.ok && r.state === "damaged" && dl.length === 1);
  const store2 = new D.Store("p", fakeIdb({})); await store2.put(h, Uint8Array.from([1, 2, 3]));
  ok("store: put never replaces an existing copy of a hash", (await store2.put(h, Uint8Array.from([7, 7, 7]))) === false && (await store2.state(h)).state === "on-device");
  ok("name: control, path and long names are bounded, empty becomes file", D.safeName("a/b\\c") === "a_b_c" && D.safeName("") === "file" && D.safeName("x".repeat(500)).length === 120 && D.safeName("..hidden") === "hidden");
  console.log(n - bad + "/" + n + " passed"); process.exit(bad ? 1 : 0);
})();
