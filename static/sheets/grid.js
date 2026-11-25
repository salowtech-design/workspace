(function () {
  "use strict";
  var root = document.getElementById("sheet-root");
  if (!root || !window.SalowFormula) return;
  var KEY = root.getAttribute("data-store-key"), COLS = 8, ROWS = 20, MAXCELL = 2000;
  var status = document.getElementById("sheet-status");
  var cells = Object.create(null);
  try { var s = JSON.parse(localStorage.getItem(KEY) || "{}"); if (s && typeof s === "object") Object.keys(s).forEach(function (k) { if (typeof s[k] === "string" && s[k].length <= MAXCELL && SalowFormula.parseAddr(k)) cells[k] = s[k]; }); } catch (e) { cells = Object.create(null); }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(cells)); status.textContent = "Saved on this device."; } catch (e) { status.textContent = "This browser would not store the sheet."; } }
  var table = document.createElement("table"), inputs = Object.create(null);
  var head = table.insertRow(); head.insertCell().textContent = "";
  for (var c = 1; c <= COLS; c++) { var th = document.createElement("th"); th.textContent = String.fromCharCode(64 + c); head.appendChild(th); }
  for (var r = 1; r <= ROWS; r++) {
    var tr = table.insertRow(); var rh = document.createElement("th"); rh.textContent = r; tr.appendChild(rh);
    for (c = 1; c <= COLS; c++) {
      var a = String.fromCharCode(64 + c) + r, inp = document.createElement("input");
      inp.type = "text"; inp.maxLength = MAXCELL; inp.setAttribute("aria-label", "Cell " + a); inp.dataset.addr = a; 
      inputs[a] = inp; tr.insertCell().appendChild(inp);
    }
  }
  root.appendChild(table);
  function show() {
    var sh = new SalowFormula.Sheet(cells);
    Object.keys(inputs).forEach(function (a) {
      if (document.activeElement === inputs[a]) return;
      var v = sh.get(a); inputs[a].value = SalowFormula.isErr(v) ? v.code : String(v);
      inputs[a].title = cells[a] || "";
    });
  }
  var bar = document.getElementById("sheet-bar"), sel = document.getElementById("sheet-sel"), cur = null;
  function commit(a, v) { if (v === "") delete cells[a]; else cells[a] = v; save(); show(); }
  bar.addEventListener("change", function () { if (cur) commit(cur, bar.value); });
  var undo = null, undoBtn = document.getElementById("sheet-undo");
  function snapshot() { var o = Object.create(null); Object.keys(cells).forEach(function (k) { o[k] = cells[k]; }); return o; }
  function parseImport(obj) {  // returns {cells, kept, skipped} or null; nothing is changed here
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    var n = Object.create(null), skipped = 0, k;
    for (k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
      if (typeof obj[k] === "string" && obj[k].length <= MAXCELL && SalowFormula.parseAddr(k)) n[k] = obj[k]; else skipped++;
    }
    var count = Object.keys(n).length;
    return count ? { cells: n, count: count, skipped: skipped } : null;
  }
  function outside(c) { return Object.keys(c).filter(function (k) { return !Object.prototype.hasOwnProperty.call(inputs, k); }).length; }
  function apply(p) {
    undo = snapshot(); undoBtn.hidden = false;
    cells = p.cells; save(); show();
    var o = outside(cells);
    status.textContent = "Imported " + p.count + " cells" + (p.skipped ? ", skipped " + p.skipped + " entries that were not valid cells" : "") + (o ? ". " + o + " cells are outside the visible grid (A1 to H20): they are kept and used by formulas, and exported, but not shown" : "") + ". You can undo this import.";
  }
  undoBtn.addEventListener("click", function () { if (!undo) return; cells = undo; undo = null; undoBtn.hidden = true; save(); show(); status.textContent = "Import undone."; });
  document.getElementById("sheet-export").addEventListener("click", function () {
    var u = URL.createObjectURL(new Blob([JSON.stringify(cells)], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = "sheet.json"; document.body.appendChild(l); l.click(); l.remove(); URL.revokeObjectURL(u);
  });
  document.getElementById("sheet-import").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    if (f.size > 1000000) { status.textContent = "That file is too large."; return; }
    var rd = new FileReader();
    rd.onload = function () {
      var p = null; try { p = parseImport(JSON.parse(String(rd.result))); } catch (x) { p = null; }
      if (!p) { status.textContent = "That file has no usable cells. Your sheet was not changed."; return; }
      if (Object.keys(cells).length && !window.confirm("Replace the " + Object.keys(cells).length + " cells in this sheet with the " + p.count + " cells in this file? You can undo it afterwards.")) { status.textContent = "Import cancelled. Your sheet was not changed."; return; }
      apply(p);
    };
    rd.readAsText(f); e.target.value = "";
  });
  table.addEventListener("focusin", function (e) { var a = e.target.dataset && e.target.dataset.addr; if (a) { e.target.value = cells[a] || ""; cur = a; bar.value = cells[a] || ""; sel.textContent = a; } });
  table.addEventListener("focusout", function (e) {
    var a = e.target.dataset && e.target.dataset.addr; if (!a) return;
    commit(a, e.target.value);
  });
  document.getElementById("sheet-clear").addEventListener("click", function () { cells = Object.create(null); try { localStorage.removeItem(KEY); } catch (e) {} show(); status.textContent = "Cleared from this device."; });
  show();
})();
