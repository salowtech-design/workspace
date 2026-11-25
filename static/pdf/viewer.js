(function () {
  "use strict";
  var N = window.SalowPdfNotes, C = window.SalowPdfCheck, $ = function (i) { return document.getElementById(i); };
  var status = $("pdf-status"), canvas = $("pdf-canvas"), ctx = canvas.getContext("2d");
  var BASE = "/static/pdf/vendor/";
  var lib = null, doc = null, loadTask = null, seq = new C.Seq(), page = 1, zoom = 1, task = null, token = 0, settle = Promise.resolve();
  var mark = ""; // the query whose matches are marked in the page text, set by a successful find
  var shown = "", shownCut = false;
  // Draws the current page text, marking matches of `mark` with real elements built from text nodes.
  function paintText() {
    var tx = $("pdf-text");
    if (shown === null) return; // the page text is still being read
    if (!shown) { tx.textContent = "This page has no text the viewer can read. It may be a scanned image."; return; }
    tx.textContent = "";
    C.splitMatches(shown, mark).forEach(function (s) {
      if (!s.hit) { tx.appendChild(document.createTextNode(s.t)); return; }
      var m = document.createElement("mark"); m.textContent = s.t; tx.appendChild(m);
    });
    if (shownCut) tx.appendChild(document.createTextNode("\n[Shortened at 20000 characters.]"));
  }
  function nop() {}
  // Cancel the running render, and remember its settlement for every later draw and close.
  function cancelCurrent() {
    var t = task; task = null; if (!t) return;
    try { t.cancel(); } catch (e) { /* already done */ }
    settle = Promise.all([settle, t.promise.then(nop, nop)]).then(nop);
  }
  function loadLib() {
    if (lib) return lib;
    lib = import(BASE + "pdf.min.mjs").then(function (m) { m.GlobalWorkerOptions.workerSrc = BASE + "pdf.worker.min.mjs"; return m; });
    lib.catch(function () { lib = null; });
    return lib;
  }
  function controls() {
    var on = !!doc;
    $("pdf-prev").disabled = !on || page <= 1; $("pdf-next").disabled = !on || page >= doc.numPages;
    $("pdf-zin").disabled = !on || zoom >= 3; $("pdf-zout").disabled = !on || zoom <= 0.5;
    $("pdf-close").disabled = !on; $("pdf-page").disabled = !on; $("pdf-q").disabled = !on; $("pdf-find").disabled = !on || finding;
    $("pdf-where").textContent = on ? "Page " + page + " of " + doc.numPages + ", zoom " + Math.round(zoom * 100) + "%" : "No file open.";
    if (on) $("pdf-page").value = page;
  }
  function draw() {
    if (!doc) return;
    var my = ++token, pageNo = page; cancelCurrent(); drawn = 0; $("pdf-pins").textContent = "";
    // wait for every cancelled render to settle before the canvas is resized and reused
    settle.then(function () { return my === token ? doc.getPage(page) : null; }).then(function (pg) {
      if (!pg || my !== token) return;
      var wrap = $("pdf-stage").clientWidth || 600, v1 = pg.getViewport({ scale: 1 });
      var css = Math.min(wrap, 1600) / v1.width * zoom, dpr = Math.min(window.devicePixelRatio || 1, 2);
      var vp = pg.getViewport({ scale: css * dpr }), px = vp.width * vp.height;
      var shrink = Math.min(1, Math.sqrt(24e6 / px), 8192 / vp.width, 8192 / vp.height);
      if (shrink < 1) vp = pg.getViewport({ scale: css * dpr * shrink });
      canvas.width = Math.floor(vp.width); canvas.height = Math.floor(vp.height);
      canvas.style.width = Math.floor(v1.width * css) + "px"; canvas.style.height = Math.floor(v1.height * css) + "px";
      var tx = $("pdf-text"); shown = null; tx.textContent = "Reading the text of this page...";
      pg.getTextContent().then(function (tc) {
        if (my !== token) return;
        var r = C.takeText(tc.items, 20000), t = r.text;
        shown = t; shownCut = r.cut; paintText();
      }, function () { if (my === token) tx.textContent = "The text of this page could not be read."; });
      task = pg.render({ canvasContext: ctx, viewport: vp });
      return task.promise.then(function () { if (my === token) { status.textContent = ""; drawn = pageNo; renderPins(); } });
    }).catch(function (e) { if (my !== token || (e && e.name === "RenderingCancelledException")) return; status.textContent = "This page could not be drawn."; });
  }
  var fseq = new C.Seq(), finding = false;
  function fmsg(t) { $("pdf-fstatus").textContent = t; }
  function fend() { finding = false; $("pdf-fstop").disabled = true; controls(); }
  // Reads the text of each page in turn, stops at the first page with a match. Any file change, close,
  // new find or Stop makes the older find stale, and a stale find never writes anything.
  function find() {
    if (!doc || finding) return;
    var q = C.normQuery($("pdf-q").value);
    if (!q) { fmsg("Type 2 to 100 characters to find."); return; }
    if (mark) { mark = ""; if (shown !== null) paintText(); } // old marks never stay beside a new query
    var d = doc, mine = fseq.begin(), order = C.pageOrder(page, d.numPages), i = 0, partial = 0, unread = 0, failed = 0;
    finding = true; $("pdf-fstop").disabled = false; $("pdf-find").disabled = true;
    var live = function () { return fseq.current(mine) && doc === d; };
    function done() {
      if (!live()) return;
      var more = d.numPages > order.length ? " Only the first " + order.length + " pages in this order were searched." : "";
      var note = (partial ? " " + partial + " page(s) have more than 20000 characters, so only the first 20000 were searched." : "") + (unread ? " " + unread + " page(s) had no extractable text (scans are not read)." : "") + (failed ? " " + failed + " page(s) could not be read." : "");
      fmsg("No match found for \"" + q + "\" in " + order.length + " page(s)." + more + note + " This searches the extracted plain text only. A word split by the file's own layout can be missed, so this does not prove the text is absent."); fend();
    }
    function step() {
      if (!live()) return;
      if (i >= order.length) return done();
      var p = order[i++]; fmsg("Searching page " + p + " of " + d.numPages + "...");
      var held = null;
      d.getPage(p).then(function (pg) { held = pg; return pg.getTextContent(); }).then(function (tc) {
        if (held) { try { held.cleanup(); } catch (x) { /* already released */ } }
        if (!live()) return;
        var r = C.takeText(tc.items, 20000); if (r.cut) partial++; if (!r.text) unread++;
        var n = C.countIn(r.text, q);
        if (n > 0) { fmsg("Found \"" + q + "\" on page " + p + ": " + (n >= 500 ? "500 or more" : n) + " match(es)" + (r.cut ? " in the first 20000 characters" : "") + ". Press Find again for the next page."); fend(); mark = q; go(p); return; }
        setTimeout(step, 0);
      }, function () { if (held) { try { held.cleanup(); } catch (x) { /* already released */ } } if (!live()) return; failed++; setTimeout(step, 0); });
    }
    step();
  }
  $("pdf-find").addEventListener("click", find);
  $("pdf-q").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); find(); } });
  $("pdf-fstop").addEventListener("click", function () { if (!finding) return; fseq.begin(); fmsg("Find stopped."); fend(); });
  // ---- Notes: pinned text labels kept in this browser for this file only; never written into the PDF.
  var drawn = 0, nDoc = null, nKey = "", notes = [], nState = "off", pending = null, adding = false, clearArmed = false;
  var prefix = $("pdf-notes").getAttribute("data-prefix") || "salow.pdfnotes.";
  function nmsg(t) { $("pdf-notestatus").textContent = t; }
  function sorted() { return notes.slice().sort(function (a, b) { return a.page - b.page || a.y - b.y || a.x - b.x; }); }
  function nControls() {
    var on = nState === "ok" && !!doc;
    $("pdf-addnote").disabled = !on; $("pdf-exportnotes").disabled = !on || !notes.length;
    $("pdf-clearnotes").disabled = !(nState === "ok" || nState === "bad") || !doc;
    $("pdf-addnote").setAttribute("aria-pressed", adding ? "true" : "false");
    $("pdf-stage").classList.toggle("pdf-adding", adding);
  }
  function renderPins() {
    var layer = $("pdf-pins"); layer.textContent = "";
    if (!doc || nState !== "ok" || drawn !== page) return;
    var all = sorted();
    all.forEach(function (n, i) {
      if (n.page !== page) return;
      var b = document.createElement("button"); b.type = "button"; b.className = "pdf-pin";
      b.style.left = (n.x * 100) + "%"; b.style.top = (n.y * 100) + "%"; b.textContent = String(i + 1);
      b.title = n.text; b.setAttribute("aria-label", "Note " + (i + 1) + " on page " + n.page + ": " + n.text);
      b.addEventListener("click", function () { var li = document.getElementById("pdf-note-" + n.id); if (li) li.scrollIntoView({ block: "nearest" }); });
      layer.appendChild(b);
    });
  }
  function renderNotes() {
    var ol = $("pdf-notelist"); ol.textContent = "";
    sorted().forEach(function (n, i) {
      var li = document.createElement("li"); li.id = "pdf-note-" + n.id;
      li.appendChild(document.createTextNode("Page " + n.page + ": " + n.text + " "));
      var del = document.createElement("button"); del.type = "button"; del.className = "btn"; del.textContent = "Delete";
      del.setAttribute("aria-label", "Delete note " + (i + 1) + " on page " + n.page);
      del.addEventListener("click", function () { removeNote(n.id); });
      li.appendChild(del); ol.appendChild(li);
    });
    nControls(); renderPins();
  }
  function persist(next) {
    try { localStorage.setItem(nKey, N.dump(next)); return true; } catch (e) { return false; }
  }
  function removeNote(id) {
    if (!doc || doc !== nDoc || nState !== "ok") return;
    var next = N.remove(notes, id);
    if (!persist(next)) { nmsg("This browser would not save that change, so the note was not deleted."); return; }
    notes = next; nmsg("Note deleted."); renderNotes();
  }
  function notesClose() {
    nDoc = null; nKey = ""; notes = []; nState = "off"; pending = null; adding = false; clearArmed = false;
    $("pdf-noteform").hidden = true; $("pdf-notetext").value = ""; nmsg(""); renderNotes();
  }
  function notesOpen(d) {
    notesClose(); nDoc = d;
    var fp = d.fingerprints && d.fingerprints[0];
    if (typeof fp !== "string" || !/^[0-9a-f]{16,64}$/.test(fp)) { nmsg("Notes are not available for this file because the viewer could not identify it."); return; }
    nKey = prefix + fp;
    var raw; try { raw = localStorage.getItem(nKey); } catch (e) { nmsg("This browser will not keep notes, so notes are off."); return; }
    if (raw === null) {
      var count = 0; try { for (var i = 0; i < localStorage.length; i++) if ((localStorage.key(i) || "").indexOf(prefix) === 0) count++; } catch (e) { /* counted as zero */ }
      if (count >= N.MAX_FILES) { nmsg("Notes are kept for up to " + N.MAX_FILES + " files on this device. Open a file you no longer need and use Delete all notes for this file first."); return; }
    }
    var l = N.load(raw);
    if (l.unreadable) { nState = "bad"; nmsg("The saved notes for this file could not be read. They were left as they are and notes are off for this file. Delete all notes for this file to start again."); nControls(); return; }
    notes = l.notes; nState = "ok"; nmsg(notes.length ? notes.length + " saved note(s) for this file on this device." : "No notes yet for this file."); renderNotes();
  }
  $("pdf-addnote").addEventListener("click", function () {
    if (nState !== "ok") return; adding = !adding; pending = null; $("pdf-noteform").hidden = true;
    nmsg(adding ? "Click the page where the note belongs." : ""); nControls();
  });
  $("pdf-pagewrap").addEventListener("click", function (e) {
    if (!adding || nState !== "ok" || !doc || doc !== nDoc) return;
    if (drawn !== page) { nmsg("Wait for the page to finish drawing, then click again."); return; }
    var r = canvas.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return;
    pending = { page: page, x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height, doc: doc };
    $("pdf-noteformpage").textContent = String(page); $("pdf-noteform").hidden = false; $("pdf-notetext").focus(); nmsg("");
  });
  $("pdf-noteform").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!pending || pending.doc !== doc || doc !== nDoc || nState !== "ok") { nmsg("That page changed. Click the page again."); return; }
    var res = N.add(notes, pending.page, pending.x, pending.y, $("pdf-notetext").value);
    if (res.error) { nmsg(res.error); return; }
    if (!persist(res.notes)) { nmsg("This browser would not save the note, so it was not added."); return; }
    notes = res.notes; pending = null; adding = false; $("pdf-noteform").hidden = true; $("pdf-notetext").value = "";
    nmsg("Note saved on this device."); renderNotes();
  });
  $("pdf-notecancel").addEventListener("click", function () { pending = null; adding = false; $("pdf-noteform").hidden = true; $("pdf-notetext").value = ""; nmsg(""); nControls(); });
  $("pdf-exportnotes").addEventListener("click", function () {
    if (!doc || nState !== "ok" || !notes.length) return;
    var nameText = $("pdf-name").textContent || "PDF file";
    var blob = new Blob([N.toText(notes, nameText)], { type: "text/plain;charset=utf-8" }), url = URL.createObjectURL(blob);
    var a = document.createElement("a"); a.href = url; a.download = nameText.replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) + "-notes.txt";
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    nmsg("Notes downloaded as a text file. The PDF itself was not changed.");
  });
  $("pdf-clearnotes").addEventListener("click", function () {
    if (!doc || doc !== nDoc || !(nState === "ok" || nState === "bad")) return;
    if (!clearArmed) { clearArmed = true; nmsg("Press Delete all notes for this file again to remove them from this device."); return; }
    clearArmed = false;
    try { localStorage.removeItem(nKey); } catch (x) { nmsg("This browser would not remove them."); return; }
    notes = []; nState = "ok"; nmsg("All notes for this file were deleted from this device."); renderNotes();
  });
  function go(p) { if (!doc) return; page = C.pageIndex(p, 0, doc.numPages); controls(); draw(); }
  function closeDoc(msg) {
    token++; cancelCurrent(); fseq.begin(); finding = false; mark = ""; $("pdf-fstop").disabled = true; fmsg(""); $("pdf-q").value = "";
    var d = doc; doc = null; page = 1; zoom = 1; notesClose(); $("pdf-text").textContent = ""; shown = "";
    settle.then(function () { if (doc) return; canvas.width = 1; canvas.height = 1; canvas.style.width = "0"; canvas.style.height = "0"; });
    var lt = loadTask; loadTask = null; if (lt) { try { lt.destroy(); } catch (e) { /* already gone */ } }
    controls(); status.textContent = msg || "";
  }
  function open(buf, name, mine) {
    loadLib().then(function (m) {
      var t = m.getDocument({ data: new Uint8Array(buf), isEvalSupported: false, disableAutoFetch: true, disableStream: true, standardFontDataUrl: BASE + "standard_fonts/" });
      return t.promise.then(function (d) { return { d: d, t: t }; }, function (e) { try { t.destroy(); } catch (x) { /* gone */ } throw e; });
    }).then(function (r) {
      if (!seq.current(mine)) { try { r.t.destroy(); } catch (x) { /* superseded by a newer open */ } return; }
      var d = r.d; closeDoc(""); doc = d; loadTask = r.t; page = 1; zoom = 1; $("pdf-name").textContent = name; notesOpen(d); controls(); draw();
    }).catch(function (e) {
      if (!seq.current(mine)) return;
      status.textContent = e && e.name === "PasswordException" ? "This PDF is password protected. Password files are not supported yet." : e && e.name === "InvalidPDFException" ? "That PDF is damaged or not readable." : "The viewer could not open that file.";
    });
  }
  $("pdf-open").addEventListener("change", function (e) {
    var f = e.target.files && e.target.files[0]; e.target.value = ""; if (!f) return;
    var mine = seq.begin();
    var head = new FileReader();
    head.onerror = function () { if (!seq.current(mine)) return; status.textContent = "This browser could not read that file."; };
    head.onload = function () {
      if (!seq.current(mine)) return;
      var r = C.check(f.name, f.size, String(head.result));
      if (!r.ok) { status.textContent = r.why; return; }
      status.textContent = "Opening...";
      var rd = new FileReader();
      rd.onerror = function () { if (!seq.current(mine)) return; status.textContent = "This browser could not read that file."; };
      rd.onload = function () { if (!seq.current(mine)) return; open(rd.result, f.name.slice(0, 120), mine); };
      rd.readAsArrayBuffer(f);
    };
    head.readAsBinaryString(f.slice(0, 1024));
  });
  $("pdf-prev").addEventListener("click", function () { go(page - 1); });
  $("pdf-next").addEventListener("click", function () { go(page + 1); });
  $("pdf-zin").addEventListener("click", function () { zoom = C.zoomStep(zoom, 1); controls(); draw(); });
  $("pdf-zout").addEventListener("click", function () { zoom = C.zoomStep(zoom, -1); controls(); draw(); });
  $("pdf-page").addEventListener("change", function () { var n = parseInt(this.value, 10); if (isNaN(n)) { controls(); return; } go(n); });
  $("pdf-close").addEventListener("click", function () { seq.begin(); closeDoc("File closed. Nothing was stored."); $("pdf-name").textContent = ""; });
  var rz = null; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { if (doc) draw(); }, 150); });
  controls();
})();
