/* Save to Drive / Open from Drive for Sheets and Slides, for the signed-in person's OWN files on THIS device.
 * Nothing new is sent to the server: Save uses the same Drive calls as "Add a file from this device" (a file record at the top of My Drive,
 * name, size and checksum only) and keeps the bytes in this browser's Drive store. Open lists the person's own top-level Drive files, keeps
 * the ones whose bytes are on this device, re-checks the checksum, and hands the file to the page's existing Import control, so the page's
 * own checks, confirmation and Undo apply unchanged. Nothing here shares a file or writes into a shared folder. */
(function (root) {
  "use strict";
  function init(cfg) {
    var box = cfg.box, D = root.SalowDriveDevice, st = cfg.status;
    if (!box || !D) return null;
    var store = new D.Store("salow-drive-" + box.dataset.user);
    var deps = { store: store, csrf: box.dataset.csrf, urls: { fileNew: box.dataset.new, version: function (id) { return box.dataset.versionTpl.replace("00000000-0000-0000-0000-000000000000", id); } }, fetch: function (u, o) { return root.fetch(u, o); } };
    var nameIn = cfg.nameInput, sel = cfg.select, saving = false, listing = false, pending = null;  // pending: a record Drive made for this page whose version call failed
    function say(t) { st.textContent = t; }
    function extOf(n) { var m = /\.([A-Za-z0-9]+)$/.exec(n || ""); return m ? m[1].toLowerCase() : ""; }
    function save() {
      if (saving) return;
      var c = cfg.getContent(); if (!c.ok) { say(c.message); return; }
      var name = (nameIn.value || "").trim();
      if (!name) { say("Type a file name first. Nothing was saved."); return; }
      if (extOf(name) !== cfg.saveExt) { say("The name must end in ." + cfg.saveExt + " so Drive and Import know what the file is. Nothing was saved."); return; }
      var bytes = new TextEncoder().encode(c.text);
      var file = new root.File([bytes], name, { type: cfg.saveType });
      saving = true; cfg.saveBtn.disabled = true; say("Saving to Drive...");
      var resume = pending && pending.name === name && pending.fileId;
      var wasPending = resume ? pending : null;
      (resume ? D.addVersion(deps, pending.fileId, 0, file) : D.addFile(deps, file, "")).then(function (r) {
        if (r.ok) pending = null; else if (r.fileId && r.resumable && r.baseVersion === 0) pending = { name: name, fileId: r.fileId };
        var tail = r.ok ? " It is at the top of My Drive; only you can see it until you share it in Drive." : (pending && pending.name === name ? " Press Save again with the same name to save what the page shows now (not the content of the failed save), or look at your Drive list first." : "");
        var msg = r.message;
        if (!r.ok && r.stage === "network") { msg = "The file is on this device. Drive did not return a confirmation, so it is not known whether this version was saved."; }
        if (!r.ok && wasPending && r.stage === "version" && r.sha256) {  // Drive refused: read the list to tell an earlier attempt that did arrive from a different saved version
          return getList().then(function (j) {
            var f = j.files.filter(function (x) { return x.id === wasPending.fileId; })[0];
            pending = null;
            if (f && f.version >= 1 && f.sha256 === r.sha256) say("Saved to Drive. Your earlier attempt had reached Drive, and the file is on this device. It is at the top of My Drive; only you can see it until you share it in Drive.");
            else if (f && f.version >= 1) say("The file is on this device. Drive already has a saved version under this name with different content, and it was not changed; nothing was overwritten. Save with a different name to keep what the page shows now.");
            else say("The file is on this device. Drive did not accept a new version, and nothing was overwritten. Look at your Drive list, or save with a different name.");
          }, function () { pending = null; say("The file is on this device. Drive did not accept a new version, and nothing was overwritten. Look at your Drive list, or save with a different name."); });
        }
        say(msg + tail);
      }, function (e) { say("Not saved: " + (e && e.message ? e.message : "unexpected error")); }).then(function () { saving = false; cfg.saveBtn.disabled = false; });
    }
    function getList() {
      return root.fetch(box.dataset.list, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (resp) {
        var type = resp.headers.get("content-type") || "";
        if (resp.redirected || resp.status === 401) throw new Error("Your session needs signing in again.");
        if (type.indexOf("application/json") < 0) throw new Error("Drive gave an unexpected reply (" + resp.status + ").");
        return resp.json();
      });
    }
    function listFiles() {
      if (listing) return; listing = true; cfg.listBtn.disabled = true; say("Looking at your Drive files...");
      getList().then(function (j) {
        var want = j.files.filter(function (f) { return cfg.openExts.indexOf(extOf(f.name)) >= 0 && f.sha256; });
        return Promise.all(want.map(function (f) { return store.has(f.sha256).then(function (y) { return { f: f, here: y }; }, function () { return { f: f, here: false }; }); })).then(function (rows) { return { rows: rows, truncated: j.truncated }; });
      }).then(function (o) {
        sel.textContent = "";
        var here = 0;
        o.rows.forEach(function (x) {
          var op = root.document.createElement("option"); op.value = x.f.id; op.dataset.sha = x.f.sha256; op.dataset.name = x.f.name;
          op.textContent = x.f.name + " (" + x.f.size + " bytes)" + (x.here ? "" : " - not on this device"); op.disabled = !x.here; if (x.here) here++;
          sel.appendChild(op);
        });
        sel.hidden = cfg.openBtn.hidden = !here;
        if (here) { for (var i = 0; i < sel.options.length; i++) if (!sel.options[i].disabled) { sel.selectedIndex = i; break; } }
        say(!o.rows.length ? "No " + cfg.openExts.map(function (e) { return "." + e; }).join(" or ") + " files at the top of your My Drive." : here ? here + " file(s) on this device. Choose one and press the Open button; the page then asks before it replaces anything." + (o.rows.length > here ? " " + (o.rows.length - here) + " more are in Drive but not on this device." : "") + (o.truncated ? " Only the first 200 Drive files were checked." : "") : "Your Drive has " + o.rows.length + " matching file(s) but none is on this device, so none can be opened here.");
      }).then(null, function (e) { say("Could not list your Drive files: " + (e && e.message ? e.message : "unexpected error") + " Nothing was changed."); }).then(function () { listing = false; cfg.listBtn.disabled = false; });
    }
    function open() {
      var o = sel.options[sel.selectedIndex]; if (!o || o.disabled) { say("Choose a file that is on this device."); return; }
      var name = o.dataset.name, sha = o.dataset.sha;
      say("Checking Drive and the copy on this device...");
      getList().then(function (j) {
        var now = j.files.filter(function (f) { return f.id === o.value; })[0];
        if (!now) throw new Error("That file is no longer at the top of your My Drive (it was moved, trashed or removed). Press the list button again.");
        if (now.sha256 !== sha) throw new Error("That file was saved again in Drive after this list was made. Press the list button again.");
        return store.state(sha);
      }).then(function (s) {
        if (s.state !== "on-device") throw new Error(s.state === "damaged" ? "The copy on this device is damaged, so it was not opened." : "That file is not on this device.");
        return store.get(sha);
      }).then(function (rec) {
        var f = new root.File([rec.bytes], D.safeName(name), { type: "application/octet-stream" });
        var input = cfg.inputFor(extOf(name)); if (!input) throw new Error("This page cannot import that kind of file.");
        var dt = new root.DataTransfer(); dt.items.add(f); input.files = dt.files;
        say("Checked. Handing the file to Import, which asks before it changes anything...");
        input.dispatchEvent(new root.Event("change", { bubbles: true }));
      }).then(null, function (e) { say((e && e.message ? e.message : "The file could not be read.") + " Nothing was changed."); });
    }
    cfg.saveBtn.addEventListener("click", save); cfg.listBtn.addEventListener("click", listFiles); cfg.openBtn.addEventListener("click", open);
    return { save: save, list: listFiles, open: open };
  }
  root.SalowDriveLink = { init: init };
})(typeof self !== "undefined" ? self : this);
