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
    var h = this; h.cells = initial; h.past = []; h.future = []; h.pm = []; h.fm = []; h.limit = limit || 100; h.store = store; h.saveFailed = false; h.note = "";
    // pm and fm run alongside past and future: null for an ordinary step, or { before, after } (the four raw stored texts) for a structural one.
    h.persist = function (note) {
      var ok = false; try { ok = h.store.save(h.cells) !== false; } catch (e) { ok = false; }
      h.saveFailed = !ok;
      h.note = ok ? (note || "Saved on this device.") : "This browser would not store the sheet, so these changes are on screen only and will be lost on reload." + (note ? " " + note : "");
      return ok;
    };
    function push(meta) { h.past.push(copy(h.cells)); h.pm.push(meta); if (h.past.length > h.limit) { h.past.shift(); h.pm.shift(); } h.future = []; h.fm = []; }
    h.change = function (next, note) {
      if (same(h.cells, next)) return false;
      push(null); h.cells = next; h.persist(note); return true;
    };
    // A structural step was already written to storage by the caller (all four keys); this only records it.
    h.recordExternal = function (next, meta, note) { push(meta); h.cells = next; h.saveFailed = false; h.note = note || ""; };
    h.topMeta = function (redo) { var a = redo ? h.fm : h.pm; return a.length ? a[a.length - 1] : null; };
    h.undo = function () { if (!h.past.length || h.topMeta(false)) return false; h.future.push(copy(h.cells)); h.fm.push(null); h.cells = h.past.pop(); h.pm.pop(); h.persist("Undone."); return true; };
    h.redo = function () { if (!h.future.length || h.topMeta(true)) return false; h.past.push(copy(h.cells)); h.pm.push(null); h.cells = h.future.pop(); h.fm.pop(); h.persist("Redone."); return true; };
    // Undo or redo of a structural step, after the caller wrote the four keys: moves the cells exactly as undo and redo do, without saving them again.
    h.undoExternal = function () { var m = h.pm.pop(); h.future.push(copy(h.cells)); h.fm.push(m); h.cells = h.past.pop(); h.saveFailed = false; h.note = "Undone."; };
    h.redoExternal = function () { var m = h.fm.pop(); h.past.push(copy(h.cells)); h.pm.push(m); h.cells = h.future.pop(); h.saveFailed = false; h.note = "Redone."; };
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

  // Scatter: rows of two numbers (x then y). A row with a blank, text or error in either cell is skipped and counted. The axes fit the data (they do not
  // have to include zero); when every x or every y is the same the axis is widened by 1 so the points still draw. Null when no row has two numbers.
  function scatterPoints(rows) {
    var pts = [], skipped = 0, num = function (v) { return typeof v === "number" && isFinite(v); };
    rows.forEach(function (r) { if (num(r.x) && num(r.y)) pts.push({ a: r.a, x: r.x, y: r.y }); else skipped++; });
    if (!pts.length) return null;
    var xs = pts.map(function (q) { return q.x; }), ys = pts.map(function (q) { return q.y; });
    var xlo = Math.min.apply(null, xs), xhi = Math.max.apply(null, xs), ylo = Math.min.apply(null, ys), yhi = Math.max.apply(null, ys);
    if (xhi === xlo) xhi = xlo + 1; if (yhi === ylo) yhi = ylo + 1;
    return { pts: pts, xlo: xlo, xhi: xhi, ylo: ylo, yhi: yhi, skipped: skipped };
  }

  // Pie: only positive values make slices. Others are counted as left out. frac sums to 1.
  function pieSlices(pts) {
    var pos = pts.filter(function (q) { return q.v > 0; }), tot = pos.reduce(function (a, q) { return a + q.v; }, 0);
    if (!pos.length || !(tot > 0) || !isFinite(tot)) return null;
    return { slices: pos.map(function (q) { return { a: q.a, v: q.v, frac: q.v / tot }; }), left: pts.length - pos.length };
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
  // A half of an emoji (a lone surrogate, which LEFT, RIGHT and MID can leave) cannot be written as UTF-8. It is replaced by U+FFFD
  // here, on purpose and counted, so the file never depends on the browser's silent choice and the status can say so.
  function wellFormed(t) {
    var out = "", n = 0, i, c, d;
    for (i = 0; i < t.length; i++) {
      c = t.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff && i + 1 < t.length && (d = t.charCodeAt(i + 1)) >= 0xdc00 && d <= 0xdfff) { out += t.charAt(i) + t.charAt(i + 1); i++; }
      else if (c >= 0xd800 && c <= 0xdfff) { out += "\ufffd"; n++; }
      else out += t.charAt(i);
    }
    return { text: out, broken: n };
  }
  function csvField(v, isErr, tally) {
    var t;
    if (isErr(v)) t = String(v.code);
    else if (typeof v === "boolean") t = v ? "TRUE" : "FALSE";
    else if (typeof v === "number") t = Object.is(v, -0) ? "0" : String(v);
    else { t = String(v); var w = wellFormed(t); if (w.broken) { t = w.text; if (tally) tally.broken += w.broken; } if (typeof v === "string" && RISKY.test(t)) t = "'" + t; }
    return /[",\r\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
  }
  // Addresses here are single letters A to Z (the engine rejects AA and beyond), so a wider cell is skipped, not mis-addressed.
  var CSV_COLS = 26, CSV_ROWS = 1000;
  function toCsv(cells, parseAddr, get, isErr) {
    var maxC = 0, maxR = 0, any = false, guarded = 0, tally = { broken: 0 };
    Object.keys(cells).forEach(function (k) { var p = parseAddr(k); if (!p || p.col > CSV_COLS || p.row > CSV_ROWS) return; any = true; if (p.col > maxC) maxC = p.col; if (p.row > maxR) maxR = p.row; });
    if (!any) return null;
    var lines = [];
    for (var r = 1; r <= maxR; r++) {
      var row = [];
      for (var c = 1; c <= maxC; c++) {
        var a = String.fromCharCode(64 + c) + r, v = has(cells, a) ? get(a) : "";
        var f = csvField(v, isErr, tally); if (typeof v === "string" && RISKY.test(v)) guarded++;
        row.push(f);
      }
      lines.push(row.join(","));
    }
    return { text: lines.join("\r\n") + "\r\n", rows: maxR, cols: maxC, guarded: guarded, broken: tally.broken };
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
  // Remove duplicate rows in a range of typed values: a row is a duplicate when every cell of it matches an earlier row exactly as typed
  // (case and spaces count). The first one stays, later ones are dropped, the rows below move up and the freed rows at the bottom of the
  // range are emptied. Rows that are entirely empty are never counted as duplicates. Formulas are refused, as in sort.
  function dedupRange(cells, rg, header) {
    if (!rg) return { error: "Enter a range like A1:C10." };
    var first = header ? rg.r1 + 1 : rg.r1;
    if (first >= rg.r2) return { error: "There are fewer than two rows to check." };
    var r, c, formulas = 0, seen = Object.create(null), keep = [], removed = 0;
    for (r = rg.r1; r <= rg.r2; r++) for (c = rg.c1; c <= rg.c2; c++) { var rv = cells[colName(c) + r]; if (typeof rv === "string" && rv.charAt(0) === "=") formulas++; }
    if (formulas) return { error: "The range holds " + formulas + " formula" + (formulas === 1 ? "" : "s") + ". Removing rows would change what they refer to, so nothing was removed. Use a range of typed values." };
    for (r = first; r <= rg.r2; r++) {
      var vals = {}, parts = [], any = false;
      for (c = rg.c1; c <= rg.c2; c++) { var a = colName(c) + r, has_ = has(cells, a) && String(cells[a]) !== ""; if (has_) { vals[c] = cells[a]; any = true; } parts.push(has_ ? String(cells[a]) : ""); }
      var k = JSON.stringify(parts);
      if (any && seen[k]) { removed++; continue; }
      if (any) seen[k] = true;
      keep.push({ vals: vals, any: any });
    }
    if (!removed) return { cells: cells, removed: 0, rows: r - first };
    var next = copy(cells);
    for (r = first; r <= rg.r2; r++) for (c = rg.c1; c <= rg.c2; c++) delete next[colName(c) + r];
    keep.forEach(function (row, i) { Object.keys(row.vals).forEach(function (cc) { next[colName(Number(cc)) + (first + i)] = row.vals[cc]; }); });
    return { cells: next, removed: removed, rows: rg.r2 - first + 1 };
  }
  // Trim spaces in typed text cells of a range: leading and trailing spaces go and runs of spaces inside become one. Only text changes. Formulas
  // are left as they are and counted, numbers typed as numbers and blank cells are untouched, and cells outside the range are not read.
  function trimRange(cells, rg) {
    if (!rg) return { error: "Enter a range like A1:C10." };
    var next = copy(cells), changed = 0, formulas = 0, r, c;
    for (r = rg.r1; r <= rg.r2; r++) for (c = rg.c1; c <= rg.c2; c++) {
      var a = colName(c) + r, v = cells[a];
      if (typeof v !== "string") continue;
      if (v.charAt(0) === "=") { formulas++; continue; }
      var t = v.replace(/ {2,}/g, " ").replace(/^ +| +$/g, "");
      if (t === v) continue;
      changed++; if (t === "") delete next[a]; else next[a] = t;
    }
    return { cells: changed ? next : cells, changed: changed, formulas: formulas };
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
  function cloneMeta(m) { return { v: m.v === 2 ? 2 : 1, active: m.active, tabs: m.tabs.map(function (t) { return { id: t.id, name: t.name }; }) }; }
  // raw is the stored string or null. A stored value that cannot be read is reported, never treated as empty.
  function loadTabs(raw) {
    if (raw === null || raw === undefined || raw === "") return { meta: defaultTabs(), unreadable: false };
    var m = null; try { m = JSON.parse(raw); } catch (e) { m = null; }
    var bad = { meta: defaultTabs(), unreadable: true };
    if (!m || typeof m !== "object" || (m.v !== 1 && m.v !== 2) || !Array.isArray(m.tabs) || !m.tabs.length || m.tabs.length > MAX_TABS) return bad;
    var seen = Object.create(null), names = Object.create(null), out = [];
    for (var i = 0; i < m.tabs.length; i++) {
      var t = m.tabs[i], nm = t && cleanName(t.name);
      if (!t || typeof t.id !== "string" || !TAB_ID.test(t.id) || seen[t.id] || !nm || names[nm.toLowerCase()]) return bad;
      seen[t.id] = true; names[nm.toLowerCase()] = true; out.push({ id: t.id, name: nm });
    }
    return { meta: { v: m.v, active: seen[m.active] ? m.active : out[0].id, tabs: out }, unreadable: false };
  }
  // Version 2 of the tab list means "this file uses cell formats". Code from before formats existed only reads version 1, so it
  // refuses a version 2 list (tab changes off, nothing rewritten) instead of reusing ids whose format rules it cannot see.
  // The marker is set the first time a format rule is saved and is never lowered.
  function markFmt(m) { var n = cloneMeta(m); n.v = 2; return n; }
  function nameTaken(meta, name, exceptId) { var l = name.toLowerCase(); return meta.tabs.some(function (t) { return t.id !== exceptId && t.name.toLowerCase() === l; }); }
  // inUse(id) is optional: ids whose stored cells still exist (for example after a failed removal) are never handed out again.
  function addTab(meta, inUse) {
    if (meta.tabs.length >= MAX_TABS) return null;
    var n = meta.tabs.reduce(function (mx, t) { return Math.max(mx, Number(t.id.slice(1))); }, 0) + 1, k = meta.tabs.length + 1, name;
    while (typeof inUse === "function" && inUse("t" + n) && n < 10000) n++;
    do { name = "Sheet " + k++; } while (nameTaken(meta, name));
    var next = cloneMeta(meta); next.tabs.push({ id: "t" + n, name: name }); next.active = "t" + n; return next;
  }
  // A new tab after the given one, named "<name> copy" (then "copy 2", "copy 3") and cut to the name limit; it becomes the active tab. Only the list changes here:
  // the page copies the cells. Null at the tab cap or for an unknown tab.
  function duplicateTab(meta, id, inUse) {
    var i = meta.tabs.findIndex(function (t) { return t.id === id; }); if (i < 0 || meta.tabs.length >= MAX_TABS) return null;
    var n = meta.tabs.reduce(function (mx, t) { return Math.max(mx, Number(t.id.slice(1))); }, 0) + 1, base = meta.tabs[i].name, name, k = 1;
    while (typeof inUse === "function" && inUse("t" + n) && n < 10000) n++;
    do { var suffix = k === 1 ? " copy" : " copy " + k; name = cleanName(Array.from(base).slice(0, Math.max(1, NAME_MAX - suffix.length)).join("")) + suffix; k++; } while (nameTaken(meta, name) && k < 100);
    if (nameTaken(meta, name)) return null;
    var next = cloneMeta(meta); next.tabs.splice(i + 1, 0, { id: "t" + n, name: name }); next.active = "t" + n; return next;
  }
  // Moves one tab a place in the list (dir -1 left, +1 right). Only the order changes: ids, names, cells and the active tab stay. Null at either end or for an unknown tab.
  function moveTab(meta, id, dir) {
    var i = meta.tabs.findIndex(function (t) { return t.id === id; }), j = i + (dir < 0 ? -1 : 1);
    if (i < 0 || (dir !== -1 && dir !== 1) || j < 0 || j >= meta.tabs.length) return null;
    var next = cloneMeta(meta), t = next.tabs[i]; next.tabs[i] = next.tabs[j]; next.tabs[j] = t; return next;
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


  // ---- Data validation: rules per tab, checked when a value is typed into a cell. ----
  // A rule is { range: "A1:A10", kind: "list", items: [...] } or { range, kind: "number", min, max }. Formulas are not checked.
  var MAX_RULES = 10, MAX_ITEMS = 20, MAX_ITEM_LEN = 40;
  function parseRange(t, parseAddr) {
    var m = /^\s*([A-Za-z]{1,2}[0-9]{1,4})\s*:\s*([A-Za-z]{1,2}[0-9]{1,4})\s*$/.exec(String(t)), p1 = m && parseAddr(m[1].toUpperCase()), p2 = m && parseAddr(m[2].toUpperCase());
    if (!p1 || !p2) return null;
    return { c1: Math.min(p1.col, p2.col), c2: Math.max(p1.col, p2.col), r1: Math.min(p1.row, p2.row), r2: Math.max(p1.row, p2.row) };
  }
  function rangeLabel(g) { return String.fromCharCode(64 + g.c1) + g.r1 + ":" + String.fromCharCode(64 + g.c2) + g.r2; }
  function overlap(a, b) { return a.c1 <= b.c2 && b.c1 <= a.c2 && a.r1 <= b.r2 && b.r1 <= a.r2; }
  // Build one rule from form text. Returns { rule } or { error }.
  function makeRule(rangeText, kind, a, b, parseAddr) {
    var g = parseRange(rangeText, parseAddr); if (!g) return { error: "Enter a range like A1:A10." };
    if (kind === "list") {
      var seen = Object.create(null), items = [];
      String(a).split(",").forEach(function (x) { var t = x.trim(); if (t && !seen[t]) { seen[t] = true; items.push(t); } });
      if (!items.length) return { error: "Enter the allowed values, separated by commas." };
      if (items.length > MAX_ITEMS) return { error: "A list holds up to " + MAX_ITEMS + " values." };
      if (items.some(function (t) { return Array.from(t).length > MAX_ITEM_LEN || t.charAt(0) === "="; })) return { error: "Each value can be up to " + MAX_ITEM_LEN + " characters and cannot start with =." };
      return { rule: { range: rangeLabel(g), kind: "list", items: items } };
    }
    if (kind === "number") {
      var lo = toNum(String(a)), hi = toNum(String(b));
      if (lo === null || hi === null) return { error: "Enter a smallest and a largest number." };
      if (lo > hi) return { error: "The smallest number is above the largest." };
      return { rule: { range: rangeLabel(g), kind: "number", min: lo, max: hi } };
    }
    return { error: "Choose a rule type." };
  }
  function addRule(rules, rule, parseAddr) {
    if (rules.length >= MAX_RULES) return { error: "A tab holds up to " + MAX_RULES + " rules." };
    var g = parseRange(rule.range, parseAddr);
    for (var i = 0; i < rules.length; i++) if (overlap(g, parseRange(rules[i].range, parseAddr))) return { error: "That range overlaps the rule for " + rules[i].range + ". Remove it first." };
    return { rules: rules.concat([rule]) };
  }
  function removeRule(rules, i) { return i >= 0 && i < rules.length ? rules.filter(function (_, k) { return k !== i; }) : null; }
  // Strict load: anything unexpected is "unreadable", never an empty list.
  function loadRules(raw, parseAddr) {
    if (raw === null || raw === undefined || raw === "") return { rules: [], unreadable: false };
    var v = null; try { v = JSON.parse(raw); } catch (e) { v = null; }
    var bad = { rules: [], unreadable: true };
    if (!v || typeof v !== "object" || v.v !== 1 || !Array.isArray(v.rules) || v.rules.length > MAX_RULES) return bad;
    var out = [];
    for (var i = 0; i < v.rules.length; i++) {
      var r = v.rules[i], g = r && typeof r.range === "string" ? parseRange(r.range, parseAddr) : null, ok = null;
      if (!g) return bad;
      if (r.kind === "list") { ok = makeRule(r.range, "list", Array.isArray(r.items) ? r.items.join(",") : "", "", parseAddr); if (r.items.some(function (t) { return typeof t !== "string" || t.indexOf(",") >= 0; })) return bad; }
      else if (r.kind === "number") ok = typeof r.min === "number" && typeof r.max === "number" && isFinite(r.min) && isFinite(r.max) && r.min <= r.max ? { rule: { range: rangeLabel(g), kind: "number", min: r.min, max: r.max } } : { error: "x" };
      else return bad;
      if (ok.error) return bad;
      for (var k = 0; k < out.length; k++) if (overlap(g, parseRange(out[k].range, parseAddr))) return bad;
      out.push(ok.rule);
    }
    return { rules: out, unreadable: false };
  }
  function rulesJson(rules) { return JSON.stringify({ v: 1, rules: rules }); }
  // Typed value for a cell: null when allowed, otherwise the sentence to show. Blank and formulas are allowed.
  function checkValue(rules, addr, value, parseAddr) {
    var p = parseAddr(addr); if (!p || value === "" || String(value).charAt(0) === "=") return null;
    for (var i = 0; i < rules.length; i++) {
      var g = parseRange(rules[i].range, parseAddr); if (!g || p.col < g.c1 || p.col > g.c2 || p.row < g.r1 || p.row > g.r2) continue;
      var r = rules[i], t = String(value).trim();
      if (r.kind === "list") return r.items.indexOf(t) >= 0 ? null : addr + " accepts only: " + r.items.join(", ") + ".";
      var n = toNum(String(value)); return n !== null && n >= r.min && n <= r.max ? null : addr + " accepts only a number from " + r.min + " to " + r.max + ".";
    }
    return null;
  }
  // ---- Conditional formatting: colours a cell by its shown value. Never changes data. First matching rule wins. ----
  var CF_KINDS = ["greater", "less", "equals", "contains"], CF_COLORS = ["red", "yellow", "green", "blue"], MAX_CF_TEXT = 40;
  function makeCf(rangeText, kind, valueText, color, parseAddr) {
    var g = parseRange(rangeText, parseAddr); if (!g) return { error: "Enter a range like A1:A10." };
    if (CF_KINDS.indexOf(kind) < 0) return { error: "Choose a condition." };
    if (CF_COLORS.indexOf(color) < 0) return { error: "Choose a colour." };
    var v = String(valueText).trim(), rule = { range: rangeLabel(g), kind: kind, color: color };
    if (kind === "greater" || kind === "less") { var n = toNum(v); if (n === null) return { error: "Enter a number to compare with." }; rule.value = n; }
    else { if (!v) return { error: "Enter the text to look for." }; if (Array.from(v).length > MAX_CF_TEXT) return { error: "The text can be up to " + MAX_CF_TEXT + " characters." }; rule.value = v; }
    return { rule: rule };
  }
  function addCf(rules, rule) { if (rules.length >= MAX_RULES) return { error: "A tab holds up to " + MAX_RULES + " colour rules." }; return { rules: rules.concat([rule]) }; }
  function loadCf(raw, parseAddr) {
    var bad = { rules: [], unreadable: true }; if (raw === null || raw === undefined) return { rules: [], unreadable: false };
    var v; try { v = JSON.parse(raw); } catch (e) { return bad; }
    if (!v || typeof v !== "object" || v.v !== 1 || !Array.isArray(v.rules) || v.rules.length > MAX_RULES) return bad;
    var out = [];
    for (var i = 0; i < v.rules.length; i++) {
      var r = v.rules[i], g = r && typeof r === "object" ? parseRange(r.range, parseAddr) : null;
      if (!g || r.range !== rangeLabel(g) || CF_KINDS.indexOf(r.kind) < 0 || CF_COLORS.indexOf(r.color) < 0) return bad;
      if (r.kind === "greater" || r.kind === "less") { if (typeof r.value !== "number" || !isFinite(r.value)) return bad; }
      else if (typeof r.value !== "string" || !r.value || Array.from(r.value).length > MAX_CF_TEXT) return bad;
      out.push({ range: r.range, kind: r.kind, color: r.color, value: r.value });
    }
    return { rules: out, unreadable: false };
  }
  function cfJson(rules) { return JSON.stringify({ v: 1, rules: rules }); }
  // shown is the text on screen for the cell. Returns a colour name or null.
  function cfColor(rules, addr, shown, parseAddr) {
    var p = parseAddr(addr); if (!p) return null; var t = String(shown).trim();
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i], g = parseRange(r.range, parseAddr); if (!g || p.col < g.c1 || p.col > g.c2 || p.row < g.r1 || p.row > g.r2) continue;
      var hit = false;
      if (r.kind === "greater" || r.kind === "less") { var n = toNum(t); hit = n !== null && (r.kind === "greater" ? n > r.value : n < r.value); }
      else if (r.kind === "equals") hit = t === r.value;
      else hit = t.toLowerCase().indexOf(r.value.toLowerCase()) >= 0;
      if (hit) return r.color;
    }
    return null;
  }
  // ---- Cell format: bold, alignment and a number format per range. Display only: it never changes, blocks or moves a value. ----
  // A rule is { range, bold?: true|false, align?: "left"|"center"|"right", nf?: "int"|"dec2"|"thousands"|"percent"|"general" }.
  // Later rules override earlier ones, property by property. Format v:1 is read by this code only; unknown properties make it unreadable.
  var FMT_ALIGNS = ["left", "center", "right"], FMT_NFS = ["general", "int", "dec2", "thousands", "percent"], MAX_FMT = 20;
  function makeFmt(rangeText, bold, align, nf, parseAddr) {
    var g = parseRange(rangeText, parseAddr); if (!g) return { error: "Enter a range like A1:A10." };
    var rule = { range: rangeLabel(g) };
    if (bold === "on") rule.bold = true; else if (bold === "off") rule.bold = false; else if (bold !== "") return { error: "Choose bold on, off or unchanged." };
    if (align !== "") { if (FMT_ALIGNS.indexOf(align) < 0) return { error: "Choose an alignment." }; rule.align = align; }
    if (nf !== "") { if (FMT_NFS.indexOf(nf) < 0) return { error: "Choose a number format." }; rule.nf = nf; }
    if (Object.keys(rule).length < 2) return { error: "Choose at least one thing to change." };
    return { rule: rule };
  }
  function addFmt(rules, rule) { if (rules.length >= MAX_FMT) return { error: "A tab holds up to " + MAX_FMT + " format rules." }; return { rules: rules.concat([rule]) }; }
  function loadFmt(raw, parseAddr) {
    var bad = { rules: [], unreadable: true }; if (raw === null || raw === undefined) return { rules: [], unreadable: false };
    var v; try { v = JSON.parse(raw); } catch (e) { return bad; }
    if (!v || typeof v !== "object" || v.v !== 1 || !Array.isArray(v.rules) || v.rules.length > MAX_FMT) return bad;
    var out = [];
    for (var i = 0; i < v.rules.length; i++) {
      var r = v.rules[i], g = r && typeof r === "object" && !Array.isArray(r) ? parseRange(r.range, parseAddr) : null;
      if (!g || r.range !== rangeLabel(g)) return bad;
      var keys = Object.keys(r).filter(function (k) { return k !== "range"; }), o = { range: r.range };
      if (!keys.length) return bad;
      for (var j = 0; j < keys.length; j++) {
        var k = keys[j];
        if (k === "bold" && typeof r.bold === "boolean") o.bold = r.bold;
        else if (k === "align" && FMT_ALIGNS.indexOf(r.align) >= 0) o.align = r.align;
        else if (k === "nf" && FMT_NFS.indexOf(r.nf) >= 0) o.nf = r.nf;
        else return bad;
      }
      out.push(o);
    }
    return { rules: out, unreadable: false };
  }
  function fmtJson(rules) { return JSON.stringify({ v: 1, rules: rules }); }
  // The merged format of one cell: { bold, align, nf }, each undefined when no rule sets it.
  function fmtFor(rules, addr, parseAddr) {
    var p = parseAddr(addr), out = {}; if (!p) return out;
    for (var i = 0; i < rules.length; i++) {
      var r = rules[i], g = parseRange(r.range, parseAddr); if (!g || p.col < g.c1 || p.col > g.c2 || p.row < g.r1 || p.row > g.r2) continue;
      if (r.bold !== undefined) out.bold = r.bold; if (r.align !== undefined) out.align = r.align; if (r.nf !== undefined) out.nf = r.nf;
    }
    return out;
  }
  // The text a number format shows. Text, errors, blanks and anything not a plain number stay as they are.
  function fmtText(nf, shown) {
    var n = nf && nf !== "general" ? toNum(String(shown)) : null; if (n === null) return shown;
    var t;
    if (nf === "int") t = String(Math.round(n));
    else if (nf === "dec2") t = n.toFixed(2);
    else if (nf === "thousands") t = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    else if (nf === "percent") t = (n * 100).toFixed(1) + "%";
    else return shown;
    return /^-0(\.0+)?%?$/.test(t) ? t.slice(1) : t;
  }
  // ---- Paste a block of tab and newline separated text (as copied from another spreadsheet) at a start cell. ----
  // Quoted fields may hold tabs and line breaks ("" is a quote). Cells outside the grid are clipped and counted. Nothing is guessed:
  // every value is kept as the text it is, a leading = is a formula exactly as if typed. Returns { rows, cols, items } or { error }.
  var MAX_PASTE_CHARS = 200000, MAX_PASTE_CELLS = 2600;
  function parseTsv(text) {
    var rows = [], row = [], f = "", i = 0, q = false, n = text.length, c;
    while (i < n) {
      c = text.charAt(i);
      if (q) {
        if (c === '"') {
          if (text.charAt(i + 1) === '"') { f += '"'; i += 2; continue; }
          var nx = text.charAt(i + 1);
          if (nx !== "" && nx !== "\t" && nx !== "\r" && nx !== "\n") return null;  // text after a closing quote: refuse, never drop the quotes silently
          q = false; i++; continue;
        }
        f += c; i++; continue;
      }
      if (c === '"' && f === "") { q = true; i++; continue; }
      if (c === "\t") { row.push(f); f = ""; i++; continue; }
      if (c === "\r" || c === "\n") { row.push(f); rows.push(row); row = []; f = ""; if (c === "\r" && text.charAt(i + 1) === "\n") i++; i++; continue; }
      f += c; i++;
    }
    if (q) return null;  // a quote that never closes: refuse rather than guess
    if (f !== "" || row.length) { row.push(f); rows.push(row); }
    return rows;
  }
  function parsePaste(text, parseAddr) {
    if (typeof text !== "string" || text.length > MAX_PASTE_CHARS) return { error: "That is more than " + MAX_PASTE_CHARS + " characters, so nothing was pasted." };
    var rows = parseTsv(text); if (!rows) return { error: "The pasted text has a quoted cell that does not close or is followed by more text, so nothing was pasted." };
    var cols = rows.reduce(function (m, r) { return Math.max(m, r.length); }, 0);
    if (rows.length * cols > MAX_PASTE_CELLS) return { error: "That is more than " + MAX_PASTE_CELLS + " cells, so nothing was pasted." };
    return { rows: rows.length, cols: cols, items: rows };
  }
  // True when the text is a block (more than one cell) rather than a single value.
  function isBlock(text) { return typeof text === "string" && /[\t\r\n]/.test(text.replace(/(\r\n|\r|\n)$/, "")); }
  // Apply a parsed paste to cells at (col, row). maxCol/maxRow clip; check(addr, value) returns null or a reason. Returns the new cells and counts.
  function applyPaste(cells, parsed, col, row, maxCol, maxRow, maxCell, check, addrOf) {
    var n = copy(cells), set = 0, cleared = 0, clipped = 0, refused = 0, tooLong = 0, formulas = 0, breaks = 0;
    for (var r = 0; r < parsed.items.length; r++) for (var c = 0; c < parsed.items[r].length; c++) {
      var cc = col + c, rr = row + r, v = parsed.items[r][c];
      if (cc > maxCol || rr > maxRow) { clipped++; continue; }
      var a = addrOf(cc, rr);
      // A cell box holds one line, so a line break inside a pasted cell would be lost on the next edit: it becomes a space, and is counted.
      if (/[\r\n]/.test(v)) { v = v.replace(/\r\n|\r|\n/g, " "); breaks++; }
      if (v.length > maxCell) { tooLong++; continue; }
      if (check && check(a, v)) { refused++; continue; }
      if (v === "") { if (a in n) { delete n[a]; cleared++; } } else { n[a] = v; set++; if (v.charAt(0) === "=") formulas++; }
    }
    return { cells: n, set: set, cleared: cleared, clipped: clipped, refused: refused, tooLong: tooLong, formulas: formulas, breaks: breaks };
  }
  // ---- CSV import. Comma separated, quoted fields may hold commas, quotes ("") and line breaks. Nothing is guessed: every value stays text.
  // A field that starts with = is kept as literal text with a leading apostrophe (the same mark the CSV download adds), never run as a formula.
  var MAX_CSV_CHARS = 1000000, MAX_CSV_ROWS = 5000, MAX_CSV_COLS = 200;
  function parseCsv(text) {
    if (text.charAt(0) === "\uFEFF") text = text.slice(1);
    var rows = [], row = [], f = "", i = 0, q = false, n = text.length, c, any = false;
    while (i < n) {
      c = text.charAt(i);
      if (q) {
        if (c === '"') {
          if (text.charAt(i + 1) === '"') { f += '"'; i += 2; continue; }
          var nx = text.charAt(i + 1);
          if (nx !== "" && nx !== "," && nx !== "\r" && nx !== "\n") return null;
          q = false; i++; continue;
        }
        f += c; i++; continue;
      }
      if (c === '"' && f === "") { q = true; any = true; i++; continue; }
      if (c === ",") { row.push(f); f = ""; any = true; i++; continue; }
      if (c === "\r" || c === "\n") { row.push(f); rows.push(row); row = []; f = ""; any = false; if (c === "\r" && text.charAt(i + 1) === "\n") i++; i++; continue; }
      f += c; any = true; i++;
    }
    if (q) return null;
    if (f !== "" || row.length || any) { row.push(f); rows.push(row); }
    return rows;
  }
  function csvImport(text, check, addrOf, maxCol, maxRow, maxCell) {
    if (typeof text !== "string" || !text.length) return { error: "That file is empty. Your sheet was not changed." };
    if (text.length > MAX_CSV_CHARS) return { error: "That file is more than " + MAX_CSV_CHARS + " characters. Your sheet was not changed." };
    var rows = parseCsv(text); if (!rows) return { error: "The file has a quoted cell that does not close or is followed by more text, so it was not imported. Your sheet was not changed." };
    var cols = rows.reduce(function (m, r) { return Math.max(m, r.length); }, 0);
    if (rows.length > MAX_CSV_ROWS || cols > MAX_CSV_COLS) return { error: "That file has more than " + MAX_CSV_ROWS + " rows or " + MAX_CSV_COLS + " columns. Your sheet was not changed." };
    var literal = 0;
    var items = rows.map(function (r) { return r.map(function (v) { if (v.charAt(0) === "=") { literal++; return "'" + v; } return v; }); });
    var res = applyPaste(Object.create(null), { rows: rows.length, cols: cols, items: items }, 1, 1, maxCol, maxRow, maxCell, check, addrOf);
    res.rows = rows.length; res.cols = cols; res.literal = literal; return res;
  }
  // ---- Frozen rows and columns: how many top rows and left columns stay in view while the grid scrolls. A view setting only, kept per tab. ----
  // One stored object { v: 1, tabs: { <tab id>: { r, c } } }. Entries for tabs that are not in the list are dropped when read, so a deleted
  // tab's setting can never show on another tab. Anything unreadable or out of range reads as "no freeze" and the stored text is not touched.
  var MAX_FREEZE = 5;
  function loadFreeze(raw, ids) {
    var none = { map: Object.create(null), unreadable: false };
    if (raw === null || raw === undefined) return none;
    var v; try { v = JSON.parse(raw); } catch (e) { return { map: Object.create(null), unreadable: true }; }
    if (!v || typeof v !== "object" || v.v !== 1 || !v.tabs || typeof v.tabs !== "object" || Array.isArray(v.tabs)) return { map: Object.create(null), unreadable: true };
    var out = Object.create(null);
    Object.keys(v.tabs).forEach(function (id) {
      var e = v.tabs[id];
      if (ids.indexOf(id) < 0 || !e || typeof e !== "object") return;
      var ok = function (n) { return typeof n === "number" && n === Math.floor(n) && n >= 0 && n <= MAX_FREEZE; };
      if (!ok(e.r) || !ok(e.c)) return;
      if (e.r || e.c) out[id] = { r: e.r, c: e.c };
    });
    return { map: out, unreadable: false };
  }
  function freezeJson(map) {
    var tabs = {}; Object.keys(map).forEach(function (id) { if (map[id].r || map[id].c) tabs[id] = { r: map[id].r, c: map[id].c }; });
    return Object.keys(tabs).length ? JSON.stringify({ v: 1, tabs: tabs }) : null;
  }
  function parseFreeze(rt, ct) {
    var one = function (t) { t = String(t == null ? "" : t).trim(); return /^[0-5]$/.test(t) ? Number(t) : null; };
    var r = one(rt), c = one(ct);
    if (r === null || c === null) return { error: "Frozen rows and columns must each be a whole number from 0 to " + MAX_FREEZE + "." };
    return { r: r, c: c };
  }
  // Copy the first row of a range down over the rest (dir "down") or its first column right over the rest ("right").
  // Text is copied as written, except that with a shift function a formula's relative references move with the fill ($ pins). Empty source cells clear the targets.

  /* Find and replace over one tab's cells. Plain text only (no patterns), so "a.b" and "$1" mean what they say.
   * Search: a typed (constant) cell is searched by its text. A formula cell is searched by its shown result by default; that match is
   * listed but the formula is never changed. With o.formulas on, a formula cell is searched and replaced by its formula text instead
   * (its shown result is then not searched). Replace: every occurrence in the cells that are searched in that way. A replacement is
   * skipped, and counted, when it would turn typed text into a formula, would leave a formula without its leading "=", is too long,
   * is refused by a validation rule, or (formula text) no longer reads as a formula (ctx.badFormula(addr, text) true, a #PARSE! result; a formula that reads but gives another error such as #REF! or #NAME? is still written and shows that error, undo restores it). Emptying a cell removes it. o: { find, repl (null = find only), matchCase, formulas }.
   * ctx: { cols, rows (the searched grid; cells beyond it are never matched, listed or changed, only counted in outside), parseAddr, shown(addr) -> display text of a formula cell, check(addr, v) -> null | message, maxCell }. */
  function findReplace(cells, o, ctx) {
    var find = String(o.find == null ? "" : o.find);
    if (!find) return { error: "Type the text to find." };
    var flags = o.matchCase ? "g" : "gi", re = new RegExp(find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags);
    var addrs = Object.keys(cells).filter(function (a) { return ctx.parseAddr(a); }).sort(function (x, y) { var p = ctx.parseAddr(x), q = ctx.parseAddr(y); return p.row - q.row || p.col - q.col; });
    var out = copy(cells), res = { hits: [], matched: 0, changed: 0, cleared: 0, resultOnly: 0, becameFormula: 0, lostEquals: 0, tooLong: 0, refused: 0, badFormula: 0, outside: 0 };
    var doRepl = o.repl !== null && o.repl !== undefined, repl = String(doRepl ? o.repl : "");
    addrs.forEach(function (a) {
      var raw = cells[a], isF = raw.charAt(0) === "=", text, pa = ctx.parseAddr(a);
      if (ctx.cols && (pa.col > ctx.cols || pa.row > ctx.rows)) { re.lastIndex = 0; if (!isF || o.formulas) { if (raw.search(re) >= 0) res.outside++; } re.lastIndex = 0; return; }
      if (isF && !o.formulas) { text = ctx.shown(a); text = text == null ? "" : String(text); if (text.search(re) < 0) return; re.lastIndex = 0; res.hits.push(a); res.matched++; res.resultOnly++; return; }
      re.lastIndex = 0; if (raw.search(re) < 0) return; re.lastIndex = 0;
      res.hits.push(a); res.matched++;
      if (!doRepl) return;
      var nv = raw.replace(re, function () { return repl; });
      if (nv === raw) return;
      if (!isF && nv.charAt(0) === "=") { res.becameFormula++; return; }
      if (isF && nv.charAt(0) !== "=") { res.lostEquals++; return; }
      if (nv.length > ctx.maxCell) { res.tooLong++; return; }
      if (isF && ctx.badFormula && ctx.badFormula(a, nv)) { res.badFormula++; return; }
      if (nv !== "") { var why = ctx.check ? ctx.check(a, nv) : null; if (why) { res.refused++; return; } delete out[a]; out[a] = nv; res.changed++; }
      else { delete out[a]; res.cleared++; }
    });
    res.cells = out; return res;
  }
  function fillRange(cells, rg, dir, check, addrOf, maxCell, shift) {
    var n = copy(cells), refErrs = 0, adjusted = 0, set = 0, cleared = 0, refused = 0, tooLong = 0, formulas = 0, down = dir === "down";
    var rows = rg.r2 - rg.r1 + 1, cols = rg.c2 - rg.c1 + 1;
    if (down ? rows < 2 : cols < 2) return { error: down ? "Pick a range with at least two rows. The first row is copied down." : "Pick a range with at least two columns. The first column is copied right." };
    for (var r = rg.r1; r <= rg.r2; r++) for (var c = rg.c1; c <= rg.c2; c++) {
      if (down ? r === rg.r1 : c === rg.c1) continue;
      var v = cells[addrOf(down ? c : rg.c1, down ? rg.r1 : r)] || "", a = addrOf(c, r);
      if (shift && v.charAt(0) === "=") { var v0 = v; v = shift(v, down ? 0 : c - rg.c1, down ? r - rg.r1 : 0); if (v !== v0) adjusted++; if (v.indexOf("#REF!") >= 0 && cells[addrOf(down ? c : rg.c1, down ? rg.r1 : r)].indexOf("#REF!") < 0) refErrs++; }
      if (v.length > maxCell) { tooLong++; continue; }
      if (check && check(a, v)) { refused++; continue; }
      if (v === "") { if (a in n) { delete n[a]; cleared++; } } else { if (n[a] !== v) set++; n[a] = v; if (v.charAt(0) === "=") formulas++; }
    }
    return { cells: n, set: set, cleared: cleared, refused: refused, tooLong: tooLong, formulas: formulas, refErrs: refErrs, adjusted: adjusted };
  }
  // ---- Insert or delete one row or column. Pure: takes the cells and the three rule lists, returns the moved copies or { error }. ----
  // op: { axis: "row"|"col", kind: "insert"|"delete", at }. grid: { rows, cols } is the editable grid (A1:Z1000). Nothing is changed when an error is
  // returned. Insert is refused when a filled cell, or a rule range, in the last row or column would be pushed off the grid. Delete drops the cells
  // on that line (counted), shrinks rule ranges, and drops a rule whose whole range was deleted (counted).
  function structRange(g, axis, kind, at) {
    var k1 = axis === "row" ? "r1" : "c1", k2 = axis === "row" ? "r2" : "c2", o = { c1: g.c1, c2: g.c2, r1: g.r1, r2: g.r2 }, lo = g[k1], hi = g[k2];
    if (kind === "insert") { o[k1] = lo >= at ? lo + 1 : lo; o[k2] = hi >= at ? hi + 1 : hi; return o; }
    if (lo === at && hi === at) return null;
    o[k1] = lo > at ? lo - 1 : lo; o[k2] = hi >= at ? hi - 1 : hi;
    return o[k2] < o[k1] ? null : o;
  }
  function structEdit(st, op, parseAddr, structFormula, grid, maxCell) {
    var row = op.axis === "row", lim = row ? grid.rows : grid.cols, at = op.at, ins = op.kind === "insert";
    if ((op.axis !== "row" && op.axis !== "col") || (!ins && op.kind !== "delete") || !(at >= 1 && at <= lim) || Math.floor(at) !== at) return { error: "Choose a " + (row ? "row 1 to " + grid.rows : "column A to " + String.fromCharCode(64 + grid.cols)) + "." };
    var stats = { removed: 0, formulas: 0, refErrs: 0, dropped: 0, resized: 0 }, cells = Object.create(null), keys = Object.keys(st.cells);
    var lineName = row ? "row " + lim : "column " + String.fromCharCode(64 + lim);
    for (var i = 0; i < keys.length; i++) {
      var a = parseAddr(keys[i]), v = st.cells[keys[i]]; if (!a) { cells[keys[i]] = v; continue; }
      var p = row ? a.row : a.col;
      if (ins && p >= lim && v !== "") return { error: "Nothing was inserted: " + lineName + " has content that would be pushed off the grid. Clear it or move it first." };
      if (!ins && p === at) { if (v !== "") stats.removed++; continue; }
      var q = ins ? (p >= at ? p + 1 : p) : (p > at ? p - 1 : p), nk = row ? colName(a.col) + q : colName(q) + a.row;
      if (v.charAt(0) === "=") { var nv = structFormula(v, op.axis, op.kind, at); if (nv !== v) { stats.formulas++; if (nv.indexOf("#REF!") >= 0 && v.indexOf("#REF!") < 0) stats.refErrs++; } if (nv.length > maxCell) return { error: "Nothing was changed: a formula would grow past the cell length limit." }; v = nv; }
      cells[nk] = v;
    }
    function lists(list, what) {
      var out = [];
      for (var k = 0; k < list.length; k++) {
        var g = parseRange(list[k].range, parseAddr); if (!g) return { error: "A " + what + " could not be read, so nothing was changed." };
        if (ins && g[row ? "r2" : "c2"] >= at && g[row ? "r2" : "c2"] >= lim) return { error: "Nothing was inserted: a " + what + " reaches the last " + (row ? "row" : "column") + " and would be pushed off the grid." };
        var ng = structRange(g, op.axis, op.kind, at); if (!ng) { stats.dropped++; continue; }
        if (rangeLabel(ng) !== rangeLabel(g)) stats.resized++;
        var c = {}; Object.keys(list[k]).forEach(function (f) { c[f] = list[k][f]; }); c.range = rangeLabel(ng); out.push(c);
      }
      return { list: out };
    }
    var r1 = lists(st.rules, "validation rule"); if (r1.error) return r1;
    var r2 = lists(st.cf, "colour rule"); if (r2.error) return r2;
    var r3 = lists(st.fmt, "format rule"); if (r3.error) return r3;
    return { cells: cells, rules: r1.list, cf: r2.list, fmt: r3.list, stats: stats };
  }
  // ---- Writing a structural change to the four stored keys (cells, validation, colour, format). Not atomic, and it does not claim to be. ----
  // st is a storage (getItem, setItem, removeItem). keys is the four key names, expect the raw text each held when this page loaded it (null: absent),
  // next the raw text to store (null: remove). A recovery copy of expect and next goes under bakKey before the first write and is removed only when the
  // result is known: all four keys read back as intended. Statuses: done (bakLeft: the copy could not be removed), refused (nothing written; reason
  // recovery-exists, stale, storage, no-recovery-copy, recovery-stuck), failed-restored (every key read back as before), failed-unresolved (bad lists the
  // keys that are not known to be as before; the recovery copy is kept). Another window can write between the checks and the writes; that is not preventable here.
  function sRead(st, k) { try { var v = st.getItem(k); return { ok: true, v: v === undefined ? null : v }; } catch (e) { return { ok: false, v: null }; } }
  function sWrite(st, k, v) { try { if (v === null) st.removeItem(k); else st.setItem(k, v); } catch (e) { return false; } var r = sRead(st, k); return r.ok && r.v === v; }
  function sDrop(st, k) { try { st.removeItem(k); } catch (e) { /* checked below */ } var r = sRead(st, k); return r.ok && r.v === null; }
  function structApply(st, keys, bakKey, expect, next) {
    var e = sRead(st, bakKey), i, cur = [];
    if (!e.ok) return { status: "refused", reason: "storage" };
    if (e.v !== null) return { status: "refused", reason: "recovery-exists" };
    for (i = 0; i < 4; i++) { var r = sRead(st, keys[i]); if (!r.ok) return { status: "refused", reason: "storage" }; if (r.v !== expect[i]) return { status: "refused", reason: "stale", key: keys[i] }; cur.push(r.v); }
    if (next.every(function (x, k) { return x === expect[k]; })) return { status: "refused", reason: "no-change" };
    var bak = JSON.stringify({ v: 1, keys: keys, raw: expect, next: next });
    if (!sWrite(st, bakKey, bak)) { return sDrop(st, bakKey) ? { status: "refused", reason: "no-recovery-copy" } : { status: "refused", reason: "recovery-stuck", bakKept: true }; }
    var attempted = [], failed = false;
    for (i = 0; i < 4; i++) { if (next[i] === expect[i]) continue; attempted.push(i); if (!sWrite(st, keys[i], next[i])) { failed = true; break; } }
    if (!failed) for (i = 0; i < 4; i++) { var c = sRead(st, keys[i]); if (!c.ok || c.v !== next[i]) failed = true; }
    if (!failed) return { status: "done", bakLeft: !sDrop(st, bakKey) };
    var bad = [];
    attempted.slice().reverse().forEach(function (j) {
      var q = sRead(st, keys[j]);
      if (!q.ok) { bad.push(keys[j]); return; }
      if (q.v === expect[j]) return;
      if (q.v === next[j]) { if (!sWrite(st, keys[j], expect[j])) bad.push(keys[j]); return; }
      bad.push(keys[j]);   // neither what was there nor what was written: not overwritten
    });
    for (i = 0; i < 4; i++) { var z = sRead(st, keys[i]); if ((!z.ok || z.v !== expect[i]) && bad.indexOf(keys[i]) < 0) bad.push(keys[i]); }
    if (bad.length) return { status: "failed-unresolved", bad: bad, bakKept: true };
    return { status: "failed-restored", bakLeft: !sDrop(st, bakKey) };
  }
  function bakRead(st, bakKey, keys) {
    var r = sRead(st, bakKey); if (!r.ok) return { state: "storage" }; if (r.v === null) return { state: "none" };
    var b = null; try { b = JSON.parse(r.v); } catch (e) { b = null; }
    var arr = function (a, nullable) { return Array.isArray(a) && a.length === 4 && a.every(function (x) { return typeof x === "string" && (nullable || x !== "") || (nullable && x === null); }); };
    if (!b || b.v !== 1 || !arr(b.keys, false) || b.keys.join("\n") !== keys.join("\n") || !arr(b.raw, true) || !arr(b.next, true)) return { state: "unreadable" };
    return { state: "found", keys: b.keys, raw: b.raw, next: b.next };
  }
  // Put the four keys back as the recovery copy recorded them. A key that holds neither the recorded original nor the recorded new text was changed by
  // something else: without force it is left alone and the call returns conflict with nothing written; with force (the user said so) it is overwritten.
  function structRecover(st, bakKey, keys, force) {
    var b = bakRead(st, bakKey, keys); if (b.state !== "found") return { status: b.state === "none" ? "none" : b.state };
    var todo = [], conflict = [], i;
    for (i = 0; i < 4; i++) { var r = sRead(st, b.keys[i]); if (!r.ok) return { status: "storage" }; if (r.v === b.raw[i]) continue; if (r.v === b.next[i] || force) todo.push(i); else conflict.push(b.keys[i]); }
    if (conflict.length) return { status: "conflict", keys: conflict };
    var bad = []; todo.forEach(function (j) { if (!sWrite(st, b.keys[j], b.raw[j])) bad.push(b.keys[j]); });
    for (i = 0; i < 4; i++) { var z = sRead(st, b.keys[i]); if ((!z.ok || z.v !== b.raw[i]) && bad.indexOf(b.keys[i]) < 0) bad.push(b.keys[i]); }
    if (bad.length) return { status: "unresolved", bad: bad };
    return { status: "restored", bakLeft: !sDrop(st, bakKey) };
  }
  function structRecovery(st, bakKey, keys) { return bakRead(st, bakKey, keys).state; }
  function structRecoveryInfo(st, bakKey, keys) { return bakRead(st, bakKey, keys); }
  // ---- Pivot: group rows of a range by one column and total another. Read only: it never writes to cells. ----
  var PIVOT_AGGS = ["count", "sum", "average", "min", "max"], MAX_GROUPS = 20;
  function colNo(t) { var m = /^\s*([A-Za-z])\s*$/.exec(String(t)); return m ? m[1].toUpperCase().charCodeAt(0) - 64 : null; }
  // Returns { spec } or { error }. keyCol and valCol are single letters that must lie inside the range.
  function makePivot(rangeText, keyCol, valCol, agg, header, parseAddr) {
    var g = parseRange(rangeText, parseAddr); if (!g) return { error: "Enter a range like A1:C10." };
    var kc = colNo(keyCol), vc = colNo(valCol);
    if (!kc || !vc) return { error: "Enter a column letter for the group column and for the value column." };
    if (kc < g.c1 || kc > g.c2 || vc < g.c1 || vc > g.c2) return { error: "Both columns must be inside " + rangeLabel(g) + "." };
    if (PIVOT_AGGS.indexOf(agg) < 0) return { error: "Choose how to total." };
    var r1 = g.r1 + (header ? 1 : 0); if (r1 > g.r2) return { error: "The range has no rows below the header." };
    return { spec: { r1: r1, r2: g.r2, kc: kc, vc: vc, agg: agg, label: rangeLabel(g), header: !!header } };
  }
  // A blank group cell is its own group (blank: true); typing the words "(blank)" makes a different group. A sum or average that
  // overflows is marked tooLarge with no value, never shown as Infinity.
  // pairs: [{ k: shown text of the group cell, v: the value cell's value }]. Groups keep first-seen order. Only real numbers are
  // added, averaged or compared; other values in a group are counted in "skipped". Count counts rows, not numbers.
  function pivot(pairs, agg) {
    var idx = Object.create(null), groups = [], skipped = 0, rows = 0;
    for (var i = 0; i < pairs.length; i++) {
      var k = String(pairs[i].k).trim(), v = pairs[i].v, blankV = v === "" || v === null || v === undefined;
      if (k === "" && blankV) continue;
      rows++;
      var key = k === "" ? "b" : "t" + k, gi = idx[key];
      if (gi === undefined) { if (groups.length >= MAX_GROUPS) return { error: "There are more than " + MAX_GROUPS + " different groups, so no pivot was made. Use a smaller range." }; gi = groups.length; idx[key] = gi; groups.push({ key: k, blank: k === "", rows: 0, nums: [] }); }
      groups[gi].rows++;
      if (typeof v === "number" && isFinite(v)) groups[gi].nums.push(v); else if (!blankV) skipped++;
    }
    var out = groups.map(function (g) {
      var n = g.nums, val;
      if (agg === "count") val = g.rows;
      else if (!n.length) val = null;
      else if (agg === "sum") val = n.reduce(function (a, b) { return a + b; }, 0);
      else if (agg === "average") { val = 0; n.forEach(function (x, q) { val += (x - val) / (q + 1); }); }
      else if (agg === "min") val = Math.min.apply(null, n);
      else val = Math.max.apply(null, n);
      var big = val !== null && !isFinite(val);
      return { key: g.key, blank: g.blank, rows: g.rows, numbers: n.length, value: val === null || big ? null : +val.toPrecision(12), tooLarge: big };
    });
    return { groups: out, skipped: skipped, rows: rows };
  }
  // ---- Named versions of one tab, kept in this browser only. A version is the four stored texts of the tab (cells, validation, colour and format rules) exactly as stored.
  // Nothing about tab names, the freeze setting, the chart or the filter is in it. Not a backup: it lives in the same browser storage as the tab.
  var MAX_VERS = 5, MAX_VER_NAME = 40, MAX_VER_BYTES = 1500000;
  function verName(raw) { var n = String(raw === undefined || raw === null ? "" : raw).replace(/\s+/g, " ").trim(); return n.length >= 1 && n.length <= MAX_VER_NAME ? n : null; }
  function verNameTaken(items, name, exceptIndex) { var l = name.toLowerCase(); return items.some(function (v, i) { return i !== exceptIndex && v.n.toLowerCase() === l; }); }
  function loadVers(raw) {
    if (raw === null || raw === undefined) return { items: [], unreadable: false };
    var j = null; try { j = JSON.parse(raw); } catch (e) { return { items: [], unreadable: true }; }
    if (!j || j.v !== 1 || !Array.isArray(j.items) || j.items.length > MAX_VERS) return { items: [], unreadable: true };
    var out = [];
    for (var i = 0; i < j.items.length; i++) {
      var v = j.items[i];
      if (!v || typeof v.n !== "string" || verName(v.n) !== v.n || !Array.isArray(v.k) || v.k.length !== 4) return { items: [], unreadable: true };
      for (var q = 0; q < 4; q++) if (!(v.k[q] === null || (typeof v.k[q] === "string" && v.k[q].length <= MAX_VER_BYTES))) return { items: [], unreadable: true };
      if (verNameTaken(out, v.n, -1)) return { items: [], unreadable: true };
      out.push({ n: v.n, k: v.k.slice() });
    }
    return { items: out, unreadable: false };
  }
  function versJson(items) { return items.length ? JSON.stringify({ v: 1, items: items }) : null; }
  // Adds a version. Returns { items } or { error }. The four texts are what is stored now.
  function addVer(items, name, k) {
    var n = verName(name); if (n === null) return { error: "Give the version a name of 1 to " + MAX_VER_NAME + " characters." };
    if (items.length >= MAX_VERS) return { error: "A tab keeps up to " + MAX_VERS + " versions. Delete one first." };
    if (verNameTaken(items, n, -1)) return { error: "A version with that name already exists on this tab. Pick another name." };
    var next = items.map(function (v) { return { n: v.n, k: v.k.slice() }; }); next.push({ n: n, k: k.slice() });
    if (JSON.stringify({ v: 1, items: next }).length > MAX_VER_BYTES) return { error: "The sheet is too large to keep as a version (over " + MAX_VER_BYTES + " characters stored)." };
    return { items: next };
  }
  function dropVer(items, index) { if (index < 0 || index >= items.length) return null; return items.filter(function (v, i) { return i !== index; }).map(function (v) { return { n: v.n, k: v.k.slice() }; }); }

  // ---- Column widths (view only, per tab). A width belongs to a column position (A to Z), like a freeze: inserting or deleting a column does not move it.
  // Stored in the tab's own ".cw" entry. Anything unreadable reads as "no custom widths" and the stored text is left alone. ----
  var MIN_CW = 40, MAX_CW = 400, DEFAULT_CW = 144;
  function loadWidths(raw) {
    var none = { w: Object.create(null), unreadable: false };
    if (raw === null || raw === undefined) return none;
    var v; try { v = JSON.parse(raw); } catch (e) { return { w: Object.create(null), unreadable: true }; }
    if (!v || typeof v !== "object" || v.v !== 1 || !v.w || typeof v.w !== "object" || Array.isArray(v.w)) return { w: Object.create(null), unreadable: true };
    var out = Object.create(null), bad = false;
    Object.keys(v.w).forEach(function (k) {
      var n = v.w[k];
      if (!/^[A-Z]$/.test(k) || typeof n !== "number" || n !== Math.floor(n) || n < MIN_CW || n > MAX_CW) { bad = true; return; }
      if (n !== DEFAULT_CW) out[k] = n;
    });
    if (bad) return { w: Object.create(null), unreadable: true };
    return { w: out, unreadable: false };
  }
  function widthsJson(w) {
    var o = {}; Object.keys(w).sort().forEach(function (k) { if (w[k] !== DEFAULT_CW) o[k] = w[k]; });
    return Object.keys(o).length ? JSON.stringify({ v: 1, w: o }) : null;
  }
  function parseWidth(col, text) {
    var c = String(col == null ? "" : col).trim().toUpperCase(), t = String(text == null ? "" : text).trim();
    if (!/^[A-Z]$/.test(c)) return { error: "Choose a column from A to Z." };
    if (!/^[0-9]{1,4}$/.test(t) || Number(t) < MIN_CW || Number(t) > MAX_CW) return { error: "The width must be a whole number of pixels from " + MIN_CW + " to " + MAX_CW + " (the standard width is " + DEFAULT_CW + ")." };
    return { col: c, px: Number(t) };
  }
  var api = { trimRange: trimRange, dedupRange: dedupRange, moveTab: moveTab, duplicateTab: duplicateTab, scatterPoints: scatterPoints, loadWidths: loadWidths, widthsJson: widthsJson, parseWidth: parseWidth, MIN_CW: MIN_CW, MAX_CW: MAX_CW, DEFAULT_CW: DEFAULT_CW, loadVers: loadVers, versJson: versJson, addVer: addVer, dropVer: dropVer, verName: verName, MAX_VERS: MAX_VERS, structApply: structApply, structRecover: structRecover, structRecovery: structRecovery, structRecoveryInfo: structRecoveryInfo, structEdit: structEdit, structRange: structRange, fillRange: fillRange, findReplace: findReplace, parseRange: parseRange, makeRule: makeRule, addRule: addRule, removeRule: removeRule, loadRules: loadRules, rulesJson: rulesJson, checkValue: checkValue, makePivot: makePivot, pivot: pivot, MAX_GROUPS: MAX_GROUPS, makeCf: makeCf, addCf: addCf, loadCf: loadCf, cfJson: cfJson, cfColor: cfColor, parsePaste: parsePaste, loadFreeze: loadFreeze, freezeJson: freezeJson, parseFreeze: parseFreeze, MAX_FREEZE: MAX_FREEZE, parseCsv: parseCsv, csvImport: csvImport, isBlock: isBlock, applyPaste: applyPaste, MAX_PASTE_CELLS: MAX_PASTE_CELLS, makeFmt: makeFmt, addFmt: addFmt, loadFmt: loadFmt, fmtJson: fmtJson, fmtFor: fmtFor, fmtText: fmtText, MAX_FMT: MAX_FMT, MAX_RULES: MAX_RULES, loadTabs: loadTabs, addTab: addTab, markFmt: markFmt, renameTab: renameTab, deleteTab: deleteTab, setActive: setActive, cleanName: cleanName, MAX_TABS: MAX_TABS, sortRange: sortRange, filterRows: filterRows, toNum: toNum, toCsv: toCsv, clean: clean, History: History, chartPoints: chartPoints, pieSlices: pieSlices, isComposing: isComposing, keyMove: keyMove, copy: copy, same: same };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSheetModel = api;
})(typeof self !== "undefined" ? self : this);
