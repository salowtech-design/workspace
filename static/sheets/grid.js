(function () {
  "use strict";
  var root = document.getElementById("sheet-root");
  if (!root || !window.SalowFormula) return;
  var F = SalowFormula, KEY = root.getAttribute("data-store-key"), COLS = 26, ROWS = 1000, MAXCELL = 2000, HIST = 100;
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
  var fmtRules = [], fmtLocked = false, fmtRaw = null;
  function fmtKey(id) { return tabKey(id) + ".fmt"; }
  function reloadFmt() {
    var raw = null, ok = true; try { raw = localStorage.getItem(fmtKey(meta.active)); } catch (e) { ok = false; }
    var r = ok ? M.loadFmt(raw, F.parseAddr) : { rules: [], unreadable: true };
    fmtRules = r.rules; fmtLocked = r.unreadable; fmtRaw = ok ? raw : null; if (typeof renderFmt === "function") renderFmt();
  }
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
  // The formula row sticks to the top; focused cells scroll to just below it, whatever height it wraps to.
  var barRow = document.getElementById("sheet-barrow");
  function setBarH() { if (barRow) root.style.setProperty("--sheet-bar-h", barRow.offsetHeight + "px"); }
  setBarH(); window.addEventListener("resize", function () { setBarH(); }); if (window.ResizeObserver && barRow) new ResizeObserver(function () { setBarH(); placeFreeze(); }).observe(barRow);
  // ---- Cross-tab references: Name!A1, 'My tab'!A1:B3 (see formula.js). A tab is found by its name, ignoring case. The tab you are on is read as shown. Another tab is read from this page's copy of it, but only
  // when that copy is exactly what is stored (otherwise #TAB!, so a value is never taken from a copy that was not saved or was changed in another window). ----
  function tabCellsFor(t, own) {
    if (t.id === meta.active) return own;
    var bs = "none"; try { bs = M.structRecovery(localStorage, bakKeyOf(t.id), structKeys(t.id)); } catch (e) { bs = "storage"; }
    if (bs !== "none") return null;
    var hh = histFor(t.id); if (unreadableTabs[t.id]) return null;
    var rc = null; try { rc = localStorage.getItem(tabKey(t.id)); } catch (e) { return null; }
    var same = rc === null ? !Object.keys(hh.cells).length : (function () { try { var st0 = clean(JSON.parse(rc)); return !!st0 && sameCells(st0.cells, hh.cells); } catch (e) { return false; } })();
    return same ? hh.cells : null;
  }
  function mkSheet(c) {
    var memo = Object.create(null);
    return new F.Sheet(c, { self: meta.active, tab: function (name) {
      var k = String(name).toLowerCase(); if (k in memo) return memo[k];
      var t = null; for (var i = 0; i < meta.tabs.length; i++) if (meta.tabs[i].name.toLowerCase() === k) { t = meta.tabs[i]; break; }
      return (memo[k] = t ? { id: t.id, cells: tabCellsFor(t, c) } : null);
    } });
  }
  // Formulas, on any tab, that refer to the tab called name. unknown: a tab whose cells cannot be read, so the count may be short.
  function refsToTab(name) {
    var low = String(name).toLowerCase(), out = { formulas: 0, tabs: 0, unknown: false, example: "" };
    meta.tabs.forEach(function (t) {
      var cs = t.id === meta.active ? H.cells : (function () { var hh = histFor(t.id); return unreadableTabs[t.id] ? null : hh.cells; })();
      if (!cs) { out.unknown = true; return; }
      var hit = 0; Object.keys(cs).forEach(function (a) { var v = cs[a]; if (typeof v === "string" && v.charAt(0) === "=" && F.tabRefs(v).some(function (x) { return x.toLowerCase() === low; })) { hit++; if (!out.example) out.example = t.name + " " + a; } });
      if (hit) { out.formulas += hit; out.tabs++; }
    });
    return out;
  }
  function sync() { cells = H.cells; reloadRules(); reloadCf(); reloadFmt(); if (typeof clearPivot === "function") clearPivot(); show(); refreshSelected(); buttons(); drawChart(); applyFilter(); if (typeof reloadCw === "function") reloadCw(); if (typeof applyFreeze === "function") applyFreeze(); if (typeof renderVers === "function") renderVers(); status.textContent = H.note; }
  // After any whole-sheet change (import, undo, redo, fill, sort, clear, tab switch) the focused cell and the formula bar must show the new content, not what was there before. A focused cell is refreshed here too, so a later blur cannot write the old text back. The formula bar keeps what the user typed in it (barDirty) and is refreshed when untouched.
  var barDirty = false;
  function refreshSelected() {
    var ai = document.activeElement;
    if (ai && ai.dataset && ai.dataset.addr && inputs[ai.dataset.addr] === ai) {
      var fa = ai.dataset.addr; orig = cells[fa] || ""; ai.value = orig;
      if (fa === cur) { bar.value = orig; barDirty = false; }
      return;
    }
    if (!cur || (ai === bar && barDirty)) return;
    orig = cells[cur] || ""; bar.value = orig; barDirty = false;
  }
  function buttons() { undoBtn.disabled = !H.past.length; redoBtn.disabled = !H.future.length; }
  function change(next, note) { if (heldMsg()) return false; if (!H.change(next, note)) return false; sync(); return true; }
  function undo() { if (heldMsg()) return; var m = H.topMeta(false); if (m) { structStep(false, m); return; } if (H.undo()) sync(); }
  function redo() { if (heldMsg()) return; var m = H.topMeta(true); if (m) { structStep(true, m); return; } if (H.redo()) sync(); }
  undoBtn.addEventListener("click", undo); redoBtn.addEventListener("click", redo);

  var table = document.createElement("table"), inputs = Object.create(null), cur = null, orig = "";
  var head = table.insertRow(), corner = document.createElement("th"); corner.setAttribute("aria-hidden", "true"); head.appendChild(corner);  // a th, so the blank corner pins like the headers and covers a row number that scrolls under it
  for (var c = 1; c <= COLS; c++) { var th = document.createElement("th"); th.textContent = String.fromCharCode(64 + c); head.appendChild(th); }
  // Virtualised rows: all ROWS rows exist, but only the rows near the view hold their 26 inputs. The others are a row header and a fixed-height gap, so a
  // 1000-row sheet costs about 80 rows of inputs, not 26,000. A row that holds the focus, the selected cell or a frozen row is never emptied.
  var ROWH = 38, mat = Object.create(null), nMat = 0, frzTop = 0, frzLefts = null, frzTops = null;
  for (var r = 1; r <= ROWS; r++) {
    var tr = table.insertRow(), rh = document.createElement("th"); rh.textContent = r; tr.appendChild(rh); tr.style.height = ROWH + "px";
  }
  function inGridAddr(k) { var p = F.parseAddr(k); return !!p && p.row >= 1 && p.row <= ROWS && p.col >= 1 && p.col <= COLS; }
  function buildRow(r) {
    if (mat[r]) return; var tr = table.rows[r]; if (!tr) return;
    for (var c = 1; c <= COLS; c++) {
      var L = String.fromCharCode(64 + c), a = L + r, inp = document.createElement("input");
      inp.type = "text"; inp.maxLength = MAXCELL; inp.setAttribute("aria-label", "Cell " + a); inp.dataset.addr = a;
      if (typeof cwMap !== "undefined" && cwMap[L]) inp.style.width = cwMap[L] + "px";
      inputs[a] = inp; tr.insertCell().appendChild(inp);
    }
    mat[r] = true; nMat++; tr.style.height = "";
    if (typeof frzCellsOf === "function") frzCellsOf(r);
  }
  function inpFor(a) { if (!inputs[a] && inGridAddr(a)) { buildRow(F.parseAddr(a).row); paintRows([F.parseAddr(a).row]); } return inputs[a]; }
  function keepRow(r) {
    if (r <= Math.max(1, frzTop)) return true;
    var ae = document.activeElement; if (ae && ae.dataset && ae.dataset.addr && F.parseAddr(ae.dataset.addr).row === r) return true;
    return !!cur && F.parseAddr(cur).row === r;
  }
  function dropRow(r) {
    if (!mat[r] || keepRow(r)) return; var tr = table.rows[r], h = tr.offsetHeight;
    for (var c = 1; c <= COLS; c++) delete inputs[String.fromCharCode(64 + c) + r];
    while (tr.cells.length > 1) tr.deleteCell(1);
    tr.style.height = (h || ROWH) + "px"; delete mat[r]; nMat--;
  }
  // Painting is time-sliced: cells are worked out in slices of about SLICE_MS and the page yields between slices, so a sheet of very heavy formulas
  // cannot block it. A cell still waiting shows "..." and is marked data-calc (never its old value as if current). Every whole-sheet repaint
  // (show) starts a new generation: the queue, and the Sheet it reads, are replaced, so a slice of an older generation cannot run. A queued
  // address is looked up when it is painted, so a row dropped and rebuilt meanwhile gets its own value and an old value cannot land in a recycled input.
  var SLICE_MS = 40, pGen = 0, pQ = [], pAt = 0, pSh = null, pTimer = 0;
  function pNow() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function pRun(gen) {
    pTimer = 0; if (gen !== pGen) return;
    var t0 = pNow();
    while (pAt < pQ.length) {
      var a = pQ[pAt++], inp = inputs[a];
      if (inp) paintAddr(pSh, a);
      if (pNow() - t0 > SLICE_MS) break;
    }
    if (pAt >= pQ.length) { pQ = []; pAt = 0; return; }
    for (var i = pAt; i < pQ.length; i++) { var w = inputs[pQ[i]]; if (w && !w.dataset.calc && document.activeElement !== w) { w.dataset.calc = "1"; w.value = "\u2026"; w.title = "Calculating"; } }
    pTimer = setTimeout(function () { pRun(gen); }, 0);
  }
  function pStart(addrs, fresh) {
    if (fresh) { pGen++; root.dataset.paintGen = String(pGen); pQ = []; pAt = 0; pSh = mkSheet(cells); if (pTimer) { clearTimeout(pTimer); pTimer = 0; } }
    else if (!pSh) pSh = mkSheet(cells);
    if (pAt) { pQ = pQ.slice(pAt); pAt = 0; }
    for (var i = 0; i < addrs.length; i++) pQ.push(addrs[i]);
    if (pTimer) return;
    pRun(pGen);
  }
  function pFront(a) { if (!pSh) pSh = mkSheet(cells); pQ.splice(pAt, 0, a); if (pTimer) { clearTimeout(pTimer); pTimer = 0; } pRun(pGen); }
  function paintRows(list) { var q = []; list.forEach(function (r) { for (var c = 1; c <= COLS; c++) { var a = String.fromCharCode(64 + c) + r; if (inputs[a]) q.push(a); } }); pStart(q, false); }
  // Which rows are in view: the rows whose box meets the part of the grid box that is on screen, found by halving over the real row positions (hidden rows have no height).
  function winRange() {
    var sc = table.parentNode.getBoundingClientRect(), top = sc.top, bot = sc.bottom;
    if (bot <= top) return null;
    var rows = table.rows, lo = 1, hi = ROWS, mid, p;
    // first row whose bottom reaches the top of the grid box. A hidden row has no box, so the probe moves to the next shown row.
    while (lo < hi) {
      mid = (lo + hi) >> 1; p = mid; while (p <= hi && rows[p].hidden) p++;
      if (p > hi) { hi = mid; continue; }
      if (rows[p].getBoundingClientRect().bottom < top) lo = p + 1; else hi = p > mid ? mid : p;
    }
    var first = lo; hi = ROWS;
    // last row whose top is above the bottom of the grid box
    while (lo < hi) {
      mid = (lo + hi + 1) >> 1; p = mid; while (p >= lo && rows[p].hidden) p--;
      if (p < lo) { lo = mid; continue; }
      if (rows[p].getBoundingClientRect().top > bot) hi = Math.max(lo, p - 1); else lo = mid;
    }
    return { first: first, last: lo };
  }
  var WBUF = 12, WKEEP = 50, winRaf = 0;
  function winUpdate() {
    winRaf = 0; var w = winRange(); if (!w) return;
    var from = Math.max(1, w.first - WBUF), to = Math.min(ROWS, w.last + WBUF), made = [], r, nShown = 0;
    for (r = from; r <= to; r++) if (!table.rows[r].hidden) { nShown++; if (!mat[r]) { buildRow(r); made.push(r); } }  // a hidden row has no box, so it gets no inputs until the filter shows it
    if (made.length) paintRows(made);
    if (nMat > nShown + 2 * WKEEP) Object.keys(mat).forEach(function (k) { k = +k; if (k < from - WKEEP || k > to + WKEEP) dropRow(k); });  // sparse filter: the window is many index rows wide, so count the shown ones
  }
  function winSoon() { if (!winRaf) winRaf = requestAnimationFrame(winUpdate); }
  root.appendChild(table);
  window.addEventListener("scroll", winSoon, { passive: true }); table.parentNode.addEventListener("scroll", winSoon, { passive: true }); window.addEventListener("resize", winSoon);
  buildRow(1); ROWH = table.rows[1].offsetHeight || ROWH; for (var rr0 = 2; rr0 <= ROWS; rr0++) table.rows[rr0].style.height = ROWH + "px";
  function showVal() {
    var v = $("sheet-val"); if (!v) return;
    var raw = cur ? cells[cur] : null;
    if (typeof raw !== "string" || raw[0] !== "=") { v.textContent = ""; return; }
    var sv = mkSheet(cells).get(cur), shown = F.isErr(sv) ? sv.code : String(sv);
    var f = fmtLocked ? {} : M.fmtFor(fmtRules, cur, F.parseAddr);
    v.textContent = "Result: " + (F.isErr(sv) ? shown : M.fmtText(f.nf, shown));
  }
  function show() {
    showVal();
    pStart(Object.keys(inputs), true);
  }
  function paintAddr(sh, a) {
    {
      delete inputs[a].dataset.calc;
      var sv = sh.get(a), shown = F.isErr(sv) ? sv.code : String(sv), col = cfLocked ? null : M.cfColor(cfRules, a, shown, F.parseAddr);
      if (col) inputs[a].dataset.cf = col; else delete inputs[a].dataset.cf;
      var f = fmtLocked ? {} : M.fmtFor(fmtRules, a, F.parseAddr), inp = inputs[a];
      if (f.bold) inp.dataset.bold = "1"; else delete inp.dataset.bold;
      if (f.align) inp.dataset.align = f.align; else delete inp.dataset.align;
      if (document.activeElement === inp) return;
      var text = F.isErr(sv) ? shown : M.fmtText(f.nf, shown);
      inp.value = text; inp.title = (cells[a] || "") + (typeof cells[a] === "string" && cells[a][0] === "=" && text === shown ? " (result " + shown + ")" : "") + (col ? (cells[a] ? " " : "") + "(highlighted " + col + " by a colour rule)" : "") + (text !== shown ? (cells[a] || col ? " " : "") + "(shown with a number format; value " + shown + ")" : "");
    }
  }
  function commit(a, v) {
    if (rulesLocked && v !== (cells[a] || "")) { status.textContent = "This tab's validation rules could not be read, so edits are off until they are fixed or removed. Use Download unreadable rules to keep a copy. The edit was not applied."; return; }
    var why = rulesLocked ? null : M.checkValue(rules, a, v, F.parseAddr);
    if (why) { status.textContent = why + " The edit was not applied."; return; }
    var n = M.copy(cells); if (v === "") delete n[a]; else n[a] = v; change(n); }
  function move(dc, dr) {
    if (!cur) return; var p = F.parseAddr(cur), nc = Math.min(COLS, Math.max(1, p.col + dc)), nr = Math.min(ROWS, Math.max(1, p.row + dr));
    while (dr && rowHidden(nr) && nr + (dr > 0 ? 1 : -1) >= 1 && nr + (dr > 0 ? 1 : -1) <= ROWS) nr += dr > 0 ? 1 : -1;
    if (rowHidden(nr)) return;
    inpFor(String.fromCharCode(64 + nc) + nr).focus();
  }
  table.addEventListener("focusin", function (e) {
    var a = e.target.dataset && e.target.dataset.addr; if (!a) return;
    cur = a; barDirty = false; orig = cells[a] || ""; delete e.target.dataset.calc; e.target.value = orig; bar.value = orig; sel.textContent = a; showVal(); e.target.select();
  });
  table.addEventListener("focusout", function (e) {
    var a = e.target.dataset && e.target.dataset.addr, c0 = cells;
    if (a) commit(a, e.target.value);
    // Nothing changed (the same cells object): do not restart the whole queue, only bring this cell's own value back (it was showing its raw text).
    if (a && cells === c0 && inputs[a] === e.target) { showVal(); pFront(a); return; }
    show();
  });
  table.addEventListener("input", function (e) { if (e.target.dataset && e.target.dataset.addr === cur) bar.value = e.target.value; });
  // Paste a block (tabs and line breaks) from another spreadsheet at the focused cell. A single value pastes normally.
  table.addEventListener("paste", function (e) {
    var t = e.target, a = t.dataset && t.dataset.addr; if (!a) return;
    var text = e.clipboardData ? e.clipboardData.getData("text/plain") : ""; if (!M.isBlock(text)) return;
    e.preventDefault();
    if (filt) { status.textContent = "A filter is on, so nothing was pasted (it would also fill rows you cannot see). Clear the filter first."; return; }
    if (rulesLocked) { status.textContent = "This tab's validation rules could not be read, so a block cannot be checked and nothing was pasted. The rules were left untouched."; return; }
    var parsed = M.parsePaste(text, F.parseAddr); if (parsed.error) { status.textContent = parsed.error; return; }
    var p = F.parseAddr(a);
    var res = M.applyPaste(cells, parsed, p.col, p.row, COLS, ROWS, MAXCELL, function (addr, v) { return M.checkValue(rules, addr, v, F.parseAddr); }, function (c, r) { return String.fromCharCode(64 + c) + r; });
    var bits = [res.set + " cells filled"]; if (res.cleared) bits.push(res.cleared + " cleared"); if (res.clipped) bits.push(res.clipped + " outside Z1000 left out"); if (res.refused) bits.push(res.refused + " refused by validation rules"); if (res.tooLong) bits.push(res.tooLong + " too long (over " + MAXCELL + " characters) left out"); if (res.breaks) bits.push(res.breaks + " with a line break inside, shown with a space instead"); if (res.formulas) bits.push(res.formulas + " start with = and are formulas");
    if (!res.set && !res.cleared) { status.textContent = "Nothing was pasted: " + bits.slice(1).join(", ") + "."; return; }
    if (!change(res.cells, "Pasted " + parsed.rows + " rows by " + parsed.cols + " columns at " + a + ": " + bits.join(", ") + ". Undo works.")) return;
    orig = cells[a] || ""; t.value = orig; bar.value = orig;
  });
  function fill(dir) {
    var rg = parseRange($("fill-range").value);
    if (!rg) { status.textContent = "Enter a range like A1:A10."; return; }
    if (rg.c2 > COLS || rg.r2 > ROWS) { status.textContent = "A fill range must stay inside A1:Z1000."; return; }
    if (filt) { status.textContent = "A filter is on, so nothing was filled (it would also fill rows you cannot see). Clear the filter first."; return; }
    if (rulesLocked) { status.textContent = "This tab's validation rules could not be read, so a fill cannot be checked and nothing was filled. The rules were left untouched."; return; }
    var res = M.fillRange(cells, rg, dir, function (addr, v) { return M.checkValue(rules, addr, v, F.parseAddr); }, function (c, r) { return String.fromCharCode(64 + c) + r; }, MAXCELL, F.shiftFormula, mkSheet(cells).isVirtual);
    if (res.error) { status.textContent = res.error; return; }
    var bits = [res.set + " cells filled"]; if (res.cleared) bits.push(res.cleared + " cleared"); if (res.refused) bits.push(res.refused + " refused by validation rules"); if (res.tooLong) bits.push(res.tooLong + " too long"); if (res.formulas) bits.push(res.formulas + " formulas copied" + (res.adjusted ? ", " + res.adjusted + " with references adjusted (a $ pins a column or row)" : "")); if (res.refErrs) bits.push(res.refErrs + " now show #REF! because a reference moved off the grid");
    if (!res.set && !res.cleared) { status.textContent = "Nothing changed: " + bits.slice(1).join(", ") + (bits.length > 1 ? "." : "the cells already match."); return; }
    if (!change(res.cells, "Copied " + (dir === "down" ? "the first row down" : "the first column right") + " in " + $("fill-range").value.trim().toUpperCase() + ": " + bits.join(", ") + ". Undo works.")) return;
    if (cur) { orig = cells[cur] || ""; bar.value = orig; }
  }
  var findStat = $("find-status");
  function findOpts(repl) { return { find: $("find-text").value, repl: repl, matchCase: $("find-case").checked, formulas: $("find-formulas").checked }; }
  function findCtx(check) { var sh = mkSheet(cells); return { parseAddr: F.parseAddr, shown: function (a) { var v = sh.get(a); return F.isErr(v) ? v.code : String(v); }, check: check, maxCell: MAXCELL, cols: COLS, rows: ROWS, badFormula: function (a, t) { try { F.parse(t.slice(1)); return false; } catch (e) { return !!(e && e.code === "#PARSE!"); } } };
  }
  $("find-next").addEventListener("click", function () {
    var r = M.findReplace(cells, findOpts(null), findCtx(null));
    if (r.error) { findStat.textContent = r.error; return; }
    if (!r.hits.length) { findStat.textContent = "No match in A1:Z1000." + (r.outside ? " " + r.outside + " matches outside it are counted but not listed or changed." : ""); return; }
    var cp = cur ? F.parseAddr(cur) : null, pick = r.hits[0];
    if (cp) for (var i = 0; i < r.hits.length; i++) { var q = F.parseAddr(r.hits[i]); if (q.row > cp.row || (q.row === cp.row && q.col > cp.col)) { pick = r.hits[i]; break; } }
    var pq = F.parseAddr(pick);
    if (pq.row > ROWS || pq.col > COLS) { findStat.textContent = "The match " + pick + " is outside the visible grid."; return; }
    if (table.rows[pq.row].hidden) { findStat.textContent = "The next match " + pick + " is in a row hidden by the filter."; return; }
    inpFor(pick).focus(); findStat.textContent = "Match in " + pick + " (" + (r.hits.indexOf(pick) + 1) + " of " + r.hits.length + ")" + (r.resultOnly ? ", " + r.resultOnly + " of them in formula results" : "") + ".";
  });
  $("find-all").addEventListener("click", function () {
    if (heldMsg()) return;
    if (filt) { findStat.textContent = "A filter is on, so nothing was replaced (it would also change rows you cannot see). Clear the filter first."; return; }
    if (rulesLocked) { findStat.textContent = "This tab's validation rules could not be read, so a replacement cannot be checked and nothing was replaced. The rules were left untouched."; return; }
    var r = M.findReplace(cells, findOpts($("find-repl").value), findCtx(function (addr, v) { return M.checkValue(rules, addr, v, F.parseAddr); }));
    if (r.error) { findStat.textContent = r.error; return; }
    var bits = [r.changed + " cells changed"]; if (r.cleared) bits.push(r.cleared + " emptied"); if (r.resultOnly) bits.push(r.resultOnly + " formula results matched and left alone"); if (r.becameFormula) bits.push(r.becameFormula + " skipped (would become a formula)"); if (r.lostEquals) bits.push(r.lostEquals + " skipped (formula would lose its =)"); if (r.badFormula) bits.push(r.badFormula + " skipped (formula would no longer parse)"); if (r.outside) bits.push(r.outside + " matches outside A1:Z1000 counted, not listed or changed"); if (r.tooLong) bits.push(r.tooLong + " too long"); if (r.refused) bits.push(r.refused + " refused by validation rules");
    if (!r.changed && !r.cleared) { var why = bits.slice(1).join(", "); findStat.textContent = "Nothing changed" + (why ? ": " + why : r.matched ? ": the text is already what you asked for" : r.outside ? "" : ": no match") + (r.outside && !why ? ": " + r.outside + " matches outside A1:Z1000 counted, not listed or changed" : "") + "."; return; }
    if (!change(r.cells, "Replaced in " + (r.changed + r.cleared) + " cells. Undo works.")) return;
    findStat.textContent = bits.join(", ") + ". Undo works."; if (cur) { orig = cells[cur] || ""; bar.value = orig; }
  });
  $("fill-down").addEventListener("click", function () { fill("down"); });
  $("fill-right").addEventListener("click", function () { fill("right"); });
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
  bar.addEventListener("input", function () { barDirty = true; });
  bar.addEventListener("change", function () { barDirty = false; if (cur) { commit(cur, bar.value); show(); } });
  bar.addEventListener("keydown", function (e) { if (e.key === "Escape") { bar.value = cells[cur] || ""; barDirty = false; } });

  document.getElementById("sheet-clear").addEventListener("click", function () {
    if (!change(Object.create(null), "Sheet cleared. Undo works until you reload this page.")) { status.textContent = "Nothing to clear."; return; }
    if (!H.saveFailed) status.textContent += " The stored copy is now empty; after a reload the cleared content cannot be brought back.";
  });
  function outside(c) { return Object.keys(c).filter(function (k) { return !inGridAddr(k); }).length; }
  document.getElementById("sheet-export").addEventListener("click", function () {
    var u = URL.createObjectURL(new Blob([JSON.stringify(cells)], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = "sheet.json"; document.body.appendChild(l); l.click(); l.remove(); URL.revokeObjectURL(u);
  });
  (function () {  // Save to Drive / Open from Drive: the person's own files on this device (see static/drive/link.js)
    var box = document.getElementById("drive-link"); if (!box || !window.SalowDriveLink) return;
    window.SalowDriveLink.init({ box: box, status: document.getElementById("dl-status"), nameInput: document.getElementById("dl-name"), saveBtn: document.getElementById("dl-save"), listBtn: document.getElementById("dl-list"), select: document.getElementById("dl-files"), openBtn: document.getElementById("dl-open"),
      saveExt: "json", saveType: "application/json", openExts: ["json", "csv"],
      getContent: function () { return Object.keys(cells).length ? { ok: true, text: JSON.stringify(cells) } : { ok: false, message: "The sheet is empty, so nothing was saved." }; },
      inputFor: function (ext) { return document.getElementById(ext === "csv" ? "sheet-import-csv" : "sheet-import"); } });
  })();
  var csvBusy = false;
  function csvSame(snap) { var k1 = Object.keys(snap), k2 = Object.keys(cells); return k1.length === k2.length && k1.every(function (k) { return cells[k] === snap[k]; }); }
  document.getElementById("sheet-csv").addEventListener("click", function () {
    if (csvBusy) { status.textContent = "The CSV is still being calculated."; return; }
    var snap = Object.assign({}, cells), sh = mkSheet(snap), ext = Object.assign({}, snap), g0 = pGen, tab0 = meta.active, bcs = sh.blockCells(), keys = Object.keys(snap).concat(bcs).filter(function (k) { var q = F.parseAddr(k); return q && q.col <= 26 && q.row <= 1000; }), vals = Object.create(null), i = 0, btn = document.getElementById("sheet-csv");
    bcs.forEach(function (k) { ext[k] = ""; }); csvBusy = true; btn.disabled = true; btn.dataset.csv = "calculating";
    function step() {
      if (meta.active !== tab0 || (pGen !== g0 && !csvSame(snap))) { csvBusy = false; btn.disabled = false; btn.dataset.csv = "cancelled"; status.textContent = "The sheet changed while the CSV was being calculated, so nothing was downloaded. Press Download CSV again."; return; }
      var t0 = pNow();
      while (i < keys.length) { vals[keys[i]] = sh.get(keys[i]); i++; if (pNow() - t0 > SLICE_MS) break; }
      if (i < keys.length) { status.textContent = "Calculating the CSV: " + Math.floor(100 * i / keys.length) + "% (" + i + " of " + keys.length + " cells)."; setTimeout(step, 0); return; }
      csvBusy = false; btn.disabled = false; btn.dataset.csv = "done"; finish();
    }
    function finish() {
    var r = M.toCsv(ext, F.parseAddr, function (a) { return vals[a]; }, F.isErr);
    if (!r) { status.textContent = "The sheet is empty, so nothing was downloaded."; return; }
    var u = URL.createObjectURL(new Blob([r.text], { type: "text/csv;charset=utf-8" })), l = document.createElement("a");
    l.href = u; l.download = "sheet.csv"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
    status.textContent = "Downloaded sheet.csv: " + r.rows + " rows by " + r.cols + " columns, with the values shown (not formulas)." + (filt ? " It includes the rows the filter hides." : "") + (r.broken ? " " + r.broken + " half emoji character(s) from LEFT, RIGHT or MID cannot be saved in a file and were written as the replacement character (U+FFFD)." : "") + " The file is UTF-8 without a byte-order mark, so some programs may need you to pick UTF-8 when importing. It has not been tried in a spreadsheet program." + (r.guarded ? " " + r.guarded + " text cell(s) looked like formulas (they start with = + - or @), so a leading apostrophe was added. The apostrophe is part of the text in the file, so it shows up when you open it. That lowers the chance a spreadsheet program runs them, but it is not a guarantee for every program." : "");
      }
    step();
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
      change(p.cells, "Imported " + p.count + " cells" + (p.skipped ? ", skipped " + p.skipped + " entries that were not valid cells" : "") + (o ? ". " + o + " cells are outside the visible grid (A1:Z1000): they are kept, used by formulas and exported, but not shown" : "") + ". You can undo this import.");
    };
    rd.readAsText(f); e.target.value = "";
  });

  document.getElementById("sheet-import-csv").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    var impTab = meta.active, impH = H;
    if (f.size > 1000000) { status.textContent = "That file is too large. Your sheet was not changed."; e.target.value = ""; return; }
    var rd = new FileReader();
    rd.onload = function () {
      if (H !== impH || meta.active !== impTab) { status.textContent = "You switched tabs while the file was loading, so the import was cancelled. Nothing was changed."; return; }
      if (filt) { status.textContent = "A filter is on, so nothing was imported. Clear the filter first. Your sheet was not changed."; return; }
      if (rulesLocked) { status.textContent = "This tab's validation rules could not be read, so the file cannot be checked and was not imported. Your sheet was not changed."; return; }
      var raw = String(rd.result), bad = (raw.match(/\uFFFD/g) || []).length;
      var r = M.csvImport(raw, function (addr, v) { return M.checkValue(rules, addr, v, F.parseAddr); }, function (c, rr) { return String.fromCharCode(64 + c) + rr; }, COLS, ROWS, MAXCELL);
      if (r.error) { status.textContent = r.error; return; }
      if (!r.set) { status.textContent = "That file has no cells to import. Your sheet was not changed."; return; }
      var notes = [];
      if (r.clipped) notes.push(r.clipped + " cells past column Z or row 1000 will be left out");
      if (r.refused) notes.push(r.refused + " cells a validation rule refuses will be left out");
      if (r.tooLong) notes.push(r.tooLong + " cells over " + MAXCELL + " characters will be left out");
      if (r.breaks) notes.push(r.breaks + " cells with a line break will have it turned into a space");
      if (r.literal) notes.push(r.literal + " cells in the file start with = and will be kept as text with a leading apostrophe, not run as formulas");
      if (bad) notes.push(bad + " characters could not be read as UTF-8 and show as the replacement character. This page reads UTF-8 only, so the file may be in another encoding and some text may be damaged");
      var have = Object.keys(cells).length;
      if (have || notes.length) {
        var q = "Import " + r.set + " cells from this CSV file (" + r.rows + " rows by " + r.cols + " columns)?" + (have ? " This replaces all " + have + " cells in this tab, including cells in places the file does not fill, which become blank." : "") + (notes.length ? "\n\n" + notes.join(".\n") + "." : "") + "\n\nUndo works afterwards.";
        if (!window.confirm(q)) { status.textContent = "Import cancelled. Your sheet was not changed."; return; }
        if (H !== impH || meta.active !== impTab) { status.textContent = "The tab changed while the question was open, so the import was cancelled. Nothing was changed."; return; }
      }
      var bits = [r.set + " cells imported from " + r.rows + " rows by " + r.cols + " columns"]; if (r.clipped) bits.push(r.clipped + " outside Z1000 left out"); if (r.refused) bits.push(r.refused + " refused by validation rules"); if (r.tooLong) bits.push(r.tooLong + " too long (over " + MAXCELL + " characters) left out"); if (r.breaks) bits.push(r.breaks + " line breaks inside cells became spaces"); if (r.literal) bits.push(r.literal + " cells in the file started with = and were kept as text with a leading apostrophe, not run as formulas"); if (bad) bits.push(bad + " replacement characters were in the text (not valid UTF-8)");
      change(r.cells, bits.join(", ") + ". Everything is text as written in the file, numbers are not converted. This replaced the tab's cells. Undo works.");
    };
    rd.readAsText(f); e.target.value = "";
  });

  // Chart: bars from one row or column of the sheet, computed here from the same formula engine.
  var out = document.getElementById("chart-out"), NS = "http://www.w3.org/2000/svg";
  function el(n, at, tx) { var x = document.createElementNS(NS, n); Object.keys(at || {}).forEach(function (k) { x.setAttribute(k, at[k]); }); if (tx !== undefined) x.textContent = tx; return x; }
  var outP = null, chartKind = "bar";
  function drawChart() {
    out.textContent = ""; if (!chartRange) return;
    var rg = chartRange, sh = mkSheet(cells), vals = [], rr, cc;
    for (rr = rg.r1; rr <= rg.r2; rr++) for (cc = rg.c1; cc <= rg.c2; cc++) { var ad = String.fromCharCode(64 + cc) + rr; vals.push({ a: ad, v: sh.get(ad) }); }
    if (chartKind === "scatter") { drawScatter(rg, sh); return; }
    var ch = M.chartPoints(vals);
    if (!ch) { out.textContent = "No numbers in " + rg.label + " right now."; return; }
    var W = 560, H2 = 260, L = 48, B = 30, T = 14, lo = ch.lo, hi = ch.hi, kind = chartKind;
    var y = function (v) { return T + (H2 - T - B) * (hi - v) / (hi - lo); }, bw = (W - L - 10) / ch.bars.length;
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H2, role: "img", "aria-label": (kind === "line" ? "Line" : kind === "area" ? "Area" : kind === "pie" ? "Pie" : kind === "scatter" ? "Scatter" : "Bar") + " chart of " + rg.label });
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
        if (kind === "line" || kind === "area") {
          var px = x + bw * 0.35, py = y(q.v); pts2.push(px + "," + py);
          var dot = el("circle", { cx: px, cy: py, r: 4, fill: "#7a2e22" }); dot.appendChild(el("title", {}, q.a + ": " + q.v)); g.appendChild(dot);
        } else if (q.bar) { var rc = el("rect", { x: x, y: Math.min(y(q.v), y(0)), width: bw * 0.7, height: Math.abs(y(q.v) - y(0)), fill: "#7a2e22" }); rc.appendChild(el("title", {}, q.a + ": " + q.v)); g.appendChild(rc); }
        else { var zt = el("text", { x: x + bw * 0.35, y: y(0) - 4, "font-size": 10, "text-anchor": "middle" }, "0"); zt.appendChild(el("title", {}, q.a + ": 0")); g.appendChild(zt); }
        g.appendChild(el("text", { x: x + bw * 0.35, y: H2 - 12, "font-size": 10, "text-anchor": "middle" }, q.a));
        svg.appendChild(g);
      });
      if (kind === "area" && pts2.length > 1) {
        var fx = pts2[0].split(",")[0], lx = pts2[pts2.length - 1].split(",")[0];
        svg.insertBefore(el("polygon", { points: fx + "," + y(0) + " " + pts2.join(" ") + " " + lx + "," + y(0), fill: "#7a2e22", "fill-opacity": 0.25, stroke: "none" }), svg.children[3]);
      }
      if ((kind === "line" || kind === "area") && pts2.length > 1) svg.insertBefore(el("polyline", { points: pts2.join(" "), fill: "none", stroke: "#7a2e22", "stroke-width": 2 }), svg.children[3]);
    }
    out.appendChild(svg); if (legend) out.appendChild(legend);
    var note = document.createElement("p"); note.className = "cal-meta";
    note.textContent = (pie ? pie.slices.length + " of " + ch.pts.length : ch.pts.length) + " values charted from " + rg.label + (ch.skipped ? ", " + ch.skipped + " cells skipped (blank, text or error)" : "") + (pie && pie.left ? ", " + pie.left + " values at or below zero left out of the pie" : "") + ". The chart follows the sheet as you edit and is not saved.";
    out.appendChild(note);
  }
  function drawScatter(rg, sh) {
    var rows = [], r;
    for (r = rg.r1; r <= rg.r2; r++) { var ax = String.fromCharCode(64 + rg.c1) + r, ay = String.fromCharCode(64 + rg.c2) + r; rows.push({ a: ax + ":" + ay, x: sh.get(ax), y: sh.get(ay) }); }
    var sp = M.scatterPoints(rows);
    if (!sp) { out.textContent = "No row in " + rg.label + " has a number in both columns right now."; return; }
    var W = 560, H2 = 280, L = 52, B = 34, T = 14, R = 14;
    var X = function (v) { return L + (W - L - R) * (v - sp.xlo) / (sp.xhi - sp.xlo); }, Y = function (v) { return T + (H2 - T - B) * (sp.yhi - v) / (sp.yhi - sp.ylo); };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H2, role: "img", "aria-label": "Scatter chart of " + rg.label });
    svg.appendChild(el("line", { x1: L, y1: H2 - B, x2: W - R, y2: H2 - B, stroke: "#555" }));
    svg.appendChild(el("line", { x1: L, y1: T, x2: L, y2: H2 - B, stroke: "#555" }));
    svg.appendChild(el("text", { x: L, y: H2 - 14, "font-size": 11, "text-anchor": "start" }, String(+sp.xlo.toPrecision(4))));
    svg.appendChild(el("text", { x: W - R, y: H2 - 14, "font-size": 11, "text-anchor": "end" }, String(+sp.xhi.toPrecision(4))));
    svg.appendChild(el("text", { x: L - 4, y: T + 8, "font-size": 11, "text-anchor": "end" }, String(+sp.yhi.toPrecision(4))));
    svg.appendChild(el("text", { x: L - 4, y: H2 - B, "font-size": 11, "text-anchor": "end" }, String(+sp.ylo.toPrecision(4))));
    sp.pts.forEach(function (q) { var d = el("circle", { cx: X(q.x), cy: Y(q.y), r: 4, fill: "#7a2e22", "fill-opacity": 0.8 }); d.appendChild(el("title", {}, q.a + ": (" + q.x + ", " + q.y + ")")); svg.appendChild(d); });
    out.appendChild(svg);
    var note = document.createElement("p"); note.className = "cal-meta";
    note.textContent = sp.pts.length + " points charted from " + rg.label + " (first column across, second column up)" + (sp.skipped ? ", " + sp.skipped + (sp.skipped === 1 ? " row" : " rows") + " skipped (a blank, text or error in either cell)" : "") + ". The chart follows the sheet as you edit.";
    out.appendChild(note);
  }
  document.getElementById("chart-draw").addEventListener("click", function () {
    var m = /^\s*([A-Za-z]{1,2}[0-9]{1,4})\s*:\s*([A-Za-z]{1,2}[0-9]{1,4})\s*$/.exec(document.getElementById("chart-range").value);
    var p1 = m && F.parseAddr(m[1].toUpperCase()), p2 = m && F.parseAddr(m[2].toUpperCase());
    chartRange = null; out.textContent = ""; chartKind = document.getElementById("chart-type").value; if (["bar", "line", "area", "pie", "scatter"].indexOf(chartKind) < 0) chartKind = "bar";
    if (!p1 || !p2) { out.textContent = "Enter a range like A1:A8."; return; }
    var c1 = Math.min(p1.col, p2.col), c2 = Math.max(p1.col, p2.col), r1 = Math.min(p1.row, p2.row), r2 = Math.max(p1.row, p2.row);
    if (chartKind === "scatter") { if (c2 - c1 !== 1) { out.textContent = "A scatter chart needs exactly two columns: x values on the left, y values on the right (for example A1:B8)."; return; } }
    else if (c1 !== c2 && r1 !== r2) { out.textContent = "Use one row or one column."; return; }
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
    $("val-raw").hidden = !(rulesLocked && rulesRaw !== null); $("val-reset").hidden = !(rulesLocked && rulesRaw !== null);
    if (rulesLocked) $("val-status").textContent = "This tab's validation rules could not be read, so they are not checked and cannot be changed. They were left untouched.";
  }
  function saveRules(next, msg) {
    if (rulesLocked || !next || heldMsg()) return;
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
  $("val-reset").addEventListener("click", function () {
    if (heldMsg()) return;
    if (!rulesLocked || rulesRaw === null) return;
    var st = $("val-status"), MSG = "This tab or its rules were changed in another window, or the storage cannot be read, so nothing was changed. Reload this page.";
    function stale() { var lt = null, cur = null; try { lt = M.loadTabs(localStorage.getItem(KEY + ".tabs")); cur = localStorage.getItem(rulesKey(meta.active)); } catch (e) { return true; } return !lt || lt.unreadable || !lt.meta.tabs.some(function (t) { return t.id === meta.active; }) || cur !== rulesRaw; }
    if (stale()) { st.textContent = MSG; return; }
    var bk = rulesKey(meta.active) + ".backup", had = null; try { had = localStorage.getItem(bk); } catch (e) { had = null; }
    if (!window.confirm("Remove this tab's unreadable validation rules? Only the rules are removed: your cells are not changed. A copy of the unreadable rules is kept in this browser under a backup key (use Download unreadable rules first if you want a file). " + (had !== null ? "A backup from an earlier removal on this tab already exists and WILL BE REPLACED by this copy. " : "") + "Afterwards values typed in this tab are not checked until you add a rule.")) { st.textContent = "Cancelled. The unreadable rules were left untouched."; return; }
    if (stale()) { st.textContent = MSG; return; } // the confirm box can stay open while another window changes things: check again right after it
    try { localStorage.setItem(bk, rulesRaw); if (localStorage.getItem(bk) !== rulesRaw) throw new Error("backup differs"); } catch (e) { st.textContent = "This browser would not keep a backup of the unreadable rules, so nothing was removed."; return; }
    try { localStorage.removeItem(rulesKey(meta.active)); } catch (e) { st.textContent = "This browser would not remove the unreadable rules, so they were not removed. A backup copy of them was written" + (had !== null ? " and it replaced the earlier backup for this tab" : "") + "."; return; }
    rulesLocked = false; rulesRaw = null; rules = []; renderRules();
    st.textContent = "Removed the unreadable rules. A copy is kept in this browser under a backup key and is replaced only if you do this again on this tab. Your cells were not changed.";
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
    if (cfLocked || !next || heldMsg()) return;
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
  // ---- Cell format: bold, alignment, number format per range. Display only, kept in this browser with the tab. ----
  function renderFmt() {
    var ul = $("fmt-list"); ul.textContent = "";
    fmtRules.forEach(function (r, i) {
      var li = document.createElement("li"), b = document.createElement("button"), bits = [];
      if (r.bold !== undefined) bits.push(r.bold ? "bold" : "not bold"); if (r.align) bits.push(r.align + " aligned"); if (r.nf) bits.push("number format " + r.nf);
      li.textContent = r.range + ": " + bits.join(", ") + " "; b.type = "button"; b.className = "btn"; b.textContent = "Remove"; b.setAttribute("aria-label", "Remove the format rule for " + r.range);
      b.addEventListener("click", function () { saveFmt(fmtRules.filter(function (x, j) { return j !== i; }), "Removed the format rule for " + r.range + "."); }); li.appendChild(b); ul.appendChild(li);
    });
    $("fmt-none").hidden = fmtRules.length > 0 || fmtLocked; $("fmt-add").disabled = fmtLocked; $("fmt-raw").hidden = !(fmtLocked && fmtRaw !== null);
    if (fmtLocked) $("fmt-status").textContent = "This tab's format rules could not be read, so no formats are applied and the rules cannot be changed. They were left untouched.";
  }
  function saveFmt(next, msg) {
    if (fmtLocked || !next || heldMsg()) return;
    var lt = null; try { lt = M.loadTabs(localStorage.getItem(KEY + ".tabs")); } catch (e) { lt = null; }
    if (!lt || lt.unreadable || !lt.meta.tabs.some(function (t) { return t.id === meta.active; })) { $("fmt-status").textContent = "This tab was deleted or changed in another window, or the tab list cannot be read, so the format rules were not changed. Reload this page."; return; }
    // First use: mark the tab list (version 2) before any format rule is stored, so a failure here leaves no format rule behind.
    if (next.length && meta.v !== 2) { var mk = M.markFmt(meta); if (!commitMeta(mk)) { $("fmt-status").textContent = "The tab list could not be marked for formats, so the format rule was not saved. " + tabMsg.textContent; return; } }
    try { if (next.length) localStorage.setItem(fmtKey(meta.active), M.fmtJson(next)); else localStorage.removeItem(fmtKey(meta.active)); }
    catch (e) { $("fmt-status").textContent = "This browser would not store the format rules, so nothing was changed."; return; }
    fmtRules = next; renderFmt(); show(); $("fmt-status").textContent = msg;
  }
  $("fmt-add").addEventListener("click", function () {
    var r = M.makeFmt($("fmt-range").value, $("fmt-bold").value, $("fmt-align").value, $("fmt-nf").value, F.parseAddr);
    if (r.error) { $("fmt-status").textContent = r.error; return; }
    var a = M.addFmt(fmtRules, r.rule); if (a.error) { $("fmt-status").textContent = a.error; return; }
    saveFmt(a.rules, "Added a format rule for " + r.rule.range + ". It only changes how cells look.");
  });
  $("fmt-raw").addEventListener("click", function () {
    if (fmtRaw === null) return;
    var u = URL.createObjectURL(new Blob([fmtRaw], { type: "text/plain;charset=utf-8" })), l = document.createElement("a");
    l.href = u; l.download = "unreadable-format-rules.txt"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
  });
  reloadFmt();
  // ---- Pivot: a read-only summary table, built when asked from the values shown now ----
  var pvOut = $("pivot-out"), pvSt = $("pivot-status");
  // The table describes the tab and cells it was built from, so it is cleared whenever the open tab or its cells are replaced (tab switch, add, delete, restore, import, clear, undo and redo).
  function clearPivot() { if (pvOut && pvOut.firstChild) { pvOut.textContent = ""; pvSt.textContent = "The pivot was cleared because the sheet or tab changed. Build it again to see current values."; } }
  $("pivot-build").addEventListener("click", function () {
    pvOut.textContent = "";
    var sp = M.makePivot($("pivot-range").value, $("pivot-key").value, $("pivot-val").value, $("pivot-agg").value, $("pivot-header").checked, F.parseAddr);
    if (sp.error) { pvSt.textContent = sp.error; return; }
    var spec = sp.spec, sh = mkSheet(cells), pairs = [];
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
  function applyFilter() { applyFilter0(); if (table.parentNode) winUpdate(); }  // rows a filter just showed must get their inputs
  function applyFilter0() {
    showAllRows(); $("filter-clear").disabled = !filt;
    if (!filt) { fstat.textContent = ""; return; }
    var last = lastRow(), sh = mkSheet(cells), res = M.filterRows(function (a) { return sh.get(a); }, F.isErr, 1, Math.max(1, last), filt.col, filt.op, filt.val, filt.header);
    if (res.error) { fstat.textContent = res.error; return; }
    for (var i = 1; i <= last; i++) table.rows[i].hidden = !res.vis[i];
    fstat.textContent = "Showing " + res.shown + " of " + res.total + " rows. Hidden rows are only hidden on screen: nothing is deleted and the filter is not saved." + (res.shown === 0 ? " No rows match." : "");
    var ae = document.activeElement, aeRow = ae && ae.dataset && ae.dataset.addr ? F.parseAddr(ae.dataset.addr).row : 0, curRow = cur ? F.parseAddr(cur).row : 0;
    if ((aeRow && rowHidden(aeRow)) || (ae === document.body && curRow && rowHidden(curRow))) {
      var hr = aeRow || curRow, best = 0, d;
      for (d = 1; d <= ROWS && !best; d++) { if (hr - d >= 1 && !rowHidden(hr - d)) best = hr - d; else if (hr + d <= ROWS && !rowHidden(hr + d)) best = hr + d; }
      var colL = cur ? cur.replace(/[0-9]+$/, "") : "A";
      if (best) { inpFor(colL + best).focus(); fstat.textContent = "Row " + hr + " no longer matches the filter and is hidden. Focus moved to row " + best + ". " + fstat.textContent; }
      else { $("filter-clear").focus(); fstat.textContent = "Row " + hr + " no longer matches the filter and is hidden, and no row matches. " + fstat.textContent; }
    }
  }
  $("filter-go").addEventListener("click", function () {
    var col = colNum($("filter-col").value);
    if (!col || col > COLS) { fstat.textContent = "Enter a column from A to " + String.fromCharCode(64 + COLS) + "."; return; }
    if (frz.r) { fstat.textContent = "This tab has " + frz.r + " frozen row(s). A filter would hide rows between frozen and scrolling rows, so it is refused. Set frozen rows to 0 first."; return; }
    var f = { col: col, op: $("filter-op").value, val: $("filter-val").value, header: $("filter-header").checked };
    var sh = mkSheet(cells), test = M.filterRows(function (a) { return sh.get(a); }, F.isErr, 1, Math.max(1, lastRow()), f.col, f.op, f.val, f.header);
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
    var t2 = $("sort-col2").value.trim(), key2 = t2 ? colNum(t2) : 0;
    if (t2 && !key2) { status.textContent = "Enter one column letter for the second sort, or leave it empty."; return; }
    if (mkSheet(cells).blockCells().some(function (k) { var q = F.parseAddr(k); return q && q.col >= rg.c1 && q.col <= rg.c2 && q.row >= rg.r1 && q.row <= rg.r2; })) { status.textContent = "Nothing was sorted: the range includes cells that only show a SORT result. Sort moves what is typed, not what is shown. Pick a range that does not include them."; return; }
    var r = M.sortRange(cells, F.parseAddr, rg, key, $("sort-dir").value === "desc", $("sort-header").checked, key2);
    if (r.error) { status.textContent = r.error; return; }
    var note = "Sorted " + r.rows + " rows by column " + String.fromCharCode(64 + key) + (key2 ? " then column " + String.fromCharCode(64 + key2) : "") + "; " + r.moved + " moved. Undo restores the old order." + (filt ? " The filter is still on." : "") + outsideFormulas(rg);
    if (!change(r.cells, note)) status.textContent = "Already in that order. Nothing changed.";
  });
  $("dedup-go").addEventListener("click", function () {
    if (heldMsg()) return;  // keep the held-sheet notice instead of a generic line
    var rg = parseRange($("sort-range").value);
    if (!rg) { status.textContent = "Enter a range like A1:C10."; return; }
    if ((rg.c2 - rg.c1 + 1) * (rg.r2 - rg.r1 + 1) > 2000 || rg.c2 > 26 || rg.r2 > 1000) { status.textContent = "The range can hold up to 2000 cells."; return; }
    var r = M.dedupRange(cells, rg, $("sort-header").checked);
    if (r.error) { status.textContent = r.error; return; }
    if (!r.removed) { status.textContent = "No duplicate rows found. Nothing changed."; return; }
    var note = "Removed " + r.removed + " duplicate row" + (r.removed === 1 ? "" : "s") + " from " + r.rows + "; the rows below moved up and the last " + r.removed + " row" + (r.removed === 1 ? " of the range was" : "s of the range were") + " emptied. Undo brings them back." + (filt ? " The filter is still on." : "") + outsideFormulas(rg);
    if (!change(r.cells, note)) status.textContent = "Nothing changed.";
  });
  $("blank-go").addEventListener("click", function () {
    if (heldMsg()) return;
    var rg = parseRange($("sort-range").value);
    if (!rg) { status.textContent = "Enter a range like A1:C10."; return; }
    if ((rg.c2 - rg.c1 + 1) * (rg.r2 - rg.r1 + 1) > 2000 || rg.c2 > 26 || rg.r2 > 1000) { status.textContent = "The range can hold up to 2000 cells."; return; }
    var r = M.blankRowsRange(cells, rg, $("sort-header").checked);
    if (r.error) { status.textContent = r.error; return; }
    if (!r.removed) { status.textContent = "No blank rows found. Nothing changed."; return; }
    var note = "Removed " + r.removed + " blank row" + (r.removed === 1 ? "" : "s") + " from " + r.rows + "; the rows below moved up and the last " + r.removed + " row" + (r.removed === 1 ? " of the range was" : "s of the range were") + " emptied. Undo brings them back." + (filt ? " The filter is still on." : "") + outsideFormulas(rg);
    if (!change(r.cells, note)) status.textContent = "Nothing changed.";
  });
  $("trim-go").addEventListener("click", function () {
    if (heldMsg()) return;
    var rg = parseRange($("sort-range").value);
    if (!rg) { status.textContent = "Enter a range like A1:C10."; return; }
    if ((rg.c2 - rg.c1 + 1) * (rg.r2 - rg.r1 + 1) > 2000 || rg.c2 > 26 || rg.r2 > 1000) { status.textContent = "The range can hold up to 2000 cells."; return; }
    var r = M.trimRange(cells, rg), fnote = (r.formulas ? " " + r.formulas + " formula" + (r.formulas === 1 ? " was" : "s were") + " left as typed." : "") + (r.kept ? " " + r.kept + " cell" + (r.kept === 1 ? " was" : "s were") + " left as typed because trimming would turn " + (r.kept === 1 ? "it" : "them") + " into a formula or TRUE/FALSE." : "");
    if (!r.changed) { status.textContent = "No extra spaces found. Nothing changed." + fnote; return; }
    if (!change(r.cells, "Trimmed spaces in " + r.changed + " cell" + (r.changed === 1 ? "" : "s") + ". Undo brings the old text back." + fnote)) status.textContent = "Nothing changed.";
  });
  [["case-upper", "upper", "UPPERCASE"], ["case-lower", "lower", "lowercase"]].forEach(function (c) {
    $(c[0]).addEventListener("click", function () {
      if (heldMsg()) return;
      var rg = parseRange($("sort-range").value);
      if (!rg) { status.textContent = "Enter a range like A1:C10."; return; }
      if ((rg.c2 - rg.c1 + 1) * (rg.r2 - rg.r1 + 1) > 2000 || rg.c2 > 26 || rg.r2 > 1000) { status.textContent = "The range can hold up to 2000 cells."; return; }
      var r = M.caseRange(cells, rg, c[1], MAXCELL), extra = (r.formulas ? " " + r.formulas + " formula" + (r.formulas === 1 ? " was" : "s were") + " left as typed." : "") + (r.kept ? " " + r.kept + " cell" + (r.kept === 1 ? " was" : "s were") + " left as typed because " + (r.kept === 1 ? "it reads" : "they read") + " as a number or TRUE/FALSE." : "") + (r.long ? " " + r.long + " cell" + (r.long === 1 ? " was" : "s were") + " left as typed because the result would be too long." : "");
      if (r.error) { status.textContent = r.error; return; }
      if (!r.changed) { status.textContent = "No text to change. Nothing changed." + extra; return; }
      if (!change(r.cells, "Made " + r.changed + " cell" + (r.changed === 1 ? "" : "s") + " " + c[2] + ". Undo brings the old text back." + extra)) status.textContent = "Nothing changed.";
    });
  });
  // ---- Frozen rows and columns (view only, per tab). Model: loadFreeze, freezeJson, parseFreeze in model.js. A frozen row or column
  // pins under the column letters or beside the row numbers, at the offset measured from the real sizes. Not saved in Export (.json) or CSV. ----
  var fzKey = KEY + ".freeze", fzMap = Object.create(null), fzLocked = false, frz = { r: 0, c: 0 }, fzDeleted = null;
  function tabIds() { return meta.tabs.map(function (t) { return t.id; }); }
  function readFreeze() {
    var raw = null, ok = true; try { raw = localStorage.getItem(fzKey); } catch (e) { ok = false; }
    var l = ok ? M.loadFreeze(raw, tabIds()) : { map: Object.create(null), unreadable: true };
    fzMap = l.map; fzLocked = l.unreadable;
  }
  function saveFreeze() {
    if (fzLocked) return false;
    var cur = null; try { cur = M.loadFreeze(localStorage.getItem(fzKey), tabIds()); } catch (e) { return false; }
    if (cur.unreadable) return false;
    var m = cur.map; if (fzMap[meta.active] && (fzMap[meta.active].r || fzMap[meta.active].c)) m[meta.active] = fzMap[meta.active]; else delete m[meta.active];
    try { var j = M.freezeJson(m); if (j === null) localStorage.removeItem(fzKey); else localStorage.setItem(fzKey, j); fzMap = m; return true; } catch (e) { return false; }
  }
  // Sticky left or top for the cells of one drawn row; also called when a row is drawn later.
  function frzCellsOf(r) {
    if (!frzLefts || (!frz.r && !frz.c)) return; var row = table.rows[r];
    for (var c = 1; c <= COLS; c++) {
      var inR = r <= frz.r, inC = c <= frz.c; if (!inR && !inC) continue;
      var td = row.cells[c]; if (!td) continue; td.classList.add("sheet-frz"); td.style.position = "sticky";
      if (inR) td.style.top = (frzTops[r - 1] + fzOff) + "px";
      if (inC) td.style.left = frzLefts[c - 1] + "px";
      td.style.zIndex = inR && inC ? "2" : "1";
    }
  }
  function applyFreeze() {
    Array.prototype.forEach.call(table.querySelectorAll(".sheet-frz"), function (n) { n.classList.remove("sheet-frz"); n.style.position = ""; n.style.top = ""; n.style.left = ""; n.style.zIndex = ""; });
    Array.prototype.forEach.call(table.rows[0].cells, function (n) { n.style.top = ""; });
    fzBase = null; table.parentNode.style.scrollPaddingTop = ""; table.parentNode.style.scrollPaddingLeft = "";
    var f = fzMap[meta.active] || { r: 0, c: 0 }; frz = { r: f.r, c: f.c }; frzTop = frz.r; frzLefts = null; frzTops = null;
    if (frz.r) { var fr0 = []; for (var fi = 1; fi <= frz.r; fi++) if (!mat[fi]) { buildRow(fi); fr0.push(fi); } if (fr0.length) paintRows(fr0); }
    $("frz-rows").value = String(frz.r); $("frz-cols").value = String(frz.c);
    if (!frz.r && !frz.c) return;
    var hh = table.rows[0].offsetHeight, rw = table.rows[1].cells[0].offsetWidth, tops = [hh], lefts = [rw], i;
    for (i = 1; i <= frz.r; i++) tops.push(tops[i - 1] + table.rows[i].offsetHeight);
    for (i = 1; i <= frz.c; i++) lefts.push(lefts[i - 1] + table.rows[1].cells[i].offsetWidth);
    frzTops = tops; frzLefts = lefts;
    Object.keys(mat).forEach(function (k) { frzCellsOf(+k); });
    for (c = 1; c <= frz.c; c++) { var th = table.rows[0].cells[c]; th.style.left = lefts[c - 1] + "px"; th.style.zIndex = "4"; th.classList.add("sheet-frz"); }
    for (r = 1; r <= frz.r; r++) { var rh = table.rows[r].cells[0]; rh.style.top = tops[r - 1] + "px"; rh.style.zIndex = "3"; rh.classList.add("sheet-frz"); }
    fzBase = tops; fzLeft = lefts[lefts.length - 1]; placeFreeze();
  }
  // The formula bar is sticky at the top of the page, so when the page is scrolled until the grid's top edge is under it, the column letters and
  // frozen rows would sit hidden behind it. While a freeze is on, shift the pinned header and rows down inside the grid by the covered height.
  var fzBase = null, fzOff = 0, fzRaf = 0, fzLeft = 0;
  function placeFreeze() {
    if (!fzBase) return;
    var bar = $("sheet-barrow"), br = table.parentNode.getBoundingClientRect(), d = bar ? Math.max(0, Math.round(bar.getBoundingClientRect().bottom - br.top)) : 0;
    d = Math.min(d, Math.max(0, table.parentNode.clientHeight - fzBase[fzBase.length - 1] - 40));
    if (d === fzOff && table.rows[0].cells[0].style.top !== "") return;
    fzOff = d;
    var sc = table.parentNode; sc.style.scrollPaddingTop = (fzBase[fzBase.length - 1] + d) + "px"; sc.style.scrollPaddingLeft = (fzLeft || 0) + "px";
    Array.prototype.forEach.call(table.rows[0].cells, function (n) { n.style.top = d + "px"; });
    for (var r = 1; r <= frz.r; r++) {
      table.rows[r].cells[0].style.top = (fzBase[r - 1] + d) + "px";
      for (var c = 1; c <= COLS; c++) table.rows[r].cells[c].style.top = (fzBase[r - 1] + d) + "px";
    }
  }
  window.addEventListener("scroll", function () { if (!fzBase || fzRaf) return; fzRaf = requestAnimationFrame(function () { fzRaf = 0; placeFreeze(); }); }, { passive: true });
  function saveFreezeFor() { if (fzLocked) return; var cur = null; try { cur = M.loadFreeze(localStorage.getItem(fzKey), tabIds()); } catch (e) { return; } if (cur.unreadable) return; try { var j = M.freezeJson(cur.map); if (j === null) localStorage.removeItem(fzKey); else localStorage.setItem(fzKey, j); fzMap = cur.map; } catch (e) { /* the entry of a tab that is not listed is ignored when read */ } }
  window.addEventListener("resize", function () { applyFreeze(); });
  var fzStat = $("frz-status");
  $("frz-apply").addEventListener("click", function () {
    if (fzLocked) { fzStat.textContent = "The stored freeze setting could not be read, so it was left untouched and changes are off."; return; }
    var p = M.parseFreeze($("frz-rows").value, $("frz-cols").value); if (p.error) { fzStat.textContent = p.error; return; }
    if (p.r && filt) { fzStat.textContent = "A filter is on, so rows cannot be frozen (hidden rows would sit between frozen and scrolling rows). Show all rows first. Nothing changed."; return; }
    if (!tabStillListed(meta.active)) { fzStat.textContent = "This tab was changed in another window. Reload this page. Nothing changed."; return; }
    var before = fzMap[meta.active];
    if (p.r || p.c) fzMap[meta.active] = p; else delete fzMap[meta.active];
    if (!saveFreeze()) { if (before) fzMap[meta.active] = before; else delete fzMap[meta.active]; fzStat.textContent = "This browser would not store the setting. Nothing changed."; return; }
    applyFreeze();
    fzStat.textContent = p.r || p.c ? "Frozen: the first " + p.r + " row(s) and " + p.c + " column(s) of this tab stay in view. Saved for this tab only, in this browser." : "Nothing is frozen on this tab.";
  });
  // ---- Column widths (view only, per tab; model in model.js). A width belongs to a column position, so inserting or deleting a column does not move it.
  // Stored in the tab's own ".cw" entry, carried by Delete and Restore deleted tab. Not in Export (.json), CSV or saved versions. ----
  function cwKey(id) { return tabKey(id) + ".cw"; }
  var cwMap = Object.create(null), cwLocked = false, cwReady = false, cwStat = $("cw-status");
  function paintCw() {
    for (var c = 1; c <= COLS; c++) {
      var L = String.fromCharCode(64 + c), px = cwMap[L] ? cwMap[L] + "px" : "";
      for (var r in mat) inputs[L + r].style.width = px;
    }
  }
  function reloadCw() {
    var raw = null, ok = true; try { raw = localStorage.getItem(cwKey(meta.active)); } catch (e) { ok = false; }
    var l = ok ? M.loadWidths(raw) : { w: Object.create(null), unreadable: true };
    cwMap = l.w; cwLocked = l.unreadable; paintCw();
    var sh = Object.keys(cwMap).sort().map(function (k) { return k + " " + cwMap[k]; }).join(", ");
    $("cw-list").textContent = cwLocked ? "The stored column widths of this tab could not be read, so standard widths are shown and changes are off." : (sh ? "Custom widths on this tab: " + sh + " px." : "All columns have the standard width (" + M.DEFAULT_CW + " px).");
  }
  function writeCw(next) {
    if (cwLocked) { cwStat.textContent = "The stored column widths could not be read, so they were left untouched and changes are off."; return; }
    if (!tabStillListed(meta.active)) { cwStat.textContent = "This tab was changed in another window. Reload this page. Nothing changed."; return; }
    var key = cwKey(meta.active), j = M.widthsJson(next), old = null;
    try { old = localStorage.getItem(key); if (j === null) localStorage.removeItem(key); else localStorage.setItem(key, j); }
    catch (e) { try { if (old === null) localStorage.removeItem(key); else localStorage.setItem(key, old); } catch (e2) { /* the read below shows what is stored */ } cwStat.textContent = "This browser would not store the width. Nothing changed."; reloadCw(); applyFreeze(); return; }
    reloadCw(); applyFreeze();
    return true;
  }
  $("cw-apply").addEventListener("click", function () {
    var p = M.parseWidth($("cw-col").value, $("cw-px").value); if (p.error) { cwStat.textContent = p.error; return; }
    var next = Object.assign(Object.create(null), cwMap); if (p.px === M.DEFAULT_CW) delete next[p.col]; else next[p.col] = p.px;
    if (writeCw(next)) cwStat.textContent = "Column " + p.col + " is " + p.px + " px wide on this tab. Saved for this tab only, in this browser.";
  });
  $("cw-reset").addEventListener("click", function () {
    if (!Object.keys(cwMap).length && !cwLocked) { cwStat.textContent = "Nothing to reset: every column has the standard width."; return; }
    if (cwLocked) { cwStat.textContent = "The stored column widths could not be read, so they were left untouched and changes are off."; return; }
    if (writeCw(Object.create(null))) cwStat.textContent = "All columns on this tab are back to the standard width.";
  });
  reloadCw();
  readFreeze(); applyFreeze();

  // ---- Tabs UI (model in model.js). Export, CSV, chart, sort and filter work on the tab you are on. ----
  var tabBar = $("sheet-tabs"), tabName = $("tab-name"), tabMsg = $("tab-status");
  function saveMeta(m) { try { localStorage.setItem(KEY + ".tabs", JSON.stringify(m)); return true; } catch (e) { return false; } }
  function commitMeta(next) {
    if (heldMsg()) { tabMsg.textContent = status.textContent; return false; }
    if (tabsLocked) { tabMsg.textContent = "The stored tab list could not be read, so tab changes are off. Your cells were not touched."; return false; }
    var curRaw = null, readOk = true; try { curRaw = localStorage.getItem(KEY + ".tabs"); } catch (e) { readOk = false; }
    var other = readOk ? M.loadTabs(curRaw) : null;
    if (!other || other.unreadable || JSON.stringify(other.meta.tabs) !== JSON.stringify(meta.tabs)) { tabMsg.textContent = "The tab list was changed in another window or tab of this browser. Reload this page to continue. Nothing was changed here."; return false; }
    if (other.meta.v === 2 && next.v !== 2) next = M.markFmt(next);  // a window opened before the marker was set must not lower it
    if (!saveMeta(next)) { tabMsg.textContent = "This browser would not store the tab list, so nothing was changed."; return false; }
    meta = next; return true;
  }
  var EXTRAS = [{ key: rulesKey, load: M.loadRules, label: "validation rules" }, { key: cfKey, load: M.loadCf, label: "colour rules" }, { key: fmtKey, load: M.loadFmt, label: "format rules" }, { key: verKey, load: M.loadVers, label: "saved versions" }, { key: cwKey, load: M.loadWidths, label: "column widths" }];
  // an id that still has a stored freeze entry (for example when removing it failed) is never handed out again, so a new tab cannot inherit it
  function freezeHas(id) { var raw = localStorage.getItem(fzKey); return raw !== null && raw.indexOf("\"" + id + "\":") >= 0; }
  function keyUsed(id) { try { return localStorage.getItem(tabKey(id)) !== null || localStorage.getItem(rulesKey(id)) !== null || localStorage.getItem(cfKey(id)) !== null || localStorage.getItem(fmtKey(id)) !== null || localStorage.getItem(bakKeyOf(id)) !== null || freezeHas(id) || localStorage.getItem(verKey(id)) !== null || localStorage.getItem(cwKey(id)) !== null; } catch (e) { return true; } }
  function renderTabs() {
    tabBar.textContent = "";
    meta.tabs.forEach(function (t) {
      var b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = t.name; b.dataset.tab = t.id;
      b.setAttribute("aria-pressed", t.id === meta.active ? "true" : "false"); if (t.id === meta.active) b.setAttribute("aria-current", "true");
      b.addEventListener("click", function () { switchTo(t.id, b); }); tabBar.appendChild(b);
    });
    var ct = meta.tabs.filter(function (t) { return t.id === meta.active; })[0]; if (document.activeElement !== tabName) tabName.value = ct.name;
    var dl = $("tab-raw"); dl.hidden = !(unreadableTabs[meta.active] && rawTabs[meta.active] !== undefined);
    $("tab-add").disabled = tabsLocked || meta.tabs.length >= M.MAX_TABS; $("tab-rename").disabled = tabsLocked; (function () { var ai = meta.tabs.findIndex(function (t) { return t.id === meta.active; }); $("tab-left").disabled = tabsLocked || ai <= 0; $("tab-right").disabled = tabsLocked || ai < 0 || ai >= meta.tabs.length - 1; })(); $("tab-dup").disabled = tabsLocked || meta.tabs.length >= M.MAX_TABS || !!unreadableTabs[meta.active]; $("tab-del").disabled = tabsLocked || meta.tabs.length <= 1;
    $("tab-restore").disabled = tabsLocked || !lastDeleted || meta.tabs.length >= M.MAX_TABS;
  }
  function switchTo(id, keep) {
    var next = M.setActive(meta, id); if (!next) return;
    if (!commitMeta(next)) return;
    H = histFor(id); chartRange = null; out.textContent = ""; cur = null; bar.value = ""; sel.textContent = ""; showVal();
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
    tabMsg.textContent = "Added a tab. A formula can read it by name, like =Name!A1."; tabName.focus(); tabName.select();
  });
  function moveActive(dir) {
    var next = M.moveTab(meta, meta.active, dir);
    if (!next) { tabMsg.textContent = "This tab is already at the " + (dir < 0 ? "start" : "end") + " of the list."; return; }
    if (!commitMeta(next)) return;
    renderTabs(); tabMsg.textContent = "Moved " + meta.tabs.filter(function (t) { return t.id === meta.active; })[0].name + " " + (dir < 0 ? "left" : "right") + ". Only the tab order changed; the cells were not touched.";
  }
  $("tab-left").addEventListener("click", function () { moveActive(-1); });
  $("tab-right").addEventListener("click", function () { moveActive(1); });
  $("tab-dup").addEventListener("click", function () {
    if (heldMsg()) { tabMsg.textContent = status.textContent; return; }
    if (unreadableTabs[meta.active]) { tabMsg.textContent = "The stored cells of this tab could not be read, so it cannot be copied."; return; }
    var from = meta.tabs.filter(function (t) { return t.id === meta.active; })[0].name, next = M.duplicateTab(meta, meta.active, keyUsed);
    if (!next) { tabMsg.textContent = "A file holds up to " + M.MAX_TABS + " tabs, so this tab was not copied."; return; }
    var key = tabKey(next.active);
    try { localStorage.setItem(key, JSON.stringify(H.cells)); } catch (e) { tabMsg.textContent = "This browser would not store the copy, so nothing was changed."; return; }
    if (!commitMeta(next)) { try { localStorage.removeItem(key); } catch (e2) { /* the new id is not in the tab list, and keyUsed keeps it from being handed out */ } return; }
    H = histFor(meta.active); chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    var nn = meta.tabs.filter(function (t) { return t.id === meta.active; })[0].name;
    tabMsg.textContent = "Copied the cells of " + from + " to the new tab " + nn + ". Formats, colour and validation rules, frozen rows, column widths and saved versions were not copied, and a formula that names " + from + " still reads the original.";
  });
  $("tab-rename").addEventListener("click", function () {
    var r = M.renameTab(meta, meta.active, tabName.value); if (r.error) { tabMsg.textContent = r.error; return; }
    var oldName = meta.tabs.filter(function (t) { return t.id === meta.active; })[0].name;
    if (oldName.toLowerCase() !== r.name.toLowerCase()) {
      var rf = refsToTab(oldName);
      if (rf.unknown) { tabMsg.textContent = "Another tab's cells could not be read, so it cannot be checked for formulas that use the name " + oldName + ". Nothing was renamed."; return; }
      if (rf.formulas && !window.confirm(rf.formulas + " formula(s) on " + rf.tabs + " tab(s) (for example " + rf.example + ") refer to this tab by the name " + oldName + ". They are not rewritten, so after renaming they show #REF! until a tab is named " + oldName + " again. Rename anyway?")) { tabMsg.textContent = "Rename cancelled. Nothing was changed."; return; }
    }
    if (!commitMeta(r.meta)) return; renderTabs(); sync(); tabMsg.textContent = "Renamed to " + r.name + "." + (r.cut ? " The name was shortened to 30 characters." : "");
  });
  tabName.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); $("tab-rename").click(); } });
  $("tab-del").addEventListener("click", function () {
    if (heldMsg()) { tabMsg.textContent = status.textContent; return; }
    var id = meta.active, n = Object.keys(H.cells).length, nm = meta.tabs.filter(function (t) { return t.id === id; })[0].name, bad = !!unreadableTabs[id];
    var r = M.deleteTab(meta, id); if (r.error) { tabMsg.textContent = r.error; return; }
    var q = "";
    if (bad) q += "The stored cells of the tab " + nm + " could not be read. They stay in this browser's storage but will no longer be shown, and cannot be restored here. Use Download unreadable cells first if you want a copy. ";
    else if (n) q += "Delete the tab " + nm + " and its " + n + " cells? ";
    if (lastDeleted && !bad) q += (q ? "" : "Delete the tab " + nm + "? ") + "Only the most recently deleted tab can be restored, so this replaces the restorable tab " + lastDeleted.name + ", which will be lost. ";
    var rfd = bad ? { formulas: 0 } : refsToTab(nm);
    if (!bad && rfd.unknown) { tabMsg.textContent = "Another tab's cells could not be read, so it cannot be checked for formulas that use the name " + nm + ". Nothing was deleted."; return; }
    if (!bad && rfd.formulas) q += (q ? "" : "Delete the tab " + nm + "? ") + rfd.formulas + " formula(s) on " + rfd.tabs + " tab(s) (for example " + rfd.example + ") refer to it by name and will show #REF! until it is restored. ";
    if (q && !bad) q += "You can bring this tab back with Restore deleted tab until you reload this page. After a reload it cannot be brought back.";
    if (q && !window.confirm(q)) { tabMsg.textContent = "Delete cancelled."; return; }
    var saved = bad ? null : M.copy(H.cells);
    if (!commitMeta(r.meta)) return;
    if (!bad) { try { localStorage.removeItem(tabKey(id)); } catch (e) { /* the tab is out of the list; its id will not be reused while the cells remain */ } }
    delete hist[id]; delete unreadableTabs[id]; if (!bad) { var ex = [], left = false; EXTRAS.forEach(function (x) { var raw = null, was = false; try { raw = localStorage.getItem(x.key(id)); was = raw !== null; if (raw !== null && !x.load(raw, F.parseAddr).unreadable) localStorage.removeItem(x.key(id)); else raw = null; } catch (e) { raw = null; } ex.push(raw); if (raw === null && was) left = true; }); lastDeleted = { name: nm, cells: saved, extras: ex, rulesLeft: left }; }
    fzDeleted = bad ? null : (fzMap[id] || null); delete fzMap[id]; var keepA = meta.active; saveFreezeFor(keepA);
    H = histFor(meta.active); chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = bad ? "Removed the tab " + nm + " from the list. Its unreadable stored cells were not deleted." : "Deleted the tab " + nm + ". Restore deleted tab brings back only this one, until you reload; after a reload it cannot be brought back.";
  });
  $("tab-restore").addEventListener("click", function () {
    if (!lastDeleted) return;
    if (heldMsg()) { tabMsg.textContent = status.textContent; return; }
    var next = M.addTab(meta, keyUsed); if (!next) { tabMsg.textContent = "A file holds up to " + M.MAX_TABS + " tabs. The deleted tab is still kept until you reload."; return; }
    var nm = lastDeleted.name, rn = M.renameTab(next, next.active, nm); if (!rn.error) next = rn.meta;
    var key = tabKey(next.active);
    try { localStorage.setItem(key, JSON.stringify(lastDeleted.cells)); } catch (e) { tabMsg.textContent = "This browser would not store the restored tab, so nothing was restored. The deleted tab is still kept until you reload."; return; }
    if (lastDeleted.extras[2] && next.v !== 2) next = M.markFmt(next);
    var done = [key];
    function undoAll() { try { done.forEach(function (k) { localStorage.removeItem(k); }); } catch (e2) { /* nothing else points at these keys */ } }
    for (var xi = 0; xi < EXTRAS.length; xi++) {
      if (!lastDeleted.extras[xi]) continue;
      try { localStorage.setItem(EXTRAS[xi].key(next.active), lastDeleted.extras[xi]); done.push(EXTRAS[xi].key(next.active)); }
      catch (e) { undoAll(); tabMsg.textContent = "This browser would not store the tab's " + EXTRAS[xi].label + ", so nothing was restored. The deleted tab and its rules are still kept until you reload. Try Restore again."; return; }
    }
    if (!commitMeta(next)) { undoAll(); return; }
    var left = lastDeleted.rulesLeft; H = histFor(meta.active); lastDeleted = null;
    if (fzDeleted) { fzMap[meta.active] = fzDeleted; saveFreeze(); } fzDeleted = null;
    chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); renderTabs();
    tabMsg.textContent = "Restored the tab" + (rn.error ? " under a new name, because " + nm + " is in use." : " " + nm + ".") + (left ? " Its validation, colour or format rules were unreadable or could not be removed, so they were not restored." : "");
  });

  // ---- Insert and delete a row or column. Four stored keys change together, with a recovery copy; see model.js structApply for what is and is not guaranteed. ----
  var SNAMES = ["cells", "validation rules", "colour rules", "format rules"];
  function structKeys(id) { return [tabKey(id), rulesKey(id), cfKey(id), fmtKey(id)]; }
  function bakKeyOf(id) { return tabKey(id) + ".structbak"; }
  var recTab = null;
  // A recovery copy on any tab means a structural change may have left the stored tab partly changed: nothing is saved until it is restored.
  function heldTab() { for (var i = 0; i < meta.tabs.length; i++) { var st = "storage"; try { st = M.structRecovery(localStorage, bakKeyOf(meta.tabs[i].id), structKeys(meta.tabs[i].id)); } catch (e) { st = "storage"; } if (st !== "none") return meta.tabs[i].id; } return null; }
  function heldMsg() {
    var id = heldTab(); if (id === null) return false;
    status.textContent = "A row or column change or a version restore did not finish, so the stored sheet may be partly changed. Nothing can be edited or saved until you use Restore in the notice above."; renderRecovery(false); return true;
  }
  function tabName2(id) { var t = meta.tabs.filter(function (x) { return x.id === id; })[0]; return t ? t.name : id; }
  var recSig = null;
  // heldMsg runs on every refused edit, including the blur that precedes a click on the Restore button: keep the notice as it is then, or the click would land on a replaced button.
  function renderRecovery(rebuild) {
    var box = $("struct-recovery"); if (!box) return; var id = heldTab(), sig = id === null ? "none" : id; recTab = id;
    if (rebuild === false && sig === recSig && (id === null || box.firstChild)) return; recSig = sig; box.textContent = "";
    if (id === null) { box.hidden = true; return; }
    var info = null; try { info = M.structRecoveryInfo(localStorage, bakKeyOf(id), structKeys(id)); } catch (e) { info = { state: "storage" }; }
    var p = document.createElement("p"); p.className = "cal-meta"; box.hidden = false;
    var b1 = document.createElement("button"); b1.type = "button"; b1.className = "btn"; b1.id = "struct-restore";
    if (info.state === "found") {
      p.textContent = "A row or column change or a version restore on the tab " + tabName2(id) + " did not finish, and the stored sheet may be partly changed. Editing and saving are off until it is restored. A copy of the stored text from before the change is kept in this browser. Restore puts these back as they were before the change: " + SNAMES.join(", ") + ".";
      b1.textContent = "Restore this tab from the recovery copy"; b1.addEventListener("click", function () { doRestore(false); }); box.appendChild(p); box.appendChild(b1);
    } else {
      p.textContent = info.state === "unreadable" ? "A recovery copy exists for the tab " + tabName2(id) + " but it cannot be read or does not match this tab, so it cannot be restored here. Editing and saving stay off. Download it first if you want a copy, then discard it." : "This browser would not let the page read the recovery copy for the tab " + tabName2(id) + ", so editing and saving stay off. Reload the page to try again.";
      box.appendChild(p);
      if (info.state === "unreadable") {
        var d1 = document.createElement("button"); d1.type = "button"; d1.className = "btn"; d1.id = "struct-dl"; d1.textContent = "Download the recovery copy (.txt)";
        d1.addEventListener("click", function () { var raw = null; try { raw = localStorage.getItem(bakKeyOf(id)); } catch (e) { raw = null; } if (raw === null) return; var u = URL.createObjectURL(new Blob([raw], { type: "text/plain" })), l = document.createElement("a"); l.href = u; l.download = "unreadable-recovery-copy.txt"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000); });
        var d2 = document.createElement("button"); d2.type = "button"; d2.className = "btn"; d2.id = "struct-discard"; d2.textContent = "Discard the unreadable recovery copy";
        d2.addEventListener("click", function () { if (!window.confirm("Discard the unreadable recovery copy for " + tabName2(id) + "? The stored cells and rules stay as they are now, which may be partly changed.")) return; try { localStorage.removeItem(bakKeyOf(id)); } catch (e) { /* checked on the next render */ } renderRecovery(); status.textContent = heldTab() === null ? "Discarded the recovery copy. The stored sheet was not changed by this." : "This browser would not remove the recovery copy."; });
        box.appendChild(d1); box.appendChild(d2);
      }
    }
  }
  function backToStorage(id) { delete hist[id]; if (id === meta.active) { H = histFor(id); chartRange = null; out.textContent = ""; cur = null; filt = null; sync(); } }
  function doRestore(force) {
    var id = recTab; if (id === null) return;
    if (force) {
      var info = M.structRecoveryInfo(localStorage, bakKeyOf(id), structKeys(id));
      var names = (force.keys || []).join(", ");
      if (!window.confirm("These stored items hold changes made after the failed change: " + names + ". Restoring replaces them with the older text from the recovery copy, and those later changes will be lost. Continue?")) { status.textContent = "Restore cancelled. Nothing was changed."; return; }
    }
    var r = M.structRecover(localStorage, bakKeyOf(id), structKeys(id), !!force);
    if (r.status === "restored") { backToStorage(id); renderRecovery(); status.textContent = "Restored the tab " + tabName2(id) + " to how it was before the unfinished change." + (r.bakLeft ? " The recovery copy could not be removed and is still there." : ""); if (r.bakLeft) renderRecovery(); return; }
    if (r.status === "conflict") {
      var box = $("struct-recovery"), keyNames = r.keys.map(function (k) { return SNAMES[structKeys(id).indexOf(k)]; });
      status.textContent = "Nothing was restored: " + keyNames.join(", ") + " hold text that is neither the old nor the new text from the unfinished change, so something else changed them. Restoring anyway would replace those later changes.";
      var fb = $("struct-force"); if (!fb) { fb = document.createElement("button"); fb.type = "button"; fb.className = "btn"; fb.id = "struct-force"; box.appendChild(fb); }
      fb.textContent = "Restore anyway, replacing: " + keyNames.join(", "); fb.onclick = function () { doRestore({ keys: keyNames }); }; return;
    }
    if (r.status === "unresolved") { status.textContent = "Not everything could be restored. Not as before: " + r.bad.map(function (k) { return SNAMES[structKeys(id).indexOf(k)]; }).join(", ") + ". The recovery copy was kept. Editing stays off."; renderRecovery(); return; }
    status.textContent = "The recovery copy could not be used (" + r.status + "). Nothing was changed."; renderRecovery();
  }
  function sameCells(a, b) { var ka = Object.keys(a), kb = Object.keys(b); return ka.length === kb.length && ka.every(function (k) { return has(b, k) && a[k] === b[k]; }); }
  function tabStillListed(id) { var lt = null; try { lt = M.loadTabs(localStorage.getItem(KEY + ".tabs")); } catch (e) { lt = null; } return !!lt && !lt.unreadable && lt.meta.tabs.some(function (t) { return t.id === id; }); }
  var STRUCT_WHY = { "recovery-exists": "an earlier row or column change or version restore left a recovery copy", stale: "the stored sheet was changed in another window or changed since this page showed it", storage: "this browser would not read the stored sheet", "no-recovery-copy": "this browser would not keep a recovery copy first", "recovery-stuck": "a recovery copy could not be cleaned up", "no-change": "it would not change anything" };
  function structFail(r, what) {
    if (r.status === "refused") { status.textContent = "Nothing was changed: " + (STRUCT_WHY[r.reason] || r.reason) + (r.reason === "stale" ? " (" + SNAMES[0 + structKeysIndex(r.key)] + ")" : "") + ". " + (r.reason === "stale" ? "Reload this page to see what is stored." : ""); renderRecovery(); return; }
    if (r.status === "failed-restored") { status.textContent = "The " + what + " could not be saved. Every stored item was read back as it was before, so nothing was changed."; return; }
    status.textContent = "The " + what + " could not be completed and the stored sheet may be partly changed. Not known to be as before: " + r.bad.map(function (k) { return SNAMES[structKeysIndex(k)]; }).join(", ") + ". A recovery copy was kept. Editing is off until you use Restore in the notice above."; renderRecovery();
  }
  function structKeysIndex(k) { return structKeys(meta.active).indexOf(k); }
  // Formulas that refer to this tab by name would point at the wrong cells after cells move, and they are not rewritten. So moving cells is refused while any exist.
  function xrefBlock() {
    var nm = meta.tabs.filter(function (t) { return t.id === meta.active; })[0].name, rf = refsToTab(nm);
    if (rf.unknown) { status.textContent = "Another tab's cells could not be read, so it cannot be checked for formulas that refer to this tab. Nothing was changed."; return true; }
    if (rf.formulas) { status.textContent = "Nothing was changed: " + rf.formulas + " formula(s) on " + rf.tabs + " tab(s) (for example " + rf.example + ") refer to this tab by name, and moving rows or columns would leave them pointing at the wrong cells. Change or remove those formulas first."; return true; }
    return false;
  }
  function structGo(axis, kind) {
    var id = meta.active, row = axis === "row";
    if (heldMsg()) return;
    if (!cur) { status.textContent = "Select a cell first. The row or column of the selected cell is used."; return; }
    if (tabsLocked || unreadableTabs[id]) { status.textContent = "The stored cells or tab list could not be read, so nothing was changed."; return; }
    if (rulesLocked || cfLocked || fmtLocked) { status.textContent = "This tab's validation, colour or format rules could not be read, so nothing was changed. The rules were left untouched."; return; }
    if (filt) { status.textContent = "A filter is on, so nothing was changed (it would also change rows you cannot see). Clear the filter first."; return; }
    if (!tabStillListed(id)) { status.textContent = "This tab was deleted or changed in another window, so nothing was changed. Reload this page."; return; }
    if (xrefBlock()) return;
    var a = F.parseAddr(cur); if (!a) return; var at = row ? a.row : a.col;
    var seen = JSON.stringify([rules, cfRules, fmtRules]); reloadRules(); reloadCf(); reloadFmt();
    if (rulesLocked || cfLocked || fmtLocked || JSON.stringify([rules, cfRules, fmtRules]) !== seen) { status.textContent = "The rules for this tab were changed in another window or could not be read, so nothing was changed. Reload this page."; return; }
    var rc = null; try { rc = localStorage.getItem(tabKey(id)); } catch (e) { rc = undefined; }
    if (rc === undefined) { status.textContent = "This browser would not read the stored cells, so nothing was changed."; return; }
    var okCells = rc === null ? !Object.keys(H.cells).length : (function () { try { var st0 = clean(JSON.parse(rc)); return !!st0 && sameCells(st0.cells, H.cells); } catch (e) { return false; } })();
    if (!okCells) { status.textContent = "The stored cells differ from what this page shows (not saved, or changed in another window), so nothing was changed."; return; }
    var res = M.structEdit({ cells: H.cells, rules: rules, cf: cfRules, fmt: fmtRules }, { axis: axis, kind: kind, at: at }, F.parseAddr, F.structFormula, { rows: ROWS, cols: COLS }, MAXCELL);
    if (res.error) { status.textContent = res.error; return; }
    var line = (row ? "row " + at : "column " + String.fromCharCode(64 + at)), st = res.stats, bits = [];
    if (kind === "delete") bits.push(st.removed + " filled cells in it are removed");
    if (st.formulas) bits.push(st.formulas + " formulas change" + (st.refErrs ? ", " + st.refErrs + " will show #REF!" : ""));
    if (st.dropped) bits.push(st.dropped + " rules lose their whole range and are removed");
    if (st.resized) bits.push(st.resized + " rule ranges are resized");
    if (kind === "delete" && !window.confirm("Delete " + line + "? " + bits.join("; ") + ". Undo brings it back until you reload this page.")) { status.textContent = "Delete cancelled. Nothing was changed."; return; }
    var next = [JSON.stringify(res.cells), res.rules.length ? M.rulesJson(res.rules) : null, res.cf.length ? M.cfJson(res.cf) : null, res.fmt.length ? M.fmtJson(res.fmt) : null];
    var expect = [rc, rulesRaw, cfRaw, fmtRaw];
    var r = M.structApply(localStorage, structKeys(id), bakKeyOf(id), expect, next);
    if (r.status !== "done") { structFail(r, (kind === "insert" ? "insert" : "delete")); return; }
    H.recordExternal(res.cells, { before: expect, after: next }, "");
    chartRange = null; out.textContent = ""; sync();
    status.textContent = (kind === "insert" ? "Inserted a new " + (row ? "row above row " : "column left of column ") + (row ? at : String.fromCharCode(64 + at)) : "Deleted " + line) + (bits.length && kind === "insert" ? ": " + bits.join("; ") : kind === "delete" ? ": " + bits.join("; ") : "") + ". The chart and pivot were cleared. Undo works until you reload this page." + (r.bakLeft ? " The recovery copy could not be removed and is still there." : "");
    if (r.bakLeft) renderRecovery();
  }
  // Undo or redo of a structural step: it applies only if all four stored items are exactly what that step left (or undid).
  function structStep(redo, m) {
    var id = meta.active, what = redo ? "redo" : "undo";
    if (!tabStillListed(id)) { status.textContent = "This tab was deleted or changed in another window, so nothing was changed. Reload this page."; return; }
    if (xrefBlock()) return;
    var r = M.structApply(localStorage, structKeys(id), bakKeyOf(id), redo ? m.before : m.after, redo ? m.after : m.before);
    if (r.status === "refused" && r.reason === "stale") { status.textContent = "Nothing was changed: the cells or rules were changed after the row, column or restore step (" + SNAMES[structKeysIndex(r.key)] + "), so it cannot be " + (redo ? "redone" : "undone") + " safely. Reload the page to see what is stored."; return; }
    if (r.status !== "done") { structFail(r, what); return; }
    if (redo) H.redoExternal(); else H.undoExternal();
    chartRange = null; out.textContent = ""; sync(); status.textContent = (redo ? "Redone" : "Undone") + ". The chart and pivot were cleared." + (r.bakLeft ? " The recovery copy could not be removed and is still there." : ""); if (r.bakLeft) renderRecovery();
  }
  ["row-ins", "row-del", "col-ins", "col-del"].forEach(function (id) { var b = $(id); if (b) b.addEventListener("click", function () { structGo(id.slice(0, 3) === "row" ? "row" : "col", id.slice(4) === "ins" ? "insert" : "delete"); }); });
  // ---- Named versions of this tab (model in model.js). Kept in this browser only, next to the tab's own stored items. Not a backup. ----
  function verKey(id) { return tabKey(id) + ".ver"; }
  var verItems = [], verLocked = false, verRaw = null, verSig = null, verList = $("ver-list"), verStat = $("ver-status");
  function readVers() {
    var raw = null; try { raw = localStorage.getItem(verKey(meta.active)); } catch (e) { verItems = []; verLocked = true; verRaw = null; return false; }
    var l = M.loadVers(raw); verItems = l.items; verLocked = l.unreadable; verRaw = raw; return true;
  }
  function verInfo(v) {
    var bits = [], st = null; try { st = v.k[0] === null ? { count: 0 } : clean(JSON.parse(v.k[0])); } catch (e) { st = null; }
    bits.push(st ? st.count + " cells" : "cells unreadable");
    [[1, M.loadRules, "validation"], [2, M.loadCf, "colour"], [3, M.loadFmt, "format"]].forEach(function (x) { var l = v.k[x[0]] === null ? { rules: [], unreadable: false } : x[1](v.k[x[0]], F.parseAddr); bits.push(l.unreadable ? x[2] + " rules unreadable" : l.rules.length + " " + x[2] + " rules"); });
    return bits.join(", ");
  }
  function renderVers() {
    if (!verList) return; readVers(); var sig = meta.active + "|" + verLocked + "|" + verRaw; if (sig === verSig) return; verSig = sig; verList.textContent = "";
    if (verLocked) { var u = document.createElement("li"); u.textContent = "The saved versions for this tab could not be read, so they were left untouched and saving or deleting versions is off."; verList.appendChild(u); return; }
    if (!verItems.length) { var z = document.createElement("li"); z.textContent = "No saved versions on this tab."; verList.appendChild(z); return; }
    verItems.forEach(function (v, i) {
      var li = document.createElement("li"), t = document.createElement("span"), b1 = document.createElement("button"), b2 = document.createElement("button");
      t.textContent = (i + 1) + ". " + v.n + ": " + verInfo(v) + " "; b1.type = "button"; b1.className = "btn"; b1.textContent = "Restore"; b1.setAttribute("aria-label", "Restore the version " + v.n); b2.type = "button"; b2.className = "btn"; b2.textContent = "Delete"; b2.setAttribute("aria-label", "Delete the version " + v.n);
      b1.addEventListener("click", function () { verRestore(i, v.n); }); b2.addEventListener("click", function () { verDelete(i, v.n); });
      li.appendChild(t); li.appendChild(b1); li.appendChild(document.createTextNode(" ")); li.appendChild(b2); verList.appendChild(li);
    });
  }
  // The stored four texts of this tab, but only when they are exactly what this page shows. Otherwise a message and null.
  function verCurrent(what) {
    var id = meta.active;
    if (heldMsg()) { verStat.textContent = status.textContent; return null; }
    if (tabsLocked || unreadableTabs[id]) { verStat.textContent = "The stored cells or tab list could not be read, so nothing was " + what + ". Nothing changed."; return null; }
    if (!tabStillListed(id)) { verStat.textContent = "This tab was deleted or changed in another window, so nothing was " + what + ". Reload this page."; return null; }
    var seen = JSON.stringify([rules, cfRules, fmtRules]); reloadRules(); reloadCf(); reloadFmt();
    if (rulesLocked || cfLocked || fmtLocked || JSON.stringify([rules, cfRules, fmtRules]) !== seen) { verStat.textContent = "This tab's validation, colour or format rules were changed in another window or could not be read, so nothing was " + what + ". Reload this page."; return null; }
    var rc = null; try { rc = localStorage.getItem(tabKey(id)); } catch (e) { verStat.textContent = "This browser would not read the stored cells, so nothing was " + what + "."; return null; }
    var okCells = rc === null ? !Object.keys(H.cells).length : (function () { try { var st0 = clean(JSON.parse(rc)); return !!st0 && sameCells(st0.cells, H.cells); } catch (e) { return false; } })();
    if (!okCells) { verStat.textContent = "The stored cells differ from what this page shows (not saved on this device, or changed in another window), so nothing was " + what + "."; return null; }
    return [rc, rulesRaw, cfRaw, fmtRaw];
  }
  function verFresh(what) {
    var before = verRaw, before2 = verLocked; readVers();
    if (verLocked) { verStat.textContent = "The saved versions for this tab could not be read, so nothing was " + what + ". They were left untouched."; return false; }
    if (verRaw !== before) { verSig = null; renderVers(); verStat.textContent = "The saved versions were changed in another window, so nothing was " + what + ". The list now shows what is stored."; return false; }
    return true;
  }
  function verWrite(items) {
    var key = verKey(meta.active), old = verRaw, j = M.versJson(items);
    try { if (j === null) localStorage.removeItem(key); else localStorage.setItem(key, j); } catch (e) { return false; }
    var back = null; try { back = localStorage.getItem(key); } catch (e) { back = undefined; }
    if (back === j) return true;
    try { if (old === null) localStorage.removeItem(key); else localStorage.setItem(key, old); } catch (e) { /* the list is read back on the next render */ }
    return false;
  }
  $("ver-save").addEventListener("click", function () {
    if (verLocked) { verStat.textContent = "The saved versions for this tab could not be read, so saving is off. They were left untouched."; return; }
    var k = verCurrent("saved"); if (!k) return; if (!verFresh("saved")) return;
    var r = M.addVer(verItems, $("ver-name").value, k); if (r.error) { verStat.textContent = r.error + " Nothing was saved."; return; }
    if (!verWrite(r.items)) { verSig = null; renderVers(); verStat.textContent = "This browser would not store the version, so nothing was saved. Your cells, rules and earlier versions are unchanged."; return; }
    var nm = r.items[r.items.length - 1].n; $("ver-name").value = ""; verSig = null; renderVers();
    verStat.textContent = "Saved the version " + nm + ": " + verInfo(r.items[r.items.length - 1]) + ". It is kept in this browser only, with this tab.";
  });
  function verDelete(i, name) {
    if (verLocked) { verStat.textContent = "The saved versions for this tab could not be read, so deleting is off. They were left untouched."; return; }
    if (!verFresh("deleted")) return; if (!verItems[i] || verItems[i].n !== name) { verSig = null; renderVers(); verStat.textContent = "That version is no longer in the list. Nothing was deleted."; return; }
    var tab = meta.active, hh = H;
    if (!window.confirm("Delete the saved version " + name + "? It cannot be brought back. Your current cells and rules are not changed.")) { verStat.textContent = "Delete cancelled. Nothing was changed."; return; }
    if (meta.active !== tab || H !== hh) { verStat.textContent = "The tab changed while the question was open, so nothing was deleted."; return; }
    if (!verFresh("deleted")) return; var nx = M.dropVer(verItems, i); if (!nx || verItems[i].n !== name) { verStat.textContent = "That version is no longer in the list. Nothing was deleted."; return; }
    if (!verWrite(nx)) { verSig = null; renderVers(); verStat.textContent = "This browser would not update the saved versions, so nothing was deleted."; return; }
    verSig = null; renderVers(); verStat.textContent = "Deleted the version " + name + ".";
  }
  function verRestore(i, name) {
    var id = meta.active;
    if (filt) { verStat.textContent = "A filter is on, so nothing was restored (it would also change rows you cannot see). Clear the filter first."; return; }
    if (verLocked) { verStat.textContent = "The saved versions for this tab could not be read, so restoring is off. They were left untouched."; return; }
    var cur4 = verCurrent("restored"); if (!cur4) return; if (!verFresh("restored")) return;
    var v = verItems[i]; if (!v || v.n !== name) { verSig = null; renderVers(); verStat.textContent = "That version is no longer in the list. Nothing was restored."; return; }
    var st = null; try { st = v.k[0] === null ? { cells: Object.create(null), count: 0 } : clean(JSON.parse(v.k[0])); } catch (e) { st = null; }
    var rl = v.k[1] === null ? { unreadable: false } : M.loadRules(v.k[1], F.parseAddr), cl = v.k[2] === null ? { unreadable: false } : M.loadCf(v.k[2], F.parseAddr), fl = v.k[3] === null ? { unreadable: false } : M.loadFmt(v.k[3], F.parseAddr);
    if (!st || rl.unreadable || cl.unreadable || fl.unreadable) { verStat.textContent = "The version " + name + " holds stored text this page cannot read, so nothing was restored. Your tab was not changed."; return; }
    if (JSON.stringify(cur4) === JSON.stringify(v.k)) { verStat.textContent = "This tab is already exactly the version " + name + ". Nothing was changed."; return; }
    var have = Object.keys(H.cells).length, hh = H;
    if (!window.confirm("Restore the version " + name + " (" + verInfo(v) + ")? This replaces the " + have + " cells in this tab and its validation, colour and format rules with the saved ones. Undo works until you reload this page, and your saved versions are kept.")) { verStat.textContent = "Restore cancelled. Nothing was changed."; return; }
    if (meta.active !== id || H !== hh) { verStat.textContent = "The tab changed while the question was open, so nothing was restored."; return; }
    var again = verCurrent("restored"); if (!again || JSON.stringify(again) !== JSON.stringify(cur4)) { if (again) verStat.textContent = "This tab changed while the question was open, so nothing was restored."; return; }
    if (!verFresh("restored")) return;
    var r = M.structApply(localStorage, structKeys(id), bakKeyOf(id), cur4, v.k);
    if (r.status !== "done") { structFail(r, "restore"); verStat.textContent = status.textContent; return; }
    H.recordExternal(st.cells, { before: cur4, after: v.k.slice() }, "");
    chartRange = null; out.textContent = ""; sync();
    status.textContent = "Restored the version " + name + ": " + verInfo(v) + ". Undo works until you reload this page." + (r.bakLeft ? " The recovery copy could not be removed and is still there." : ""); verStat.textContent = status.textContent;
    if (r.bakLeft) renderRecovery();
  }
  renderVers();

  renderRecovery();
  status.textContent = H.note;
  if (heldTab() !== null) status.textContent = "A row or column change or a version restore did not finish. See the notice above.";
  if (tabsLocked) tabMsg.textContent = "The stored tab list could not be read, so tab changes are off. Your cells were not touched.";
  renderTabs();
  winUpdate(); show(); buttons();
})();
