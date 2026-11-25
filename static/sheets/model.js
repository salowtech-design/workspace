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
  // inUse(id) is optional: ids whose stored cells still exist (for example after a failed removal) are never handed out again.
  function addTab(meta, inUse) {
    if (meta.tabs.length >= MAX_TABS) return null;
    var n = meta.tabs.reduce(function (mx, t) { return Math.max(mx, Number(t.id.slice(1))); }, 0) + 1, k = meta.tabs.length + 1, name;
    while (typeof inUse === "function" && inUse("t" + n) && n < 10000) n++;
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
  // pairs: [{ k: shown text of the group cell, v: the value cell's value }]. Groups keep first-seen order. Only real numbers are
  // added, averaged or compared; other values in a group are counted in "skipped". Count counts rows, not numbers.
  function pivot(pairs, agg) {
    var idx = Object.create(null), groups = [], skipped = 0, rows = 0;
    for (var i = 0; i < pairs.length; i++) {
      var k = String(pairs[i].k).trim(), v = pairs[i].v, blankV = v === "" || v === null || v === undefined;
      if (k === "" && blankV) continue;
      rows++;
      var key = k === "" ? "(blank)" : k, gi = idx[key];
      if (gi === undefined) { if (groups.length >= MAX_GROUPS) return { error: "There are more than " + MAX_GROUPS + " different groups, so no pivot was made. Use a smaller range." }; gi = groups.length; idx[key] = gi; groups.push({ key: key, rows: 0, nums: [] }); }
      groups[gi].rows++;
      if (typeof v === "number" && isFinite(v)) groups[gi].nums.push(v); else if (!blankV) skipped++;
    }
    var out = groups.map(function (g) {
      var n = g.nums, val;
      if (agg === "count") val = g.rows;
      else if (!n.length) val = null;
      else if (agg === "sum") val = n.reduce(function (a, b) { return a + b; }, 0);
      else if (agg === "average") val = n.reduce(function (a, b) { return a + b; }, 0) / n.length;
      else if (agg === "min") val = Math.min.apply(null, n);
      else val = Math.max.apply(null, n);
      return { key: g.key, rows: g.rows, numbers: n.length, value: val === null ? null : +val.toPrecision(12) };
    });
    return { groups: out, skipped: skipped, rows: rows };
  }
  var api = { parseRange: parseRange, makeRule: makeRule, addRule: addRule, removeRule: removeRule, loadRules: loadRules, rulesJson: rulesJson, checkValue: checkValue, makePivot: makePivot, pivot: pivot, MAX_GROUPS: MAX_GROUPS, makeCf: makeCf, addCf: addCf, loadCf: loadCf, cfJson: cfJson, cfColor: cfColor, MAX_RULES: MAX_RULES, loadTabs: loadTabs, addTab: addTab, renameTab: renameTab, deleteTab: deleteTab, setActive: setActive, cleanName: cleanName, MAX_TABS: MAX_TABS, sortRange: sortRange, filterRows: filterRows, toNum: toNum, toCsv: toCsv, clean: clean, History: History, chartPoints: chartPoints, pieSlices: pieSlices, isComposing: isComposing, keyMove: keyMove, copy: copy, same: same };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSheetModel = api;
})(typeof self !== "undefined" ? self : this);
