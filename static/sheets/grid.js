(function () {
  "use strict";
  var root = document.getElementById("sheet-root");
  if (!root || !window.SalowFormula) return;
  var F = SalowFormula, KEY = root.getAttribute("data-store-key"), COLS = 26, ROWS = 100, MAXCELL = 2000, HIST = 100;
  var status = document.getElementById("sheet-status"), bar = document.getElementById("sheet-bar"), sel = document.getElementById("sheet-sel");
  var undoBtn = document.getElementById("sheet-undo"), redoBtn = document.getElementById("sheet-redo");
  var M = SalowSheetModel, has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  var clean = function (o) { return M.clean(o, F.parseAddr, MAXCELL); };
  // Tabs: the first tab keeps the original storage key, so a sheet saved before tabs existed opens as "Sheet 1".
  var tabsRaw = null, tabsRead = true; try { tabsRaw = localStorage.getItem(KEY + ".tabs"); } catch (e) { tabsRead = false; }
  var tl = M.loadTabs(tabsRaw), meta = tl.meta, tabsLocked = tl.unreadable || !tabsRead, hist = Object.create(null), lastDeleted = null;
  function tabKey(id) { return id === "t0" ? KEY : KEY + ".tab." + id; }
  var unreadableTabs = Object.create(null), rawTabs = Object.create(null);
  function makeHist(id) {
    var init = Object.create(null), raw = null, bad = false;
    try { raw = localStorage.getItem(tabKey(id)); } catch (e) { bad = true; }
    if (raw !== null && !bad) { try { var st0 = clean(JSON.parse(raw)); if (st0) init = st0.cells; else bad = true; } catch (e) { bad = true; } }
    var stale = false;
    var h = new M.History(init, { save: function (c) {
      // Do not write cells for a tab that another window deleted or that is no longer in the stored list.
      var lr = null; try { lr = M.loadTabs(localStorage.getItem(KEY + ".tabs")); } catch (e) { lr = null; }
      if (lr && !lr.unreadable && !lr.meta.tabs.some(function (t) { return t.id === id; })) { stale = true; return false; }
      stale = false;
      try { localStorage.setItem(tabKey(id), JSON.stringify(c)); return true; } catch (e) { return false; }
    } }, HIST);
    var basePersist = h.persist;
    h.persist = function (note) {
      var ok = basePersist(note);
      if (stale) h.note = "This tab was deleted or changed in another window, so this edit was not saved. Reload this page to see the stored tabs.";
      return ok;
    };
    if (raw !== null && bad) rawTabs[id] = raw;
    if (bad) {
      // The stored cells are kept exactly as they are: this tab opens empty and never writes over them.
      unreadableTabs[id] = true;
      h.note = "The stored cells for this tab could not be read, so they were left untouched. Edits here are on screen only and are lost on reload.";
      h.persist = function (note) { h.saveFailed = true; h.note = "The stored cells for this tab could not be read and were left untouched, so these changes are on screen only and will be lost on reload." + (note ? " " + note : ""); return false; };
    }
    return h;
  }
  function histFor(id) { if (!hist[id]) hist[id] = makeHist(id); return hist[id]; }
  var H = histFor(meta.active);
  var cells = H.cells, chartRange = null;
  function $(id) { return document.getElementById(id); }
  // Data validation rules live in their own key per tab, so they never touch the cells. Damaged rules are kept as they are.
  var rules = [], rulesLocked = false, rulesRaw = null;
  var cfRules = [], cfLocked = false, cfRaw = null;
  function cfKey(id) { return tabKey(id) + ".cf"; }
  function reloadCf() {
    var raw = null, ok = true; try { raw = localStorage.getItem(cfKey(meta.active)); } catch (e) { ok = false; }
    var r = ok ? M.loadCf(raw, F.parseAddr) : { rules: [], unreadable: true };
    cfRules = r.rules; cfLocked = r.unreadable; cfRaw = ok ? raw : null; if (typeof renderCf === "function") renderCf();
  }
  function rulesKey(id) { return tabKey(id) + ".rules"; }
  function reloadRules() {
    var raw = null, ok = true; try { raw = localStorage.getItem(rulesKey(meta.active)); } catch (e) { ok = false; }
    var r = ok ? M.loadRules(raw, F.parseAddr) : { rules: [], unreadable: true };
    rules = r.rules; rulesLocked = r.unreadable; rulesRaw = ok ? raw : null; if (typeof renderRules === "function") renderRules();
  }
  function sync() { cells = H.cells; reloadRules(); reloadCf(); if (typeof clearPivot === "function") clearPivot(); show(); buttons(); drawChart(); applyFilter(); status.textContent = H.note; }
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
      var sv = sh.get(a), shown = F.isErr(sv) ? sv.code : String(sv), col = cfLocked ? null : M.cfColor(cfRules, a, shown, F.parseAddr);
      if (col) inputs[a].dataset.cf = col; else delete inputs[a].dataset.cf;
      if (document.activeElement === inputs[a]) return;
      var v = sv; inputs[a].value = shown; inputs[a].title = (cells[a] || "") + (col ? (cells[a] ? " " : "") + "(highlighted " + col + " by a colour rule)" : "");
    });
  }
  function commit(a, v) {
    var why = rulesLocked ? null : M.checkValue(rules, a, v, F.parseAddr);
    if (why) { status.textContent = why + " The edit was not applied."; return; }
    var n = M.copy(cells); if (v === "") delete n[a]; else n[a] = v; change(n); }
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
    var impTab = meta.active, impH = H;
    if (f.size > 1000000) { status.textContent = "That file is too large. Your sheet was not changed."; return; }
    var rd = new FileReader();
    rd.onload = function () {
      if (H !== impH || meta.active !== impTab) { status.textContent = "You switched tabs while the file was loading, so the import was cancelled. Nothing was changed."; return; }
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
  var outP = null, chartKind = "bar";
  function drawChart() {
    out.textContent = ""; if (!chartRange) return;
    var rg = chartRange, sh = new F.Sheet(cells), vals = [], rr, cc;
    for (rr = rg.r1; rr <= rg.r2; rr++) for (cc = rg.c1; cc <= rg.c2; cc++) { var ad = String.fromCharCode(64 + cc) + rr; vals.push({ a: ad, v: sh.get(ad) }); }
    var ch = M.chartPoints(vals);
    if (!ch) { out.textContent = "No numbers in " + rg.label + " right now."; return; }
    var W = 560, H2 = 260, L = 48, B = 30, T = 14, lo = ch.lo, hi = ch.hi, kind = chartKind;
    var y = function (v) { return T + (H2 - T - B) * (hi - v) / (hi - lo); }, bw = (W - L - 10) / ch.bars.length;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H2, role: "img", "aria-label": (kind === "line" ? "Line" : kind === "pie" ? "Pie" : "Bar") + " chart of " + rg.label });
    var pie = kind === "pie" ? M.pieSlices(ch.pts) : null, legend = null;
    if (kind === "pie" && !pie) { out.textContent = ch.pts.some(function (q) { return q.v > 0; }) ? "The values above zero in " + rg.label + " add up to more than a pie chart can show, so no pie was drawn." : "A pie chart needs at least one value above zero in " + rg.label + "."; return; }
    if (pie) {
      var cx = 130, cy = 130, R = 110, ang = -Math.PI / 2, pal = ["#7a2e22", "#c58b3c", "#4d7a5a", "#3e6f9e", "#8a5a9e", "#b5533c", "#6b6b6b", "#a89a3c"];
      legend = document.createElement("ul"); legend.className = "cal-meta";
      pie.slices.forEach(function (q, i) {
        var a2 = ang + q.frac * 2 * Math.PI, col = pal[i % pal.length], sl;
        if (pie.slices.length === 1) sl = el("circle", { cx: cx, cy: cy, r: R, fill: col });
        else sl = el("path", { d: "M" + cx + "," + cy + " L" + (cx + R * Math.cos(ang)) + "," + (cy + R * Math.sin(ang)) + " A" + R + "," + R + " 0 " + (q.frac > 0.5 ? 1 : 0) + " 1 " + (cx + R * Math.cos(a2)) + "," + (cy + R * Math.sin(a2)) + " Z", fill: col, stroke: "#fff" });
        sl.appendChild(el("title", {}, q.a + ": " + q.v + " (" + (q.frac * 100).toFixed(1) + "%)")); svg.appendChild(sl); ang = a2;
        var li = document.createElement("li"); li.textContent = q.a + ": " + q.v + " (" + (q.frac * 100).toFixed(1) + "%)"; li.style.borderLeft = "10px solid " + col; li.style.paddingLeft = "6px"; legend.appendChild(li);
      });
    } else {
      svg.appendChild(el("line", { x1: L, y1: y(0), x2: W - 6, y2: y(0), stroke: "#555" }));
      svg.appendChild(el("text", { x: 4, y: y(hi) + 4, "font-size": 11 }, String(+hi.toPrecision(4))));
      svg.appendChild(el("text", { x: 4, y: y(lo), "font-size": 11 }, String(+lo.toPrecision(4))));
      var pts2 = [];
      ch.bars.forEach(function (q, i) {
        var x = L + i * bw + bw * 0.15, g = el("g", {});
        if (kind === "line") {
          var px = x + bw * 0.35, py = y(q.v); pts2.push(px + "," + py);
          var dot = el("circle", { cx: px, cy: py, r: 4, fill: "#7a2e22" }); dot.appendChild(el("title", {}, q.a + ": " + q.v)); g.appendChild(dot);
        } else if (q.bar) { var rc = el("rect", { x: x, y: Math.min(y(q.v), y(0)), width: bw * 0.7, height: Math.abs(y(q.v) - y(0)), fill: "#7a2e22" }); rc.appendChild(el("title", {}, q.a + ": " + q.v)); g.appendChild(rc); }
        else { var zt = el("text", { x: x + bw * 0.35, y: y(0) - 4, "font-size": 10, "text-anchor": "middle" }, "0"); zt.appendChild(el("title", {}, q.a + ": 0")); g.appendChild(zt); }
        g.appendChild(el("text", { x: x + bw * 0.35, y: H2 - 12, "font-size": 10, "text-anchor": "middle" }, q.a));
        svg.appendChild(g);
      });
      if (kind === "line" && pts2.length > 1) svg.insertBefore(el("polyline", { points: pts2.join(" "), fill: "none", stroke: "#7a2e22", "stroke-width": 2 }), svg.children[3]);
    }
    out.appendChild(svg); if (legend) out.appendChild(legend);
    var note = document.createElement("p"); note.className = "cal-meta";
    note.textContent = (pie ? pie.slices.length + " of " + ch.pts.length : ch.pts.length) + " values charted from " + rg.label + (ch.skipped ? ", " + ch.skipped + " cells skipped (blank, text or error)" : "") + (pie && pie.left ? ", " + pie.left + " values at or below zero left out of the pie" : "") + ". The chart follows the sheet as you edit and is not saved.";
    out.appendChild(note);
  }
  document.getElementById("chart-draw").addEventListener("click", function () {
    var m = /^\s*([A-Za-z]{1,2}[0-9]{1,4})\s*:\s*([A-Za-z]{1,2}[0-9]{1,4})\s*$/.exec(document.getElementById("chart-range").value);
    var p1 = m && F.parseAddr(m[1].toUpperCase()), p2 = m && F.parseAddr(m[2].toUpperCase());
    chartRange = null; out.textContent = ""; chartKind = document.getElementById("chart-type").value; if (["bar", "line", "pie"].indexOf(chartKind) < 0) chartKind = "bar";
    if (!p1 || !p2) { out.textContent = "Enter a range like A1:A8."; return; }
    var c1 = Math.min(p1.col, p2.col), c2 = Math.max(p1.col, p2.col), r1 = Math.min(p1.row, p2.row), r2 = Math.max(p1.row, p2.row);
    if (c1 !== c2 && r1 !== r2) { out.textContent = "Use one row or one column."; return; }
    if ((c2 - c1 + 1) * (r2 - r1 + 1) > 50) { out.textContent = "A chart takes up to 50 cells."; return; }
    chartRange = { c1: c1, c2: c2, r1: r1, r2: r2, label: m[1].toUpperCase() + ":" + m[2].toUpperCase() };
    drawChart();
  });
  // ---- Data validation ----
  function renderRules() {
    var ul = $("val-list"); ul.textContent = "";
    rules.forEach(function (r, i) {
      var li = document.createElement("li"), b = document.createElement("button");
      li.textContent = r.range + ": " + (r.kind === "list" ? "one of " + r.items.join(", ") : "a number from " + r.min + " to " + r.max) + " ";
      b.type = "button"; b.className = "btn"; b.textContent = "Remove"; b.setAttribute("aria-label", "Remove the rule for " + r.range);
      b.addEventListener("click", function () { saveRules(M.removeRule(rules, i), "Removed the rule for " + r.range + "."); }); li.appendChild(b); ul.appendChild(li);
    });
    $("val-none").hidden = rules.length > 0 || rulesLocked; $("val-add").disabled = rulesLocked;
    $("val-raw").hidden = !(rulesLocked && rulesRaw !== null);
    if (rulesLocked) $("val-status").textContent = "This tab's validation rules could not be read, so they are not checked and cannot be changed. They were left untouched.";
  }
  function saveRules(next, msg) {
    if (rulesLocked || !next) return;
    var lt = null; try { lt = M.loadTabs(localStorage.getItem(KEY + ".tabs")); } catch (e) { lt = null; }
    if (!lt || lt.unreadable || !lt.meta.tabs.some(function (t) { return t.id === meta.active; })) { $("val-status").textContent = "This tab was deleted or changed in another window, or the tab list cannot be read, so the rules were not changed. Reload this page."; return; }
    try { if (next.length) localStorage.setItem(rulesKey(meta.active), M.rulesJson(next)); else localStorage.removeItem(rulesKey(meta.active)); }
    catch (e) { $("val-status").textContent = "This browser would not store the rules, so nothing was changed."; return; }
    rules = next; renderRules(); $("val-status").textContent = msg;
  }
  function valKind() { var k = $("val-kind").value; $("val-items-wrap").hidden = k !== "list"; $("val-num-wrap").hidden = k !== "number"; }
  $("val-kind").addEventListener("change", valKind); valKind();
  $("val-add").addEventListener("click", function () {
    var k = $("val-kind").value, r = M.makeRule($("val-range").value, k, k === "list" ? $("val-items").value : $("val-min").value, $("val-max").value, F.parseAddr);
    if (r.error) { $("val-status").textContent = r.error; return; }
    var a = M.addRule(rules, r.rule, F.parseAddr); if (a.error) { $("val-status").textContent = a.error; return; }
    saveRules(a.rules, "Added a rule for " + r.rule.range + ". Cells already in that range were not changed or checked.");
  });
  $("val-raw").addEventListener("click", function () {
    if (rulesRaw === null) return;
    var u = URL.createObjectURL(new Blob([rulesRaw], { type: "text/plain;charset=utf-8" })), l = document.createElement("a");
    l.href = u; l.download = "unreadable-validation-rules.txt"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
  });
  reloadRules();
  // ---- Colour rules (conditional formatting) ----
  function renderCf() {
    var ul = $("cf-list"); ul.textContent = "";
    cfRules.forEach(function (r, i) {
      var li = document.createElement("li"), b = document.createElement("button");
      li.textContent = r.range + ": " + ({ greater: "above ", less: "below ", equals: "equal to ", contains: "containing " })[r.kind] + r.value + " is " + r.color + " ";
      b.type = "button"; b.className = "btn"; b.textContent = "Remove"; b.setAttribute("aria-label", "Remove the colour rule for " + r.range);
      b.addEventListener("click", function () { saveCf(cfRules.filter(function (x, j) { return j !== i; }), "Removed the colour rule for " + r.range + "."); }); li.appendChild(b); ul.appendChild(li);
    });
    $("cf-none").hidden = cfRules.length > 0 || cfLocked; $("cf-add").disabled = cfLocked; $("cf-raw").hidden = !(cfLocked && cfRaw !== null);
    if (cfLocked) $("cf-status").textContent = "This tab's colour rules could not be read, so no colours are applied and the rules cannot be changed. They were left untouched.";
    if (typeof show === "function") show();
  }
  function saveCf(next, msg) {
    if (cfLocked || !next) return;
    var lt = null; try { lt = M.loadTabs(localStorage.getItem(KEY + ".tabs")); } catch (e) { lt = null; }
    if (!lt || lt.unreadable || !lt.meta.tabs.some(function (t) { return t.id === meta.active; })) { $("cf-status").textContent = "This tab was deleted or changed in another window, or the tab list cannot be read, so the colour rules were not changed. Reload this page."; return; }
    try { if (next.length) localStorage.setItem(cfKey(meta.active), M.cfJson(next)); else localStorage.removeItem(cfKey(meta.active)); }
    catch (e) { $("cf-status").textContent = "This browser would not store the colour rules, so nothing was changed."; return; }
    cfRules = next; renderCf(); $("cf-status").textContent = msg;
  }
  $("cf-add").addEventListener("click", function () {
    var r = M.makeCf($("cf-range").value, $("cf-kind").value, $("cf-value").value, $("cf-color").value, F.parseAddr);
    if (r.error) { $("cf-status").textContent = r.error; return; }
    var a = M.addCf(cfRules, r.rule); if (a.error) { $("cf-status").textContent = a.error; return; }
    saveCf(a.rules, "Added a colour rule for " + r.rule.range + ". It only changes how cells look.");
  });
  $("cf-raw").addEventListener("click", function () {
    if (cfRaw === null) return;
    var u = URL.createObjectURL(new Blob([cfRaw], { type: "text/plain;charset=utf-8" })), l = document.createElement("a");
    l.href = u; l.download = "unreadable-colour-rules.txt"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
  });
  reloadCf();
  // ---- Pivot: a read-only summary table, built when asked from the values shown now ----
  var pvOut = $("pivot-out"), pvSt = $("pivot-status");
  // The table describes the tab and cells it was built from, so it is cleared whenever the open tab or its cells are replaced (tab switch, add, delete, restore, import, clear, undo and redo).
  function clearPivot() { if (pvOut && pvOut.firstChild) { pvOut.textContent = ""; pvSt.textContent = "The pivot was cleared because the sheet or tab changed. Build it again to see current values."; } }
  $("pivot-build").addEventListener("click", function () {
    pvOut.textContent = "";
    var sp = M.makePivot($("pivot-range").value, $("pivot-key").value, $("pivot-val").value, $("pivot-agg").value, $("pivot-header").checked, F.parseAddr);
    if (sp.error) { pvSt.textContent = sp.error; return; }
    var spec = sp.spec, sh = new F.Sheet(cells), pairs = [];
    for (var r = spec.r1; r <= spec.r2; r++) {
      var ka = String.fromCharCode(64 + spec.kc) + r, va = String.fromCharCode(64 + spec.vc) + r, kv = sh.get(ka), vv = sh.get(va);
      pairs.push({ k: F.isErr(kv) ? kv.code : String(kv), v: F.isErr(vv) ? vv.code : vv });
    }
    var res = M.pivot(pairs, spec.agg); if (res.error) { pvSt.textContent = res.error; return; }
    if (!res.groups.length) { pvSt.textContent = "There are no rows with a value in " + spec.label + " right now."; return; }
    var names = { count: "Rows", sum: "Sum", average: "Average", min: "Smallest", max: "Largest" }, tbl = document.createElement("table"), cap = document.createElement("caption");
    function shownAt(c) { var v = sh.get(String.fromCharCode(64 + c) + (spec.r1 - 1)); return F.isErr(v) ? v.code : String(v); }
    var kh = spec.header ? shownAt(spec.kc) : "", vh = spec.header && spec.agg !== "count" ? shownAt(spec.vc) : "";
    cap.textContent = names[spec.agg] + (spec.agg === "count" ? "" : " of column " + String.fromCharCode(64 + spec.vc)) + " by column " + String.fromCharCode(64 + spec.kc) + ", rows " + spec.r1 + " to " + spec.r2; tbl.appendChild(cap);
    var hr = tbl.insertRow(); [kh || "Group", names[spec.agg] + (vh ? " (" + vh + ")" : "")].forEach(function (t) { var th = document.createElement("th"); th.scope = "col"; th.textContent = t; hr.appendChild(th); });
    res.groups.forEach(function (g) { var tr = tbl.insertRow(), td = tr.insertCell(), td2 = tr.insertCell(); if (g.blank) { td.textContent = "(blank)"; td.className = "pivot-blank"; td.title = "Rows whose group cell is empty"; } else td.textContent = g.key; td2.textContent = g.tooLarge ? "too large" : g.value === null ? "no numbers" : String(g.value); });
    pvOut.appendChild(tbl);
    pvSt.textContent = "Built from " + res.rows + " rows as they are now." + (res.skipped ? " " + res.skipped + " non-number value" + (res.skipped === 1 ? " was" : "s were") + " not added." : "") + " It is cleared if a cell or the tab changes.";
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
    var curRaw = null, readOk = true; try { curRaw = localStorage.getItem(KEY + ".tabs"); } catch (e) { readOk = false; }
    var other = readOk ? M.loadTabs(curRaw) : null;
    if (!other || other.unreadable || JSON.stringify(other.meta.tabs) !== JSON.stringify(meta.tabs)) { tabMsg.textContent = "The tab list was changed in another window or tab of this browser. Reload this page to continue. Nothing was changed here."; return false; }
    if (!saveMeta(next)) { tabMsg.textContent = "This browser would not store the tab list, so nothing was changed."; return false; }
    meta = next; return true;
  }
  function keyUsed(id) { try { return localStorage.getItem(tabKey(id)) !== null || localStorage.getItem(rulesKey(id)) !== null || localStorage.getItem(cfKey(id)) !== null; } catch (e) { return true; } }
  function renderTabs() {
    tabBar.textContent = "";
    meta.tabs.forEach(function (t) {
      var b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = t.name; b.dataset.tab = t.id;
      b.setAttribute("aria-pressed", t.id === meta.active ? "true" : "false"); if (t.id === meta.active) b.setAttribute("aria-current", "true");
      b.addEventListener("click", function () { switchTo(t.id, b); }); tabBar.appendChild(b);
    });
    var ct = meta.tabs.filter(function (t) { return t.id === meta.active; })[0]; if (document.activeElement !== tabName) tabName.value = ct.name;
    var dl = $("tab-raw"); dl.hidden = !(unreadableTabs[meta.active] && rawTabs[meta.active] !== undefined);
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
  $("tab-raw").addEventListener("click", function () {
    var raw = rawTabs[meta.active]; if (raw === undefined) return;
    var u = URL.createObjectURL(new Blob([raw], { type: "text/plain;charset=utf-8" })), l = document.createElement("a");
    l.href = u; l.download = "unreadable-tab-cells.txt"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
    tabMsg.textContent = "Downloaded the stored text of this tab exactly as it is in this browser. It is not a valid sheet file, so it cannot be imported as is.";
  });
  $("tab-add").addEventListener("click", function () {
    var next = M.addTab(meta, keyUsed); if (!next) { tabMsg.textContent = "A file holds up to " + M.MAX_TABS + " tabs."; return; }
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
    var id = meta.active, n = Object.keys(H.cells).length, nm = meta.tabs.filter(function (t) { return t.id === id; })[0].name, bad = !!unreadableTabs[id];
    var r = M.deleteTab(meta, id); if (r.error) { tabMsg.textContent = r.error; return; }
    var q = "";
    if (bad) q += "The stored cells of the tab " + nm + " could not be read. They stay in this browser's storage but will no longer be shown, and cannot be restored here. Use Download unreadable cells first if you want a copy. ";
    else if (n) q += "Delete the tab " + nm + " and its " + n + " cells? ";
    if (lastDeleted && !bad) q += (q ? "" : "Delete the tab " + nm + "? ") + "Only the most recently deleted tab can be restored, so this replaces the restorable tab " + lastDeleted.name + ", which will be lost. ";
    if (q && !bad) q += "You can bring this tab back with Restore deleted tab until you reload this page. After a reload it cannot be brought back.";
    if (q && !window.confirm(q)) { tabMsg.textContent = "Delete cancelled."; return; }
    var saved = bad ? null : M.copy(H.cells);
    if (!commitMeta(r.meta)) return;
    if (!bad) { try { localStorage.removeItem(tabKey(id)); } catch (e) { /* the tab is out of the list; its id will not be reused while the cells remain */ } }
    delete hist[id]; delete unreadableTabs[id]; if (!bad) { var rr = null, rulesWasThere = false; try { rr = localStorage.getItem(rulesKey(id)); rulesWasThere = rr !== null; if (rr !== null && !M.loadRules(rr, F.parseAddr).unreadable) localStorage.removeItem(rulesKey(id)); else rr = null; } catch (e) { rr = null; } var cr = null, cfWas = false; try { cr = localStorage.getItem(cfKey(id)); cfWas = cr !== null; if (cr !== null && !M.loadCf(cr, F.parseAddr).unreadable) localStorage.removeItem(cfKey(id)); else cr = null; } catch (e) { cr = null; } lastDeleted = { name: nm, cells: saved, rules: rr, cf: cr, rulesLeft: (rr === null && rulesWasThere) || (cr === null && cfWas) }; }
    H = histFor(meta.active); chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = bad ? "Removed the tab " + nm + " from the list. Its unreadable stored cells were not deleted." : "Deleted the tab " + nm + ". Restore deleted tab brings back only this one, until you reload; after a reload it cannot be brought back.";
  });
  $("tab-restore").addEventListener("click", function () {
    if (!lastDeleted) return;
    var next = M.addTab(meta, keyUsed); if (!next) { tabMsg.textContent = "A file holds up to " + M.MAX_TABS + " tabs. The deleted tab is still kept until you reload."; return; }
    var nm = lastDeleted.name, rn = M.renameTab(next, next.active, nm); if (!rn.error) next = rn.meta;
    var key = tabKey(next.active);
    try { localStorage.setItem(key, JSON.stringify(lastDeleted.cells)); } catch (e) { tabMsg.textContent = "This browser would not store the restored tab, so nothing was restored. The deleted tab is still kept until you reload."; return; }
    if (lastDeleted.rules) {
      try { localStorage.setItem(rulesKey(next.active), lastDeleted.rules); }
      catch (e) { try { localStorage.removeItem(key); } catch (e2) { /* nothing else points at this key */ } tabMsg.textContent = "This browser would not store the tab's validation rules, so nothing was restored. The deleted tab and its rules are still kept until you reload. Try Restore again."; return; }
    }
    if (lastDeleted.cf) {
      try { localStorage.setItem(cfKey(next.active), lastDeleted.cf); }
      catch (e) { try { localStorage.removeItem(key); if (lastDeleted.rules) localStorage.removeItem(rulesKey(next.active)); } catch (e2) { /* nothing else points at these keys */ } tabMsg.textContent = "This browser would not store the tab's colour rules, so nothing was restored. The deleted tab and its rules are still kept until you reload. Try Restore again."; return; }
    }
    if (!commitMeta(next)) { try { localStorage.removeItem(key); if (lastDeleted.rules) localStorage.removeItem(rulesKey(next.active)); if (lastDeleted.cf) localStorage.removeItem(cfKey(next.active)); } catch (e) { /* nothing else points at these keys */ } return; }
    var left = lastDeleted.rulesLeft; H = histFor(meta.active); lastDeleted = null;
    chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = "Restored the tab" + (rn.error ? " under a new name, because " + nm + " is in use." : " " + nm + ".") + (left ? " Its validation or colour rules were unreadable or could not be removed, so they were not restored." : "");
  });
  status.textContent = H.note;
  if (tabsLocked) tabMsg.textContent = "The stored tab list could not be read, so tab changes are off. Your cells were not touched.";
  renderTabs();
  show(); buttons();
})();
