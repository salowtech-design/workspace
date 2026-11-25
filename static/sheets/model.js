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

  var api = { clean: clean, History: History, chartPoints: chartPoints, isComposing: isComposing, keyMove: keyMove, copy: copy, same: same };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSheetModel = api;
})(typeof self !== "undefined" ? self : this);
