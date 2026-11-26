(function () {
  "use strict";
  var N = window.SalowPdfNotes, C = window.SalowPdfCheck, $ = function (i) { return document.getElementById(i); };
  var status = $("pdf-status"), canvas = $("pdf-canvas"), ctx = canvas.getContext("2d");
  var BASE = "/static/pdf/vendor/";
  var lib = null, doc = null, loadTask = null, seq = new C.Seq(), page = 1, zoom = 1, fitPage = false, fitZoom = 1, rot = 0, task = null, token = 0, settle = Promise.resolve();
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
    if (typeof fControls === "function") fControls();
    if (typeof pControls === "function") pControls();
    if (typeof mControls === "function") mControls();
    var on = !!doc;
    $("pdf-prev").disabled = !on || page <= 1; $("pdf-next").disabled = !on || page >= doc.numPages;
    $("pdf-zin").disabled = !on || zoom >= 3; $("pdf-zout").disabled = !on || zoom <= 0.5;
    $("pdf-fitw").disabled = !on || (!fitPage && zoom === 1); $("pdf-fitp").disabled = !on || fitPage; $("pdf-rot").disabled = !on; $("pdf-close").disabled = !on; $("pdf-page").disabled = !on; $("pdf-q").disabled = !on; $("pdf-find").disabled = !on || finding; $("pdf-exporttext").disabled = !on || textBusy;
    $("pdf-where").textContent = on ? "Page " + page + " of " + doc.numPages + ", " + (fitPage ? "fit to page, " + Math.round(fitZoom * 100) + "%" : "zoom " + Math.round(zoom * 100) + "%") + (rot ? ", turned " + rot + " degrees" : "") : "No file open.";
    if (on) $("pdf-page").value = page;
    if (typeof thMark === "function") { thMark(); thFollow(); }
  }
  function draw() {
    if (!doc) return;
    var my = ++token, pageNo = page; cancelCurrent(); drawn = 0; $("pdf-pins").textContent = ""; $("pdf-marks").textContent = ""; mDrag = null;
    // wait for every cancelled render to settle before the canvas is resized and reused
    settle.then(function () { return my === token ? doc.getPage(page) : null; }).then(function (pg) {
      if (!pg || my !== token) return;
      var wrap = $("pdf-stage").clientWidth || 600, R = ((pg.rotate || 0) + rot) % 360, v1 = pg.getViewport({ scale: 1, rotation: R });
      var css = fitPage ? Math.min(Math.min(wrap, 1600) / v1.width, Math.max(200, window.innerHeight - 40) / v1.height) : Math.min(wrap, 1600) / v1.width * zoom;
      if (fitPage) { fitZoom = css * v1.width / Math.min(wrap, 1600); controls(); }
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var vp = pg.getViewport({ scale: css * dpr, rotation: R }), px = vp.width * vp.height;
      var shrink = Math.min(1, Math.sqrt(24e6 / px), 8192 / vp.width, 8192 / vp.height);
      if (shrink < 1) vp = pg.getViewport({ scale: css * dpr * shrink, rotation: R });
      canvas.width = Math.floor(vp.width); canvas.height = Math.floor(vp.height);
      canvas.style.width = Math.floor(v1.width * css) + "px"; canvas.style.height = Math.floor(v1.height * css) + "px";
      var tx = $("pdf-text"); shown = null; tx.textContent = "Reading the text of this page...";
      pg.getTextContent().then(function (tc) {
        if (my !== token) return;
        var r = C.takeText(tc.items, 20000), t = r.text;
        shown = t; shownCut = r.cut; paintText();
      }, function () { if (my === token) tx.textContent = "The text of this page could not be read."; });
      task = pg.render({ canvasContext: ctx, viewport: vp });
      return task.promise.then(function () { if (my === token) { status.textContent = ""; drawn = pageNo; renderPins(); renderMarks(); } });
    }).catch(function (e) { if (my !== token || (e && e.name === "RenderingCancelledException")) return; status.textContent = "This page could not be drawn."; });
  }
  var textBusy = false;
  $("pdf-exporttext").addEventListener("click", function () {
    if (!doc || textBusy) return;
    var d = doc, p = page, name = $("pdf-name").textContent || "PDF file"; textBusy = true; controls(); fmsg("Reading the text of page " + p + "...");
    d.getPage(p).then(function (pg) { return pg.getTextContent(); }).then(function (tc) {
      if (d !== doc) return;
      var r = C.takeText(tc.items, C.EXPORT_BUDGET);
      if (!r.text) { fmsg("Page " + p + " has no text the viewer can read (it may be a scanned image), so nothing was downloaded."); return; }
      var url = URL.createObjectURL(new Blob([C.textFile(name, p, d.numPages, r)], { type: "text/plain;charset=utf-8" })), a = document.createElement("a");
      a.href = url; a.download = name.replace(/\.pdf$/i, "").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) + "-page" + p + "-text.txt";
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      fmsg("Downloaded the text of page " + p + " as a text file. It is not OCR, and the PDF was not changed.");
    }).catch(function () { if (d === doc) fmsg("The text of page " + p + " could not be read, so nothing was downloaded."); }).then(function () { textBusy = false; if (d === doc) controls(); });
  });
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
  // ---- Notes: pinned or page-level text kept in this browser for this file only; never written into the PDF.
  // The key is a SHA-256 of the file's bytes, so a file cannot claim another file's notes.
  var drawn = 0, nDoc = null, nKey = "", notes = [], nState = "off", pending = null, adding = false, clearArmed = false, clearTimer = null, lastRaw = null, saving = false, exporting = false;
  var prefix = $("pdf-notes").getAttribute("data-prefix") || "salow.pdfnotes.";
  function nmsg(t) { $("pdf-notestatus").textContent = t; }
  function sorted() { return notes.slice().sort(function (a, b) { return a.page - b.page || (a.y || 0) - (b.y || 0) || (a.x || 0) - (b.x || 0); }); }
  function disarm() { clearArmed = false; if (clearTimer) { clearTimeout(clearTimer); clearTimer = null; } }
  function nControls() {
    var on = nState === "ok" && !!doc;
    $("pdf-addnote").disabled = !on; $("pdf-addpagenote").disabled = !on; $("pdf-exportnotes").disabled = !on || !notes.length; $("pdf-exportcopy").disabled = !on || !notes.length || exporting;
    $("pdf-clearnotes").disabled = !(nState === "ok" || nState === "bad") || !doc;
    mNotesBox();
    $("pdf-addnote").setAttribute("aria-pressed", adding ? "true" : "false");
    $("pdf-stage").classList.toggle("pdf-adding", adding);
  }
  function renderPins() {
    var layer = $("pdf-pins"); layer.textContent = "";
    if (!doc || nState !== "ok" || drawn !== page || rot) return;
    sorted().forEach(function (n, i) {
      if (n.page !== page || n.x === null) return;
      var b = document.createElement("button"); b.type = "button"; b.className = "pdf-pin";
      b.style.left = (n.x * 100) + "%"; b.style.top = (n.y * 100) + "%"; b.textContent = String(i + 1);
      b.title = n.text; b.setAttribute("aria-label", "Note " + (i + 1) + " on page " + n.page + ": " + n.text);
      b.addEventListener("click", function (e) { e.stopPropagation(); var li = document.getElementById("pdf-note-" + n.id); if (li) li.scrollIntoView({ block: "nearest" }); });
      layer.appendChild(b);
    });
  }
  function renderNotes() {
    var ol = $("pdf-notelist"); ol.textContent = "";
    sorted().forEach(function (n, i) {
      var li = document.createElement("li"); li.id = "pdf-note-" + n.id;
      li.appendChild(document.createTextNode("Page " + n.page + (n.x === null ? " (whole page)" : "") + ": " + n.text + " "));
      var del = document.createElement("button"); del.type = "button"; del.className = "btn"; del.textContent = "Delete";
      del.setAttribute("aria-label", "Delete note " + (i + 1) + " on page " + n.page);
      del.addEventListener("click", function () { removeNote(n.id); });
      li.appendChild(del); ol.appendChild(li);
    });
    nControls(); renderPins();
  }
  function fileCount() {
    var c = 0; try { for (var i = 0; i < localStorage.length; i++) if ((localStorage.key(i) || "").indexOf(prefix) === 0) c++; } catch (e) { return 0; }
    return c;
  }
  // Shows what is stored now (another tab changed it) and keeps any draft the person was typing.
  function adopt(raw, why) {
    var l = N.load(raw); lastRaw = raw;
    if (l.unreadable) { notes = []; nState = "bad"; adding = false; pending = null; $("pdf-noteform").hidden = true; nmsg(why + " The saved notes could not be read, so notes are off for this file."); }
    else { notes = l.notes; nState = "ok"; nmsg(why); }
    renderNotes();
  }
  // One save: re-reads the stored value first and refuses if it is not what this tab last saw. Where the browser
  // offers Web Locks the check and write run one at a time across tabs (one lock for all notes, so the file limit is
  // serialized too). Without Web Locks nothing is saved, so two tabs can never silently overwrite each other.
  function commit(next, done) {
    var myDoc = doc, myKey = nKey, myLast = lastRaw; // the baseline this change was made against, fixed now
    var run = function () {
      var r = N.applySave(localStorage, myKey, myLast, next, fileCount);
      if (r.stale) return { stale: true, raw: r.raw };
      if (r.limit) return { err: "Notes are kept for up to " + N.MAX_FILES + " files on this device. Delete the notes of a file you no longer need first." };
      if (r.err === "read") return { err: "This browser would not read the saved notes, so nothing was changed." };
      if (r.err) return { err: "This browser would not save that change, so nothing was changed." };
      if (myDoc === nDoc && myKey === nKey) lastRaw = r.raw;
      return { ok: true };
    };
    if (!(navigator.locks && navigator.locks.request)) { saving = false; nmsg("This browser or connection cannot make saving safe when the same notes are open in two tabs, so notes cannot be saved here. Open the site over a secure (https) connection to save notes."); return; }
    var p = navigator.locks.request("salow-pdfnotes", function () { return run(); });
    p.then(function (r) { if (myDoc !== doc || myDoc !== nDoc) return; done(r); }, function () { if (myDoc === doc) { saving = false; nmsg("The change could not be saved."); } });
  }
  function removeNote(id) {
    if (!doc || doc !== nDoc || nState !== "ok" || saving) return;
    var next = N.remove(notes, id); saving = true;
    commit(next, function (r) {
      saving = false;
      if (r.stale) { adopt(r.raw, "Notes for this file changed in another tab, so the delete was not applied. Showing the saved notes."); return; }
      if (r.err) { nmsg(r.err); return; }
      notes = next; nmsg(next.length ? "Note deleted." : "Note deleted. No notes are kept for this file now."); renderNotes();
    });
  }
  function notesClose() {
    nDoc = null; nKey = ""; notes = []; nState = "off"; pending = null; adding = false; disarm(); lastRaw = null; saving = false; exportToken++; exporting = false; if (signedHold) { clearTimeout(signedHold.timer); signedHold = null; }
    $("pdf-noteform").hidden = true; $("pdf-notetext").value = ""; nmsg(""); renderNotes();
  }
  function notesOpen(d, hash) {
    notesClose(); nDoc = d;
    if (!(navigator.locks && navigator.locks.request)) { nmsg("Notes need a secure (https) connection, so they are off here."); return; }
    if (typeof hash !== "string" || !/^[0-9a-f]{64}$/.test(hash)) { nmsg("Notes are not available for this file because the viewer could not identify it."); return; }
    nKey = prefix + hash;
    var raw; try { raw = localStorage.getItem(nKey); } catch (e) { nmsg("This browser will not keep notes, so notes are off."); return; }
    if (raw === null && fileCount() >= N.MAX_FILES) { nmsg("Notes are kept for up to " + N.MAX_FILES + " files on this device. Delete the notes of a file you no longer need first, then open this file again."); return; }
    var l = N.load(raw); lastRaw = raw;
    if (l.unreadable) { nState = "bad"; nmsg("The saved notes for this file could not be read. They were left as they are and notes are off for this file. Delete all notes for this file to start again."); nControls(); return; }
    notes = l.notes; nState = "ok"; nmsg(notes.length ? notes.length + " saved note(s) for this file on this device." : "No notes yet for this file."); renderNotes();
  }
  function openForm(p, x, y) {
    pending = { page: p, x: x, y: y, doc: doc };
    $("pdf-noteformpage").textContent = String(p); $("pdf-noteform").hidden = false; $("pdf-notetext").focus(); nmsg("");
  }
  $("pdf-addnote").addEventListener("click", function () {
    if (nState !== "ok") return;
    if (rot) { adding = false; nControls(); nmsg("A note cannot be placed on a spot while the view is turned. Turn the view back, or use Add a note to this page."); return; }
    adding = !adding; pending = null; $("pdf-noteform").hidden = true;
    if (adding && typeof mTool !== "undefined" && mTool !== "off") { mTool = "off"; mDrag = null; renderMarks(); mmsg("Area highlight and Pen were turned off because you are placing a note."); }
    nmsg(adding ? "Click the page where the note belongs. To add a note without choosing a spot, use Add a note to this page." : ""); nControls();
  });
  $("pdf-addpagenote").addEventListener("click", function () {
    if (nState !== "ok" || !doc || doc !== nDoc) return; adding = false; nControls(); openForm(page, null, null);
  });
  $("pdf-pagewrap").addEventListener("click", function (e) {
    if (!adding || nState !== "ok" || !doc || doc !== nDoc) return;
    if (rot) { adding = false; nControls(); nmsg("A note cannot be placed on a spot while the view is turned."); return; }
    if (drawn !== page) { nmsg("Wait for the page to finish drawing, then click again."); return; }
    var r = canvas.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return;
    openForm(page, (e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  });
  $("pdf-noteform").addEventListener("submit", function (e) {
    e.preventDefault();
    if (saving) return;
    if (!pending || pending.doc !== doc || doc !== nDoc || nState !== "ok") { nmsg("That page changed. Start the note again."); return; }
    var res = N.add(notes, pending.page, pending.x, pending.y, $("pdf-notetext").value);
    if (res.error) { nmsg(res.error); return; }
    saving = true;
    commit(res.notes, function (r) {
      saving = false;
      if (r.stale) { adopt(r.raw, "Notes for this file changed in another tab, so your note was not saved. Your text is still in the box; save it again to add it to the current notes."); return; }
      if (r.err) { nmsg(r.err); return; }
      notes = res.notes; pending = null; adding = false; $("pdf-noteform").hidden = true; $("pdf-notetext").value = "";
      nmsg("Note saved on this device."); renderNotes();
    });
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
  function loadScript(src) {
    return new Promise(function (ok, no) { var t = document.createElement("script"); t.src = src; t.onload = ok; t.onerror = function () { no(new Error("script")); }; document.head.appendChild(t); });
  }
  var SIGWARN = " Saving a copy rewrites the whole file, so any digital signature in the original will not validate in the copy. Keep the original for anything that must stay signed.";
  var exportToken = 0, signedHold = null;
  function exportDone(myDoc, token) { if (token === exportToken) { exporting = false; if (myDoc === doc) nControls(); } }
  function saveCopy(r, myDoc, nameText) {
    var url = URL.createObjectURL(new Blob([r.bytes], { type: "application/pdf" })), a = document.createElement("a");
    a.href = url; a.download = nameText.replace(/\.pdf$/i, "").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) + "-with-notes.pdf";
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    if (myDoc === doc) nmsg("Downloaded a copy with " + r.added + " note(s) added as text annotations. The file you opened was not changed, and neither were the notes kept in this browser." + (r.signed ? " This file has a digital signature, and the copy's signature will not validate." : SIGWARN));
  }
  $("pdf-exportcopy").addEventListener("click", function () {
    if (!doc || doc !== nDoc || nState !== "ok" || !notes.length || exporting) return;
    var myDoc = doc, snap = notes.slice(), nameText = $("pdf-name").textContent || "PDF file", token = ++exportToken;
    if (signedHold) { var h = signedHold; signedHold = null; clearTimeout(h.timer); if (h.doc === myDoc && h.sig === N.dump(snap)) { saveCopy(h.r, myDoc, nameText); return; } }
    exporting = true; nControls(); nmsg("Building a copy with your notes in this browser. A large file can make this page pause for a moment.");
    var libs = window.PDFLib ? Promise.resolve() : loadScript(BASE + "pdf-lib.min.js").catch(function () { throw { stage: "load" }; });
    libs.then(function () { return myDoc.getData().catch(function () { throw { stage: "read" }; }); }).then(function (bytes) {
      if (bytes.length > window.PdfNotesExport.MAX_BYTES) throw { stage: "size", mb: Math.round(bytes.length / 1048576) };
      return window.PdfNotesExport.build(window.PDFLib, bytes, snap);
    }).then(function (r) {
      if (token !== exportToken || myDoc !== doc) return;
      if (r.signed) {
        signedHold = { doc: myDoc, r: r, sig: N.dump(snap), timer: setTimeout(function () { signedHold = null; if (myDoc === doc) nmsg("The copy was not downloaded."); }, 15000) };
        nmsg("This file has a digital signature. A copy rewrites the file, so the signature in the copy will not validate. The original is not touched. Press Download a copy with my notes again within 15 seconds to download it anyway.");
        return;
      }
      saveCopy(r, myDoc, nameText);
    }).catch(function (e) {
      if (token !== exportToken || myDoc !== doc) return;
      var c = e && (e.stage || e.code), m = "A copy could not be made. Nothing was downloaded and the file you opened was not changed.";
      if (c === "load") m = "The part of this page that writes copies could not be loaded, so nothing was downloaded. Check the connection and try again.";
      else if (c === "read") m = "This page could not read the open file's bytes again, so nothing was downloaded. Open the file again and retry.";
      else if (c === "size") m = "This file is " + e.mb + " MB. Copies are limited to " + Math.round(window.PdfNotesExport.MAX_BYTES / 1048576) + " MB so the page does not run out of memory. Nothing was downloaded. Download notes (text) still works.";
      else if (c === "encrypted") m = "This file is encrypted, so a copy cannot be made. Nothing was downloaded.";
      else if (c === "damaged") m = "This file could not be read for copying (it may be damaged). Nothing was downloaded.";
      else if (c === "annots") m = "A page in this file has an annotation list in a form this copy cannot safely extend, so nothing was downloaded and nothing was replaced.";
      nmsg(m);
    }).then(function () { exportDone(myDoc, token); });
  });

  // Form fields (standard AcroForm): listed from the open file, filled here, saved only as a separate copy. Values live in the page's inputs and nowhere else.
  var fForm = null, fExporting = false, fHold = null, fFindArm = 0, fToken = 0, fInputs = [];
  function fmsgSet(t) { $("pdf-formstatus").textContent = t; }
  function fControls() { $("pdf-formfind").disabled = !doc || fExporting; $("pdf-formsave").disabled = !doc || !fForm || fExporting || !fForm.fields.length || fForm.xfa; }
  function formsReset() { fToken++; fForm = null; fInputs = []; if (fHold) clearTimeout(fHold.timer); fHold = null; fExporting = false; if (fFindArm) { clearTimeout(fFindArm); fFindArm = 0; } $("pdf-formlist").hidden = true; $("pdf-formlist").textContent = ""; fmsgSet(""); fControls(); }
  function el(tag, props) { var e = document.createElement(tag); Object.keys(props || {}).forEach(function (k) { e[k] = props[k]; }); return e; }
  function renderForm(info) {
    var host = $("pdf-formlist"); host.textContent = ""; fInputs = [];
    info.fields.forEach(function (f) {
      var wrap, ctl, get;
      if (f.kind === "radio") {
        wrap = el("fieldset"); wrap.appendChild(el("legend", { textContent: f.label }));
        var rs = [{ v: "", t: "(none)" }].concat(f.options.map(function (o) { return { v: o, t: o }; })), gname = "pdf-r-" + fInputs.length;
        var radios = rs.map(function (o) { var l = el("label"), r = el("input", { type: "radio", name: gname, value: o.v, disabled: f.readOnly, checked: o.v === f.value }); l.appendChild(r); l.appendChild(document.createTextNode(" " + o.t)); wrap.appendChild(l); return r; });
        get = function () { var c = radios.filter(function (r) { return r.checked; })[0]; return c ? c.value : ""; }; ctl = radios;
      } else {
        wrap = el("label"); wrap.appendChild(document.createTextNode(f.label + (f.readOnly ? " (read only)" : "") + " "));
        if (f.kind === "text") { ctl = f.multiline ? el("textarea", { rows: 3 }) : el("input", { type: "text" }); ctl.value = f.value; if (f.max) ctl.maxLength = f.max; get = function () { return ctl.value; }; }
        else if (f.kind === "check") { ctl = el("input", { type: "checkbox", checked: f.value }); get = function () { return ctl.checked; }; }
        else {
          ctl = el("select"); if (f.kind === "list" && f.multi) ctl.multiple = true; else ctl.appendChild(el("option", { value: "", textContent: "(none)" }));
          f.options.forEach(function (o) { var op = el("option", { value: o, textContent: o }); op.selected = Array.isArray(f.value) ? f.value.indexOf(o) >= 0 : false; ctl.appendChild(op); });
          if (!ctl.multiple) { var cur = f.value[0] || ""; ctl.value = cur; }
          get = function () { var s = [].filter.call(ctl.options, function (o) { return o.selected && o.value !== ""; }).map(function (o) { return o.value; }); return s; };
        }
        if (f.readOnly) (ctl.length ? ctl : [ctl]).forEach(function (c) { c.disabled = true; });
        wrap.appendChild(ctl);
      }
      host.appendChild(wrap);
      fInputs.push({ f: f, get: get, orig: JSON.stringify(get()) });
    });
    host.hidden = !info.fields.length;
  }
  function formNotes(info) {
    var m = info.fields.length + " field(s) can be filled here.";
    if (info.skipped.length) m += " Listed but not supported, so left as they are: " + info.skipped.slice(0, 8).map(function (s) { return s.name + " (" + s.kind + ")"; }).join(", ") + (info.skipped.length > 8 ? ", and " + (info.skipped.length - 8) + " more" : "") + ".";
    if (info.truncated) m += " This file has " + info.total + " fields; only the first " + window.PdfForms.MAX_FIELDS + " are listed.";
    if (info.xfa) m += " This file also has an XFA form (a different, scripted form type). It is not read or filled here, and a filled copy is not offered for it, because writing a copy would drop that data.";
    if (info.signed) m += " This file has a digital signature. A filled copy rewrites the file, so the signature in the copy will not validate.";
    return m;
  }
  $("pdf-formfind").addEventListener("click", function () {
    if (!doc || fExporting) return;
    var typed = fInputs.some(function (i) { return !i.f.readOnly && JSON.stringify(i.get()) !== i.orig; });
    if (typed && !fFindArm) { fFindArm = setTimeout(function () { fFindArm = 0; }, 8000); fmsgSet("Finding the fields again will discard what you typed. Press Find form fields again within 8 seconds to discard it, or press Download a filled copy first."); return; }
    if (fFindArm) { clearTimeout(fFindArm); fFindArm = 0; }
    var myDoc = doc, tok = ++fToken; fForm = null; fInputs = []; fHold = null; $("pdf-formlist").hidden = true; $("pdf-formlist").textContent = ""; fmsgSet("Reading the form fields in this browser."); fControls();
    var libs = window.PDFLib ? Promise.resolve() : loadScript(BASE + "pdf-lib.min.js").catch(function () { throw { stage: "load" }; });
    libs.then(function () { return myDoc.getData().catch(function () { throw { stage: "read" }; }); }).then(function (bytes) {
      if (bytes.length > window.PdfForms.MAX_BYTES) throw { stage: "size", mb: Math.round(bytes.length / 1048576) };
      return window.PdfForms.list(window.PDFLib, bytes, window.PdfNotesExport.looksSigned);
    }).then(function (info) {
      if (tok !== fToken || myDoc !== doc) return;
      fForm = info;
      if (!info.fields.length && !info.skipped.length) { fmsgSet("This file has no standard form fields."); }
      else { renderForm(info); fmsgSet(formNotes(info)); }
      fControls();
    }).catch(function (e) {
      if (tok !== fToken || myDoc !== doc) return;
      var c = e && (e.stage || e.code), m = "The form fields could not be read. Nothing was changed.";
      if (c === "load") m = "The part of this page that reads forms could not be loaded. Check the connection and try again.";
      else if (c === "read") m = "This page could not read the open file's bytes again. Open the file again and retry.";
      else if (c === "size") m = "This file is " + e.mb + " MB. Form filling is limited to " + Math.round(window.PdfForms.MAX_BYTES / 1048576) + " MB so the page does not run out of memory.";
      else if (c === "encrypted") m = "This file is encrypted, so its form cannot be filled here.";
      else if (c === "damaged") m = "This file could not be read as a form (it may be damaged).";
      fmsgSet(m); fControls();
    });
  });
  function saveFilled(r, nameText) {
    var url = URL.createObjectURL(new Blob([r.bytes], { type: "application/pdf" })), a = document.createElement("a");
    a.href = url; a.download = nameText.replace(/\.pdf$/i, "").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) + "-filled.pdf";
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }
  $("pdf-formsave").addEventListener("click", function () {
    if (!doc || !fForm || fExporting) return;
    var myDoc = doc, nameText = $("pdf-name").textContent || "PDF file", tok = ++fToken, values = Object.create(null), changed = 0;
    fInputs.forEach(function (i) { if (i.f.readOnly) return; var now = i.get(); if (JSON.stringify(now) !== i.orig) { values[i.f.name] = now; changed++; } });
    if (!changed) { fmsgSet("Nothing was changed in the form, so no copy was made."); return; }
    var sig = JSON.stringify(values);
    if (fHold && fHold.doc === myDoc && fHold.sig === sig) { var h = fHold; fHold = null; clearTimeout(h.timer); saveFilled(h.r, nameText); fmsgSet("Downloaded a filled copy. The file you opened was not changed."); return; }
    if (fHold) { clearTimeout(fHold.timer); fHold = null; }
    fExporting = true; fControls(); fmsgSet("Building a filled copy in this browser.");
    myDoc.getData().catch(function () { throw { stage: "read" }; }).then(function (bytes) {
      if (bytes.length > window.PdfForms.MAX_BYTES) throw { stage: "size", mb: Math.round(bytes.length / 1048576) };
      return window.PdfForms.fill(window.PDFLib, bytes, values);
    }).then(function (r) {
      if (tok !== fToken || myDoc !== doc) return;
      if (fForm.signed) {
        fHold = { doc: myDoc, r: r, sig: sig, timer: setTimeout(function () { fHold = null; if (myDoc === doc) fmsgSet("The copy was not downloaded."); }, 15000) };
        fmsgSet("This file has a digital signature. A filled copy rewrites the file, so the signature in the copy will not validate. The original is not touched. Press Download a filled copy again within 15 seconds, with the same values, to download it anyway.");
        return;
      }
      saveFilled(r, nameText); fmsgSet("Downloaded a filled copy with " + r.filled + " field(s) changed. The file you opened was not changed.");
    }).catch(function (e) {
      if (tok !== fToken || myDoc !== doc) return;
      var c = e && (e.stage || e.code), m = "A filled copy could not be made. Nothing was downloaded and the file you opened was not changed.";
      if (c === "value") m = "A value could not be written into the file (for example a character its form font cannot draw). Nothing was downloaded. Change that value and try again.";
      else if (c === "read") m = "This page could not read the open file's bytes again. Open the file again and retry.";
      else if (c === "size") m = e.mb ? "This file is " + e.mb + " MB. Copies are limited to 20 MB. Nothing was downloaded." : "The filled copy would be over 20 MB. Nothing was downloaded.";
      else if (c === "readonly") m = "A field in the file is read only. Nothing was downloaded.";
      else if (c === "xfa") m = "This file has an XFA form, which a copy made here would drop. Nothing was downloaded.";
      fmsgSet(m);
    }).then(function () { if (tok === fToken) { fExporting = false; fControls(); } });
  });

  // Pages: keep chosen pages as a new file, or merge this file with added files. Separate copies only.
  var pExtras = [], pBusy = false, pHold = null, pToken = 0, pSeq = 0;
  function pmsg(t) { $("pdf-pagesstatus").textContent = t; }
  function pControls() { var on = !!doc && !pBusy; $("pdf-range").disabled = !doc; $("pdf-splitgo").disabled = !on; $("pdf-mergeadd").disabled = !on || pExtras.length >= window.PdfPages.MAX_FILES - 1; $("pdf-mergeclear").disabled = !on || !pExtras.length; $("pdf-mergego").disabled = !on || !pExtras.length; $("pdf-orderstart").disabled = !on; }
  function pList() { var ol = $("pdf-mergelist"); ol.textContent = ""; pExtras.forEach(function (x) { var li = document.createElement("li"); li.textContent = x.name + " (" + Math.round(x.bytes.length / 1024) + " KB)"; ol.appendChild(li); }); }
  // Order list: a helper that writes the page order into the same "pages to keep" box, so downloading goes through the one build path.
  // Runs of consecutive ascending pages are written as ranges, so any order of up to 100 pages fits the box (worst case 291 characters).
  var pOrder = null, ORDER_MAX = 100;
  function pSerialize(a) {
    var out = [], i = 0;
    while (i < a.length) { var j = i; while (j + 1 < a.length && a[j + 1] === a[j] + 1) j++; out.push(j - i >= 2 ? a[i] + "-" + a[j] : a.slice(i, j + 1).join(",")); i = j + 1; }
    return out.join(",");
  }
  function pOrderSync() {
    var ol = $("pdf-orderlist"); ol.textContent = "";
    $("pdf-orderstart").textContent = pOrder ? "Reset the list to file order (replaces the pages box)" : "List pages from the file (replaces the pages box)";
    if (!pOrder) return;
    pOrder.forEach(function (n, i) {
      var li = document.createElement("li"), t = document.createElement("span");
      t.textContent = "Page " + n + " ";
      li.appendChild(t);
      [["Up", -1], ["Down", 1], ["Remove", 0]].forEach(function (b, bi) {
        var el = document.createElement("button"); el.type = "button"; el.className = "btn"; el.textContent = b[0];
        el.dataset.row = String(i); el.dataset.act = String(bi);
        el.setAttribute("aria-label", b[0] + ": page " + n + ", position " + (i + 1) + " of " + pOrder.length);
        el.disabled = (b[1] === -1 && i === 0) || (b[1] === 1 && i === pOrder.length - 1) || (b[1] === 0 && pOrder.length === 1);
        el.addEventListener("click", function () { pOrderAct(i, b[1], bi); });
        li.appendChild(el);
      });
      ol.appendChild(li);
    });
    $("pdf-range").value = pSerialize(pOrder);
  }
  function pOrderAct(i, d, bi) {
    if (pBusy || !pOrder) return;
    var n = pOrder[i], text;
    if (pHold) { clearTimeout(pHold.timer); pHold = null; }
    if (d === 0) { pOrder.splice(i, 1); text = "Page " + n + " removed. " + pOrder.length + " page(s) left."; }
    else { var o = pOrder[i]; pOrder[i] = pOrder[i + d]; pOrder[i + d] = o; text = "Page " + n + " moved to position " + (i + d + 1) + " of " + pOrder.length + "."; }
    pOrderSync();
    // Put focus back where the person was: same page and action, or the nearest enabled button in that row, then the nearest row.
    var row = d === 0 ? Math.min(i, pOrder.length - 1) : i + d, all = $("pdf-orderlist").querySelectorAll("button"), best = null, bestScore = 1e9;
    all.forEach(function (el) { if (el.disabled) return; var sc = Math.abs(+el.dataset.row - row) * 10 + (d === 0 ? Math.abs(+el.dataset.act - bi) : Math.abs(+el.dataset.act - bi)); if (sc < bestScore) { best = el; bestScore = sc; } });
    if (best) best.focus();
    pmsg(text);
  }
  // A typed change to the box means the list no longer describes it: drop the list (the box keeps what was typed).
  $("pdf-range").addEventListener("input", function () { if (pOrder) { pOrder = null; $("pdf-orderlist").textContent = ""; $("pdf-orderstart").textContent = "List pages from the file (replaces the pages box)"; } if (pHold) { clearTimeout(pHold.timer); pHold = null; } });
  $("pdf-orderstart").addEventListener("click", function () {
    if (!doc || pBusy) return;
    if (doc.numPages > ORDER_MAX) { pmsg("The list handles up to " + ORDER_MAX + " pages. Type the pages in the box for a longer file."); return; }
    pOrder = []; for (var i = 1; i <= doc.numPages; i++) pOrder.push(i);
    if (pHold) { clearTimeout(pHold.timer); pHold = null; }
    pOrderSync(); pmsg("The pages box now follows the list. Typing in the box ends the list.");
  });
  function pagesReset() { pOrder = null; pOrderSync(); pToken++; pExtras = []; if (pHold) clearTimeout(pHold.timer); pHold = null; pBusy = false; $("pdf-range").value = ""; pList(); pmsg(""); pControls(); }
  function pSave(r, nameText, suffix) {
    var url = URL.createObjectURL(new Blob([r.bytes], { type: "application/pdf" })), a = document.createElement("a");
    a.href = url; a.download = nameText.replace(/\.pdf$/i, "").replace(/[^A-Za-z0-9._-]+/g, "_").slice(0, 80) + suffix;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }
  function pRun(mode) {
    if (!doc || pBusy) return;
    var myDoc = doc, nameText = $("pdf-name").textContent || "PDF file", tok = ++pToken, sel = null;
    if (mode === "split") { sel = window.PdfPages.parseRanges($("pdf-range").value, myDoc.numPages); if (sel.error) { pmsg(sel.error); return; } }
    var sig = mode + "|" + (sel ? sel.pages.join(",") : "") + "|" + pExtras.map(function (x) { return x.id + ":" + x.name + ":" + x.bytes.length; }).join(";");
    if (pHold && pHold.doc === myDoc && pHold.sig === sig) { var h = pHold; pHold = null; clearTimeout(h.timer); pSave(h.r, nameText, mode === "split" ? "-pages.pdf" : "-merged.pdf"); pmsg("Downloaded a new file with " + h.r.pageCount + " page(s). The files you opened were not changed."); return; }
    if (pHold) { clearTimeout(pHold.timer); pHold = null; }
    pBusy = true; pControls(); pmsg("Building the new file in this browser.");
    var extras = pExtras.slice();
    var libs = window.PDFLib ? Promise.resolve() : loadScript(BASE + "pdf-lib.min.js").catch(function () { throw { stage: "load" }; });
    libs.then(function () { return myDoc.getData().catch(function () { throw { stage: "read" }; }); }).then(function (bytes) {
      var parts = [{ bytes: bytes, pages: sel ? sel.pages : null, name: nameText }];
      if (mode === "merge") extras.forEach(function (x) { parts.push({ bytes: x.bytes, pages: null, name: x.name }); });
      return window.PdfPages.build(window.PDFLib, parts, window.PdfNotesExport.looksSigned);
    }).then(function (r) {
      if (tok !== pToken || myDoc !== doc) return;
      if (r.signed.length) {
        pHold = { doc: myDoc, r: r, sig: sig, timer: setTimeout(function () { pHold = null; if (myDoc === doc) pmsg("The new file was not downloaded."); }, 15000) };
        pmsg("This includes a digitally signed file (" + r.signed.slice(0, 3).join(", ").slice(0, 120) + "). The signature will not validate in the new file. The originals are not touched. Press the same button again within 15 seconds, with the same choices, to download it anyway.");
        return;
      }
      pSave(r, nameText, mode === "split" ? "-pages.pdf" : "-merged.pdf"); pmsg("Downloaded a new file with " + r.pageCount + " page(s). The files you opened were not changed.");
    }).catch(function (e) {
      if (tok !== pToken || myDoc !== doc) return;
      var c = e && (e.stage || e.code), who = e && e.name2 ? " (" + String(e.name2).slice(0, 80) + ")" : "", m = "The new file could not be made. Nothing was downloaded and the files you opened were not changed.";
      if (c === "load") m = "The part of this page that writes PDFs could not be loaded. Check the connection and try again.";
      else if (c === "read") m = "This page could not read the open file's bytes again. Open the file again and retry.";
      else if (c === "size") m = "The files together, or the new file, are over 20 MB. Nothing was downloaded.";
      else if (c === "pages") m = "A new file is limited to " + window.PdfPages.MAX_PAGES + " pages. Nothing was downloaded.";
      else if (c === "files") m = "A merge is limited to " + window.PdfPages.MAX_FILES + " files. Nothing was downloaded.";
      else if (c === "encrypted") m = "A file" + who + " is encrypted, so it cannot be copied. Nothing was downloaded.";
      else if (c === "damaged") m = "A file" + who + " could not be read (it may be damaged). Nothing was downloaded.";
      else if (c === "leak") m = "Something in a file points at pages that are not being kept, so a clean copy could not be made. Nothing was downloaded.";
      pmsg(m);
    }).then(function () { if (tok === pToken) { pBusy = false; pControls(); } });
  }
  $("pdf-splitgo").addEventListener("click", function () { pRun("split"); });
  $("pdf-mergego").addEventListener("click", function () { pRun("merge"); });
  $("pdf-mergeclear").addEventListener("click", function () { if (pBusy) return; pExtras = []; pHold = null; pList(); pmsg("Added files cleared."); pControls(); });
  $("pdf-mergeadd").addEventListener("change", function (e) {
    var files = [].slice.call(e.target.files || []); e.target.value = ""; if (!doc || pBusy || !files.length) return;
    var myDoc = doc, tok = pToken, room = window.PdfPages.MAX_FILES - 1 - pExtras.length;
    if (files.length > room) { pmsg("A merge is limited to " + window.PdfPages.MAX_FILES + " files including this one. Nothing was added."); return; }
    var total = pExtras.reduce(function (a, x) { return a + x.bytes.length; }, 0) + files.reduce(function (a, f) { return a + f.size; }, 0);
    if (total > window.PdfPages.MAX_BYTES) { pmsg("Added files may total at most 20 MB (with this file counted at read time). Nothing was added."); return; }
    pBusy = true; pControls(); pmsg("Reading the added files.");
    var added = [], chain = Promise.resolve();
    files.forEach(function (f) {
      chain = chain.then(function () { return new Promise(function (ok, no) { var r = new FileReader(); r.onerror = function () { no({ bad: f.name }); }; r.onload = function () { ok(new Uint8Array(r.result)); }; r.readAsArrayBuffer(f); }); }).then(function (b) {
        var head = ""; for (var i = 0; i < Math.min(b.length, 1024); i++) head += String.fromCharCode(b[i]);
        var chk = C.check(f.name, f.size, head); if (!chk.ok) throw { bad: f.name, why: chk.why };
        added.push({ id: ++pSeq, name: f.name.slice(0, 120), bytes: b });
      });
    });
    chain.then(function () {
      if (tok !== pToken || myDoc !== doc) return;
      pExtras = pExtras.concat(added); pHold = null; pList(); pmsg(added.length + " file(s) added after this one.");
    }).catch(function (err) {
      if (tok !== pToken || myDoc !== doc) return;
      pmsg("Nothing was added: " + (err && err.bad ? String(err.bad).slice(0, 80) + " " + (err.why ? "(" + err.why + ")" : "could not be read") : "a file could not be read") + ".");
    }).then(function () { if (tok === pToken) { pBusy = false; pControls(); } });
  });
  $("pdf-clearnotes").addEventListener("click", function () {
    if (!doc || doc !== nDoc || !(nState === "ok" || nState === "bad") || saving) return;
    if (!clearArmed) {
      clearArmed = true; clearTimer = setTimeout(function () { disarm(); nmsg("Delete all notes was cancelled."); }, 6000);
      nmsg("Press Delete all notes for this file again within 6 seconds to remove them from this device."); return;
    }
    disarm(); saving = true;
    commit([], function (r) {
      saving = false;
      if (r.stale) { adopt(r.raw, "Notes for this file changed in another tab, so nothing was deleted. Showing the saved notes."); return; }
      if (r.err) { nmsg(r.err); return; }
      notes = []; nState = "ok"; nmsg("All notes for this file were deleted from this device."); renderNotes();
    });
  });
  // Another tab of this browser changed this file's notes: show what is stored now.
  window.addEventListener("storage", function (e) {
    if (!doc || doc !== nDoc || !nKey || e.key !== nKey || e.storageArea !== localStorage) return;
    adopt(e.newValue, "Notes for this file were changed in another tab. Showing the saved notes.");
  });

  // Thumbnail strip: one small button per page (first 400), drawn lazily one at a time near what is on screen and let go of when far away.
  var TH = window.SalowPdfThumbs, thStrip = $("pdf-thumbs"), thToken = 0, thItems = [], thBusy = false, thTask = null, thTimer = null;
  var TH_W = 90; // item width plus gap, in CSS px (measured from the first two items once they exist)
  function thStep() { if (thItems.length > 1) { var w = thItems[1].el.offsetLeft - thItems[0].el.offsetLeft; if (w > 0) TH_W = w; } return TH_W; }
  function thClose() { thToken++; clearTimeout(thTimer); if (thTask) { try { thTask.cancel(); } catch (e) { /* done */ } thTask = null; } thBusy = false; thItems = []; thStrip.textContent = ""; thStrip.hidden = true; $("pdf-thumbnote").hidden = true; }
  function thOpen(d) {
    thClose(); var n = Math.min(d.numPages, TH.MAX);
    for (var i = 1; i <= n; i++) {
      var b = document.createElement("button"); b.type = "button"; b.className = "pdf-thumb"; b.setAttribute("aria-label", "Go to page " + i); b.dataset.n = String(i);
      var lab = document.createElement("span"); lab.textContent = String(i); b.appendChild(lab); thStrip.appendChild(b); thItems.push({ el: b, state: "empty" });
    }
    thStrip.hidden = false;
    var note = $("pdf-thumbnote"); note.hidden = d.numPages <= TH.MAX; if (d.numPages > TH.MAX) note.textContent = "Thumbnails are shown for the first " + TH.MAX + " of " + d.numPages + " pages. Use the page box to go further.";
    thSchedule(0);
  }
  function thMark() { thItems.forEach(function (t, i) { if (i + 1 === page) t.el.setAttribute("aria-current", "true"); else t.el.removeAttribute("aria-current"); }); }
  function thFollow() { if (!thItems.length) return; var t = thItems[page - 1]; if (!t) return; var l = t.el.offsetLeft - thStrip.offsetLeft, v = thStrip.clientWidth; if (l < thStrip.scrollLeft || l + thStep() > thStrip.scrollLeft + v) thStrip.scrollLeft = Math.max(0, l - Math.floor(v / 2)); thSchedule(120); }
  function thSchedule(ms) { clearTimeout(thTimer); var my = thToken; thTimer = setTimeout(function () { if (my === thToken) thPump(my); }, ms); }
  function thRange() { return TH.want(thStrip.scrollLeft - thItems[0].el.offsetLeft + thStrip.offsetLeft, thStrip.clientWidth, thStep(), thItems.length); }
  function thPump(my) {
    if (!doc || my !== thToken || thBusy) return;
    var r = thRange();
    thItems.forEach(function (t, i) { if (t.state === "done" && TH.drop(i, r)) { var c = t.el.querySelector("canvas"); if (c) { c.width = 0; c.height = 0; c.remove(); } t.state = "empty"; } });
    var next = -1; for (var i = r[0]; i <= r[1]; i++) { if (thItems[i].state === "empty") { next = i; break; } }
    if (next < 0) return;
    thBusy = true; var it = thItems[next], d = doc, mine = null;
    d.getPage(next + 1).then(function (pg) {
      if (my !== thToken) return;
      var v1 = pg.getViewport({ scale: 1 }), dpr = Math.min(window.devicePixelRatio || 1, 2), vp = pg.getViewport({ scale: 76 * dpr / v1.width });
      var c = document.createElement("canvas"); c.width = Math.max(1, Math.floor(vp.width)); c.height = Math.max(1, Math.floor(vp.height));
      if (c.width * c.height > 400000) { it.state = "failed"; pg.cleanup(); return; } // a very tall page would make a big canvas: skip, the number stays
      mine = thTask = pg.render({ canvasContext: c.getContext("2d"), viewport: vp });
      return mine.promise.then(function () { if (my !== thToken) return; it.el.insertBefore(c, it.el.firstChild); it.state = "done"; pg.cleanup(); });
    }).catch(function (e) { if (my === thToken && !(e && e.name === "RenderingCancelledException")) it.state = "failed"; }).then(function () { if (mine === thTask) thTask = null; if (my !== thToken) return; thBusy = false; thSchedule(0); });
  }
  thStrip.addEventListener("click", function (e) { var b = e.target.closest && e.target.closest(".pdf-thumb"); if (b && doc) go(parseInt(b.dataset.n, 10)); });
  thStrip.addEventListener("scroll", function () { if (doc) thSchedule(100); });
  function go(p) { if (!doc) return; page = C.pageIndex(p, 0, doc.numPages); controls(); draw(); }
  // Marks: area highlights and pen strokes drawn over the page, kept only in this tab and written into a separate downloaded copy.
  var mMarks = [], mTool = "off", mDrag = null, mHold = null, mBusy = false, mToken = 0, SVGNS = "http://www.w3.org/2000/svg";
  function mmsg(t) { $("pdf-markstatus").textContent = t; }
  // "Also add my notes" on the marks copy: only when this file's notes are on and there is at least one. Read at the press, so changing it changes the copy asked for.
  function mNotesBox() {
    var b = $("pdf-markwithnotes"), ok = nState === "ok" && !!doc && notes.length > 0;
    b.disabled = !ok || mBusy; if (!ok) b.checked = false;
  }
  function mUsable() { return !!doc && !rot && drawn === page; }
  function mControls() {
    var on = !!doc && !rot && !mBusy; mNotesBox();
    $("pdf-tool-hl").disabled = !on; $("pdf-tool-pen").disabled = !on; $("pdf-tool-tx").disabled = !on; $("pdf-tool-sig").disabled = !on || !sigStrokes; $("pdf-markcopy").disabled = !on || !mMarks.length; $("pdf-markclear").disabled = !on || !mMarks.length; $("pdf-markflat").disabled = !on || !mMarks.length;
    $("pdf-tool-hl").setAttribute("aria-pressed", mTool === "hl" ? "true" : "false"); $("pdf-tool-pen").setAttribute("aria-pressed", mTool === "pen" ? "true" : "false"); $("pdf-tool-tx").setAttribute("aria-pressed", mTool === "tx" ? "true" : "false"); $("pdf-tool-sig").setAttribute("aria-pressed", mTool === "sig" ? "true" : "false");
    var svg = $("pdf-marks"); svg.classList.toggle("pdf-marks-on", mTool !== "off" && on);
    var showD = mBad && !!doc; $("pdf-markdiscard").hidden = !showD; $("pdf-markdiscard").style.display = showD ? "" : "none"; $("pdf-markdiscard").disabled = mBusy || mSaving;
  }
  function mEl(name, attrs) { var e = document.createElementNS(SVGNS, name); Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); }); return e; }
  function mDraw(m, temp) {
    if (m.kind === "tx") { var gt = mEl("g", {}); m.r.forEach(function (q) { gt.appendChild(mEl("rect", { x: q[0], y: q[1], width: q[2] - q[0], height: q[3] - q[1], fill: "#ffeb33", "fill-opacity": "0.45", stroke: "none" })); }); return gt; }
    if (m.kind === "hl") return mEl("rect", { x: m.r[0], y: m.r[1], width: m.r[2] - m.r[0], height: m.r[3] - m.r[1], fill: "#ffeb33", "fill-opacity": "0.45", stroke: temp ? "#7a2a2a" : "none", "stroke-width": "1", "vector-effect": "non-scaling-stroke" });
    var g = mEl("g", {});
    m.s.forEach(function (st) { g.appendChild(mEl("polyline", { points: st.map(function (p) { return p[0] + "," + p[1]; }).join(" "), fill: "none", stroke: "#d91a1a", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round", "vector-effect": "non-scaling-stroke" })); });
    return g;
  }
  function renderMarks() {
    var svg = $("pdf-marks"); svg.textContent = "";
    if (mUsable()) mMarks.forEach(function (m) { if (m.page === page) svg.appendChild(mDraw(m, false)); });
    var ol = $("pdf-marklist"); ol.textContent = "";
    mMarks.slice().sort(function (a, b) { return a.page - b.page; }).forEach(function (m, i) {
      var li = document.createElement("li"); li.appendChild(document.createTextNode("Page " + m.page + ": " + (m.kind === "hl" ? "area highlight" : m.kind === "tx" ? "text-line highlight" : "pen drawing") + " "));
      var b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = "Remove";
      b.setAttribute("aria-label", "Remove " + (m.kind === "hl" ? "area highlight" : m.kind === "tx" ? "text-line highlight" : "pen drawing") + " " + (i + 1) + " on page " + m.page);
      b.addEventListener("click", function () {
        if (mBusy) return;
        var at = i, next = window.PdfMarks.remove(mMarks, m.id);
        mApply(next, "Removed " + (m.kind === "hl" ? "an area highlight" : m.kind === "tx" ? "a text-line highlight" : "a pen drawing") + " on page " + m.page + ". " + next.length + " mark(s) left.", function () {
          var btns = $("pdf-marklist").querySelectorAll("button"), nxt = btns[Math.min(at, btns.length - 1)];
          (nxt || $("pdf-tool-hl")).focus();
        });
      });
      li.appendChild(b); ol.appendChild(li);
    });
    mControls();
  }
  function mSetTool(t) { mTool = mTool === t ? "off" : t; if (mTool !== "off" && adding) { adding = false; pending = null; nControls(); nmsg("Placing a note was turned off because a drawing tool is on."); } mDrag = null; mHold = mHold && mClearHold(); renderMarks(); if (mTool === "hl") mmsg("Area highlight is on. Drag over the page to draw a rectangle. It marks an area and does not select text."); else if (mTool === "tx") mmsg("Highlight text lines is on. Drag over the page. Every whole line or piece of text the area touches is highlighted, even a click on one, so it can take more than you dragged over. It does not select letters or copy text. Scanned pages have no text to pick."); else if (mTool === "sig") mmsg("Place signature is on. Click the page where the signature should go. It is a drawn picture, not a legal signature. Turn it off to scroll the page by touch."); else if (mTool === "pen") mmsg("Pen is on. Draw on the page with a finger, pen or mouse. Turn it off to scroll the page by touch."); else mmsg(""); }
  function mClearHold() { if (mHold) clearTimeout(mHold.timer); return null; }
  function mPoint(e) { var r = $("pdf-marks").getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / (r.width || 1))), Math.min(1, Math.max(0, (e.clientY - r.top) / (r.height || 1)))]; }
  function mTempShow() { var svg = $("pdf-marks"), old = svg.querySelector("[data-temp]"); if (old) old.remove(); if (!mDrag || mDrag.kind === "sig") return; var el = (mDrag.kind === "hl" || mDrag.kind === "tx") ? mDraw({ kind: "hl", r: [Math.min(mDrag.a[0], mDrag.b[0]), Math.min(mDrag.a[1], mDrag.b[1]), Math.max(mDrag.a[0], mDrag.b[0]) + 1e-9, Math.max(mDrag.a[1], mDrag.b[1]) + 1e-9] }, true) : mDraw({ kind: "ink", s: [mDrag.pts.length > 1 ? mDrag.pts : mDrag.pts.concat(mDrag.pts)] }, true); el.setAttribute("data-temp", "1"); svg.appendChild(el); }
  var mSvg = $("pdf-marks");
  mSvg.addEventListener("pointerdown", function (e) {
    if (mTool === "off" || !mUsable() || mBusy || e.button > 0) return;
    e.preventDefault(); try { mSvg.setPointerCapture(e.pointerId); } catch (x) { /* capture is best effort */ }
    var p = mPoint(e); mDrag = { kind: mTool, id: e.pointerId, a: p, b: p, pts: [p], n: 1 }; mTempShow();
  });
  mSvg.addEventListener("pointermove", function (e) {
    if (!mDrag || e.pointerId !== mDrag.id) return;
    var p = mPoint(e);
    if (mDrag.kind === "sig") return;
    if (mDrag.kind === "hl" || mDrag.kind === "tx") mDrag.b = p;
    else { var q = mDrag.pts[mDrag.pts.length - 1]; if (mDrag.pts.length >= window.PdfMarks.MAX_POINTS) { mmsg("This drawing reached its length limit. Lift to finish it, then draw the rest as a new line."); return; } if (Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]) < 0.003) return; mDrag.pts.push(p); }
    mTempShow();
  });
  mSvg.addEventListener("pointerup", function (e) {
    if (!mDrag || e.pointerId !== mDrag.id) return;
    var d = mDrag, p = mPoint(e); mDrag = null; if (d.kind === "hl" || d.kind === "tx") d.b = p;
    if (d.kind === "tx") { mTextPick(d); return; }
    if (d.kind === "sig") {
      var rc = mSvg.getBoundingClientRect(), rs = sigStrokes ? window.PdfMarks.addSignature(mMarks, page, sigStrokes, SIG_ASPECT, p[0], p[1], (rc.width || 1) / (rc.height || 1), 0.22) : { error: "Draw a signature and choose Use this signature first." };
      if (rs.error) { mmsg(rs.error); renderMarks(); return; }
      mApply(rs.marks, "Signature drawing placed on page " + page + ", as a pen drawing. " + (mState === "ok" ? "It is kept on this device for this file." : "It stays in this tab only."), null);
      renderMarks(); return;
    }
    var M = window.PdfMarks, r = d.kind === "hl" ? M.addHighlight(mMarks, page, d.a[0], d.a[1], d.b[0], d.b[1]) : M.addInk(mMarks, page, [d.pts]);
    if (r.error) { mmsg(r.error); renderMarks(); return; }
    var pg0 = page;
    mApply(r.marks, (d.kind === "hl" ? "Area highlight" : "Pen drawing") + " added on page " + pg0 + ". " + (mState === "ok" ? "It is kept on this device for this file." : "It stays in this tab only."), null);
    renderMarks();
  });
  function mMul(a, b) { return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3], a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]]; }
  // Text highlight: the whole text pieces (as pdf.js splits them, usually a line or part of one) that the dragged area touches. It does not cut inside a piece.
  function mTextPick(d) {
    var myDoc = doc, myTok = mToken, pg0 = page, M = window.PdfMarks;
    if (typeof M.addText !== "function") { mmsg("This page loaded mixed versions of its scripts. Reload the page, then try again."); renderMarks(); return; }
    var x0 = Math.min(d.a[0], d.b[0]), x1 = Math.max(d.a[0], d.b[0]), y0 = Math.min(d.a[1], d.b[1]), y1 = Math.max(d.a[1], d.b[1]);
    mBusy = true; mControls(); mmsg("Finding text under the area...");
    myDoc.getPage(pg0).then(function (pg) {
      return pg.getTextContent().then(function (tc) {
        var vp = pg.getViewport({ scale: 1 }), W = vp.width, H = vp.height, rects = [];
        tc.items.forEach(function (it) {
          if (!it.str || !it.str.trim() || !it.transform || !(it.width > 0)) return;
          var sc = Math.hypot(it.transform[0], it.transform[1]); if (!(sc > 0)) return;
          var t = mMul(vp.transform, it.transform), wl = it.width / sc;
          var cs = [[0, 0], [wl, 0], [0, 1], [wl, 1]].map(function (c) { return [t[0] * c[0] + t[2] * c[1] + t[4], t[1] * c[0] + t[3] * c[1] + t[5]]; });
          var xs = cs.map(function (c) { return c[0] / W; }), ys = cs.map(function (c) { return c[1] / H; });
          var bx0 = Math.min.apply(null, xs), bx1 = Math.max.apply(null, xs), by0 = Math.min.apply(null, ys), by1 = Math.max.apply(null, ys);
          if (bx1 > x0 && bx0 < x1 && by1 > y0 && by0 < y1) rects.push([bx0, by0, bx1, by1]);
        });
        return rects;
      });
    }).then(function (rects) {
      if (myDoc !== doc || myTok !== mToken) return; // another file or a reset took over: it owns the busy flag and the status now
      mBusy = false;
      if (page !== pg0 || !mUsable()) { mControls(); mmsg("The page changed before the text was found, so nothing was added."); renderMarks(); return; }
      var r = M.addText(mMarks, pg0, rects, null);
      if (r.error) { mControls(); mmsg(r.error); renderMarks(); return; }
      mApply(r.marks, "Text-line highlight added on page " + pg0 + " (" + r.marks[r.marks.length - 1].r.length + " piece(s) of text). " + (mState === "ok" ? "It is kept on this device for this file." : "It stays in this tab only."), null);
      renderMarks();
    }, function () { if (myDoc !== doc || myTok !== mToken) return; mBusy = false; mControls(); mmsg("Could not read the text on this page. Nothing was added."); renderMarks(); });
  }
  mSvg.addEventListener("pointercancel", function () { mDrag = null; renderMarks(); });
  // Keeping marks on this device: the same guarded pattern as notes (one Web Lock, re-read before write, stale refused, unreadable left alone).
  var mPrefix = $("pdf-marksbox").getAttribute("data-prefix") || "", mKey = "", mState = "tab", mLast = null, mSaving = false, mBad = false, mBadKey = "", mBadRaw = null, mDisc = null;
  function mFileCount() {
    var c = 0; try { for (var i = 0; i < localStorage.length; i++) if ((localStorage.key(i) || "").indexOf(mPrefix) === 0) c++; } catch (e) { return 0; }
    return c;
  }
  function mOpen(hash) {
    mKey = ""; mState = "tab"; mLast = null; mBad = false; mDiscDisarm();
    var M = window.PdfMarks, tab = " Marks stay in this tab only for this file.";
    if (!/^salow\.pdfmarks\.[A-Za-z0-9_-]+\.$/.test(mPrefix)) { mmsg("This page could not tell whose marks these are, so it cannot keep them." + tab); return; }
    if (!(navigator.locks && navigator.locks.request)) { mmsg("This browser or connection cannot keep marks safely when the same file is open in two tabs." + tab); return; }
    if (typeof hash !== "string" || !/^[0-9a-f]{64}$/.test(hash)) { mmsg("The viewer could not identify this file, so it cannot keep marks for it." + tab); return; }
    var key = mPrefix + hash, raw; try { raw = localStorage.getItem(key); } catch (e) { mmsg("This browser will not keep marks." + tab); return; }
    if (raw === null && mFileCount() >= M.MAX_FILES) { mmsg("Marks are kept for up to " + M.MAX_FILES + " files on this device. Remove all marks of a file you no longer need first." + tab); return; }
    var l = M.load(raw);
    if (l.unreadable) { mBad = true; mBadKey = key; mBadRaw = raw; mControls(); mmsg("The saved marks for this file could not be read. They were left as they are on this device, and nothing will be written for this file. Remove all marks only clears the marks in this tab." + tab); return; }
    mKey = key; mLast = raw; mState = "ok"; mMarks = l.marks;
    mmsg(l.marks.length ? l.marks.length + " saved mark(s) for this file on this device." : ""); renderMarks();
  }
  function mAdopt(raw, why) {
    var refocus = false, l = window.PdfMarks.load(raw); mDrag = null; mHold = mClearHold();
    if (l.unreadable) { mMarks = []; mState = "tab"; mBad = true; mBadKey = mKey || mBadKey; mBadRaw = raw; mmsg(why + " The saved marks could not be read. They were left as they are on this device, marks stay in this tab only, and nothing more is written. Remove all marks only clears the marks in this tab."); }
    else {
      // A readable (or absent) value means saving works again: leave the unreadable state so the next edit is kept, and the discard control goes away.
      if (mBad) { mBad = false; if (!mKey) mKey = mBadKey; mBadKey = ""; mBadRaw = null; mState = "ok"; refocus = document.activeElement === $("pdf-markdiscard"); }
      mMarks = l.marks; mLast = raw; mmsg(why);
    }
    renderMarks();
    if (refocus) $("pdf-tool-hl").focus();
  }
  // One change to the marks list: saved first (when this file's marks are kept), shown only after the save worked.
  function mApply(next, okMsg, after) {
    mDiscDisarm();
    if (mSaving) { mmsg("Still saving the last change. Try again."); return; }
    var myDoc = doc, myKey = mKey, myLast = mLast, M = window.PdfMarks;
    var fin = function (r) {
      mSaving = false;
      if (r.stale) { mAdopt(r.raw, "Marks for this file changed in another tab, so that change was not applied. Showing the saved marks."); return; }
      if (r.limit) { mmsg("Marks are kept for up to " + M.MAX_FILES + " files on this device. Nothing was changed."); renderMarks(); return; }
      if (r.size) { mmsg("The marks for this file are too large to keep on this device, so nothing was changed. Remove some marks first."); renderMarks(); return; }
      if (r.err) { mmsg("This browser would not save that change, so nothing was changed."); renderMarks(); return; }
      mMarks = next; mHold = mClearHold(); if (myKey === mKey && r.raw !== undefined) mLast = r.raw;
      mmsg(okMsg); renderMarks(); if (after) after();
    };
    mSaving = true;
    if (mState !== "ok") { fin({ ok: true }); return; }
    var run = function () { return M.applySave(localStorage, myKey, myLast, next, mFileCount); };
    navigator.locks.request("salow-pdfmarks", function () { return run(); }).then(function (r) { if (myDoc !== doc) return; fin(r); }, function () { if (myDoc === doc) { mSaving = false; mmsg("The change could not be saved."); renderMarks(); } });
  }
  function mDiscDisarm() { if (mDisc) { clearTimeout(mDisc); mDisc = null; } }
  // A person-triggered, confirmed delete of saved marks this page could not read. It runs under the same lock, deletes only if the stored text is still exactly what could not be read, and never runs on its own.
  $("pdf-markdiscard").addEventListener("click", function () {
    if (!mBad || !doc || mSaving || mBusy) return;
    if (!mDisc) {
      mDisc = setTimeout(function () { mDisc = null; if (mBad) mmsg("Nothing was deleted."); }, 15000);
      mmsg("This deletes the saved marks stored for this file on this device. It cannot be undone. Press Discard unreadable saved marks again within 15 seconds to delete them. Waiting does nothing.");
      return;
    }
    mDiscDisarm();
    var myDoc = doc, key = mBadKey, was = mBadRaw, M = window.PdfMarks, mine = mMarks.slice();
    mSaving = true;
    var fin = function (r) {
      mSaving = false;
      if (r.changed) { mAdopt(r.raw, "The saved marks changed in another tab, so nothing was deleted."); return; }
      if (r.err) { mmsg("This browser would not delete them, so nothing was changed."); return; }
      mBad = false; mBadKey = ""; mBadRaw = null; mKey = key; mState = "ok"; mLast = r.raw === undefined ? null : r.raw;
      $("pdf-tool-hl").focus();
      mmsg("The unreadable saved marks for this file were deleted from this device." + (mine.length ? (r.kept ? " The marks in this tab are now kept." : " The marks in this tab could not be kept: " + r.why) : ""));
      renderMarks(); mControls();
    };
    navigator.locks.request("salow-pdfmarks", function () {
      var cur; try { cur = localStorage.getItem(key); } catch (e) { return { err: true }; }
      if (cur !== was) return { changed: true, raw: cur };
      try { localStorage.removeItem(key); } catch (e) { return { err: true }; }
      if (!mine.length) return { raw: null };
      var s = M.applySave(localStorage, key, null, mine, mFileCount);
      return s.ok ? { raw: s.raw, kept: true } : { raw: null, why: s.limit ? "the file limit is reached." : s.size ? "they are too large to keep." : "this browser would not save them." };
    }).then(function (r) { if (myDoc !== doc) return; fin(r); }, function () { if (myDoc === doc) { mSaving = false; mmsg("The change could not be saved."); } });
  });
  window.addEventListener("storage", function (e) {
    if (!doc || !mKey || mState !== "ok" || e.key !== mKey || e.storageArea !== localStorage) return;
    mAdopt(e.newValue, "Marks for this file were changed in another tab. Showing the saved marks.");
  });
  function mReset() { mToken++; mKey = ""; mBad = false; mDiscDisarm(); mState = "tab"; mLast = null; mSaving = false; mMarks = []; mTool = "off"; mDrag = null; mHold = mClearHold(); mBusy = false; renderMarks(); mmsg(""); }
  $("pdf-tool-hl").addEventListener("click", function () { mSetTool("hl"); });
  $("pdf-tool-tx").addEventListener("click", function () { mSetTool("tx"); });
  $("pdf-tool-pen").addEventListener("click", function () { mSetTool("pen"); });
  $("pdf-tool-sig").addEventListener("click", function () { mSetTool("sig"); });
  // Signature pad: strokes live in this tab's memory only (fractions of the pad), never stored or sent.
  var SIG_ASPECT = 3, sigStrokes = null, sigCur = null, sigDraft = [], sigPad = $("pdf-sigpad");
  function sigPt(e) { var r = sigPad.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / (r.width || 1))), Math.min(1, Math.max(0, (e.clientY - r.top) / (r.height || 1)))]; }
  function sigCount() { return sigDraft.reduce(function (n, st) { return n + st.length; }, 0); }
  function sigShow() {
    sigPad.textContent = "";
    sigDraft.forEach(function (st) { var pl = document.createElementNS(SVGNS, "polyline"); pl.setAttribute("points", st.map(function (q) { return (q[0] * 300).toFixed(1) + "," + (q[1] * 100).toFixed(1); }).join(" ")); sigPad.appendChild(pl); });
  }
  sigPad.addEventListener("pointerdown", function (e) {
    if (e.button > 0) return; e.preventDefault(); try { sigPad.setPointerCapture(e.pointerId); } catch (x) { /* best effort */ }
    if (sigDraft.length >= window.PdfMarks.MAX_STROKES) { $("pdf-sig-state").textContent = "That is the most lines a signature can have. Clear the box to start again."; return; }
    sigCur = { id: e.pointerId, pts: [sigPt(e)] }; sigDraft.push(sigCur.pts); $("pdf-sig-state").textContent = ""; sigShow();
  });
  sigPad.addEventListener("pointermove", function (e) {
    if (!sigCur || e.pointerId !== sigCur.id) return;
    if (sigCount() >= window.PdfMarks.MAX_POINTS) { $("pdf-sig-state").textContent = "The signature reached its length limit. Lift to finish it."; return; }
    var p = sigPt(e), q = sigCur.pts[sigCur.pts.length - 1]; if (Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]) < 0.004) return; sigCur.pts.push(p); sigShow();
  });
  function sigEnd(e) { if (sigCur && e.pointerId === sigCur.id) { sigCur = null; sigDraft = sigDraft.filter(function (st) { return st.length >= 2; }); sigShow(); } }
  sigPad.addEventListener("pointerup", sigEnd); sigPad.addEventListener("pointercancel", sigEnd);
  $("pdf-sig-clear").addEventListener("click", function () { sigDraft = []; sigCur = null; sigStrokes = null; if (mTool === "sig") mSetTool("sig"); sigShow(); $("pdf-sig-state").textContent = "The box and the signature in memory are cleared."; mControls(); });
  $("pdf-sig-use").addEventListener("click", function () {
    if (!sigDraft.length) { $("pdf-sig-state").textContent = "Draw a signature in the box first."; return; }
    sigStrokes = sigDraft.map(function (st) { return st.map(function (q) { return [q[0], q[1]]; }); }); $("pdf-sig-state").textContent = "Signature ready. Choose Place signature, then click the page."; mControls();
  });
  $("pdf-markclear").addEventListener("click", function () { if (mBusy) return; mApply([], mState === "ok" ? "All marks for this file were removed from this device." : mBad ? "All marks removed from this tab. The saved marks that could not be read are still on this device." : "All marks removed from this tab.", function () { $("pdf-tool-hl").focus(); }); });
  $("pdf-markcopy").addEventListener("click", function () {
    if (!doc || mBusy || !mMarks.length) return;
    var withNotes = !$("pdf-markwithnotes").disabled && $("pdf-markwithnotes").checked && nState === "ok" && notes.length > 0, snapN = withNotes ? notes.slice() : [];
    var myDoc = doc, nameText = $("pdf-name").textContent || "PDF file", tok = ++mToken, sig = JSON.stringify(mMarks) + (withNotes ? "|notes|" + N.dump(snapN) : "");
    var sfx = withNotes ? "-marks-notes.pdf" : "-marks.pdf", said = function (r) { return "Downloaded a copy with " + r.added + " mark(s)" + (withNotes ? " and " + r.notes + " note(s)" : "") + ". The file you opened was not changed."; };
    if (mHold && mHold.doc === myDoc && mHold.sig === sig) { var h = mHold; mHold = mClearHold(); pSave(h.r, nameText, sfx); mmsg(said(h.r)); return; }
    mHold = mClearHold(); mBusy = true; mControls(); mmsg("Building the copy in this browser.");
    var libs = window.PDFLib ? Promise.resolve() : loadScript(BASE + "pdf-lib.min.js").catch(function () { throw { stage: "load" }; });
    libs.then(function () { return myDoc.getData().catch(function () { throw { stage: "read" }; }); }).then(function (bytes) {
      // Marks first (it refuses XFA files and files with an unsafe annotation list), then the notes are added to that result.
      return window.PdfMarks.build(window.PDFLib, window.PdfNotesExport, bytes, mMarks.slice()).then(function (r1) {
        if (!withNotes) return r1;
        return window.PdfNotesExport.build(window.PDFLib, r1.bytes, snapN).then(function (r2) {
          if (r2.bytes.length > window.PdfNotesExport.MAX_BYTES) throw { stage: "size" };
          return { bytes: r2.bytes, added: r1.added, notes: r2.added, signed: r1.signed };
        });
      });
    }).then(function (r) {
      if (tok !== mToken || myDoc !== doc) return;
      if (r.signed) {
        mHold = { doc: myDoc, r: r, sig: sig, timer: setTimeout(function () { mHold = null; if (myDoc === doc) mmsg("The copy was not downloaded."); }, 15000) };
        mmsg("This file has a digital signature. A copy rewrites the file, so the signature in the copy will not validate. The original is not touched. Press Download a copy with marks again within 15 seconds, with no changes to the marks or the notes choice, to download it anyway.");
        return;
      }
      pSave(r, nameText, sfx); mmsg(said(r));
    }).catch(function (e) {
      if (tok !== mToken || myDoc !== doc) return;
      var c = e && (e.stage || e.code), m = "The copy could not be made. Nothing was downloaded and the file you opened was not changed.";
      if (c === "load") m = "The part of this page that writes PDFs could not be loaded. Check the connection and try again.";
      else if (c === "read") m = "This page could not read the open file's bytes again. Open the file again and retry.";
      else if (c === "size") m = "The copy would be over 20 MB. Nothing was downloaded.";
      else if (c === "encrypted") m = "This file is encrypted, so it cannot be copied. Nothing was downloaded.";
      else if (c === "damaged") m = "This file could not be read again (it may be damaged). Nothing was downloaded.";
      else if (c === "annots") m = "A page in this file has an annotation list this page cannot add to. Nothing was downloaded.";
      else if (c === "xfa") m = "This file has an XFA form, which a copy made here would lose. Nothing was downloaded.";
      mmsg(m);
    }).then(function () { if (tok === mToken) { mBusy = false; mControls(); } });
  });
  // A marks-fixed copy: the marks are drawn into the page content, so they cannot be removed or edited in the copy. It is a separate file; the opened file and the saved marks are not changed.
  // Notes are not included. Signed files are refused. Existing annotations and form fields stay as they are.
  $("pdf-markflat").addEventListener("click", function () {
    if (!doc || mBusy || !mMarks.length) return;
    var myDoc = doc, nameText = $("pdf-name").textContent || "PDF file", tok = ++mToken;
    mHold = mClearHold(); mBusy = true; mControls(); mmsg("Building the copy with the marks fixed in, in this browser.");
    var libs = window.PDFLib ? Promise.resolve() : loadScript(BASE + "pdf-lib.min.js").catch(function () { throw { stage: "load" }; });
    libs.then(function () { return myDoc.getData().catch(function () { throw { stage: "read" }; }); }).then(function (bytes) {
      return window.PdfMarks.build(window.PDFLib, window.PdfNotesExport, bytes, mMarks.slice(), { flat: true });
    }).then(function (r) {
      if (tok !== mToken || myDoc !== doc) return;
      pSave(r, nameText, "-marks-fixed.pdf");
      mmsg("Downloaded a copy with " + r.added + " mark(s) fixed into the pages (not a full flatten: form fields and existing annotations are kept as they are in the file, not merged into the page). They cannot be removed or edited in that copy. Your notes are not in it. The file you opened and your saved marks were not changed.");
    }).catch(function (e) {
      if (tok !== mToken || myDoc !== doc) return;
      var c = e && (e.stage || e.code), m = "The copy with the marks fixed in could not be made. Nothing was downloaded and the file you opened was not changed.";
      if (c === "signed") m = "This file has a digital signature, so no copy with the marks fixed in is made. Nothing was downloaded.";
      else if (c === "xfa") m = "This file has an XFA form, which a copy made here would lose. Nothing was downloaded.";
      else if (c === "size") m = "The copy would be over 20 MB. Nothing was downloaded.";
      else if (c === "encrypted") m = "This file is encrypted, so it cannot be copied. Nothing was downloaded.";
      else if (c === "load") m = "The part of this page that writes PDFs could not be loaded. Check the connection and try again.";
      else if (c === "read") m = "This page could not read the open file's bytes again. Open the file again and retry.";
      else if (c === "annots") m = "A page in this file has an annotation list this page cannot read. Nothing was downloaded.";
      mmsg(m);
    }).then(function () { if (tok === mToken) { mBusy = false; mControls(); } });
  });
  function closeDoc(msg) {
    token++; cancelCurrent(); fseq.begin(); finding = false; mark = ""; $("pdf-fstop").disabled = true; fmsg(""); $("pdf-q").value = "";
    formsReset(); pagesReset(); mReset(); thClose(); var d = doc; doc = null; page = 1; zoom = 1; fitPage = false; rot = 0; notesClose(); $("pdf-text").textContent = ""; shown = "";
    settle.then(function () { if (doc) return; canvas.width = 1; canvas.height = 1; canvas.style.width = "0"; canvas.style.height = "0"; });
    var lt = loadTask; loadTask = null; if (lt) { try { lt.destroy(); } catch (e) { /* already gone */ } }
    controls(); status.textContent = msg || "";
  }
  // Password PDFs: the page asks, pdf.js gets the answer once, and the box is emptied at once. The password lives only inside the open call
  // (never in storage, a URL, a log line or a request). Cancel, a new file, Close or leaving the page drops the pending open.
  var pwPending = null, pwBox = $("pdf-pw"), pwForm = $("pdf-pwform");
  function pwHide() { pwBox.value = ""; pwForm.hidden = true; }
  function pwDrop() { var p = pwPending; pwPending = null; pwHide(); if (p) { try { p.t.destroy(); } catch (e) { /* already gone */ } } }
  function pwAsk(update, reason, mine, t) {
    if (!seq.current(mine)) { try { t.destroy(); } catch (e) { /* superseded */ } return; }
    pwPending = { update: update, mine: mine, t: t };
    pwBox.value = ""; pwForm.hidden = false;
    status.textContent = reason === 2 ? "That password did not work. Try again, or press Cancel." : "This PDF needs a password. This page does not store it. Your browser may offer to save it.";
    pwBox.focus();
  }
  pwForm.addEventListener("submit", function (e) {
    e.preventDefault(); var p = pwPending; if (!p) return;
    var v = pwBox.value; pwBox.value = ""; if (!v) { status.textContent = "Type the password, or press Cancel."; return; }
    if (!seq.current(p.mine)) { pwDrop(); return; }
    pwPending = null; pwForm.hidden = true; status.textContent = "Opening...";
    p.update(v); // a wrong one asks again through pwAsk with reason 2
  });
  $("pdf-pwcancel").addEventListener("click", function () { seq.begin(); pwDrop(); status.textContent = "Not opened. That PDF needs a password. Nothing was saved from this file."; });
  window.addEventListener("pagehide", pwDrop);
  function open(buf, name, mine) {
    var canSave = !!(navigator.locks && navigator.locks.request);
    var hashed = canSave ? N.sha256Hex(buf).catch(function () { return ""; }) : Promise.resolve(""); // taken before the reader can take the bytes
    loadLib().then(function (m) {
      var t = m.getDocument({ data: new Uint8Array(buf), isEvalSupported: false, disableAutoFetch: true, disableStream: true, standardFontDataUrl: BASE + "standard_fonts/" });
      t.onPassword = function (update, reason) { pwAsk(update, reason, mine, t); };
      return t.promise.then(function (d) { return { d: d, t: t }; }, function (e) { try { t.destroy(); } catch (x) { /* gone */ } throw e; });
    }).then(function (r) { return hashed.then(function (h) { r.h = h; return r; }); }).then(function (r) {
      if (!seq.current(mine)) { try { r.t.destroy(); } catch (x) { /* superseded by a newer open */ } return; }
      var d = r.d; closeDoc(""); doc = d; loadTask = r.t; page = 1; zoom = 1; fitPage = false; rot = 0; $("pdf-name").textContent = name; notesOpen(d, r.h); mOpen(r.h); controls(); fControls(); thOpen(d); thMark(); draw();
    }).catch(function (e) {
      if (!seq.current(mine)) return;
      status.textContent = e && e.name === "PasswordException" ? "This PDF is password protected. Password files are not supported yet." : e && e.name === "InvalidPDFException" ? "That PDF is damaged or not readable." : "The viewer could not open that file.";
    });
  }
  $("pdf-open").addEventListener("change", function (e) {
    var f = e.target.files && e.target.files[0]; e.target.value = ""; if (!f) return;
    var mine = seq.begin(); pwDrop();
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
  // Fit width is the 100 percent view. Fit page shrinks the page so a whole page is visible (the larger of width and height limits); zoom buttons leave it and step from 100 percent.
  $("pdf-zin").addEventListener("click", function () { if (fitPage) { fitPage = false; zoom = C.zoomFrom(fitZoom, 1); } else zoom = C.zoomStep(zoom, 1); controls(); draw(); });
  $("pdf-zout").addEventListener("click", function () { if (fitPage) { fitPage = false; zoom = C.zoomFrom(fitZoom, -1); } else zoom = C.zoomStep(zoom, -1); controls(); draw(); });
  $("pdf-fitw").addEventListener("click", function () { if (!doc) return; fitPage = false; zoom = 1; controls(); draw(); });
  // Fit page: the stage is scrolled to the top of the window first, so the page height can use the whole window (less a small margin). Resizing refits.
  $("pdf-fitp").addEventListener("click", function () { if (!doc) return; fitPage = true; $("pdf-stage").scrollIntoView({ block: "start" }); controls(); draw(); });
  // Turn view: 90 degrees clockwise a step, for this view only (the file is not changed and nothing is stored). Note pins are placed on the unturned page,
  // so they are hidden while the view is turned.
  $("pdf-rot").addEventListener("click", function () { if (!doc) return; rot = (rot + 90) % 360; if (rot && adding) { adding = false; nControls(); nmsg("Choosing a spot for a note was cancelled because the view was turned."); } controls(); draw(); });
  var rz = null;
  window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { if (doc) draw(); }, 200); });
  $("pdf-page").addEventListener("change", function () { var n = parseInt(this.value, 10); if (isNaN(n)) { controls(); return; } go(n); });
  $("pdf-close").addEventListener("click", function () { seq.begin(); pwDrop(); closeDoc("File closed. Nothing was stored."); $("pdf-name").textContent = ""; });
  var rz = null; window.addEventListener("resize", function () { clearTimeout(rz); rz = setTimeout(function () { if (doc) draw(); }, 150); });
  controls();
})();
