(function () {
  "use strict";
  var root = document.getElementById("sheet-root");
  if (!root || !window.SalowFormula) return;
  var F = SalowFormula, KEY = root.getAttribute("data-store-key"), COLS = 8, ROWS = 20, MAXCELL = 2000, HIST = 100;
  var status = document.getElementById("sheet-status"), bar = document.getElementById("sheet-bar"), sel = document.getElementById("sheet-sel");
  var undoBtn = document.getElementById("sheet-undo"), redoBtn = document.getElementById("sheet-redo");
  var M = SalowSheetModel, has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  var clean = function (o) { return M.clean(o, F.parseAddr, MAXCELL); };
  // Tabs: the first tab keeps the original storage key, so a sheet saved before tabs existed opens as "Sheet 1".
  var tabsRaw = null, tabsRead = true; try { tabsRaw = localStorage.getItem(KEY + ".tabs"); } catch (e) { tabsRead = false; }
  var tl = M.loadTabs(tabsRaw), meta = tl.meta, tabsLocked = tl.unreadable || !tabsRead, hist = Object.create(null), lastDeleted = null;
  function tabKey(id) { return id === "t0" ? KEY : KEY + ".tab." + id; }
  function makeHist(id) {
    var init = Object.create(null);
    try { var st0 = clean(JSON.parse(localStorage.getItem(tabKey(id)) || "{}")); if (st0) init = st0.cells; } catch (e) { init = Object.create(null); }
    return new M.History(init, { save: function (c) { try { localStorage.setItem(tabKey(id), JSON.stringify(c)); return true; } catch (e) { return false; } } }, HIST);
  }
  function histFor(id) { if (!hist[id]) hist[id] = makeHist(id); return hist[id]; }
  var H = histFor(meta.active);
  var cells = H.cells, chartRange = null;
  function sync() { cells = H.cells; show(); buttons(); drawChart(); applyFilter(); status.textContent = H.note; }
  function buttons() { undoBtn.disabled = !H.past.length; redoBtn.disabled = !H.future.length; }
  function change(next, note) { if (!H.change(next, note)) return false; sync(); return true; }
  function undo() { if (H.undo()) sync(); }
  function redo() { if (H.redo()) sync(); }
  undoBtn.addEventListener("click", undo); redoBtn.addEventListener("click", redo);

  var table = document.createElement("table"), inputs = Object.create(null), cur = null, orig = "";
  var head = table.insertRow(); head.insertCell().textContent = "";
  for (var c = 1; c <= COLS; c++) { var th = document.createElement("th"); th.textContent = String.fromCharCode(64 + c); head.appendChild(th); }
  for (var r = 1; r <= ROWS; r++) {
    var tr = table.insertRow(), rh = document.createElement("th"); rh.textContent = r; tr.appendChild(rh);
    for (c = 1; c <= COLS; c++) {
      var a = String.fromCharCode(64 + c) + r, inp = document.createElement("input");
      inp.type = "text"; inp.maxLength = MAXCELL; inp.setAttribute("aria-label", "Cell " + a); inp.dataset.addr = a;
      inputs[a] = inp; tr.insertCell().appendChild(inp);
    }
  }
  root.appendChild(table);
  function show() {
    var sh = new F.Sheet(cells);
    Object.keys(inputs).forEach(function (a) {
      if (document.activeElement === inputs[a]) return;
      var v = sh.get(a); inputs[a].value = F.isErr(v) ? v.code : String(v); inputs[a].title = cells[a] || "";
    });
  }
  function commit(a, v) { var n = M.copy(cells); if (v === "") delete n[a]; else n[a] = v; change(n); }
  function move(dc, dr) {
    if (!cur) return; var p = F.parseAddr(cur), nc = Math.min(COLS, Math.max(1, p.col + dc)), nr = Math.min(ROWS, Math.max(1, p.row + dr));
    while (dr && rowHidden(nr) && nr + (dr > 0 ? 1 : -1) >= 1 && nr + (dr > 0 ? 1 : -1) <= ROWS) nr += dr > 0 ? 1 : -1;
    if (rowHidden(nr)) return;
    inputs[String.fromCharCode(64 + nc) + nr].focus();
  }
  table.addEventListener("focusin", function (e) {
    var a = e.target.dataset && e.target.dataset.addr; if (!a) return;
    cur = a; orig = cells[a] || ""; e.target.value = orig; bar.value = orig; sel.textContent = a; e.target.select();
  });
  table.addEventListener("focusout", function (e) { var a = e.target.dataset && e.target.dataset.addr; if (a) commit(a, e.target.value); show(); });
  table.addEventListener("input", function (e) { if (e.target.dataset && e.target.dataset.addr === cur) bar.value = e.target.value; });
  table.addEventListener("keydown", function (e) {
    var t = e.target, k = e.key;
    if (M.isComposing(e)) return;  // IME composition owns every key, including arrows and Esc
    if (k === "Escape") { t.value = orig; bar.value = orig; return; }
    var mv = M.keyMove(k, e.shiftKey, t.selectionStart === 0, t.selectionEnd === t.value.length);
    if (mv) { e.preventDefault(); move(mv[0], mv[1]); }
  });
  document.addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    var k = e.key.toLowerCase(), t = e.target, inGrid = t.dataset && t.dataset.addr, free = t === document.body || inGrid;
    if (!free || (k !== "z" && k !== "y")) return;
    e.preventDefault(); if (inGrid) t.blur();
    if (k === "y" || e.shiftKey) redo(); else undo();
  });
  bar.addEventListener("change", function () { if (cur) { commit(cur, bar.value); show(); } });
  bar.addEventListener("keydown", function (e) { if (e.key === "Escape") bar.value = cells[cur] || ""; });

  document.getElementById("sheet-clear").addEventListener("click", function () {
    if (!change(Object.create(null), "Sheet cleared. Undo works until you reload this page.")) { status.textContent = "Nothing to clear."; return; }
    if (!H.saveFailed) status.textContent += " The stored copy is now empty; after a reload the cleared content cannot be brought back.";
  });
  function outside(c) { return Object.keys(c).filter(function (k) { return !has(inputs, k); }).length; }
  document.getElementById("sheet-export").addEventListener("click", function () {
    var u = URL.createObjectURL(new Blob([JSON.stringify(cells)], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = "sheet.json"; document.body.appendChild(l); l.click(); l.remove(); URL.revokeObjectURL(u);
  });
  document.getElementById("sheet-csv").addEventListener("click", function () {
    var sh = new F.Sheet(cells), r = M.toCsv(cells, F.parseAddr, function (a) { return sh.get(a); }, F.isErr);
    if (!r) { status.textContent = "The sheet is empty, so nothing was downloaded."; return; }
    var u = URL.createObjectURL(new Blob([r.text], { type: "text/csv;charset=utf-8" })), l = document.createElement("a");
    l.href = u; l.download = "sheet.csv"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
    status.textContent = "Downloaded sheet.csv: " + r.rows + " rows by " + r.cols + " columns, with the values shown (not formulas)." + (filt ? " It includes the rows the filter hides." : "") + " The file is UTF-8 without a byte-order mark, so some programs may need you to pick UTF-8 when importing. It has not been tried in a spreadsheet program." + (r.guarded ? " " + r.guarded + " text cell(s) looked like formulas (they start with = + - or @), so a leading apostrophe was added. The apostrophe is part of the text in the file, so it shows up when you open it. That lowers the chance a spreadsheet program runs them, but it is not a guarantee for every program." : "");
  });
  document.getElementById("sheet-import").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    if (f.size > 1000000) { status.textContent = "That file is too large. Your sheet was not changed."; return; }
    var rd = new FileReader();
    rd.onload = function () {
      var p = null; try { p = clean(JSON.parse(String(rd.result))); } catch (x) { p = null; }
      if (!p || !p.count) { status.textContent = "That file has no usable cells. Your sheet was not changed."; return; }
      if (Object.keys(cells).length && !window.confirm("Replace the " + Object.keys(cells).length + " cells in this tab with the " + p.count + " cells in this file? You can undo it afterwards.")) { status.textContent = "Import cancelled. Your sheet was not changed."; return; }
      var o = outside(p.cells);
      change(p.cells, "Imported " + p.count + " cells" + (p.skipped ? ", skipped " + p.skipped + " entries that were not valid cells" : "") + (o ? ". " + o + " cells are outside the visible grid (A1 to H20): they are kept, used by formulas and exported, but not shown" : "") + ". You can undo this import.");
    };
    rd.readAsText(f); e.target.value = "";
  });

  // Chart: bars from one row or column of the sheet, computed here from the same formula engine.
  var out = document.getElementById("chart-out"), NS = "http://www.w3.org/2000/svg";
  function el(n, at, tx) { var x = document.createElementNS(NS, n); Object.keys(at || {}).forEach(function (k) { x.setAttribute(k, at[k]); }); if (tx !== undefined) x.textContent = tx; return x; }
  var outP = null;
  function drawChart() {
    out.textContent = ""; if (!chartRange) return;
    var rg = chartRange, sh = new F.Sheet(cells), vals = [], rr, cc;
    for (rr = rg.r1; rr <= rg.r2; rr++) for (cc = rg.c1; cc <= rg.c2; cc++) { var ad = String.fromCharCode(64 + cc) + rr; vals.push({ a: ad, v: sh.get(ad) }); }
    var ch = M.chartPoints(vals);
    if (!ch) { out.textContent = "No numbers in " + rg.label + " right now."; return; }
    var W = 560, H2 = 260, L = 48, B = 30, T = 14, lo = ch.lo, hi = ch.hi;
    var y = function (v) { return T + (H2 - T - B) * (hi - v) / (hi - lo); }, bw = (W - L - 10) / ch.bars.length;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H2, role: "img", "aria-label": "Bar chart of " + rg.label });
    svg.appendChild(el("line", { x1: L, y1: y(0), x2: W - 6, y2: y(0), stroke: "#555" }));
    svg.appendChild(el("text", { x: 4, y: y(hi) + 4, "font-size": 11 }, String(+hi.toPrecision(4))));
    svg.appendChild(el("text", { x: 4, y: y(lo), "font-size": 11 }, String(+lo.toPrecision(4))));
    ch.bars.forEach(function (q, i) {
      var x = L + i * bw + bw * 0.15, g = el("g", {});
      if (q.bar) { var rc = el("rect", { x: x, y: Math.min(y(q.v), y(0)), width: bw * 0.7, height: Math.abs(y(q.v) - y(0)), fill: "#7a2e22" }); rc.appendChild(el("title", {}, q.a + ": " + q.v)); g.appendChild(rc); }
      else { var zt = el("text", { x: x + bw * 0.35, y: y(0) - 4, "font-size": 10, "text-anchor": "middle" }, "0"); zt.appendChild(el("title", {}, q.a + ": 0")); g.appendChild(zt); }
      g.appendChild(el("text", { x: x + bw * 0.35, y: H2 - 12, "font-size": 10, "text-anchor": "middle" }, q.a));
      svg.appendChild(g);
    });
    out.appendChild(svg);
    var note = document.createElement("p"); note.className = "cal-meta";
    note.textContent = ch.pts.length + " values charted from " + rg.label + (ch.skipped ? ", " + ch.skipped + " cells skipped (blank, text or error)" : "") + ". The chart follows the sheet as you edit and is not saved.";
    out.appendChild(note);
  }
  document.getElementById("chart-draw").addEventListener("click", function () {
    var m = /^\s*([A-Za-z]{1,2}[0-9]{1,4})\s*:\s*([A-Za-z]{1,2}[0-9]{1,4})\s*$/.exec(document.getElementById("chart-range").value);
    var p1 = m && F.parseAddr(m[1].toUpperCase()), p2 = m && F.parseAddr(m[2].toUpperCase());
    chartRange = null; out.textContent = "";
    if (!p1 || !p2) { out.textContent = "Enter a range like A1:A8."; return; }
    var c1 = Math.min(p1.col, p2.col), c2 = Math.max(p1.col, p2.col), r1 = Math.min(p1.row, p2.row), r2 = Math.max(p1.row, p2.row);
    if (c1 !== c2 && r1 !== r2) { out.textContent = "Use one row or one column."; return; }
    if ((c2 - c1 + 1) * (r2 - r1 + 1) > 50) { out.textContent = "A chart takes up to 50 cells."; return; }
    chartRange = { c1: c1, c2: c2, r1: r1, r2: r2, label: m[1].toUpperCase() + ":" + m[2].toUpperCase() };
    drawChart();
  });
  // ---- Sort and filter (see model.js) ----
  var $ = function (id) { return document.getElementById(id); }, fstat = $("filter-status"), filt = null;
  function parseRange(str) {
    var m = /^\s*([A-Za-z]{1,2}[0-9]{1,4})\s*:\s*([A-Za-z]{1,2}[0-9]{1,4})\s*$/.exec(str), p1 = m && F.parseAddr(m[1].toUpperCase()), p2 = m && F.parseAddr(m[2].toUpperCase());
    if (!p1 || !p2) return null;
    return { c1: Math.min(p1.col, p2.col), c2: Math.max(p1.col, p2.col), r1: Math.min(p1.row, p2.row), r2: Math.max(p1.row, p2.row) };
  }
  function colNum(t) { var m = /^\s*([A-Za-z])\s*$/.exec(t); return m ? m[1].toUpperCase().charCodeAt(0) - 64 : 0; }
  function rowHidden(r) { return !!(table.rows[r] && table.rows[r].hidden); }
  function showAllRows() { for (var i = 1; i <= ROWS; i++) table.rows[i].hidden = false; }
  // the filter covers rows down to the last one that holds anything; empty rows below stay visible
  function lastRow() { var m = 0; Object.keys(cells).forEach(function (k) { var p = F.parseAddr(k); if (p && p.row <= ROWS && p.row > m) m = p.row; }); return m; }
  function applyFilter() {
    showAllRows(); $("filter-clear").disabled = !filt;
    if (!filt) { fstat.textContent = ""; return; }
    var last = lastRow(), sh = new F.Sheet(cells), res = M.filterRows(function (a) { return sh.get(a); }, F.isErr, 1, Math.max(1, last), filt.col, filt.op, filt.val, filt.header);
    if (res.error) { fstat.textContent = res.error; return; }
    for (var i = 1; i <= last; i++) table.rows[i].hidden = !res.vis[i];
    fstat.textContent = "Showing " + res.shown + " of " + res.total + " rows. Hidden rows are only hidden on screen: nothing is deleted and the filter is not saved." + (res.shown === 0 ? " No rows match." : "");
    var ae = document.activeElement, aeRow = ae && ae.dataset && ae.dataset.addr ? F.parseAddr(ae.dataset.addr).row : 0, curRow = cur ? F.parseAddr(cur).row : 0;
    if ((aeRow && rowHidden(aeRow)) || (ae === document.body && curRow && rowHidden(curRow))) {
      var hr = aeRow || curRow, best = 0, d;
      for (d = 1; d <= ROWS && !best; d++) { if (hr - d >= 1 && !rowHidden(hr - d)) best = hr - d; else if (hr + d <= ROWS && !rowHidden(hr + d)) best = hr + d; }
      var colL = cur ? cur.replace(/[0-9]+$/, "") : "A";
      if (best) { inputs[colL + best].focus(); fstat.textContent = "Row " + hr + " no longer matches the filter and is hidden. Focus moved to row " + best + ". " + fstat.textContent; }
      else { $("filter-clear").focus(); fstat.textContent = "Row " + hr + " no longer matches the filter and is hidden, and no row matches. " + fstat.textContent; }
    }
  }
  $("filter-go").addEventListener("click", function () {
    var col = colNum($("filter-col").value);
    if (!col || col > COLS) { fstat.textContent = "Enter a column from A to " + String.fromCharCode(64 + COLS) + "."; return; }
    var f = { col: col, op: $("filter-op").value, val: $("filter-val").value, header: $("filter-header").checked };
    var sh = new F.Sheet(cells), test = M.filterRows(function (a) { return sh.get(a); }, F.isErr, 1, Math.max(1, lastRow()), f.col, f.op, f.val, f.header);
    if (test.error) { fstat.textContent = test.error; return; }
    filt = f; applyFilter();
  });
  $("filter-clear").addEventListener("click", function () { filt = null; applyFilter(); fstat.textContent = "All rows are showing."; });
  function outsideFormulas(rg) {
    var n = 0; Object.keys(cells).forEach(function (k) { var p = F.parseAddr(k), v = cells[k]; if (!p || typeof v !== "string" || v[0] !== "=") return; if (p.col >= rg.c1 && p.col <= rg.c2 && p.row >= rg.r1 && p.row <= rg.r2) return; n++; });
    return n ? " " + n + " formula" + (n === 1 ? "" : "s") + " outside the range keep their addresses, so any that refer to the sorted cells may now show different results." : "";
  }
  $("sort-go").addEventListener("click", function () {
    var rg = parseRange($("sort-range").value), key = colNum($("sort-col").value);
    if (!rg) { status.textContent = "Enter a range like A1:C10."; return; }
    if ((rg.c2 - rg.c1 + 1) * (rg.r2 - rg.r1 + 1) > 2000 || rg.c2 > 26 || rg.r2 > 1000) { status.textContent = "A sort range can hold up to 2000 cells."; return; }
    var r = M.sortRange(cells, F.parseAddr, rg, key, $("sort-dir").value === "desc", $("sort-header").checked);
    if (r.error) { status.textContent = r.error; return; }
    var note = "Sorted " + r.rows + " rows by column " + String.fromCharCode(64 + key) + "; " + r.moved + " moved. Undo restores the old order." + (filt ? " The filter is still on." : "") + outsideFormulas(rg);
    if (!change(r.cells, note)) status.textContent = "Already in that order. Nothing changed.";
  });
  // ---- Tabs UI (model in model.js). Export, CSV, chart, sort and filter work on the tab you are on. ----
  var tabBar = $("sheet-tabs"), tabName = $("tab-name"), tabMsg = $("tab-status");
  function saveMeta(m) { try { localStorage.setItem(KEY + ".tabs", JSON.stringify(m)); return true; } catch (e) { return false; } }
  function commitMeta(next) {
    if (tabsLocked) { tabMsg.textContent = "The stored tab list could not be read, so tab changes are off. Your cells were not touched."; return false; }
    if (!saveMeta(next)) { tabMsg.textContent = "This browser would not store the tab list, so nothing was changed."; return false; }
    meta = next; return true;
  }
  function renderTabs() {
    tabBar.textContent = "";
    meta.tabs.forEach(function (t) {
      var b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = t.name; b.dataset.tab = t.id;
      b.setAttribute("aria-pressed", t.id === meta.active ? "true" : "false"); if (t.id === meta.active) b.setAttribute("aria-current", "true");
      b.addEventListener("click", function () { switchTo(t.id, b); }); tabBar.appendChild(b);
    });
    var ct = meta.tabs.filter(function (t) { return t.id === meta.active; })[0]; if (document.activeElement !== tabName) tabName.value = ct.name;
    $("tab-add").disabled = tabsLocked || meta.tabs.length >= M.MAX_TABS; $("tab-rename").disabled = tabsLocked; $("tab-del").disabled = tabsLocked || meta.tabs.length <= 1;
    $("tab-restore").disabled = tabsLocked || !lastDeleted || meta.tabs.length >= M.MAX_TABS;
  }
  function switchTo(id, keep) {
    var next = M.setActive(meta, id); if (!next) return;
    if (!commitMeta(next)) return;
    H = histFor(id); chartRange = null; out.textContent = ""; cur = null; bar.value = ""; sel.textContent = "";
    var hadFilter = !!filt; filt = null; sync(); renderTabs();
    tabMsg.textContent = "Now on " + meta.tabs.filter(function (t) { return t.id === id; })[0].name + ". Undo history is kept for each tab until you reload." + (hadFilter ? " The filter was cleared." : "");
    var again = keep && tabBar.querySelector('button[data-tab="' + id + '"]'); if (again) again.focus();
  }
  $("tab-add").addEventListener("click", function () {
    var next = M.addTab(meta); if (!next) { tabMsg.textContent = "A file holds up to " + M.MAX_TABS + " tabs."; return; }
    if (!commitMeta(next)) return;
    H = histFor(meta.active); chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = "Added a tab. Formulas read only cells on their own tab."; tabName.focus(); tabName.select();
  });
  $("tab-rename").addEventListener("click", function () {
    var r = M.renameTab(meta, meta.active, tabName.value); if (r.error) { tabMsg.textContent = r.error; return; }
    if (!commitMeta(r.meta)) return; renderTabs(); tabMsg.textContent = "Renamed to " + r.name + "." + (r.cut ? " The name was shortened to 30 characters." : "");
  });
  tabName.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); $("tab-rename").click(); } });
  $("tab-del").addEventListener("click", function () {
    var id = meta.active, n = Object.keys(H.cells).length, nm = meta.tabs.filter(function (t) { return t.id === id; })[0].name;
    var r = M.deleteTab(meta, id); if (r.error) { tabMsg.textContent = r.error; return; }
    if (n && !window.confirm("Delete the tab " + nm + " and its " + n + " cells? You can bring it back with Restore deleted tab until you reload this page. After a reload it cannot be brought back.")) { tabMsg.textContent = "Delete cancelled."; return; }
    var saved = M.copy(H.cells);
    if (!commitMeta(r.meta)) return;
    try { localStorage.removeItem(tabKey(id)); } catch (e) { /* the tab is already out of the list; the stored cells are unreachable */ }
    delete hist[id]; lastDeleted = { name: nm, cells: saved };
    H = histFor(meta.active); chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = "Deleted the tab " + nm + ". Restore deleted tab brings it back until you reload; after a reload it cannot be brought back.";
  });
  $("tab-restore").addEventListener("click", function () {
    if (!lastDeleted) return;
    var next = M.addTab(meta); if (!next) { tabMsg.textContent = "A file holds up to " + M.MAX_TABS + " tabs."; return; }
    var nm = lastDeleted.name, rn = M.renameTab(next, next.active, nm); if (!rn.error) next = rn.meta;
    if (!commitMeta(next)) return;
    H = histFor(meta.active); H.change(lastDeleted.cells, "Restored the deleted tab."); lastDeleted = null;
    chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = "Restored the tab" + (rn.error ? " under a new name, because " + nm + " is in use." : " " + nm + ".");
  });
  if (tabsLocked) tabMsg.textContent = "The stored tab list could not be read, so tab changes are off. Your cells were not touched.";
  renderTabs();
  show(); buttons(); status.textContent = "";
})();
