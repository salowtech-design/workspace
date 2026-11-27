// Writing a structural change to four storage keys: failure injection against a fake storage.
const M = require("../../static/sheets/model.js");
let fails = 0, n = 0;
const ok = (d, c) => { n++; if (!c) { fails++; console.log("FAIL", d); } };
const KEYS = ["c", "r", "g", "f"], BAK = "c.structbak";
const OLD = ["cells0", "rules0", null, "fmt0"], NEW = ["cells1", "rules1", "cf1", null];
// A storage with hooks. hook(op, key, value, store) runs before each operation and may throw or change the store; readLie replaces a read's result.
function mkStore(init, hook, readLie) {
  const d = new Map(Object.entries(init || {})), log = [];
  const s = { d, log,
    getItem(k) { log.push(["get", k]); if (hook) hook("get", k, undefined, s); const lie = readLie && readLie(k, s); if (lie !== undefined) return lie.v; return d.has(k) ? d.get(k) : null; },
    setItem(k, v) { log.push(["set", k]); if (hook) hook("set", k, v, s); d.set(k, v); },
    removeItem(k) { log.push(["remove", k]); if (hook) hook("remove", k, undefined, s); d.delete(k); } };
  return s;
}
const seed = () => { const o = {}; KEYS.forEach((k, i) => { if (OLD[i] !== null) o[k] = OLD[i]; }); return o; };
const state = (s) => KEYS.map((k) => (s.d.has(k) ? s.d.get(k) : null));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
let s, r;
// success
s = mkStore(seed()); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("success: all four keys are as intended, the recovery copy is gone", r.status === "done" && same(state(s), NEW) && !s.d.has(BAK) && !r.bakLeft);
ok("success: the recovery copy was written before the first key", s.log.findIndex((x) => x[0] === "set" && x[1] === BAK) < s.log.findIndex((x) => x[0] === "set" && x[1] === "c"));
s = mkStore(seed()); r = M.structApply(s, KEYS, BAK, OLD, OLD); ok("no change: refused as no-change, nothing written", r.status === "refused" && r.reason === "no-change" && s.log.every((x) => x[0] === "get"));
// refusals before any write
s = mkStore(seed()); s.d.set("r", "someone else"); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("stale key: refused, nothing written, names the key", r.status === "refused" && r.reason === "stale" && r.key === "r" && s.log.every((x) => x[0] === "get") && !s.d.has(BAK));
s = mkStore(Object.assign(seed(), { [BAK]: "an older recovery copy" })); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("an existing recovery copy is never overwritten: refused, bytes intact", r.reason === "recovery-exists" && s.d.get(BAK) === "an older recovery copy" && same(state(s), OLD));
s = mkStore(seed(), (op, k) => { if (op === "get" && k === "g") throw new Error("denied"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("unreadable key: refused", r.status === "refused" && r.reason === "storage" && !s.d.has(BAK));
s = mkStore(seed(), (op, k) => { if (op === "set" && k === BAK) throw new Error("quota"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("recovery copy cannot be stored: refused, no key touched", r.reason === "no-recovery-copy" && same(state(s), OLD));
s = mkStore(seed(), (op, k, v, st) => { if (op === "set" && k === BAK) { st.d.set(BAK, "garbled"); throw new Error("half"); } if (op === "remove" && k === BAK) throw new Error("stuck"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("recovery copy half written and cannot be removed: refused, reported as kept", r.reason === "recovery-stuck" && r.bakKept && same(state(s), OLD));
// failure on the 1st, 2nd, 3rd, 4th changed write
[["c", 0], ["r", 1], ["g", 2], ["f", 3]].forEach(([key, idx]) => {
  s = mkStore(seed(), (op, k) => { if ((op === "set" || op === "remove") && k === key) throw new Error("quota"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
  ok("write " + (idx + 1) + " throws: every key back as before, copy removed, reported restored", r.status === "failed-restored" && same(state(s), OLD) && !s.d.has(BAK));
});
// write succeeds, read back differs (the failed key is also rolled back)
s = mkStore(seed(), null, (k, st) => (k === "r" && st.d.get("r") === "rules1" && !st.__lied ? (st.__lied = 1, { v: "different" }) : undefined)); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("read back differs on the 2nd key: rolled back including that key", r.status === "failed-restored" && same(state(s), OLD));
// write lands but the read back throws: the attempted key must be in the rollback
s = mkStore(seed(), (op, k, v, st) => { if (op === "get" && k === "g" && st.d.get("g") === "cf1" && !st.__t) { st.__t = 1; throw new Error("read"); } }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("write landed but the read back threw: that key is restored too", r.status === "failed-restored" && same(state(s), OLD));
// restore itself fails
s = mkStore(seed(), (op, k, v) => { if (op === "set" && k === "c" && v === "cells0") throw new Error("restore fails"); if (op === "set" && k === "g") throw new Error("quota"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("restore fails: unresolved, names the key, the recovery copy is kept", r.status === "failed-unresolved" && r.bad.includes("c") && r.bakKept && s.d.has(BAK) && s.d.get("c") === "cells1");
// third party changes a key during the writes: not overwritten on rollback
s = mkStore(seed(), (op, k, v, st) => { if (op === "set" && k === "g") { st.d.set("c", "third party"); throw new Error("quota"); } }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("another window wrote a key meanwhile: it is not overwritten, state reported unresolved", r.status === "failed-unresolved" && r.bad.includes("c") && s.d.get("c") === "third party" && s.d.has(BAK));
// third party changes a key after all writes, before the final read
s = mkStore(seed(), (op, k, v, st) => { if (op === "set" && k === "r") st.__x = 1; if (op === "get" && k === "c" && st.__x && st.d.get("g") === "cf1") st.d.set("c", "late third party"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("a key changes between the writes and the final check: unresolved, that key named and not overwritten, copy kept", r.status === "failed-unresolved" && r.bad.includes("c") && r.bakKept && s.d.get("c") === "late third party" && s.d.has(BAK));
// removal of the recovery copy fails after success
s = mkStore(seed(), (op, k) => { if (op === "remove" && k === BAK) throw new Error("stuck"); }); r = M.structApply(s, KEYS, BAK, OLD, NEW);
ok("success but the recovery copy cannot be removed: done with bakLeft true", r.status === "done" && r.bakLeft === true && same(state(s), NEW));
// ---- recover
ok("recovery state none / found", M.structRecovery(mkStore(seed()), BAK, KEYS) === "none");
const fault = { on: true };
s = mkStore(seed(), (op, k, v) => { if (!fault.on) return; if (op === "set" && k === "c" && v === "cells0") throw new Error("restore fails"); if (op === "set" && k === "g") throw new Error("quota"); }); M.structApply(s, KEYS, BAK, OLD, NEW);
ok("an unresolved failure leaves a recovery copy that is found", M.structRecovery(s, BAK, KEYS) === "found" && s.d.get("c") === "cells1");
const heal = s; fault.on = false; r = M.structRecover(heal, BAK, KEYS);
ok("the same storage once the fault clears: recover restores all four and removes the copy", r.status === "restored" && same(state(heal), OLD) && !heal.d.has(BAK));
// the page-level guard needs the same facts
s = mkStore(seed(), (op, k, v) => { if (!fault.on) return; if (op === "set" && k === "c" && v === "cells0") throw new Error("restore fails"); if (op === "set" && k === "g") throw new Error("quota"); }); fault.on = true; M.structApply(s, KEYS, BAK, OLD, NEW); fault.on = false;
const odd = mkStore(Object.fromEntries(s.d)); odd.d.set("r", "third party text"); r = M.structRecover(odd, BAK, KEYS);
ok("recover with a third party value: conflict, nothing written, copy kept", r.status === "conflict" && r.keys.includes("r") && odd.d.get("r") === "third party text" && odd.d.has(BAK) && odd.d.get("c") === "cells1");
r = M.structRecover(odd, BAK, KEYS, true);
ok("recover with force overwrites and restores", r.status === "restored" && same(state(odd), OLD));
const bad = mkStore({ [BAK]: "{not json" }); ok("unreadable recovery copy is reported, kept", M.structRecover(bad, BAK, KEYS).status === "unreadable" && bad.d.has(BAK) && M.structRecovery(bad, BAK, KEYS) === "unreadable");
const bad2 = mkStore({ [BAK]: JSON.stringify({ v: 1, keys: ["a", "b", "c"], raw: [], next: [] }) }); ok("wrong-shaped recovery copy is unreadable", M.structRecover(bad2, BAK, KEYS).status === "unreadable");
const bad3 = mkStore({ c: "cells1", r: "rules1", g: "cf1", [BAK]: JSON.stringify({ v: 1, keys: KEYS, raw: OLD, next: NEW }) }, (op, k) => { if (op !== "get" && k === "g") throw new Error("quota"); });
r = M.structRecover(bad3, BAK, KEYS);
ok("recover when one restore write fails: unresolved, that key named, copy kept", r.status === "unresolved" && r.bad.includes("g") && bad3.d.has(BAK));
const inj = mkStore({ victim: "keep me", [BAK]: JSON.stringify({ v: 1, keys: ["c", "r", "g", "victim"], raw: ["a", "b", "c", "overwritten"], next: ["a", "b", "c", "d"] }) });
r = M.structRecover(inj, BAK, KEYS, true);
ok("a recovery copy naming other keys is not trusted: unreadable, no key written", r.status === "unreadable" && inj.d.get("victim") === "keep me" && M.structRecovery(inj, BAK, KEYS) === "unreadable");
// History: structural steps are recorded alongside, and the plain undo and redo refuse to cross them
const mk2 = (o) => Object.assign(Object.create(null), o);
const Hh = new M.History(mk2({ A1: "1" }), { save: () => true }, 100);
Hh.change(mk2({ A1: "2" })); Hh.recordExternal(mk2({ A2: "2" }), { before: ["b"], after: ["a"] }); Hh.change(mk2({ A2: "3" }));
ok("plain undo works on the ordinary step above a structural one", Hh.undo() && Hh.cells.A2 === "2");
ok("plain undo refuses to cross the structural step", !Hh.undo() && Hh.cells.A2 === "2" && Hh.topMeta(false).before[0] === "b");
Hh.undoExternal(); ok("undoExternal moves it back and keeps redo order", Hh.cells.A1 === "2" && !Hh.cells.A2 && Hh.future.length === 2 && Hh.topMeta(true) && Hh.topMeta(true).after[0] === "a");
ok("plain redo refuses to cross the structural step", !Hh.redo());
Hh.redoExternal(); ok("redoExternal restores it, then the ordinary redo works", Hh.cells.A2 === "2" && Hh.redo() && Hh.cells.A2 === "3");
Hh.change(mk2({ A9: "x" })); ok("a new ordinary edit clears redo and its markers", Hh.future.length === 0 && Hh.fm.length === 0 && Hh.pm.length === Hh.past.length);
console.log(n - fails + "/" + n + " structure storage rows passed");
process.exit(fails ? 1 : 0);
