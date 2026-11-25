(function () {
  "use strict";
  var root = document.getElementById("slide-root");
  if (!root || !window.SalowSlides) return;
  var S = SalowSlides, KEY = root.getAttribute("data-store-key"), NS = "http://www.w3.org/2000/svg";
  var $ = function (id) { return document.getElementById(id); };
  var status = $("sl-status"), cur = 0, selId = null;
  var loaded; try { loaded = S.loadDeck(localStorage.getItem(KEY)); } catch (e) { loaded = { deck: S.newDeck(), unreadable: false, raw: null }; }
  var initial = loaded.deck, unreadable = loaded.unreadable, rawKept = loaded.raw;
  function lock(on) {
    ["sl-editing"].forEach(function (id) { var n = $(id); if (on) n.setAttribute("inert", ""); else n.removeAttribute("inert"); });
    $("sl-unreadable").hidden = !on; $("sl-present").disabled = on;
  }
  var H = new S.History(initial, { save: function (d) { try { localStorage.setItem(KEY, JSON.stringify(d)); return true; } catch (e) { return false; } } });
  H.blocked = unreadable;
  function el(n, at, tx) { var x = document.createElementNS(NS, n); Object.keys(at || {}).forEach(function (k) { x.setAttribute(k, at[k]); }); if (tx !== undefined) x.textContent = tx; return x; }
  // Shape and text of one item, shared by the editor and the slide show.
  function drawEl(g, d, e) {
    var c = S.color(d, e);
    if (e.type === "rect") g.appendChild(el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, fill: c }));
    else if (e.type === "ellipse") g.appendChild(el("ellipse", { cx: e.x + e.w / 2, cy: e.y + e.h / 2, rx: e.w / 2, ry: e.h / 2, fill: c }));
    if (e.text || e.type === "text") {
      var lines = S.wrapLines(e.text, e.size, e.type === "text" ? e.w : e.w - 8), lh = e.size * 1.2, shape = e.type !== "text";
      var top = shape ? e.y + e.h / 2 - (lines.length * lh) / 2 : e.y;
      var t = el("text", { x: shape ? e.x + e.w / 2 : e.x, "font-size": e.size, fill: S.textColor(d, e), "text-anchor": shape ? "middle" : "start", "font-family": "sans-serif" });
      lines.forEach(function (ln, k) { t.appendChild(el("tspan", { x: shape ? e.x + e.w / 2 : e.x, y: top + e.size + k * lh }, ln)); });
      g.appendChild(t);
    }
  }
  function sel() { var s = H.deck.slides[cur]; if (!s) return null; for (var i = 0; i < s.elements.length; i++) if (s.elements[i].id === selId) return s.elements[i]; return null; }
  function apply(next, note) { if (!next) return false; if (!H.change(next, note)) return false; cur = Math.min(cur, H.deck.slides.length - 1); render(); return true; }
  function render() {
    var d = H.deck, th = S.THEMES[d.theme];
    if (!sel()) selId = null;
    lock(unreadable);
    var list = $("sl-list"); list.textContent = "";
    d.slides.forEach(function (s, i) {
      var b = document.createElement("button"); b.type = "button"; if (i === cur) b.setAttribute("aria-current", "true"); b.setAttribute("aria-pressed", i === cur ? "true" : "false");
      var first = ""; s.elements.forEach(function (e) { if (!first && e.type === "text" && e.text) first = e.text; });
      b.textContent = (i + 1) + ". " + (first ? S.cut(first, 18) : "Slide");
      b.addEventListener("click", function () { cur = i; selId = null; render(); }); list.appendChild(b);
    });
    var wrap = $("sl-canvas-wrap"); wrap.textContent = "";
    var svg = el("svg", { viewBox: "0 0 " + S.W + " " + S.H, tabindex: "0", role: "img", "aria-label": "Slide " + (cur + 1) + " of " + d.slides.length });
    svg.appendChild(el("title", {}, "Slide " + (cur + 1) + " of " + d.slides.length));
    svg.appendChild(el("desc", {}, S.describe(d, cur).join("; ") || "Empty slide"));
    svg.appendChild(el("rect", { x: 0, y: 0, width: S.W, height: S.H, fill: th.bg }));
    d.slides[cur].elements.forEach(function (e) {
      var g = el("g", {}); drawEl(g, d, e);
      var hit = el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, fill: "transparent", stroke: e.id === selId ? "#2e6f95" : "none", "stroke-width": 3, "stroke-dasharray": "8 4", "data-id": e.id });
      hit.addEventListener("pointerdown", function (ev) { ev.preventDefault(); selId = e.id; render(); wrap.querySelector("svg").focus(); });
      g.appendChild(hit); svg.appendChild(g);
    });
    svg.addEventListener("pointerdown", function (ev) { if (ev.target === svg || ev.target.tagName === "rect" && !ev.target.getAttribute("data-id")) { selId = null; render(); } });
    svg.addEventListener("keydown", function (ev) {
      var e = sel(); if (!e) return; var dx = 0, dy = 0;
      if (ev.key === "ArrowLeft") dx = -10; else if (ev.key === "ArrowRight") dx = 10; else if (ev.key === "ArrowUp") dy = -10; else if (ev.key === "ArrowDown") dy = 10;
      else if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); apply(S.ops.deleteElement(H.deck, cur, e.id), "Item deleted."); return; } else return;
      ev.preventDefault(); apply(S.ops.updateElement(H.deck, cur, e.id, { x: e.x + dx, y: e.y + dy }));
      var s2 = $("sl-canvas-wrap").querySelector("svg"); if (s2) s2.focus();
    });
    wrap.appendChild(svg);
    var il = $("sl-items"); il.textContent = "";
    S.describe(d, cur).forEach(function (label, k) {
      var id = d.slides[cur].elements[k].id, li = document.createElement("li"), b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.setAttribute("aria-pressed", id === selId ? "true" : "false");
      b.addEventListener("click", function () { selId = id; render(); var again = $("sl-items").querySelectorAll("button")[k]; if (again) again.focus(); });
      li.appendChild(b); il.appendChild(li);
    });
    $("sl-items-empty").hidden = d.slides[cur].elements.length > 0;
    var e = sel(), props = $("sl-props"); props.disabled = !e; $("sl-del-el").disabled = !e;
    var pos = e ? d.slides[cur].elements.indexOf(e) : -1; $("sl-back").disabled = pos <= 0; $("sl-fwd").disabled = pos < 0 || pos >= d.slides[cur].elements.length - 1;
    ["text", "size", "x", "y", "w", "h", "fill"].forEach(function (f) { var inp = $("sl-" + f); if (document.activeElement !== inp) inp.value = e ? e[f] : ""; });
    var nt = $("sl-notes"); if (document.activeElement !== nt) nt.value = d.slides[cur].notes || "";
    $("sl-theme").value = d.theme; $("sl-undo").disabled = !H.past.length; $("sl-redo").disabled = !H.future.length;
    $("sl-up").disabled = cur === 0; $("sl-down").disabled = cur >= d.slides.length - 1; $("sl-del").disabled = d.slides.length <= 1; $("sl-dup").disabled = d.slides.length >= S.MAX_SLIDES;
    status.textContent = H.note;
    var cs = sel(); if (cs && cs.text) { var ln = S.wrapLines(cs.text, cs.size, cs.type === "text" ? cs.w : cs.w - 8); if (S.textHeight(ln, cs.size) > cs.h) status.textContent += " The text is estimated to run past the item's edges."; }
  }
  $("sl-notes").addEventListener("change", function () { apply(S.ops.setNotes(H.deck, cur, this.value), "Notes saved on this device."); });
  var fill = $("sl-fill"); S.FILLS.forEach(function (f) { var o = document.createElement("option"); o.value = f; o.textContent = f === "theme" ? "Theme color" : f; fill.appendChild(o); });
  ["text", "size", "x", "y", "w", "h"].forEach(function (f) {
    $("sl-" + f).addEventListener("change", function () {
      var e = sel(); if (!e) return; var p = {}; var asked = this.value; p[f] = asked;
      var ok = apply(S.ops.updateElement(H.deck, cur, e.id, p)), now = sel();
      if (now && f !== "text" && String(now[f]) !== String(Math.round(Number(asked)))) { status.textContent = "Adjusted " + f.toUpperCase() + " to " + now[f] + " so the item fits the slide and limits. " + H.note; }
      else if (now && f !== "text" && !isFinite(Number(asked))) { status.textContent = "Enter a number for " + f.toUpperCase() + ". Kept " + now[f] + "."; this.value = now[f]; }
    });
  });
  fill.addEventListener("change", function () { var e = sel(); if (e) apply(S.ops.updateElement(H.deck, cur, e.id, { fill: fill.value })); });
  ["text", "rect", "ellipse"].forEach(function (t) { $("sl-add-" + t).addEventListener("click", function () { var r = S.ops.addElement(H.deck, cur, t); if (!r) { status.textContent = "A slide holds up to " + S.MAX_EL + " items."; return; } selId = r.id; apply(r.deck); }); });
  [["sl-back", -1, "Item sent backward."], ["sl-fwd", 1, "Item brought forward."]].forEach(function (c) {
    $(c[0]).addEventListener("click", function () { var e = sel(); if (e && apply(S.ops.moveElement(H.deck, cur, e.id, c[1]), c[2])) { var b = $(c[0]); if (b.disabled) $("sl-del-el").focus(); else b.focus(); } });
  });
  $("sl-del-el").addEventListener("click", function () { var e = sel(); if (e) apply(S.ops.deleteElement(H.deck, cur, e.id), "Item deleted."); });
  $("sl-new").addEventListener("click", function () { var n = S.ops.addSlide(H.deck, cur); if (!n) { status.textContent = "A deck holds up to " + S.MAX_SLIDES + " slides."; return; } var prev = cur, prevSel = selId; cur++; selId = null; if (!apply(n)) { cur = prev; selId = prevSel; } });
  $("sl-del").addEventListener("click", function () { if (H.deck.slides[cur].elements.length && !window.confirm("Delete this slide and its items? You can undo until you reload.")) return; var n = S.ops.deleteSlide(H.deck, cur); if (n) { selId = null; apply(n, "Slide deleted."); } });
  $("sl-dup").addEventListener("click", function () {
    var n = S.ops.duplicateSlide(H.deck, cur); if (!n) { status.textContent = "A deck holds up to " + S.MAX_SLIDES + " slides."; return; }
    var prev = cur, prevSel = selId; cur++; selId = null; if (!apply(n, "Slide duplicated.")) { cur = prev; selId = prevSel; return; }
    var cb = $("sl-list").querySelector("button[aria-current=true]"); if (cb) cb.focus();
  });
  $("sl-up").addEventListener("click", function () { if (apply(S.ops.moveSlide(H.deck, cur, cur - 1))) { cur--; render(); } });
  $("sl-down").addEventListener("click", function () { if (apply(S.ops.moveSlide(H.deck, cur, cur + 1))) { cur++; render(); } });
  $("sl-theme").addEventListener("change", function () { apply(S.ops.setTheme(H.deck, this.value)); });
  $("sl-undo").addEventListener("click", function () { if (H.undo()) { cur = Math.min(cur, H.deck.slides.length - 1); render(); } });
  $("sl-redo").addEventListener("click", function () { if (H.redo()) { cur = Math.min(cur, H.deck.slides.length - 1); render(); } });
  $("sl-clear").addEventListener("click", function () {
    if (!window.confirm("Clear the whole deck? It becomes one empty slide. You can undo until you reload this page; after a reload the cleared deck cannot be brought back.")) return;
    var prev = cur, prevSel = selId; cur = 0; selId = null;
    if (!apply(S.newDeck(), "Deck cleared. Undo works until you reload this page.")) { cur = prev; selId = prevSel; status.textContent = H.blocked ? H.note : "Nothing to clear."; }
  });
  $("sl-dl-raw").addEventListener("click", function () {
    var u = URL.createObjectURL(new Blob([rawKept || ""], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = "unreadable-deck.json"; document.body.appendChild(l); l.click(); l.remove(); URL.revokeObjectURL(u);
  });
  $("sl-start-new").addEventListener("click", function () {
    try { localStorage.setItem(KEY + ".backup", rawKept || ""); } catch (e) { status.textContent = "This browser would not keep a backup of the unreadable deck, so I did not replace it."; return; }
    unreadable = false; H.blocked = false; rawKept = null; render();
    status.textContent = "Started a new deck. The unreadable data is kept in this browser under a backup key and is replaced only if you start another new deck.";
  });
  $("sl-export").addEventListener("click", function () {
    var u = URL.createObjectURL(new Blob([JSON.stringify(H.deck)], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = "deck.json"; document.body.appendChild(l); l.click(); l.remove(); URL.revokeObjectURL(u);
  });
  $("sl-pdf").addEventListener("click", function () {
    var btn = $("sl-pdf"), deck = H.deck;
    if (!window.PDFLib || !window.SalowSlidesPdf) { status.textContent = "The PDF maker did not load, so no PDF was made."; return; }
    btn.disabled = true; status.textContent = "Making the PDF on this device...";
    window.SalowSlidesPdf.build(window.PDFLib, S, deck).then(function (bytes) {
      var u = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" })), l = document.createElement("a");
      l.href = u; l.download = "slides.pdf"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
      status.textContent = "Downloaded slides.pdf: " + deck.slides.length + " pages, one per slide. Speaker notes are not in the PDF. Line breaks follow the screen estimate, so text may sit a little differently than in the editor.";
    }, function (err) { status.textContent = err && err.code === "chars" ? err.message : "The PDF could not be made, so nothing was downloaded."; }).then(function () { btn.disabled = false; });
  });
  $("sl-import").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    if (f.size > 1000000) { status.textContent = "That file is too large. Your deck was not changed."; return; }
    var rd = new FileReader();
    rd.onload = function () {
      var d = null, cutN = 0; try { var rawObj = JSON.parse(String(rd.result)); d = S.validate(rawObj); cutN = S.countCut(rawObj); } catch (x) { d = null; }
      if (!d) { status.textContent = "That file is not a usable deck. Your deck was not changed."; return; }
      if (!window.confirm("Replace this deck with the " + d.slides.length + " slides in this file? You can undo it until you reload.")) { status.textContent = "Import cancelled. Your deck was not changed."; return; }
      var prev = cur, prevSel = selId; cur = 0; selId = null; if (!apply(d, "Imported " + d.slides.length + " slides." + (cutN ? " " + cutN + " text " + (cutN === 1 ? "field was" : "fields were") + " shortened to fit the limits." : "") + " You can undo this import.")) { cur = prev; selId = prevSel; status.textContent = H.blocked ? H.note : "That deck is the same as the current one. Nothing changed."; }
    };
    rd.readAsText(f); e.target.value = "";
  });
  document.addEventListener("keydown", function (e) {
    if (show.open) return; // the slide show is read-only: no editing shortcut runs behind it
    if (!(e.ctrlKey || e.metaKey)) return; var k = e.key.toLowerCase(), t = e.target;
    if ((k !== "z" && k !== "y") || !(t === document.body || t.tagName === "svg")) return;
    e.preventDefault(); if (k === "y" || e.shiftKey) { if (H.redo()) render(); } else if (H.undo()) { cur = Math.min(cur, H.deck.slides.length - 1); render(); }
  });
  // Slide show: a read-only modal dialog. It never changes the deck. The browser's dialog traps focus, makes the page behind inert and closes on Escape.
  var show = $("sl-show"), showAt = 0;
  function showDraw() {
    var d = H.deck, th = S.THEMES[d.theme], box = $("sl-show-canvas"); box.textContent = "";
    var svg = el("svg", { viewBox: "0 0 " + S.W + " " + S.H, role: "img", "aria-label": "Slide " + (showAt + 1) + " of " + d.slides.length });
    svg.appendChild(el("desc", {}, S.describe(d, showAt).join("; ") || "Empty slide"));
    svg.appendChild(el("rect", { x: 0, y: 0, width: S.W, height: S.H, fill: th.bg }));
    d.slides[showAt].elements.forEach(function (e) { var g = el("g", {}); drawEl(g, d, e); svg.appendChild(g); });
    box.appendChild(svg);
    $("sl-show-notes").textContent = d.slides[showAt].notes || "No notes for this slide.";
    $("sl-show-count").textContent = "Slide " + (showAt + 1) + " of " + d.slides.length;
    $("sl-show-prev").disabled = showAt === 0; $("sl-show-next").disabled = showAt >= d.slides.length - 1;
    var ae = document.activeElement; if (!ae || ae === document.body || ae.disabled) show.focus(); // a disabled button cannot keep focus or keys
  }
  function showGo(i) { if (i === null || i === showAt) return; showAt = i; showDraw(); }
  var clock = null, t0 = 0;
  function tick() { $("sl-show-timer").textContent = "Time " + S.fmtClock((Date.now() - t0) / 1000); }
  function showNotes(on) {
    $("sl-show-notes-wrap").hidden = !on; show.classList.toggle("with-notes", on); if (clock) { clearInterval(clock); clock = null; }
    if (on) { tick(); clock = setInterval(tick, 1000); }
  }
  $("sl-show-notes-on").addEventListener("change", function () { showNotes(this.checked); });
  $("sl-present").addEventListener("click", function () {
    if (unreadable || H.blocked || typeof show.showModal !== "function") { status.textContent = "The slide show is not available here."; return; }
    showAt = Math.min(cur, H.deck.slides.length - 1); t0 = Date.now(); $("sl-show-notes-on").checked = false; showDraw(); show.showModal(); showNotes(false); $("sl-show-next").focus();
  });
  $("sl-show-prev").addEventListener("click", function () { showGo(S.showNav(showAt, H.deck.slides.length, "ArrowLeft")); });
  $("sl-show-next").addEventListener("click", function () { showGo(S.showNav(showAt, H.deck.slides.length, "ArrowRight")); });
  $("sl-show-exit").addEventListener("click", function () { show.close(); });
  $("sl-show-full").addEventListener("click", function () { if (show.requestFullscreen) show.requestFullscreen().catch(function () { $("sl-show-count").textContent += " (full screen was not allowed)"; }); });
  show.addEventListener("keydown", function (ev) {
    if ((ev.target.tagName === "BUTTON" || ev.target.tagName === "INPUT") && (ev.key === " " || ev.key === "Enter")) return; // a focused button keeps its own action
    var n = S.showNav(showAt, H.deck.slides.length, ev.key); if (n === null) return; ev.preventDefault(); showGo(n);
  });
  show.addEventListener("close", function () { if (clock) { clearInterval(clock); clock = null; } if (document.fullscreenElement) document.exitFullscreen().catch(function () {}); $("sl-present").focus(); });
  show.addEventListener("click", function (ev) { var r = show.getBoundingClientRect(); if (ev.target === show && (ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom)) show.close(); });
  render(); status.textContent = unreadable ? "Your stored deck could not be read, so it was left untouched." : "";
})();
