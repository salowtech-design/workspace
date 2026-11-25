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

  var api = { toCsv: toCsv, clean: clean, History: History, chartPoints: chartPoints, isComposing: isComposing, keyMove: keyMove, copy: copy, same: same };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSheetModel = api;
})(typeof self !== "undefined" ? self : this);
