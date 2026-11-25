/* Pure sheet logic with no DOM, so node can test it: bounded edit history, save-status
 * handling, import cleaning, chart points and keyboard movement. grid.js wires it to the page. */
(function (root) {
  "use strict";
  var has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  function copy(c) { var o = Object.create(null); Object.keys(c).forEach(function (k) { o[k] = c[k]; }); return o; }
  function same(a, b) { var ka = Object.keys(a), kb = Object.keys(b); return ka.length === kb.length && ka.every(function (k) { return b[k] === a[k]; }); }

  function clean(obj, parseAddr, maxCell) {
    var n = Object.create(null), skipped = 0, k;
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    for (k in obj) { if (!has(obj, k)) continue; if (typeof obj[k] === "string" && obj[k].length <= maxCell && parseAddr(k)) n[k] = obj[k]; else skipped++; }
    return { cells: n, count: Object.keys(n).length, skipped: skipped };
  }

  // store: { save(cells) -> true | false }. The failure text stays visible until a save works.
  function History(initial, store, limit) {
    var h = this; h.cells = initial; h.past = []; h.future = []; h.limit = limit || 100; h.store = store; h.saveFailed = false; h.note = "";
    h.persist = function (note) {
      var ok = false; try { ok = h.store.save(h.cells) !== false; } catch (e) { ok = false; }
      h.saveFailed = !ok;
      h.note = ok ? (note || "Saved on this device.") : "This browser would not store the sheet, so these changes are on screen only and will be lost on reload." + (note ? " " + note : "");
      return ok;
    };
    h.change = function (next, note) {
      if (same(h.cells, next)) return false;
      h.past.push(copy(h.cells)); if (h.past.length > h.limit) h.past.shift(); h.future = [];
      h.cells = next; h.persist(note); return true;
    };
    h.undo = function () { if (!h.past.length) return false; h.future.push(copy(h.cells)); h.cells = h.past.pop(); h.persist("Undone."); return true; };
    h.redo = function () { if (!h.future.length) return false; h.past.push(copy(h.cells)); h.cells = h.future.pop(); h.persist("Redone."); return true; };
  }

  // values: array of {a, v}; returns bars relative to a zero axis. A value of 0 has no bar.
  function chartPoints(vals) {
    var pts = [], skipped = 0;
    vals.forEach(function (x) { if (typeof x.v === "number" && isFinite(x.v)) pts.push(x); else skipped++; });
    if (!pts.length) return null;
    var lo = Math.min(0, Math.min.apply(null, pts.map(function (q) { return q.v; }))), hi = Math.max(0, Math.max.apply(null, pts.map(function (q) { return q.v; })));
    if (hi === lo) hi = lo + 1;
    return { pts: pts, lo: lo, hi: hi, skipped: skipped, bars: pts.map(function (q) { return { a: q.a, v: q.v, bar: q.v !== 0 }; }) };
  }

  // Enter during IME composition confirms the text and must not move the cursor.
  function isComposing(e) { return !!(e && (e.isComposing || e.keyCode === 229)); }

  // key -> [dcol, drow] or null. atStart/atEnd say whether the caret is at the text edge.
  function keyMove(key, shift, atStart, atEnd) {
    if (key === "Enter") return [0, shift ? -1 : 1];
    if (key === "ArrowDown") return [0, 1];
    if (key === "ArrowUp") return [0, -1];
    if (key === "ArrowLeft" && atStart) return [-1, 0];
    if (key === "ArrowRight" && atEnd) return [1, 0];
    return null;
  }

  // CSV of what the sheet shows (formula results, not formulas), over the box that holds every filled cell. get(addr) returns
  // a number, boolean, string or error. Text that starts with = + - @ (after any leading blanks or invisible characters) or with tab or CR gets a leading
  // apostrophe. This is a mitigation, not a guarantee for every spreadsheet program; numbers are written as they are.
  // Leading spaces, control characters, zero-width and no-break characters are skipped when looking for the trigger
  // character, because some programs ignore them before reading a formula. Fullwidth look-alikes are included.
  var SKIP = /^[\s\u0000-\u001f\u007f-\u009f\u00a0\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff]*/;
  var RISKY = { test: function (t) { if (/^[\t\r]/.test(t)) return true; var r = t.replace(SKIP, ""); return /^[=+\-@\uff1d\uff0b\uff0d\uff20]/.test(r); } };
  function csvField(v, isErr) {
    var t;
    if (isErr(v)) t = String(v.code);
    else if (typeof v === "boolean") t = v ? "TRUE" : "FALSE";
    else if (typeof v === "number") t = Object.is(v, -0) ? "0" : String(v);
    else { t = String(v); if (typeof v === "string" && RISKY.test(t)) t = "'" + t; }
    return /[",\r\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
  }
  // Addresses here are single letters A to Z (the engine rejects AA and beyond), so a wider cell is skipped, not mis-addressed.
  var CSV_COLS = 26, CSV_ROWS = 1000;
  function toCsv(cells, parseAddr, get, isErr) {
    var maxC = 0, maxR = 0, any = false, guarded = 0;
    Object.keys(cells).forEach(function (k) { var p = parseAddr(k); if (!p || p.col > CSV_COLS || p.row > CSV_ROWS) return; any = true; if (p.col > maxC) maxC = p.col; if (p.row > maxR) maxR = p.row; });
    if (!any) return null;
    var lines = [];
    for (var r = 1; r <= maxR; r++) {
      var row = [];
      for (var c = 1; c <= maxC; c++) {
        var a = String.fromCharCode(64 + c) + r, v = has(cells, a) ? get(a) : "";
        var f = csvField(v, isErr); if (typeof v === "string" && RISKY.test(v)) guarded++;
        row.push(f);
      }
      lines.push(row.join(","));
    }
    return { text: lines.join("\r\n") + "\r\n", rows: maxR, cols: maxC, guarded: guarded };
  }

  // ---- Sort and filter ----
  function colName(c) { return String.fromCharCode(64 + c); }
  function toNum(t) { if (typeof t !== "string") return null; var x = t.trim(); if (x === "" || !/^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(x)) return null; var n = Number(x); return isFinite(n) ? n : null; }
  // Sort whole rows of a range by one column of what was typed. Rows move together. Formulas are refused because moving
  // them would change what their references mean. Numbers come before text, text is compared without case, blanks go last
  // in either direction, equal keys keep their order. rg = {c1,c2,r1,r2}; keyCol is an absolute column number.
  function sortRange(cells, parseAddr, rg, keyCol, desc, header) {
    if (!rg || keyCol < rg.c1 || keyCol > rg.c2) return { error: "The sort column must be inside the range." };
    var first = header ? rg.r1 + 1 : rg.r1;
    if (first >= rg.r2) return { error: "There are fewer than two rows to sort." };
    var rows = [], r, c, formulas = 0;
    for (r = rg.r1; r <= rg.r2; r++) for (c = rg.c1; c <= rg.c2; c++) { var rv = cells[colName(c) + r]; if (typeof rv === "string" && rv.charAt(0) === "=") formulas++; }
    if (formulas) return { error: "The range holds " + formulas + " formula" + (formulas === 1 ? "" : "s") + ". Sorting would change what they refer to, so nothing was sorted. Sort a range of typed values." };
    for (r = first; r <= rg.r2; r++) {
      var vals = {}, kr = cells[colName(keyCol) + r];
      for (c = rg.c1; c <= rg.c2; c++) { var a = colName(c) + r; if (has(cells, a)) vals[c] = cells[a]; }
      var n = toNum(kr), blank = kr === undefined || String(kr).trim() === "";
      rows.push({ i: r - first, vals: vals, kind: blank ? 2 : n !== null ? 0 : 1, num: n, txt: blank ? "" : String(kr).toLowerCase() });
    }
    function cmp(x, y) {
      if (x.kind === 2 || y.kind === 2) return x.kind === y.kind ? x.i - y.i : x.kind === 2 ? 1 : -1;
      var d = x.kind !== y.kind ? x.kind - y.kind : x.kind === 0 ? x.num - y.num : (x.txt < y.txt ? -1 : x.txt > y.txt ? 1 : 0);
      if (desc) d = -d;
      return d !== 0 ? d : x.i - y.i;
    }
    var sorted = rows.slice().sort(cmp), next = copy(cells), moved = 0;
    for (r = first; r <= rg.r2; r++) for (c = rg.c1; c <= rg.c2; c++) delete next[colName(c) + r];
    sorted.forEach(function (row, k) {
      if (row.i !== k) moved++;
      Object.keys(row.vals).forEach(function (cc) { next[colName(Number(cc)) + (first + k)] = row.vals[cc]; });
    });
    return { cells: next, moved: moved, rows: rows.length };
  }
  // Which rows a filter shows. get(addr) gives the displayed value. The header row, if any, is always shown.
  // ops: contains, equals, gt, lt, nonblank, blank. gt and lt compare numbers only, so text never matches them.
  function filterRows(get, isErr, r1, r2, col, op, val, header) {
    var needsVal = op === "contains" || op === "equals" || op === "gt" || op === "lt";
    if (["contains", "equals", "gt", "lt", "nonblank", "blank"].indexOf(op) < 0) return { error: "Choose a filter." };
    if (needsVal && String(val).trim() === "") return { error: "Enter a value to filter by." };
    var want = null; if (op === "gt" || op === "lt") { want = toNum(String(val)); if (want === null) return { error: "Enter a number to compare with." }; }
    var vis = {}, shown = 0, total = 0, r;
    for (r = r1; r <= r2; r++) {
      if (header && r === r1) { vis[r] = true; continue; }
      total++;
      var v = get(colName(col) + r), t = isErr(v) ? String(v.code) : typeof v === "boolean" ? (v ? "TRUE" : "FALSE") : String(v), lc = t.toLowerCase(), q = String(val).trim().toLowerCase(), ok = false;
      if (op === "contains") ok = lc.indexOf(q) >= 0;
      else if (op === "equals") { var a = toNum(t), b = toNum(String(val)); ok = a !== null && b !== null ? a === b : lc === q; }
      else if (op === "gt" || op === "lt") { var n = typeof v === "number" ? v : toNum(t); ok = n !== null && (op === "gt" ? n > want : n < want); }
      else if (op === "nonblank") ok = t.trim() !== "";
      else ok = t.trim() === "";
      if (ok) { vis[r] = true; shown++; }
    }
    return { vis: vis, shown: shown, total: total };
  }

  // ---- Tabs: several sheets in one file. meta = { v: 1, active: id, tabs: [{ id, name }] }. Each tab keeps its own cells. ----
  var MAX_TABS = 10, NAME_MAX = 30, TAB_ID = /^t[0-9]{1,6}$/;
  function cleanName(n) { if (typeof n !== "string") return ""; var t = n.replace(/\s+/g, " ").trim(); return t.length <= NAME_MAX ? t : Array.from(t).slice(0, NAME_MAX).join("").trim(); }
  function defaultTabs() { return { v: 1, active: "t0", tabs: [{ id: "t0", name: "Sheet 1" }] }; }
  function cloneMeta(m) { return { v: 1, active: m.active, tabs: m.tabs.map(function (t) { return { id: t.id, name: t.name }; }) }; }
  // raw is the stored string or null. A stored value that cannot be read is reported, never treated as empty.
  function loadTabs(raw) {
    if (raw === null || raw === undefined || raw === "") return { meta: defaultTabs(), unreadable: false };
    var m = null; try { m = JSON.parse(raw); } catch (e) { m = null; }
    var bad = { meta: defaultTabs(), unreadable: true };
    if (!m || typeof m !== "object" || m.v !== 1 || !Array.isArray(m.tabs) || !m.tabs.length || m.tabs.length > MAX_TABS) return bad;
    var seen = Object.create(null), names = Object.create(null), out = [];
    for (var i = 0; i < m.tabs.length; i++) {
      var t = m.tabs[i], nm = t && cleanName(t.name);
      if (!t || typeof t.id !== "string" || !TAB_ID.test(t.id) || seen[t.id] || !nm || names[nm.toLowerCase()]) return bad;
      seen[t.id] = true; names[nm.toLowerCase()] = true; out.push({ id: t.id, name: nm });
    }
    return { meta: { v: 1, active: seen[m.active] ? m.active : out[0].id, tabs: out }, unreadable: false };
  }
  function nameTaken(meta, name, exceptId) { var l = name.toLowerCase(); return meta.tabs.some(function (t) { return t.id !== exceptId && t.name.toLowerCase() === l; }); }
  function addTab(meta) {
    if (meta.tabs.length >= MAX_TABS) return null;
    var n = meta.tabs.reduce(function (mx, t) { return Math.max(mx, Number(t.id.slice(1))); }, 0) + 1, k = meta.tabs.length + 1, name;
    do { name = "Sheet " + k++; } while (nameTaken(meta, name));
    var next = cloneMeta(meta); next.tabs.push({ id: "t" + n, name: name }); next.active = "t" + n; return next;
  }
  function renameTab(meta, id, name) {
    var nm = cleanName(name), i = meta.tabs.findIndex(function (t) { return t.id === id; });
    if (i < 0) return { error: "That tab does not exist." };
    if (!nm) return { error: "Enter a name for the tab." };
    if (nameTaken(meta, nm, id)) return { error: "Another tab already has that name." };
    var next = cloneMeta(meta); next.tabs[i].name = nm; return { meta: next, name: nm, cut: typeof name === "string" && name.replace(/\s+/g, " ").trim().length > NAME_MAX };
  }
  function deleteTab(meta, id) {
    var i = meta.tabs.findIndex(function (t) { return t.id === id; });
    if (i < 0) return { error: "That tab does not exist." };
    if (meta.tabs.length <= 1) return { error: "A file keeps at least one tab." };
    var next = cloneMeta(meta); next.tabs.splice(i, 1);
    if (next.active === id) next.active = next.tabs[Math.min(i, next.tabs.length - 1)].id;
    return { meta: next, removed: meta.tabs[i] };
  }
  function setActive(meta, id) { if (!meta.tabs.some(function (t) { return t.id === id; }) || meta.active === id) return null; var n = cloneMeta(meta); n.active = id; return n; }

  var api = { loadTabs: loadTabs, addTab: addTab, renameTab: renameTab, deleteTab: deleteTab, setActive: setActive, cleanName: cleanName, MAX_TABS: MAX_TABS, sortRange: sortRange, filterRows: filterRows, toNum: toNum, toCsv: toCsv, clean: clean, History: History, chartPoints: chartPoints, isComposing: isComposing, keyMove: keyMove, copy: copy, same: same };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSheetModel = api;
})(typeof self !== "undefined" ? self : this);
