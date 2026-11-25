(function () {
  "use strict";
  var C = window.SalowPdfCheck, $ = function (i) { return document.getElementById(i); };
  var status = $("pdf-status"), canvas = $("pdf-canvas"), ctx = canvas.getContext("2d");
  var BASE = "/static/pdf/vendor/";
  var lib = null, doc = null, loadTask = null, seq = new C.Seq(), page = 1, zoom = 1, task = null, token = 0, settle = Promise.resolve();
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
    var my = ++token; cancelCurrent();
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
      var tx = $("pdf-text"); tx.textContent = "Reading the text of this page...";
      pg.getTextContent().then(function (tc) {
        if (my !== token) return;
        var r = C.takeText(tc.items, 20000), t = r.text;
        tx.textContent = t ? t + (r.cut ? "\n[Shortened at 20000 characters.]" : "") : "This page has no text the viewer can read. It may be a scanned image.";
      }, function () { if (my === token) tx.textContent = "The text of this page could not be read."; });
      task = pg.render({ canvasContext: ctx, viewport: vp });
      return task.promise.then(function () { if (my === token) status.textContent = ""; });
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
    var d = doc, mine = fseq.begin(), order = C.pageOrder(page, d.numPages), i = 0, partial = 0, unread = 0;
    finding = true; $("pdf-fstop").disabled = false; $("pdf-find").disabled = true;
    var live = function () { return fseq.current(mine) && doc === d; };
    function done() {
      if (!live()) return;
      var more = d.numPages > order.length ? " Only the first " + order.length + " pages in this order were searched." : "";
      var note = (partial ? " " + partial + " page(s) have more than 20000 characters, so only the first 20000 were searched." : "") + (unread ? " " + unread + " page(s) had no readable text (scans are not read)." : "");
      fmsg("No match for \"" + q + "\" in " + order.length + " page(s)." + more + note); fend();
    }
    function step() {
      if (!live()) return;
      if (i >= order.length) return done();
      var p = order[i++]; fmsg("Searching page " + p + " of " + d.numPages + "...");
      d.getPage(p).then(function (pg) { return pg.getTextContent(); }).then(function (tc) {
        if (!live()) return;
        var r = C.takeText(tc.items, 20000); if (r.cut) partial++; if (!r.text) unread++;
        var n = C.countIn(r.text, q);
        if (n > 0) { fmsg("Found \"" + q + "\" on page " + p + ": " + n + " match(es)" + (r.cut ? " in the first 20000 characters" : "") + ". Press Find again for the next page."); fend(); go(p); return; }
        setTimeout(step, 0);
      }, function () { if (!live()) return; unread++; setTimeout(step, 0); });
    }
    step();
  }
  $("pdf-find").addEventListener("click", find);
  $("pdf-q").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); find(); } });
  $("pdf-fstop").addEventListener("click", function () { if (!finding) return; fseq.begin(); fmsg("Find stopped."); fend(); });
  function go(p) { if (!doc) return; page = C.pageIndex(p, 0, doc.numPages); controls(); draw(); }
  function closeDoc(msg) {
    token++; cancelCurrent(); fseq.begin(); finding = false; $("pdf-fstop").disabled = true; fmsg(""); $("pdf-q").value = "";
    var d = doc; doc = null; page = 1; zoom = 1; $("pdf-text").textContent = "";
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
      var d = r.d; closeDoc(""); doc = d; loadTask = r.t; page = 1; zoom = 1; $("pdf-name").textContent = name; controls(); draw();
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
