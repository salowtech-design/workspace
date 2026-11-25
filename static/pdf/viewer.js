(function () {
  "use strict";
  var C = window.SalowPdfCheck, $ = function (i) { return document.getElementById(i); };
  var status = $("pdf-status"), canvas = $("pdf-canvas"), ctx = canvas.getContext("2d");
  var BASE = "/static/pdf/vendor/";
  var lib = null, doc = null, loadTask = null, page = 1, zoom = 1, task = null, token = 0;
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
    $("pdf-close").disabled = !on; $("pdf-page").disabled = !on;
    $("pdf-where").textContent = on ? "Page " + page + " of " + doc.numPages + ", zoom " + Math.round(zoom * 100) + "%" : "No file open.";
    if (on) $("pdf-page").value = page;
  }
  function draw() {
    if (!doc) return;
    var my = ++token; if (task) { try { task.cancel(); } catch (e) { /* already done */ } }
    doc.getPage(page).then(function (pg) {
      if (my !== token) return;
      var wrap = $("pdf-stage").clientWidth || 600, v1 = pg.getViewport({ scale: 1 });
      var css = Math.min(wrap, 1600) / v1.width * zoom, dpr = Math.min(window.devicePixelRatio || 1, 2);
      var vp = pg.getViewport({ scale: css * dpr }), px = vp.width * vp.height;
      if (px > 24e6) vp = pg.getViewport({ scale: css * dpr * Math.sqrt(24e6 / px) });
      canvas.width = Math.floor(vp.width); canvas.height = Math.floor(vp.height);
      canvas.style.width = Math.floor(v1.width * css) + "px"; canvas.style.height = Math.floor(v1.height * css) + "px";
      task = pg.render({ canvasContext: ctx, viewport: vp });
      return task.promise.then(function () { if (my === token) status.textContent = ""; });
    }).catch(function (e) { if (e && e.name === "RenderingCancelledException") return; status.textContent = "This page could not be drawn."; });
  }
  function go(p) { if (!doc) return; page = C.pageIndex(p, 0, doc.numPages); controls(); draw(); }
  function closeDoc(msg) {
    token++; if (task) { try { task.cancel(); } catch (e) { /* done */ } }
    var d = doc; doc = null; page = 1; zoom = 1; canvas.width = 1; canvas.height = 1; canvas.style.width = "0"; canvas.style.height = "0";
    var lt = loadTask; loadTask = null; if (lt) { try { lt.destroy(); } catch (e) { /* already gone */ } }
    controls(); status.textContent = msg || "";
  }
  function open(buf, name) {
    loadLib().then(function (m) {
      var t = m.getDocument({ data: new Uint8Array(buf), isEvalSupported: false, disableAutoFetch: true, disableStream: true, standardFontDataUrl: BASE + "standard_fonts/" });
      return t.promise.then(function (d) { return { d: d, t: t }; }, function (e) { try { t.destroy(); } catch (x) { /* gone */ } throw e; });
    }).then(function (r) {
      var d = r.d; closeDoc(""); doc = d; loadTask = r.t; page = 1; zoom = 1; $("pdf-name").textContent = name; controls(); draw();
    }).catch(function (e) {
      status.textContent = e && e.name === "PasswordException" ? "This PDF is password protected. Password files are not supported yet." : e && e.name === "InvalidPDFException" ? "That PDF is damaged or not readable." : "The viewer could not open that file.";
    });
  }
  $("pdf-open").addEventListener("change", function (e) {
    var f = e.target.files && e.target.files[0]; e.target.value = ""; if (!f) return;
    var head = new FileReader();
    head.onerror = function () { status.textContent = "This browser could not read that file."; };
    head.onload = function () {
      var r = C.check(f.name, f.size, String(head.result));
      if (!r.ok) { status.textContent = r.why; return; }
      status.textContent = "Opening...";
      var rd = new FileReader();
      rd.onerror = function () { status.textContent = "This browser could not read that file."; };
      rd.onload = function () { open(rd.result, f.name.slice(0, 120)); };
      rd.readAsArrayBuffer(f);
    };
    head.readAsBinaryString(f.slice(0, 1024));
  });
  $("pdf-prev").addEventListener("click", function () { go(page - 1); });
  $("pdf-next").addEventListener("click", function () { go(page + 1); });
  $("pdf-zin").addEventListener("click", function () { zoom = C.zoomStep(zoom, 1); controls(); draw(); });
  $("pdf-zout").addEventListener("click", function () { zoom = C.zoomStep(zoom, -1); controls(); draw(); });
  $("pdf-page").addEventListener("change", function () { var n = parseInt(this.value, 10); if (isNaN(n)) { controls(); return; } go(n); });
  $("pdf-close").addEventListener("click", function () { closeDoc("File closed. Nothing was stored."); $("pdf-name").textContent = ""; });
  window.addEventListener("resize", function () { if (doc) draw(); });
  controls();
})();
