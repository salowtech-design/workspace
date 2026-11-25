(function () {
  "use strict";
  var C = window.SalowPdfCheck, $ = function (i) { return document.getElementById(i); };
  var status = $("pdf-status");
  // The renderer is not part of this build yet. Files are checked here and never leave the browser.
  $("pdf-open").addEventListener("change", function (e) {
    var f = e.target.files && e.target.files[0]; e.target.value = ""; if (!f) return;
    var rd = new FileReader();
    rd.onerror = function () { status.textContent = "This browser could not read that file."; };
    rd.onload = function () {
      var r = C.check(f.name, f.size, String(rd.result));
      status.textContent = r.ok ? "Checked: this is a PDF of " + Math.round(f.size / 1024) + " KB. Page display is not built yet, so it cannot be shown. Nothing was uploaded." : r.why;
    };
    rd.readAsBinaryString(f.slice(0, 1024));
  });
})();
