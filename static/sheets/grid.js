(function () {
  "use strict";
  var root = document.getElementById("sheet-root");
  if (!root || !window.SalowFormula) return;
  var F = SalowFormula, KEY = root.getAttribute("data-store-key"), COLS = 8, ROWS = 20, MAXCELL = 2000, HIST = 100;
  var status = document.getElementById("sheet-status"), bar = document.getElementById("sheet-bar"), sel = document.getElementById("sheet-sel");
  var undoBtn = document.getElementById("sheet-undo"), redoBtn = document.getElementById("sheet-redo");
  var has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  function clean(obj) {  // own keys, valid addresses, bounded strings only
    var n = Object.create(null), skipped = 0, k;
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    for (k in obj) { if (!has(obj, k)) continue; if (typeof obj[k] === "string" && obj[k].length <= MAXCELL && F.parseAddr(k)) n[k] = obj[k]; else skipped++; }
    return { cells: n, count: Object.keys(n).length, skipped: skipped };
  }
  var cells = Object.create(null);
  try { var st = clean(JSON.parse(localStorage.getItem(KEY) || "{}")); if (st) cells = st.cells; } catch (e) { cells = Object.create(null); }
  function copy(c) { var o = Object.create(null); Object.keys(c).forEach(function (k) { o[k] = c[k]; }); return o; }
  function same(a, b) { var ka = Object.keys(a), kb = Object.keys(b); return ka.length === kb.length && ka.every(function (k) { return b[k] === a[k]; }); }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(cells)); status.textContent = "Saved on this device."; } catch (e) { status.textContent = "This browser would not store the sheet."; } }
  var past = [], future = [];
  function buttons() { undoBtn.disabled = !past.length; redoBtn.disabled = !future.length; }
  function change(next, note) {  // every edit, clear and import goes through here so it can be undone
    if (same(cells, next)) return false;
    past.push(copy(cells)); if (past.length > HIST) past.shift(); future = [];
    cells = next; save(); show(); buttons(); if (note) status.textContent = note; return true;
  }
  function undo() { if (!past.length) return; future.push(copy(cells)); cells = past.pop(); save(); show(); buttons(); status.textContent = "Undone."; }
  function redo() { if (!future.length) return; past.push(copy(cells)); cells = future.pop(); save(); show(); buttons(); status.textContent = "Redone."; }
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
  function commit(a, v) { var n = copy(cells); if (v === "") delete n[a]; else n[a] = v; change(n); }
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
    if (k === "Escape") { t.value = orig; bar.value = orig; return; }
    if (k === "Enter") { e.preventDefault(); move(0, e.shiftKey ? -1 : 1); return; }
    if (k === "ArrowDown") { e.preventDefault(); move(0, 1); return; }
    if (k === "ArrowUp") { e.preventDefault(); move(0, -1); return; }
    if (k === "ArrowLeft" && t.selectionStart === 0) { e.preventDefault(); move(-1, 0); return; }
    if (k === "ArrowRight" && t.selectionEnd === t.value.length) { e.preventDefault(); move(1, 0); return; }
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
    if (change(Object.create(null), "Cleared from this device. You can undo this.")) return; status.textContent = "Nothing to clear.";
  });
  function outside(c) { return Object.keys(c).filter(function (k) { return !has(inputs, k); }).length; }
  document.getElementById("sheet-export").addEventListener("click", function () {
    var u = URL.createObjectURL(new Blob([JSON.stringify(cells)], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = "sheet.json"; document.body.appendChild(l); l.click(); l.remove(); URL.revokeObjectURL(u);
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
  document.getElementById("chart-draw").addEventListener("click", function () {
    out.textContent = "";
    var m = /^\s*([A-Za-z]{1,2}[0-9]{1,4})\s*:\s*([A-Za-z]{1,2}[0-9]{1,4})\s*$/.exec(document.getElementById("chart-range").value);
    var p1 = m && F.parseAddr(m[1].toUpperCase()), p2 = m && F.parseAddr(m[2].toUpperCase());
    if (!p1 || !p2) { out.textContent = "Enter a range like A1:A8."; return; }
    var c1 = Math.min(p1.col, p2.col), c2 = Math.max(p1.col, p2.col), r1 = Math.min(p1.row, p2.row), r2 = Math.max(p1.row, p2.row);
    if (c1 !== c2 && r1 !== r2) { out.textContent = "Use one row or one column."; return; }
    if ((c2 - c1 + 1) * (r2 - r1 + 1) > 50) { out.textContent = "A chart takes up to 50 cells."; return; }
    var sh = new F.Sheet(cells), pts = [], skipped = 0, rr, cc;
    for (rr = r1; rr <= r2; rr++) for (cc = c1; cc <= c2; cc++) {
      var ad = String.fromCharCode(64 + cc) + rr, v = sh.get(ad);
      if (typeof v === "number" && isFinite(v)) pts.push({ a: ad, v: v }); else skipped++;
    }
    if (!pts.length) { out.textContent = "No numbers in that range."; return; }
    var W = 560, H = 260, L = 48, B = 30, T = 14, lo = Math.min(0, Math.min.apply(null, pts.map(function (q) { return q.v; }))), hi = Math.max(0, Math.max.apply(null, pts.map(function (q) { return q.v; })));
    if (hi === lo) hi = lo + 1;
    var y = function (v) { return T + (H - T - B) * (hi - v) / (hi - lo); }, bw = (W - L - 10) / pts.length;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": "Bar chart of " + m[1].toUpperCase() + " to " + m[2].toUpperCase() });
    svg.appendChild(el("line", { x1: L, y1: y(0), x2: W - 6, y2: y(0), stroke: "#555" }));
    svg.appendChild(el("text", { x: 4, y: y(hi) + 4, "font-size": 11 }, String(+hi.toPrecision(4))));
    svg.appendChild(el("text", { x: 4, y: y(lo), "font-size": 11 }, String(+lo.toPrecision(4))));
    pts.forEach(function (q, i) {
      var x = L + i * bw + bw * 0.15, top = Math.min(y(q.v), y(0)), h = Math.abs(y(q.v) - y(0));
      var bar1 = el("rect", { x: x, y: top, width: bw * 0.7, height: Math.max(h, 1), fill: "#7a2e22" }); bar1.appendChild(el("title", {}, q.a + ": " + q.v));
      svg.appendChild(bar1); svg.appendChild(el("text", { x: x + bw * 0.35, y: H - 12, "font-size": 10, "text-anchor": "middle" }, q.a));
    });
    out.appendChild(svg);
    var note = document.createElement("p"); note.className = "cal-meta";
    note.textContent = pts.length + " values charted" + (skipped ? ", " + skipped + " cells skipped (blank, text or error)" : "") + ". The chart is drawn from the current sheet and is not saved.";
    out.appendChild(note);
  });
  show(); buttons();
})();
