/* Drive page: wires the device client. The row badge is a cheap presence check; the bytes are verified when a file is opened or added. */
(function () {
  "use strict";
  var root = document.getElementById("drive-root"), D = window.SalowDriveDevice;
  if (!root || !D) return;
  var store = new D.Store("salow-drive-" + root.dataset.user);  // one database per signed-in person
  var deps = {
    store: store, csrf: root.dataset.csrf,
    urls: { fileNew: root.dataset.new, version: function (id) { return root.dataset.versionTpl.replace("00000000-0000-0000-0000-000000000000", id); } },
    fetch: function (u, o) { return window.fetch(u, o); }, Blob: window.Blob, URL: window.URL,
    download: function (url, name) {
      var a = document.createElement("a"); a.href = url; a.download = name; a.rel = "noopener"; a.style.display = "none";
      document.body.appendChild(a); a.click(); a.remove();
    },
  };
  function label(row) { return row.querySelector(".drive-local"); }
  // The badge only says a record is present. "Checked" is said only right after a successful open (the bytes were hashed then).
  function paint(row, checked) {
    var btn = row.querySelector(".drive-open"), sha = row.dataset.sha;
    if (!sha) return;
    store.has(sha).then(function (yes) {
      label(row).textContent = yes ? (checked ? "Opened just now: the copy on this device was checked" : "A copy is on this device (not checked until opened)") : "Not on this device";
      if (btn) btn.disabled = !yes;
    }, function (e) { label(row).textContent = "This device cannot be checked: " + e.message; });
  }
  Array.prototype.forEach.call(root.querySelectorAll(".drive-file"), function (row) {
    paint(row);
    var btn = row.querySelector(".drive-open"), nv = row.querySelector(".drive-newver"), ra = row.querySelector(".drive-reattach");
    if (ra) ra.addEventListener("change", function () {
      var f = ra.files && ra.files[0]; ra.value = ""; if (!f) return;
      label(row).textContent = "Checking the file...";
      D.reattach(deps, row.dataset.sha, f).then(function (r) { if (r.ok) paint(row); else label(row).textContent = r.message; });
    });
    if (btn) btn.addEventListener("click", function () {
      btn.disabled = true;
      D.openLocal(deps, row.dataset.name, row.dataset.sha).then(function (r) { if (!r.ok) label(row).textContent = r.message; else paint(row, true); if (!r.ok && r.state === "damaged") btn.disabled = true; });
    });
    if (nv) nv.addEventListener("change", function () {
      var f = nv.files && nv.files[0]; nv.value = ""; if (!f) return;
      label(row).textContent = "Adding a version...";
      D.addVersion(deps, row.dataset.id, parseInt(row.dataset.version, 10), f).then(function (r) {
        if (r.ok) {
          // the row now describes the version this call committed, so a later Open or "Put a copy" uses the new checksum, not the old one
          row.dataset.version = String(r.version); row.dataset.sha = r.sha256;
          label(row).textContent = r.message + " This row is now version " + r.version + ".";
          var ob = row.querySelector(".drive-open"); if (ob) ob.disabled = false;  // the bytes were just stored and read back by addVersion
        } else label(row).textContent = r.message;
      });
    });
  });
  var pick = document.getElementById("drive-pick"), status = document.getElementById("drive-upload-status");
  if (pick) pick.addEventListener("change", function () {
    var files = Array.prototype.slice.call(pick.files || []), lines = [], done = 0, saved = 0;
    pick.value = "";
    if (!files.length) return;
    status.textContent = "Adding " + files.length + " file(s)...";
    files.reduce(function (p, f) {
      return p.then(function () { return D.addFile(deps, f, root.dataset.folder).then(function (r) { done++; if (r.ok) saved++; lines.push(f.name + ": " + r.message); }); });
    }, Promise.resolve()).then(function () {
      status.textContent = saved + " of " + done + " saved to Drive. " + lines.join(" ");
      if (saved) { var a = document.createElement("a"); a.href = location.href; a.textContent = " Reload the list"; status.appendChild(a); }
    });
  });
})();
