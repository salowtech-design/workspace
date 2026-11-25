(function () {
  "use strict";
  var root = document.getElementById("sheet-root");
  if (!root || !window.SalowFormula) return;
  var F = SalowFormula, KEY = root.getAttribute("data-store-key"), COLS = 8, ROWS = 20, MAXCELL = 2000, HIST = 100;
  var status = document.getElementById("sheet-status"), bar = document.getElementById("sheet-bar"), sel = document.getElementById("sheet-sel");
  var undoBtn = document.getElementById("sheet-undo"), redoBtn = document.getElementById("sheet-redo");
  var M = SalowSheetModel, has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  var clean = function (o) { return M.clean(o, F.parseAddr, MAXCELL); };
  var initial = Object.create(null);
  try { var st0 = clean(JSON.parse(localStorage.getItem(KEY) || "{}")); if (st0) initial = st0.cells; } catch (e) { initial = Object.create(null); }
  var H = new M.History(initial, { save: function (c) { try { localStorage.setItem(KEY, JSON.stringify(c)); return true; } catch (e) { return false; } } }, HIST);
  var cells = H.cells, chartRange = null;
  function sync() { cells = H.cells; show(); buttons(); drawChart(); status.textContent = H.note; }
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
    status.textContent = "Downloaded sheet.csv: " + r.rows + " rows by " + r.cols + " columns, with the values shown (not formulas)." + (r.guarded ? " " + r.guarded + " text cell(s) looked like formulas (they start with = + - or @), so a leading apostrophe was added. That lowers the chance a spreadsheet program runs them, but it is not a guarantee for every program." : "");
  });
  document.getElementById("sheet-import").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    if (f.size > 1000000) { status.textContent = "That file is too large. Your sheet was not changed."; return; }
    var rd = new FileReader();
    rd.onload = function () {
      var p = null; try { p = clean(JSON.parse(String(rd.result))); } catch (x) { p = null; }
      if (!p || !p.count) { status.textContent = "That file has no usable cells. Your sheet was not changed."; return; }
      if (Object.keys(cells).length && !window.confirm("Replace the " + Object.keys(cells).length + " cells in this sheet with the " + p.count + " cells in this file? You can undo it afterwards.")) { status.textContent = "Import cancelled. Your sheet was not changed."; return; }
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
  show(); buttons(); status.textContent = "";
})();
