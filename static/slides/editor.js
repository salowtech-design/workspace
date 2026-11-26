(function () {
  "use strict";
  var root = document.getElementById("slide-root");
  if (!root || !window.SalowSlides) return;
  var S = SalowSlides, KEY = root.getAttribute("data-store-key"), NS = "http://www.w3.org/2000/svg";
  var $ = function (id) { return document.getElementById(id); };
  var status = $("sl-status"), cur = 0, selId = null, editMaster = false;
  function tgt() { return editMaster ? (H.deck.master ? H.deck.master.elements : []) : (H.deck.slides[cur] ? H.deck.slides[cur].elements : []); }
  function eop(name) { var a = [].slice.call(arguments, 1); return editMaster ? S.ops["master_" + name].apply(null, [H.deck].concat(a)) : S.ops[name].apply(null, [H.deck, cur].concat(a)); }
  var loaded; try { loaded = S.loadDeck(localStorage.getItem(KEY)); } catch (e) { loaded = { deck: S.newDeck(), unreadable: false, raw: null }; }
  var initial = loaded.deck, unreadable = loaded.unreadable, rawKept = loaded.raw;
  function lock(on) {
    ["sl-editing"].forEach(function (id) { var n = $(id); if (on) n.setAttribute("inert", ""); else n.removeAttribute("inert"); });
    $("sl-unreadable").hidden = !on; $("sl-present").disabled = on;
  }
  var H = new S.History(initial, { save: function (d) { try { localStorage.setItem(KEY, JSON.stringify(d)); return true; } catch (e) { return false; } } });
  H.blocked = unreadable;
  // Pictures: bytes live in this browser's IndexedDB (one database per person), never in the deck. The deck holds only the SHA-256.
  var IM = window.SalowSlidesImages, store = IM ? new IM.Store("salow.slides.pictures." + KEY) : null, urls = Object.create(null), MAXB = IM ? IM.LIMITS.MAX_DECK_BYTES : 0;
  function b64(u8) { var out = "", step = 0x8000; for (var i = 0; i < u8.length; i += step) out += String.fromCharCode.apply(null, u8.subarray(i, i + step)); return btoa(out); }
  // Returns a data: URL once the picture is loaded and checked (signature, then SHA-256 against the name it is stored under), else null and urls[hash] says why.
  function pictureUrl(hash) {
    var u = urls[hash]; if (u && u.indexOf("data:") === 0) return u; if (u) return null;
    if (!store) { urls[hash] = "missing"; return null; }
    urls[hash] = "loading";
    store.get(hash).then(function (rec) {
      if (!rec) { urls[hash] = "missing"; return; }
      var bytes = new Uint8Array(rec.bytes), sn = IM.sniff(bytes);
      if (!sn.ok) { urls[hash] = "damaged"; return; }
      return IM.sha256(bytes).then(function (h) { urls[hash] = h === hash ? "data:" + sn.type + ";base64," + b64(bytes) : "damaged"; });
    }).catch(function () { urls[hash] = "missing"; }).then(function () { refresh(); });
    return null;
  }
  // A picture that finishes loading redraws the slide, but never in the middle of a drag: that would remove the item being dragged. It runs when the drag ends.
  var pendingRefresh = false;
  function refresh() { if (drag) { pendingRefresh = true; return; } if (H) { render(); if (show.open) showDraw(); } }
  function el(n, at, tx) { var x = document.createElementNS(NS, n); Object.keys(at || {}).forEach(function (k) { x.setAttribute(k, at[k]); }); if (tx !== undefined) x.textContent = tx; return x; }
  // Shape and text of one item, shared by the editor and the slide show.
  function drawEl(g, d, e) {
    if (e.type === "image") {
      var url = pictureUrl(e.img);
      if (url) { g.appendChild(el("image", { href: url, x: e.x, y: e.y, width: e.w, height: e.h, preserveAspectRatio: "xMidYMid meet" })); return; }
      var why = urls[e.img] === "loading" || urls[e.img] === undefined ? "Loading picture" : urls[e.img] === "damaged" ? "Picture damaged" : "Picture missing";
      g.appendChild(el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, fill: "#e8e8e8", stroke: "#808080", "stroke-width": 1 }));
      g.appendChild(el("text", { x: e.x + e.w / 2, y: e.y + e.h / 2, "text-anchor": "middle", "font-size": Math.max(10, Math.min(22, e.h / 3)), fill: "#333333", "font-family": "sans-serif" }, why));
      return;
    }
    var c = S.color(d, e);
    if (e.type === "rect") g.appendChild(el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, fill: c }));
    else if (e.type === "ellipse") g.appendChild(el("ellipse", { cx: e.x + e.w / 2, cy: e.y + e.h / 2, rx: e.w / 2, ry: e.h / 2, fill: c }));
    if (e.text || e.type === "text") {
      var lines = S.wrapLines(e.text, e.size, e.type === "text" ? e.w : e.w - 8, e.b === true), lh = e.size * 1.2, shape = e.type !== "text";
      var top = shape ? e.y + e.h / 2 - (lines.length * lh) / 2 : e.y;
      var al = e.al || (shape ? "center" : "left"), tx = al === "center" ? e.x + e.w / 2 : al === "right" ? e.x + e.w - (shape ? 4 : 0) : e.x + (shape ? 4 : 0);
      var ta = { x: tx, "font-size": e.size, fill: S.textColor(d, e), "text-anchor": al === "center" ? "middle" : al === "right" ? "end" : "start", "font-family": "sans-serif" };
      if (e.b) ta["font-weight"] = "bold"; if (e.i) ta["font-style"] = "italic";
      var t = el("text", ta);
      lines.forEach(function (ln, k) { t.appendChild(el("tspan", { x: tx, y: top + e.size + k * lh }, ln)); });
      g.appendChild(t);
    }
  }
  function sel() { var a = tgt(); for (var i = 0; i < a.length; i++) if (a[i].id === selId) return a[i]; return null; }
  function apply(next, note) { if (!next) return false; if (!H.change(next, note)) return false; cur = Math.min(cur, H.deck.slides.length - 1); render(); return true; }
  // Dragging an item moves it, dragging the blue corner square resizes it. Nothing is stored until the pointer is released, so a drag is one Undo step; Esc or a cancelled pointer drops it.
  var drag = null;
  function endDrag() { var w = wrap(); w.removeEventListener("pointermove", onDragMove); w.removeEventListener("pointerup", onDragUp); w.removeEventListener("pointercancel", onDragCancel); w.removeEventListener("lostpointercapture", onDragCancel); document.removeEventListener("keydown", onDragKey, true); try { if (drag) w.releasePointerCapture(drag.id); } catch (e) { /* already released */ } drag = null; if (pendingRefresh) { pendingRefresh = false; setTimeout(refresh, 0); } }
  function dragDelta(ev) { var m = drag.svg.getScreenCTM(); if (!m || !m.a) return { dx: 0, dy: 0 }; return { dx: (ev.clientX - drag.sx) / m.a, dy: (ev.clientY - drag.sy) / m.d }; }
  function onDragMove(ev) {
    if (!drag || ev.pointerId !== drag.id) return; var d = dragDelta(ev); drag.moved = drag.moved || Math.abs(ev.clientX - drag.sx) + Math.abs(ev.clientY - drag.sy) > 4;
    if (!drag.moved) return;
    if (drag.mode === "move") { drag.g.setAttribute("transform", "translate(" + d.dx + "," + d.dy + ")"); drag.hit.setAttribute("stroke", "#2e6f95"); }
    else { drag.hit.setAttribute("width", Math.max(10, drag.e.w + d.dx)); drag.hit.setAttribute("height", Math.max(10, drag.e.h + d.dy)); drag.hit.setAttribute("stroke", "#2e6f95"); }
  }
  function onDragUp(ev) {
    if (!drag || ev.pointerId !== drag.id) return; var d = dragDelta(ev), dr = drag; endDrag();
    if (!dr.moved) { render(); wrap().querySelector("svg").focus(); return; }
    if (unreadable) { render(); return; }
    var e = dr.e, p = dr.mode === "move" ? { x: Math.round(e.x + d.dx), y: Math.round(e.y + d.dy) } : { w: Math.round(e.w + d.dx), h: Math.round(e.h + d.dy) };
    var ok = apply(eop("updateElement", e.id, p), dr.mode === "move" ? "Item moved." : "Item resized."), now = sel();
    if (ok && now) { var keys = Object.keys(p).filter(function (k) { return now[k] !== p[k]; }); if (keys.length) status.textContent = "Adjusted " + keys.join(" and ").toUpperCase() + " so the item fits the slide and limits. " + H.note; }
    else if (!ok) render();
    var s2 = wrap().querySelector("svg"); if (s2) s2.focus();
  }
  function onDragCancel(ev) { if (!drag || ev.pointerId !== drag.id) return; endDrag(); render(); status.textContent = "Drag cancelled. Nothing changed."; }
  function onDragKey(ev) { if (drag && ev.key !== "Escape") { ev.preventDefault(); ev.stopPropagation(); return; }  // no other edit while a drag is open, so it cannot be overwritten by the drop
  if (drag && ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); endDrag(); render(); status.textContent = "Drag cancelled. Nothing changed."; } }
  function wrap() { return $("sl-canvas-wrap"); }
  function beginDrag(ev, e, mode) {
    if (drag) endDrag();
    selId = e.id; var prevSvg = wrap().querySelector("svg");
    if (unreadable || (ev.pointerType === "mouse" && ev.button !== 0)) { render(); wrap().querySelector("svg").focus(); return; }
    render();
    var svg = wrap().querySelector("svg"), hit = svg.querySelector('rect[data-id="' + e.id + '"]');
    if (!hit) return;
    drag = { id: ev.pointerId, sx: ev.clientX, sy: ev.clientY, e: { id: e.id, x: e.x, y: e.y, w: e.w, h: e.h }, mode: mode, svg: svg, g: hit.parentNode, hit: hit, moved: false };
    // The canvas container is never replaced by render(), so it owns the pointer for the whole gesture (mouse, touch and pen alike).
    var w = wrap(); try { w.setPointerCapture(ev.pointerId); } catch (e) { /* the pointer is gone: the gesture just does not start */ endDrag(); return; }
    w.addEventListener("pointermove", onDragMove); w.addEventListener("pointerup", onDragUp); w.addEventListener("pointercancel", onDragCancel); w.addEventListener("lostpointercapture", onDragCancel); document.addEventListener("keydown", onDragKey, true);
    svg.focus();
  }
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
    svg.appendChild(el("desc", {}, (editMaster ? "Master items: " + (S.describeEls(tgt()).join("; ") || "none") : (S.masterOf(d, cur).length ? "Master items: " + S.describeEls(S.masterOf(d, cur)).join("; ") + ". Slide items: " : "") + (S.describe(d, cur).join("; ") || "Empty slide"))));
    svg.appendChild(el("rect", { x: 0, y: 0, width: S.W, height: S.H, fill: th.bg }));
    if (!editMaster) S.masterOf(d, cur).forEach(function (e) { var g = el("g", {}); drawEl(g, d, e); svg.appendChild(g); });
    tgt().forEach(function (e) {
      var g = el("g", {}); drawEl(g, d, e);
      var hit = el("rect", { x: e.x, y: e.y, width: e.w, height: e.h, fill: "transparent", stroke: e.id === selId ? "#2e6f95" : "none", "stroke-width": 3, "stroke-dasharray": "8 4", "data-id": e.id });
      hit.addEventListener("pointerdown", function (ev) { ev.preventDefault(); beginDrag(ev, e, "move"); });
      g.appendChild(hit); svg.appendChild(g);
    });
    svg.addEventListener("pointerdown", function (ev) { if (ev.target === svg || ev.target.tagName === "rect" && !ev.target.getAttribute("data-id")) { selId = null; render(); } });
    svg.addEventListener("keydown", function (ev) {
      var e = sel(); if (!e) return; var dx = 0, dy = 0;
      if (ev.key === "ArrowLeft") dx = -10; else if (ev.key === "ArrowRight") dx = 10; else if (ev.key === "ArrowUp") dy = -10; else if (ev.key === "ArrowDown") dy = 10;
      else if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); apply(eop("deleteElement", e.id), "Item deleted."); return; } else return;
      ev.preventDefault(); apply(eop("updateElement", e.id, ev.shiftKey ? { w: e.w + dx, h: e.h + dy } : { x: e.x + dx, y: e.y + dy }), ev.shiftKey ? "Item resized." : undefined);
      var s2 = $("sl-canvas-wrap").querySelector("svg"); if (s2) s2.focus();
    });
    wrap.appendChild(svg);
    var se = sel(); if (se && !unreadable) {
      var hs = Math.max(24, 28 / Math.max(0.05, svg.getBoundingClientRect().width / S.W));  // about 28 screen pixels, whatever the canvas width
      var hd = el("rect", { x: se.x + se.w - hs / 2, y: se.y + se.h - hs / 2, width: hs, height: hs, fill: "#2e6f95", stroke: "#fff", "stroke-width": 2, "data-handle": se.id, style: "cursor:nwse-resize" });
      hd.appendChild(el("title", {}, "Drag to resize this item"));
      hd.addEventListener("pointerdown", function (ev) { ev.preventDefault(); ev.stopPropagation(); beginDrag(ev, se, "resize"); });
      svg.appendChild(hd);
    }
    var il = $("sl-items"); il.textContent = "";
    S.describeEls(tgt()).forEach(function (label, k) {
      var id = tgt()[k].id, li = document.createElement("li"), b = document.createElement("button");
      b.type = "button"; b.textContent = label; b.setAttribute("aria-pressed", id === selId ? "true" : "false");
      b.addEventListener("click", function () { selId = id; render(); var again = $("sl-items").querySelectorAll("button")[k]; if (again) again.focus(); });
      li.appendChild(b); il.appendChild(li);
    });
    $("sl-items-empty").hidden = tgt().length > 0;
    var e = sel(), props = $("sl-props"); props.disabled = !e; $("sl-del-el").disabled = !e;
    var isPic = !!(e && e.type === "image"); ["sl-size", "sl-fill", "sl-bold", "sl-italic", "sl-align"].forEach(function (id) { $(id).disabled = isPic; });
    $("sl-export-warn").hidden = !deckHashes(d).length;
    $("sl-text-label").textContent = isPic ? "Description (for screen readers, not drawn)" : "Text";
    var pos = e ? tgt().indexOf(e) : -1; $("sl-back").disabled = pos <= 0; $("sl-fwd").disabled = pos < 0 || pos >= tgt().length - 1;
    ["text", "size", "x", "y", "w", "h", "fill"].forEach(function (f) { var inp = $("sl-" + f); if (document.activeElement !== inp) inp.value = e ? e[f] : ""; });
    $("sl-bold").checked = !!(e && e.b); $("sl-italic").checked = !!(e && e.i); $("sl-align").value = e && e.al ? e.al : "default";
    var nt = $("sl-notes"); if (document.activeElement !== nt) nt.value = d.slides[cur].notes || "";
    $("sl-theme").value = d.theme; $("sl-undo").disabled = !H.past.length; $("sl-redo").disabled = !H.future.length;
    $("sl-up").disabled = cur === 0; $("sl-down").disabled = cur >= d.slides.length - 1; $("sl-del").disabled = d.slides.length <= 1; $("sl-dup").disabled = d.slides.length >= S.MAX_SLIDES;
    $("sl-trans").value = d.slides[cur] && d.slides[cur].transition === "fade" ? "fade" : "none"; $("sl-trans").disabled = editMaster;
    $("sl-master").textContent = editMaster ? "Back to slides" : "Edit master"; $("sl-master").setAttribute("aria-pressed", editMaster ? "true" : "false");
    $("sl-master-on").checked = !(d.slides[cur] && d.slides[cur].hideMaster === true); $("sl-master-on").disabled = editMaster;
    $("sl-master-state").textContent = editMaster ? "Editing the master: its items appear on every slide that shows the master (" + tgt().length + " of " + S.MAX_MASTER + " items)." : (d.master ? "This deck has a master with " + d.master.elements.length + " item" + (d.master.elements.length === 1 ? "" : "s") + ". Its items are drawn under this slide's items" + (d.slides[cur].hideMaster ? ", but this slide hides them." : ".") : "This deck has no master.");
    ["sl-new", "sl-dup", "sl-del", "sl-up", "sl-down", "sl-layout"].forEach(function (id) { if (editMaster) $(id).disabled = true; else if (id === "sl-layout" || id === "sl-new") $(id).disabled = false; });
    $("sl-notes").disabled = editMaster;
    $("sl-notes-label").textContent = editMaster ? "Speaker notes (not available while editing the master; leave the master to edit this slide's notes)" : "Speaker notes for this slide";
    var mu = editMaster ? [] : S.masterOf(d, cur); $("sl-master-under").textContent = mu.length ? "Master items drawn under this slide (not selectable here): " + S.describeEls(mu).join("; ") : "";
    status.textContent = H.note;
    var cs = sel(); if (cs && cs.text && cs.type !== "image") { var ln = S.wrapLines(cs.text, cs.size, cs.type === "text" ? cs.w : cs.w - 8, cs.b === true); if (S.textHeight(ln, cs.size) > cs.h) status.textContent += " The text is estimated to run past the item's edges."; }
  }
  $("sl-trans").addEventListener("change", function () { var n = S.ops.setTransition(H.deck, cur, this.value); if (!n || !apply(n, this.value === "fade" ? "This slide now fades in during Present." : "This slide has no transition.")) render(); });
  $("sl-master").addEventListener("click", function () { editMaster = !editMaster; selId = null; render(); status.textContent = editMaster ? "Editing the master. Items you add here appear on every slide that shows the master." : "Back to editing slides."; });
  $("sl-master-on").addEventListener("change", function () { var n = S.ops.setHideMaster(H.deck, cur, !this.checked); if (!n || !apply(n, this.checked ? "This slide now shows the master." : "This slide now hides the master.")) render(); });
  $("sl-notes").addEventListener("change", function () { apply(S.ops.setNotes(H.deck, cur, this.value), "Notes saved on this device."); });
  var fill = $("sl-fill"); S.FILLS.forEach(function (f) { var o = document.createElement("option"); o.value = f; o.textContent = f === "theme" ? "Theme color" : f; fill.appendChild(o); });
  ["text", "size", "x", "y", "w", "h"].forEach(function (f) {
    $("sl-" + f).addEventListener("change", function () {
      var e = sel(); if (!e) return; var p = {}; var asked = this.value; p[f] = asked;
      var ok = apply(eop("updateElement", e.id, p)), now = sel();
      if (now && f !== "text" && String(now[f]) !== String(Math.round(Number(asked)))) { status.textContent = "Adjusted " + f.toUpperCase() + " to " + now[f] + " so the item fits the slide and limits. " + H.note; }
      else if (now && f !== "text" && !isFinite(Number(asked))) { status.textContent = "Enter a number for " + f.toUpperCase() + ". Kept " + now[f] + "."; this.value = now[f]; }
    });
  });
  $("sl-bold").addEventListener("change", function () { var e = sel(); if (e) apply(eop("updateElement", e.id, { b: this.checked }), this.checked ? "Bold on." : "Bold off."); });
  $("sl-italic").addEventListener("change", function () { var e = sel(); if (e) apply(eop("updateElement", e.id, { i: this.checked }), this.checked ? "Italic on." : "Italic off."); });
  $("sl-align").addEventListener("change", function () { var e = sel(); if (e) apply(eop("updateElement", e.id, { al: this.value }), "Align set to " + this.value + "."); });
  fill.addEventListener("change", function () { var e = sel(); if (e) apply(eop("updateElement", e.id, { fill: fill.value })); });
  ["text", "rect", "ellipse"].forEach(function (t) { $("sl-add-" + t).addEventListener("click", function () { var r = eop("addElement", t); if (!r) { status.textContent = editMaster ? "The master holds up to " + S.MAX_MASTER + " items." : "A slide holds up to " + S.MAX_EL + " items."; return; } selId = r.id; apply(r.deck); }); });
  $("sl-add-img").addEventListener("click", function () {
    if (unreadable) return;
    if (editMaster) { status.textContent = "Pictures cannot go on the master. Leave the master to add one to a slide."; return; }
    if (!store || !IM) { status.textContent = "This browser cannot keep pictures here, so none was added."; return; }
    $("sl-img-file").click();
  });
  // Adds are queued one at a time, and the byte total is read again from the current deck right before the deck is changed, so two quick adds can never both pass the
  // cap on a stale total. The picture store is add-only: if a later step fails, the file stays stored (nothing refers to it) and the message says the deck was not changed.
  var addQ = Promise.resolve();
  function deckPicHashes() { var hs = []; H.deck.slides.forEach(function (sl) { sl.elements.forEach(function (x) { if (x.type === "image") hs.push(x.img); }); }); return hs; }
  function addPicture(f) {
    return f.arrayBuffer().then(function (buf) { return IM.qualify(new Uint8Array(buf)); }).then(function (rec) {
      function commit(tries) {
        var snap = H.deck, at = cur;
        if (!S.ops.addImage(snap, at, rec.hash, rec.w, rec.h, "")) throw new Error("This slide or deck is full (30 items per slide, 20 pictures per deck). Nothing was added to the deck.");
        var hs = deckPicHashes();
        return store.sizes(hs).then(function (sz) {
          if (hs.indexOf(rec.hash) < 0 && sz.pixels + rec.w * rec.h > IM.LIMITS.MAX_DECK_PIXELS) throw new Error("Pictures in this deck would pass " + IM.LIMITS.MAX_DECK_PIXELS / 1e6 + " million pixels in total, the most the PDF maker can handle. Nothing was added to the deck.");
          if (hs.indexOf(rec.hash) < 0 && sz.total + rec.bytes.length > MAXB) throw new Error("Pictures in this deck would pass " + MAXB / 1e6 + " MB. Nothing was added to the deck.");
          return store.put(rec);
        }).then(function () {
          if (H.deck !== snap || cur !== at) { if (tries > 3) throw new Error("The deck kept changing while the picture was being checked. Nothing was added to the deck."); return commit(tries + 1); }
          urls[rec.hash] = "data:" + rec.type + ";base64," + b64(rec.bytes);
          var r = S.ops.addImage(H.deck, cur, rec.hash, rec.w, rec.h, "");
          selId = r.id;
          if (!apply(r.deck, "Picture added (" + rec.w + " by " + rec.h + " pixels). Describe it in the Description box for screen readers.")) { selId = null; render(); throw new Error("The deck was not changed. The picture file is kept in this browser but nothing refers to it."); }
        });
      }
      return commit(0);
    });
  }
  $("sl-img-file").addEventListener("change", function (ev) {
    var f = ev.target.files[0]; ev.target.value = ""; if (!f || unreadable || editMaster) return;
    if (f.size > IM.LIMITS.MAX_BYTES) { status.textContent = "That file is " + (f.size / 1e6).toFixed(1) + " MB. The limit is " + IM.LIMITS.MAX_BYTES / 1e6 + " MB per picture. Nothing was added."; return; }
    status.textContent = "Checking the picture on this device...";
    addQ = addQ.then(function () { return addPicture(f); }).catch(function (err) { status.textContent = err && err.message ? err.message : "The picture could not be added."; });
  });
  [["sl-back", -1, "Item sent backward."], ["sl-fwd", 1, "Item brought forward."]].forEach(function (c) {
    $(c[0]).addEventListener("click", function () { var e = sel(); if (e && apply(eop("moveElement", e.id, c[1]), c[2])) { var b = $(c[0]); if (b.disabled) $("sl-del-el").focus(); else b.focus(); } });
  });
  $("sl-del-el").addEventListener("click", function () { var e = sel(); if (e) apply(eop("deleteElement", e.id), "Item deleted."); });
  $("sl-new").addEventListener("click", function () { var n = S.ops.addSlide(H.deck, cur, $("sl-layout").value); if (!n) { status.textContent = "A deck holds up to " + S.MAX_SLIDES + " slides."; return; } var prev = cur, prevSel = selId; cur++; selId = null; if (!apply(n)) { cur = prev; selId = prevSel; } });
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
    if (!window.confirm("Clear the whole deck? It becomes one empty slide and the master is removed. You can undo until you reload this page; after a reload the cleared deck cannot be brought back.")) return;
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
  function deckHashes(d) { var hs = []; d.slides.forEach(function (sl) { sl.elements.forEach(function (x) { if (x.type === "image" && hs.indexOf(x.img) < 0) hs.push(x.img); }); }); return hs; }
  // Loads and re-checks every picture a deck names: { hash: { type, bytes } } for the good ones, plus lists of the missing and damaged ones.
  function loadPictures(d) {
    var hs = deckHashes(d), out = { pics: {}, missing: [], damaged: [] };
    if (!hs.length) return Promise.resolve(out);
    if (!store) { out.missing = hs; return Promise.resolve(out); }
    return Promise.all(hs.map(function (h) {
      return store.get(h).then(function (rec) {
        if (!rec) { out.missing.push(h); return; }
        var bytes = new Uint8Array(rec.bytes), sn = IM.sniff(bytes); if (!sn.ok) { out.damaged.push(h); return; }
        return IM.sha256(bytes).then(function (x) { if (x === h) out.pics[h] = { type: sn.type, bytes: bytes, w: sn.w, h: sn.h }; else out.damaged.push(h); });
      }, function () { out.missing.push(h); });
    })).then(function () { return out; });
  }
  function download(name, text) {
    var u = URL.createObjectURL(new Blob([text], { type: "application/json" })), l = document.createElement("a");
    l.href = u; l.download = name; document.body.appendChild(l); l.click(); l.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
  }
  $("sl-export-pics").addEventListener("click", function () {
    var d = H.deck; if (!deckHashes(d).length) { status.textContent = "This deck has no pictures. Use Export (.json), which is the same file."; return; }
    status.textContent = "Collecting the pictures on this device...";
    loadPictures(d).then(function (r) {
      if (r.missing.length || r.damaged.length) { status.textContent = "No file was made: " + (r.missing.length ? r.missing.length + " picture(s) are missing from this browser" : "") + (r.missing.length && r.damaged.length ? " and " : "") + (r.damaged.length ? r.damaged.length + " are damaged" : "") + ". Export (.json) would hold references only."; return; }
      var list = Object.keys(r.pics).map(function (h) { return { hash: h, type: r.pics[h].type, data: b64(r.pics[h].bytes) }; });
      download("deck-with-pictures.json", JSON.stringify({ format: "salow-slides-with-pictures", version: 1, deck: d, pictures: list }));
      status.textContent = "Downloaded deck-with-pictures.json with " + list.length + " picture file(s) inside. Anyone with this file can see the pictures.";
    }).catch(function () { status.textContent = "The pictures could not be collected, so no file was made."; });
  });
  $("sl-export").addEventListener("click", function () {
    if (deckHashes(H.deck).length && !window.confirm("This deck has pictures. Export (.json) saves only references to them, not the pictures, so opening the file elsewhere shows 'Picture missing' boxes. Use 'Export with pictures' to include them. Download the references-only file anyway?")) { status.textContent = "Export cancelled. Nothing was downloaded."; return; }
    if (deckHashes(H.deck).length) status.textContent = "Downloaded deck.json WITHOUT the picture files: references only.";
    download("deck.json", JSON.stringify(H.deck));
  });
  $("sl-pdf").addEventListener("click", function () {
    var btn = $("sl-pdf"), deck = H.deck;
    if (!window.PDFLib || !window.SalowSlidesPdf) { status.textContent = "The PDF maker did not load, so no PDF was made."; return; }
    btn.disabled = true; status.textContent = "Making the PDF on this device...";
    var info = {}, pr;
    loadPictures(deck).then(function (r) { pr = r; var all = Object.assign({}, r.pics); info.maxPixels = IM ? IM.LIMITS.MAX_DECK_PIXELS : 0; return window.SalowSlidesPdf.build(window.PDFLib, S, deck, all, info); }).then(function (bytes) {
      var u = null;
      try {
        u = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
        var l = document.createElement("a"); l.href = u; l.download = "slides.pdf"; document.body.appendChild(l); l.click(); l.remove();
      } catch (e) { if (u) URL.revokeObjectURL(u); throw e; }
      setTimeout(function () { URL.revokeObjectURL(u); }, 4000);
      status.textContent = "Downloaded slides.pdf: " + deck.slides.length + " pages, one per slide. Speaker notes are not in the PDF. Line breaks follow the screen estimate, so text may sit a little differently than in the editor." + (info.missing.length || info.failed.length || pr.damaged.length ? " WARNING: " + (info.missing.length + pr.damaged.length) + " picture(s) were missing or damaged and are drawn as grey boxes" + (info.failed.length ? "; " + info.failed.length + " could not be read by the PDF maker and are also grey boxes" : "") + "." : "");
    }).catch(function (err) {
      status.textContent = err && (err.code === "chars" || err.code === "pixels") ? err.message : "The PDF could not be made or saved, so nothing was downloaded.";
    }).then(function () { btn.disabled = false; }, function () { btn.disabled = false; });
  });
  var B64 = /^[A-Za-z0-9+/]+={0,2}$/, WRAP = "salow-slides-with-pictures";
  // A file made by "Export with pictures": every picture is checked again exactly as if it had been added by hand, its fingerprint must equal the name it is filed under,
  // and nothing is replaced until all of them pass and the person agrees.
  function importWithPictures(raw, finish) {
    var d = S.validate(raw && raw.deck), list = raw && raw.pictures;
    if (raw.version !== 1 || !d || !Array.isArray(list) || list.length > S.MAX_EL * 20 || !store) { status.textContent = "That file is not a usable deck with pictures. Your deck was not changed."; return; }
    var seen = Object.create(null), total = 0, jobs = [], wanted = deckHashes(d), ignored = 0;
    for (var k = 0; k < list.length; k++) {
      var p = list[k];
      if (!p || typeof p.hash !== "string" || typeof p.data !== "string" || seen[p.hash] || p.data.length > Math.ceil(IM.LIMITS.MAX_BYTES * 4 / 3) + 8 || p.data.length % 4 || !B64.test(p.data)) { status.textContent = "That file has a damaged picture entry. Your deck was not changed."; return; }
      seen[p.hash] = true;
      if (wanted.indexOf(p.hash) < 0) { ignored++; continue; }  // a picture the deck does not use is neither decoded nor stored
      var bin; try { bin = atob(p.data); } catch (x) { status.textContent = "That file has a damaged picture entry. Your deck was not changed."; return; }
      var u8 = new Uint8Array(bin.length); for (var q = 0; q < bin.length; q++) u8[q] = bin.charCodeAt(q);
      total += u8.length; jobs.push({ hash: p.hash, bytes: u8 });
    }
    if (total > MAXB) { status.textContent = "The pictures in that file pass " + MAXB / 1e6 + " MB. Your deck was not changed."; return; }
    Promise.all(jobs.map(function (j) { return IM.qualify(j.bytes).then(function (rec) { if (rec.hash !== j.hash) throw new Error("A picture in that file does not match its fingerprint."); return rec; }); })).then(function (recs) {
      var have = Object.create(null); jobs.forEach(function (j) { have[j.hash] = true; }); var need = wanted.filter(function (h) { return !have[h]; });
      return store.sizes(need).then(function (sz) {
        var px = sz.pixels; recs.forEach(function (rc) { px += rc.w * rc.h; });
        if (px > IM.LIMITS.MAX_DECK_PIXELS) throw new Error("The pictures that deck uses add up to " + Math.round(px / 1e6) + " million pixels. The most this page keeps in one deck is " + IM.LIMITS.MAX_DECK_PIXELS / 1e6 + " million.");
        return { recs: recs, absent: sz.missing };
      });
    }).then(function (r) {
      var msg = "Replace this deck with the " + d.slides.length + " slides and " + r.recs.length + " picture file(s) in this file? The pictures are kept in this browser." + (ignored ? " " + ignored + " picture file(s) in the file that no slide uses are ignored." : "") + " You can undo the deck change until you reload." + (r.absent.length ? " " + r.absent.length + " picture(s) it uses are not in the file or this browser and will show as 'Picture missing'." : "");
      if (!window.confirm(msg)) { status.textContent = "Import cancelled. Your deck was not changed."; return; }
      return Promise.all(r.recs.map(function (rec) { return store.put(rec).then(function () { urls[rec.hash] = "data:" + rec.type + ";base64," + b64(rec.bytes); }); })).then(function () { finish(d, r.absent.length); });
    }).catch(function (err) { status.textContent = (err && err.message ? err.message : "The pictures could not be stored.") + " Your deck was not changed. Picture files that were already checked may have been kept in this browser; nothing refers to them."; });
  }
  $("sl-import").addEventListener("change", function (e) {
    var f = e.target.files[0]; if (!f) return;
    if (f.size > (IM ? IM.LIMITS.MAX_IMPORT_FILE : 1000000)) { status.textContent = "That file is too large. Your deck was not changed."; return; }
    var rd = new FileReader();
    rd.onload = function () {
      var rawObj = null, d = null, cutN = 0; try { rawObj = JSON.parse(String(rd.result)); } catch (x) { rawObj = null; }
      function done(deck, absent) {
        var prev = cur, prevSel = selId; cur = 0; selId = null;
        if (!apply(deck, "Imported " + deck.slides.length + " slides." + (absent ? " " + absent + " picture(s) are missing and show as grey boxes." : "") + " You can undo this import.")) { cur = prev; selId = prevSel; status.textContent = H.blocked ? H.note : "That deck is the same as the current one, so the deck did not change." + (deckHashes(deck).length ? " Its pictures are kept in this browser." : ""); refresh(); }
      }
      if (rawObj && typeof rawObj === "object" && rawObj.format === WRAP) { importWithPictures(rawObj, done); return; }
      if (f.size > (IM ? IM.LIMITS.MAX_IMPORT_DECK : 1000000)) { status.textContent = "That file is too large. Your deck was not changed."; return; }
      try { d = S.validate(rawObj); cutN = S.countCut(rawObj); } catch (x) { d = null; }
      if (!d) { status.textContent = "That file is not a usable deck. Your deck was not changed."; return; }
      var refs = deckHashes(d).length;
      if (!window.confirm("Replace this deck with the " + d.slides.length + " slides in this file? You can undo it until you reload." + (refs ? " It names " + refs + " picture(s) by reference only; any that this browser does not have will show as 'Picture missing'." : ""))) { status.textContent = "Import cancelled. Your deck was not changed."; return; }
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
  function reduced() { try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return true; } }
  function showDraw(animate) {
    var d = H.deck, th = S.THEMES[d.theme], box = $("sl-show-canvas"); box.textContent = "";
    var svg = el("svg", { viewBox: "0 0 " + S.W + " " + S.H, role: "img", "aria-label": "Slide " + (showAt + 1) + " of " + d.slides.length });
    svg.appendChild(el("desc", {}, S.describe(d, showAt).join("; ") || "Empty slide"));
    svg.appendChild(el("rect", { x: 0, y: 0, width: S.W, height: S.H, fill: th.bg }));
    S.masterOf(d, showAt).concat(d.slides[showAt].elements).forEach(function (e) { var g = el("g", {}); drawEl(g, d, e); svg.appendChild(g); });
    if (animate && H.deck.slides[showAt].transition === "fade" && !reduced()) svg.setAttribute("class", "sl-fade");
    box.appendChild(svg);
    $("sl-show-notes").textContent = d.slides[showAt].notes || "No notes for this slide.";
    $("sl-show-count").textContent = "Slide " + (showAt + 1) + " of " + d.slides.length;
    $("sl-show-prev").disabled = showAt === 0; $("sl-show-next").disabled = showAt >= d.slides.length - 1;
    var ae = document.activeElement; if (!ae || ae === document.body || ae.disabled) show.focus(); // a disabled button cannot keep focus or keys
  }
  function showGo(i) { if (i === null || i === showAt) return; showAt = i; showDraw(true); }
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
