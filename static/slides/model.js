/* Salow Slides model: pure logic, no DOM, so node can test it. A deck lives on this device.
 * Colors are chosen from fixed lists, so nothing from a file or the user reaches a style string
 * unchecked. Elements without their own color inherit the theme, so a theme change restyles them. */
(function (root) {
  "use strict";
  var W = 960, H = 540, MAX_SLIDES = 50, MAX_EL = 30, MAX_MASTER = 10, MAX_TEXT = 1000, MAX_NOTES = 2000, HIST = 100;
  var THEMES = { light: { bg: "#ffffff", fg: "#222222", accent: "#7a2e22" }, dark: { bg: "#1f2430", fg: "#f2f2f2", accent: "#e0a458" }, warm: { bg: "#faf5ec", fg: "#3b2a20", accent: "#a4472f" } };
  var FILLS = ["theme", "#7a2e22", "#2e6f95", "#3b7d4f", "#c28b1d", "#6b6b6b"];
  // Slide background colours: a fixed list, never free input. A colour may be used only while every text item drawn on that slide
  // (the slide's own and the master's, unless hidden) keeps a contrast of at least MIN_CONTRAST against it. Text drawn on a shape sits on the shape, so it is not counted.
  var BGS = [["#ffffff", "White"], ["#f4efe6", "Cream"], ["#e6eef7", "Pale blue"], ["#e7f3e9", "Pale green"], ["#fdf1d6", "Pale yellow"], ["#f6e3df", "Pale rose"], ["#1f2430", "Night"], ["#233a4d", "Deep blue"], ["#2e2a24", "Dark brown"], ["#3a2230", "Plum"]], MIN_CONTRAST = 4.5;
  var BG_HEX = BGS.map(function (b) { return b[0]; });
  var TYPES = ["text", "rect", "ellipse", "image"], HASH_RE = /^[0-9a-f]{64}$/, MAX_IMAGES = 20;
  var has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  var ID_RE = /^[A-Za-z0-9_-]{1,40}$/, MAX_SNAPSHOT_BYTES = 5000000;
  var seq = 0;
  function uid() { return "e" + Date.now().toString(36) + (seq++).toString(36); }
  // cut to n code points, so a character made of two UTF-16 halves is never split
  function cut(t, n) { return t.length <= n ? t : Array.from(t).slice(0, n).join(""); }
  function clamp(v, lo, hi) { v = Math.round(Number(v)); if (!isFinite(v)) v = lo; return Math.min(hi, Math.max(lo, v)); }
  function newEl(type) {
    var e = { id: uid(), type: type, x: 80, y: 80, w: type === "text" ? 400 : 200, h: type === "text" ? 80 : 120, fill: "theme", text: "", size: 32 };
    if (type === "text") e.text = "Text";
    return e;
  }
  // Layouts only place ordinary items on a new slide. They are not a master: later edits to a layout change nothing already made.
  var LAYOUTS = {
    blank: { label: "Blank", items: [] },
    title: { label: "Title", items: [{ text: "Title", x: 80, y: 170, w: 800, h: 100, size: 54 }, { text: "Subtitle", x: 80, y: 290, w: 800, h: 60, size: 28 }] },
    content: { label: "Title and content", items: [{ text: "Title", x: 60, y: 40, w: 840, h: 80, size: 44 }, { text: "Content", x: 60, y: 150, w: 840, h: 330, size: 28 }] }
  };
  function newSlide(layout) {
    var L = has(LAYOUTS, layout) ? LAYOUTS[layout] : LAYOUTS.blank;
    return { id: uid(), elements: L.items.map(function (it) { var e = newEl("text"); ["text", "x", "y", "w", "h", "size"].forEach(function (k) { e[k] = it[k]; }); return e; }), notes: "" };
  }
  function newDeck() { return { v: 1, theme: "light", slides: [newSlide()] }; }
  var ALIGNS = ["left", "center", "right"], FX = ["appear", "fade"];
  function isImg(e) { return e.type === "image"; }
  function isU(e) { return e.u === true; }
  function styled(e) { return e.b === true || e.i === true || has(e, "al"); }
  function fixEl(e, inMaster) {
    if (!e || typeof e !== "object" || TYPES.indexOf(e.type) < 0 || typeof e.id !== "string" || !ID_RE.test(e.id)) return null;
    var text = typeof e.text === "string" ? cut(e.text, MAX_TEXT) : "";
    var w = clamp(e.w, 10, W), h = clamp(e.h, 10, H);
    var o = { id: e.id, type: e.type, x: clamp(e.x, 0, W - w), y: clamp(e.y, 0, H - h), w: w, h: h, fill: FILLS.indexOf(e.fill) >= 0 ? e.fill : "theme", text: text, size: clamp(e.size, 8, 120) };
    // An image item holds only a reference: the SHA-256 (hex) of the picture bytes, which stay in this browser. Its text is the description.
    if (e.type === "image") {
      if (inMaster || typeof e.img !== "string" || !HASH_RE.test(e.img)) return null;
      o.img = e.img; o.fill = "theme"; o.size = 32; return o;
    }
    // Text style. Only set fields are kept, so an unstyled item serialises exactly as before and an unstyled deck stays version 1 to 3.
    if (e.b === true) o.b = true;
    if (e.i === true) o.i = true;
    if (e.u === true) o.u = true;  // underline (deck version 12, see validate)
    if (ALIGNS.indexOf(e.al) >= 0) o.al = e.al;  // unset means left for text items and centre for shapes
    return o;
  }
  // ---- Layout inheritance (version 9). A slide linked to a layout has lay = the layout key; its placeholder items carry ph = the slot number. The item still
  // stores its own x, y, w, h and size, so drawing, Present and the PDF read it exactly as before. Editing a layout slot (deck.lays) moves only the linked item
  // fields that still equal the old layout value; a field changed on the slide is a local override and is kept. Linking is always explicit. ----
  var LAY_FIELDS = ["x", "y", "w", "h", "size"], LAY_LIM = { x: [0, W], y: [0, H], w: [10, W], h: [10, H], size: [8, 120] };
  function layKeys() { return Object.keys(LAYOUTS).filter(function (k) { return LAYOUTS[k].items.length; }); }
  function layOk(v) { return typeof v === "number" && v === Math.floor(v); }
  function slotOf(d, key, i) {
    var b = LAYOUTS[key].items[i], o = {}, ov = d && d.lays && d.lays[key] && d.lays[key][i] || {};
    LAY_FIELDS.forEach(function (f) { o[f] = has(ov, f) ? ov[f] : b[f]; });
    return o;
  }
  // returns a clean deck or null; unknown fields are dropped and numbers are clamped
  function validate(d) {
    if (!d || typeof d !== "object" || Array.isArray(d) || (has(d, "v") && [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].indexOf(d.v) < 0) || !Array.isArray(d.slides) || !d.slides.length || d.slides.length > MAX_SLIDES) return null;
    var TRANS = ["none", "fade"], ids = Object.create(null), out = { v: 1, theme: has(THEMES, d.theme) ? d.theme : "light", slides: [] }, masterEls = [], anyHide = false, anyTrans = false, anyTspeed = false, anySpd = false, anyStyle = false, nImg = 0, anyAnim = false, anyAfter = false, anyBg = false, anyLay = false, outLays = null;
    if (has(d, "lays")) {
      var L0 = d.lays; if (!L0 || typeof L0 !== "object" || Array.isArray(L0)) return null; outLays = {};
      var lk = Object.keys(L0); for (var a1 = 0; a1 < lk.length; a1++) {
        var key = lk[a1], arr = L0[key]; if (layKeys().indexOf(key) < 0 || !Array.isArray(arr) || arr.length > LAYOUTS[key].items.length) return null;
        var na = []; for (var a2 = 0; a2 < arr.length; a2++) { var it = arr[a2]; if (!it || typeof it !== "object" || Array.isArray(it)) return null; var no = {}; for (var a3 = 0; a3 < LAY_FIELDS.length; a3++) { var f = LAY_FIELDS[a3]; if (!has(it, f)) continue; if (!layOk(it[f]) || it[f] < LAY_LIM[f][0] || it[f] > LAY_LIM[f][1]) return null; no[f] = it[f]; } na.push(no); }
        for (var a4 = 0; a4 < na.length; a4++) { var rs = {}; LAY_FIELDS.forEach(function (f) { rs[f] = has(na[a4], f) ? na[a4][f] : LAYOUTS[key].items[a4][f]; }); if (rs.x + rs.w > W || rs.y + rs.h > H) return null; }
        if (na.some(function (o) { return Object.keys(o).length; })) outLays[key] = na;
      }
      if (!Object.keys(outLays).length) outLays = null; else anyLay = true;
    }
    if (has(d, "master")) {
      var m = d.master; if (!m || typeof m !== "object" || Array.isArray(m) || !Array.isArray(m.elements) || m.elements.length > MAX_MASTER) return null;
      for (var k = 0; k < m.elements.length; k++) { if (m.elements[k] && typeof m.elements[k] === "object" && has(m.elements[k], "ph")) return null; var me = fixEl(m.elements[k], true); if (!me || ids[me.id]) return null; ids[me.id] = true; masterEls.push(me); if (styled(me)) anyStyle = true; }
    }
    for (var i = 0; i < d.slides.length; i++) {
      var s = d.slides[i];
      if (!s || typeof s !== "object" || typeof s.id !== "string" || !ID_RE.test(s.id) || !Array.isArray(s.elements) || s.elements.length > MAX_EL || ids[s.id]) return null;
      ids[s.id] = true; var els = [], phSeen = Object.create(null), wantLay = null;
      if (has(s, "lay")) { if (typeof s.lay !== "string" || layKeys().indexOf(s.lay) < 0) return null; wantLay = s.lay; anyLay = true; }
      for (var j = 0; j < s.elements.length; j++) { var e = fixEl(s.elements[j]); if (!e || ids[e.id]) return null; ids[e.id] = true; els.push(e);
        if (has(s.elements[j], "ph")) { var ph = s.elements[j].ph; if (!wantLay || e.type !== "text" || !layOk(ph) || ph < 0 || ph >= LAYOUTS[wantLay].items.length || phSeen[ph]) return null; phSeen[ph] = true; e.ph = ph; } if (styled(e)) anyStyle = true; if (isImg(e)) nImg++; }
      if (has(s, "hideMaster") && typeof s.hideMaster !== "boolean") return null;
      var wantBg = null;
      if (has(s, "bg")) { if (typeof s.bg !== "string" || BG_HEX.indexOf(s.bg) < 0) return null; wantBg = s.bg; }
      var ns = { id: s.id, elements: els, notes: typeof s.notes === "string" ? cut(s.notes, MAX_NOTES) : "" };
      if (wantLay) ns.lay = wantLay;
      if (s.hideMaster === true) { ns.hideMaster = true; anyHide = true; }
      if (wantBg) { ns.bg = wantBg; anyBg = true; }
      if (has(s, "transition")) { if (TRANS.indexOf(s.transition) < 0) return null; if (s.transition === "fade") { ns.transition = "fade"; anyTrans = true; } }
      if (has(s, "tspeed")) { if ((s.tspeed !== "slow" && s.tspeed !== "fast") || ns.transition !== "fade") return null; ns.tspeed = s.tspeed; anyTspeed = true; }
      if (has(s, "anim")) {
        // object animation: an ordered list of {el, fx}; each item at most once, only items on this slide, one click per entry
        if (!Array.isArray(s.anim) || s.anim.length > MAX_EL) return null;
        var seen = Object.create(null), an = [];
        for (var q = 0; q < s.anim.length; q++) {
          var a = s.anim[q];
          if (!a || typeof a !== "object" || typeof a.el !== "string" || FX.indexOf(a.fx) < 0 || seen[a.el] || !els.some(function (x) { return x.id === a.el; })) return null;
          if (has(a, "after") && typeof a.after !== "boolean") return null;
          if (has(a, "spd") && ((a.spd !== "slow" && a.spd !== "fast") || a.fx !== "fade")) return null;
          seen[a.el] = true; var item = { el: a.el, fx: a.fx }; if (a.after === true) { item.after = true; anyAfter = true; } if (a.spd) { item.spd = a.spd; anySpd = true; } an.push(item);
        }
        if (an.length) { ns.anim = an; anyAnim = true; }
      }
      out.slides.push(ns);
    }
    // Version 2 only when something needs it, so a deck without a master is still a version 1 deck that older code opens.
    if (masterEls.length) out.master = { elements: masterEls };
    // A stored background that no longer reads well with its own text is refused, never drawn. The editor never produces one (every change is checked), so only a hand-edited file can.
    if (anyBg) for (var bi = 0; bi < out.slides.length; bi++) if (out.slides[bi].bg && !bgReadable(out, bi, out.slides[bi].bg)) return null;
    // Version 3 when a transition is used: the previous (master) version only knows 1 and 2 and refuses 3, so it never drops a transition silently.
    if (masterEls.length || anyHide) out.v = 2;
    if (anyTrans) out.v = 3;
    // Version 4 when any item is styled: older code only knows 1 to 3 and refuses 4, so it never drops a style silently.
    if (anyStyle) out.v = 4;
    // Version 5 when a picture is used: older code only knows 1 to 4 and refuses 5, so it never drops a picture silently. More than MAX_IMAGES pictures is refused, not trimmed.
    if (nImg > MAX_IMAGES) return null;
    if (nImg) out.v = 5;
    // Version 6 when any item is animated: older code only knows 1 to 5 and refuses 6, so it never drops an animation silently.
    if (anyAnim) out.v = 6;
    // Version 7 when an item plays "after previous": older code only knows 1 to 6 and refuses 7, so it never drops that timing silently.
    if (anyAfter) out.v = 7;
    // Version 8 when any slide has its own background colour: older code only knows 1 to 7 and refuses 8, so it never drops a background silently.
    if (anyBg) out.v = 8;
    // Version 9 when a slide is linked to a layout or a layout was edited: older code only knows 1 to 8 and refuses 9, so it never drops a link silently.
    if (outLays) out.lays = outLays;
    if (anyLay) out.v = 9;
    // Version 10 when a slide's fade has its own speed: older code only knows 1 to 9 and refuses 10, so it never drops a speed silently.
    if (anyTspeed) out.v = 10;
    // Version 11 when an item's fade-in has its own speed: older code only knows 1 to 10 and refuses 11, so it never drops that speed silently.
    if (anySpd) out.v = 11;
    // Version 12 when a text item is underlined: older code only knows 1 to 11 and refuses 12, so it never drops an underline silently.
    if (out.slides.some(function (s) { return s.elements.some(isU); }) || masterEls.some(isU)) out.v = 12;
    return out;
  }
  // how many text fields in a raw (unvalidated) deck are longer than the limits and so will be shortened
  function countCut(raw) {
    var n = 0; if (!raw || !Array.isArray(raw.slides)) return 0;
    if (raw.master && Array.isArray(raw.master.elements)) raw.master.elements.forEach(function (e) { if (e && typeof e.text === "string" && Array.from(e.text).length > MAX_TEXT) n++; });
    raw.slides.forEach(function (s) {
      if (!s || typeof s !== "object") return;
      if (typeof s.notes === "string" && Array.from(s.notes).length > MAX_NOTES) n++;
      (Array.isArray(s.elements) ? s.elements : []).forEach(function (e) { if (e && typeof e.text === "string" && Array.from(e.text).length > MAX_TEXT) n++; });
    });
    return n;
  }
  function copy(d) { return JSON.parse(JSON.stringify(d)); }
  var ops = {
    addLinkedSlide: function (d, at, layout) {
      if (d.slides.length >= MAX_SLIDES || layKeys().indexOf(layout) < 0) return null;
      var n = copy(d), s = newSlide(layout); s.lay = layout;
      s.elements.forEach(function (e, i) { var o = slotOf(n, layout, i); LAY_FIELDS.forEach(function (f) { e[f] = o[f]; }); e.ph = i; });
      n.slides.splice(at === undefined ? n.slides.length : at + 1, 0, s); return validate(n);
    },
    unlinkSlide: function (d, i) {
      var s = d.slides[i]; if (!s || !s.lay) return null; var n = copy(d), t = n.slides[i]; delete t.lay; t.elements.forEach(function (e) { delete e.ph; }); return validate(n);
    },
    // fields: any of x, y, w, h, size. Only linked item fields equal to the old layout value follow; others are local overrides and stay.
    setLayoutSlot: function (d, key, slot, fields) {
      if (layKeys().indexOf(key) < 0 || !layOk(slot) || slot < 0 || slot >= LAYOUTS[key].items.length || !fields || typeof fields !== "object") return null;
      var n = copy(d), old = slotOf(d, key, slot), nw = {}, any = false;
      LAY_FIELDS.forEach(function (f) { nw[f] = old[f]; if (has(fields, f)) { var v = Math.round(Number(fields[f])); if (!isFinite(v)) v = old[f]; nw[f] = Math.min(LAY_LIM[f][1], Math.max(LAY_LIM[f][0], v)); } if (nw[f] !== old[f]) any = true; });
      // The slot must fit on the slide the way an item does (validate would otherwise clamp the item and break the equality rule): size first, then position.
      nw.w = Math.min(nw.w, W); nw.h = Math.min(nw.h, H); nw.x = Math.min(nw.x, W - nw.w); nw.y = Math.min(nw.y, H - nw.h);
      any = LAY_FIELDS.some(function (f) { return nw[f] !== old[f]; });
      if (!any) return null;
      n.lays = n.lays || {}; var arr = n.lays[key] = (n.lays[key] || []).slice(); while (arr.length <= slot) arr.push({}); arr[slot] = Object.assign({}, arr[slot]);
      LAY_FIELDS.forEach(function (f) { if (nw[f] === LAYOUTS[key].items[slot][f]) delete arr[slot][f]; else arr[slot][f] = nw[f]; });
      // An item the slide clamps (its own width or height is larger than the layout allows) counts as following when its x or y equals the clamped layout value,
      // so a clamp never turns into an override. Size fields first, then position, using the item's width and height from before this edit.
      n.slides.forEach(function (s) { if (s.lay !== key) return; s.elements.forEach(function (e) {
        if (e.ph !== slot) return;
        var ow = e.w, oh = e.h, fx = e.x === Math.min(old.x, W - ow), fy = e.y === Math.min(old.y, H - oh);
        ["w", "h", "size"].forEach(function (f) { if (e[f] === old[f]) e[f] = nw[f]; });
        if (fx) e.x = Math.min(nw.x, W - e.w); if (fy) e.y = Math.min(nw.y, H - e.h);
      }); });
      // Back to the built-in values leaves no stored override: empty entries are removed, and with them the layout key and the lays object.
      if (arr.every(function (o) { return !Object.keys(o).length; })) delete n.lays[key];
      if (!Object.keys(n.lays).length) delete n.lays;
      return validate(n);
    },
    addSlide: function (d, at, layout) { if (d.slides.length >= MAX_SLIDES) return null; var n = copy(d), s = newSlide(layout); n.slides.splice(at === undefined ? n.slides.length : at + 1, 0, s); return n; },
    deleteSlide: function (d, i) { if (d.slides.length <= 1 || !d.slides[i]) return null; var n = copy(d); n.slides.splice(i, 1); return n; },
    // copy of slide i placed right after it, with fresh ids so ids stay unique; null at the slide or item caps
    duplicateSlide: function (d, i) {
      var src = d.slides[i]; if (!src || d.slides.length >= MAX_SLIDES) return null;
      var n = copy(d), c = copy(src); c.id = uid(); var idmap = {}; c.elements.forEach(function (e) { var o = e.id; e.id = uid(); idmap[o] = e.id; }); if (c.anim) c.anim = c.anim.map(function (a) { var o = { el: idmap[a.el], fx: a.fx }; if (a.after === true) o.after = true; if (a.spd) o.spd = a.spd; return o; }); n.slides.splice(i + 1, 0, c); return normVersion(n);
    },
    // speaker notes for slide i: plain text, cut at MAX_NOTES (the page also stops typing there, so nothing is cut silently)
    setNotes: function (d, i, text) { if (!d.slides[i] || typeof text !== "string") return null; var n = copy(d); n.slides[i].notes = cut(text, MAX_NOTES); return n; },
    moveSlide: function (d, i, to) { if (!d.slides[i] || to < 0 || to >= d.slides.length || to === i) return null; var n = copy(d), s = n.slides.splice(i, 1)[0]; n.slides.splice(to, 0, s); return n; },
    // Adds a picture item that refers to bytes already stored in this browser. Null when the slide or deck is full.
    addImage: function (d, i, hash, nw, nh, alt) {
      var s = d.slides[i]; if (!s || typeof hash !== "string" || !HASH_RE.test(hash) || s.elements.length >= MAX_EL) return null;
      var count = 0; d.slides.forEach(function (x) { x.elements.forEach(function (e) { if (isImg(e)) count++; }); }); if (count >= MAX_IMAGES) return null;
      var r = Math.min(1, 600 / nw, 400 / nh), w = Math.max(10, Math.round(nw * r)), h = Math.max(10, Math.round(nh * r));
      var n = copy(d), e = { id: uid(), type: "image", x: Math.round((W - w) / 2), y: Math.round((H - h) / 2), w: w, h: h, fill: "theme", text: typeof alt === "string" ? cut(alt, MAX_TEXT) : "", size: 32, img: hash };
      n.slides[i].elements.push(e); return { deck: normVersion(n), id: e.id };
    },
    addElement: function (d, i, type) { var s = d.slides[i]; if (!s || s.elements.length >= MAX_EL || TYPES.indexOf(type) < 0 || type === "image") return null; var n = copy(d), e = newEl(type); n.slides[i].elements.push(e); return { deck: n, id: e.id }; },
    updateElement: function (d, i, id, patch) {
      var s = d.slides[i]; if (!s) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; }); if (k < 0) return null;
      var n = copy(d), merged = Object.assign({}, n.slides[i].elements[k]);
      ["x", "y", "w", "h", "size", "fill", "text", "b", "i", "u", "al"].forEach(function (f) { if (has(patch, f)) merged[f] = patch[f]; });
      if (has(patch, "al") && ALIGNS.indexOf(patch.al) < 0 && patch.al !== "default") return null;
      if ((has(patch, "b") && typeof patch.b !== "boolean") || (has(patch, "i") && typeof patch.i !== "boolean") || (has(patch, "u") && typeof patch.u !== "boolean")) return null;
      if (has(patch, "b") && !patch.b) delete merged.b;
      if (has(patch, "i") && !patch.i) delete merged.i;
      if (has(patch, "u") && !patch.u) delete merged.u;
      if (has(patch, "al") && patch.al === "default") delete merged.al;
      var fixed = fixEl(merged); if (fixed && has(merged, "ph")) fixed.ph = merged.ph; n.slides[i].elements[k] = fixed; return normVersion(n);
    },
    // Move one item's box to an edge or the middle of the slide: left, center, right, top, middle or bottom. Only x or y changes (the slide is 960 by 540), through
    // updateElement, so every normal rule applies. Null when the item is already there, for an unknown item or for an unknown mode.
    alignElement: function (d, i, id, mode) {
      var s = d.slides[i]; if (!s) return null; var e = null; s.elements.forEach(function (x) { if (x.id === id) e = x; }); if (!e) return null;
      var p = mode === "left" ? { x: 0 } : mode === "right" ? { x: W - e.w } : mode === "center" ? { x: Math.round((W - e.w) / 2) } : mode === "top" ? { y: 0 } : mode === "bottom" ? { y: H - e.h } : mode === "middle" ? { y: Math.round((H - e.h) / 2) } : null;
      if (!p || (has(p, "x") && p.x === e.x) || (has(p, "y") && p.y === e.y)) return null;
      return ops.updateElement(d, i, id, p);
    },
    // Copy of one item placed right above it, with a fresh id, moved 20 right and 20 down (or 20 left and up when that would leave the slide).
    // Text, size, fill, bold, italic, underline, align and a picture's reference (the same stored bytes) are kept; its animation is not copied. Null at the 30 item cap, at the 20 picture cap, or for an unknown item.
    duplicateElement: function (d, i, id) {
      var s = d.slides[i]; if (!s || s.elements.length >= MAX_EL) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; });
      if (k < 0) return null;
      var src = s.elements[k];
      if (isImg(src)) { var count = 0; d.slides.forEach(function (x) { x.elements.forEach(function (e) { if (isImg(e)) count++; }); }); if (count >= MAX_IMAGES) return null; }
      var n = copy(d), c = copy(src); c.id = uid(); delete c.ph;
      c.x = c.x + 20 <= W - c.w ? c.x + 20 : Math.max(0, c.x - 20); c.y = c.y + 20 <= H - c.h ? c.y + 20 : Math.max(0, c.y - 20);
      n.slides[i].elements.splice(k + 1, 0, c); return { deck: normVersion(n), id: c.id };
    },
    // Paste an item copied from any slide onto slide i: a fresh id, moved 20 right and 20 down (or left and up at the edge), text, style and a picture's reference kept,
    // layout link (ph) and animation not carried. item is a plain item as the page holds it; it is checked like any stored item. Null at the 30 item cap, at the 20 picture cap, or for an item that does not check out.
    pasteElement: function (d, i, item) {
      var s = d.slides[i]; if (!s || s.elements.length >= MAX_EL || !item || typeof item !== "object") return null;
      var c = fixEl(copy(item)); if (!c) return null;
      if (isImg(c)) { var count = 0; d.slides.forEach(function (x) { x.elements.forEach(function (e) { if (isImg(e)) count++; }); }); if (count >= MAX_IMAGES) return null; }
      c.id = uid();
      c.x = c.x + 20 <= W - c.w ? c.x + 20 : Math.max(0, c.x - 20); c.y = c.y + 20 <= H - c.h ? c.y + 20 : Math.max(0, c.y - 20);
      var n = copy(d); n.slides[i].elements.push(c); var v = normVersion(n); return v ? { deck: v, id: c.id } : null;
    },
    // dir -1 sends the item one step back (drawn earlier, underneath), +1 brings it one step forward. Null at the ends.
    moveElement: function (d, i, id, dir) {
      var s = d.slides[i]; if (!s || (dir !== 1 && dir !== -1)) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; });
      if (k < 0 || k + dir < 0 || k + dir >= s.elements.length) return null;
      var n = copy(d), a = n.slides[i].elements; var t = a[k]; a[k] = a[k + dir]; a[k + dir] = t; return n;
    },
    // to is "front" (drawn last, on top of everything) or "back" (drawn first, underneath everything). Only the order changes. Null when the item is already there.
    stackElement: function (d, i, id, to) {
      var s = d.slides[i]; if (!s || (to !== "front" && to !== "back")) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; });
      if (k < 0 || (to === "front" && k === s.elements.length - 1) || (to === "back" && k === 0)) return null;
      var n = copy(d), a = n.slides[i].elements, it = a.splice(k, 1)[0]; if (to === "front") a.push(it); else a.unshift(it); return n;
    },
    deleteElement: function (d, i, id) { var s = d.slides[i]; if (!s) return null; var n = copy(d), l = n.slides[i].elements.length; n.slides[i].elements = n.slides[i].elements.filter(function (e) { return e.id !== id; }); if (n.slides[i].anim) { n.slides[i].anim = n.slides[i].anim.filter(function (a) { return a.el !== id; }); if (!n.slides[i].anim.length) delete n.slides[i].anim; } return n.slides[i].elements.length === l ? null : normVersion(n); },
    setHideMaster: function (d, i, hide) { var s = d.slides[i]; if (!s || typeof hide !== "boolean" || (s.hideMaster === true) === hide) return null; var n = copy(d); if (hide) n.slides[i].hideMaster = true; else delete n.slides[i].hideMaster; return normVersion(n); },
    // how a slide appears in Present: "none" or "fade". The PDF and the editor are not animated.
    setTransition: function (d, i, name) { var s = d.slides[i]; if (!s || (name !== "none" && name !== "fade") || (s.transition === "fade" ? "fade" : "none") === name) return null; var n = copy(d); if (name === "fade") n.slides[i].transition = "fade"; else { delete n.slides[i].transition; delete n.slides[i].tspeed; } return normVersion(n); },
    // how fast the fade runs in Present: "slow" (0.8 s), "normal" (0.4 s, the same as every deck before this) or "fast" (0.2 s). Only a slide that fades has a speed. Normal stores nothing.
    setTransitionSpeed: function (d, i, name) { var s = d.slides[i]; if (!s || s.transition !== "fade" || (name !== "slow" && name !== "normal" && name !== "fast") || (s.tspeed || "normal") === name) return null; var n = copy(d); if (name === "normal") delete n.slides[i].tspeed; else n.slides[i].tspeed = name; return normVersion(n); },
    // Object animation in Present. fx is "appear", "fade" or "none" (which removes it). Each item is animated at most once; entries play in list order, one click each.
    setAnim: function (d, i, id, fx) {
      var s = d.slides[i]; if (!s || (fx !== "none" && FX.indexOf(fx) < 0) || !s.elements.some(function (e) { return e.id === id; })) return null;
      var n = copy(d), t = n.slides[i], a = t.anim || [], k = -1; a.forEach(function (x, q) { if (x.el === id) k = q; });
      if (fx === "none") { if (k < 0) return null; a.splice(k, 1); } else if (k >= 0) { if (a[k].fx === fx) return null; a[k].fx = fx; if (fx !== "fade") delete a[k].spd; } else a.push({ el: id, fx: fx });
      if (a.length) t.anim = a; else delete t.anim; return normVersion(n);
    },
    // Timing: after = true plays the item by itself 1 second after the step before it (or after the slide appears, when it is first). false is "on click".
    setAnimAfter: function (d, i, id, after) {
      var s = d.slides[i]; if (!s || !s.anim || typeof after !== "boolean") return null; var k = -1; s.anim.forEach(function (x, q) { if (x.el === id) k = q; });
      if (k < 0 || (s.anim[k].after === true) === after) return null;
      var n = copy(d), e = n.slides[i].anim[k]; if (after) e.after = true; else delete e.after; return normVersion(n);
    },
    // how fast an item's fade-in runs in Present: "slow" (0.8 s), "normal" (0.4 s, as in every earlier deck) or "fast" (0.2 s). Only a fade-in has a speed. Normal stores nothing.
    setAnimSpeed: function (d, i, id, name) {
      var s = d.slides[i]; if (!s || !s.anim || (name !== "slow" && name !== "normal" && name !== "fast")) return null; var k = -1; s.anim.forEach(function (x, q) { if (x.el === id) k = q; });
      if (k < 0 || s.anim[k].fx !== "fade" || (s.anim[k].spd || "normal") === name) return null;
      var n = copy(d), e = n.slides[i].anim[k]; if (name === "normal") delete e.spd; else e.spd = name; return normVersion(n);
    },
    // dir -1 plays the item's animation one click earlier, +1 one click later. Null at the ends or when the item has none.
    moveAnim: function (d, i, id, dir) {
      var s = d.slides[i]; if (!s || !s.anim || (dir !== 1 && dir !== -1)) return null; var k = -1; s.anim.forEach(function (x, q) { if (x.el === id) k = q; });
      if (k < 0 || k + dir < 0 || k + dir >= s.anim.length) return null;
      var n = copy(d), a = n.slides[i].anim, t = a[k]; a[k] = a[k + dir]; a[k + dir] = t; return n;
    },
    setTheme: function (d, name) { if (!has(THEMES, name) || d.theme === name) return null; var n = copy(d); n.theme = name; return n; }
  };
  // The master is edited through the same item operations as a slide, on a one-slide stand-in deck.
  function normVersion(n) {
    var any = n.slides.some(function (s) { return s.hideMaster === true; }), trans = n.slides.some(function (s) { return s.transition === "fade"; }), anim = n.slides.some(function (s) { return s.anim && s.anim.length; }), after = n.slides.some(function (s) { return s.anim && s.anim.some(function (x) { return x.after === true; }); });
    if (n.master && !n.master.elements.length) delete n.master;
    var sty = n.slides.some(function (s) { return s.elements.some(styled); }) || (n.master && n.master.elements.some(styled));
    var img = n.slides.some(function (s) { return s.elements.some(isImg); }); var bgs = n.slides.some(function (s) { return !!s.bg; }); var lay = !!n.lays || n.slides.some(function (s) { return !!s.lay; }); var spd = n.slides.some(function (s) { return s.anim && s.anim.some(function (x) { return !!x.spd; }); }); var tsp = n.slides.some(function (s) { return !!s.tspeed; }); var und = n.slides.some(function (s) { return s.elements.some(isU); }) || !!(n.master && n.master.elements.some(isU)); n.v = und ? 12 : spd ? 11 : tsp ? 10 : lay ? 9 : bgs ? 8 : after ? 7 : anim ? 6 : img ? 5 : sty ? 4 : trans ? 3 : (n.master || any ? 2 : 1); return n;
  }
  function viaMaster(d, fn, adding) {
    var els = d.master ? d.master.elements : [];
    if (adding && els.length >= MAX_MASTER) return null;
    var t = { v: 1, theme: d.theme, slides: [{ id: "master", elements: copy(els), notes: "" }] }, r = fn(t); if (!r) return null;
    var nd = r.deck || r, n = copy(d); n.master = { elements: nd.slides[0].elements }; normVersion(n);
    return r.deck ? { deck: n, id: r.id } : n;
  }
  ops.master_addElement = function (d, type) { return viaMaster(d, function (t) { return ops.addElement(t, 0, type); }, true); };
  ops.master_alignElement = function (d, id, mode) { return viaMaster(d, function (t) { return ops.alignElement(t, 0, id, mode); }); };
  ops.master_updateElement = function (d, id, patch) { return viaMaster(d, function (t) { return ops.updateElement(t, 0, id, patch); }); };
  ops.master_stackElement = function (d, id, to) { return viaMaster(d, function (t) { return ops.stackElement(t, 0, id, to); }); };
  ops.master_moveElement = function (d, id, dir) { return viaMaster(d, function (t) { return ops.moveElement(t, 0, id, dir); }); };
  ops.master_duplicateElement = function (d, id) { return viaMaster(d, function (t) { return ops.duplicateElement(t, 0, id); }, true); };
  ops.master_deleteElement = function (d, id) { return viaMaster(d, function (t) { return ops.deleteElement(t, 0, id); }); };
  // the master items drawn under slide i (none when the slide hides the master)
  function masterOf(d, i) { return d.master && d.slides[i] && d.slides[i].hideMaster !== true ? d.master.elements : []; }
  // ---- slide backgrounds ----
  function drawnText(d, i) {  // text items that sit directly on the slide background: master items (unless hidden) then the slide's own
    var out = []; masterOf(d, i).concat(d.slides[i].elements).forEach(function (e) { if (e.type === "text" && typeof e.text === "string" && e.text.trim() !== "") out.push(e); });
    return out;
  }
  function bgReadable(d, i, hex) { return drawnText(d, i).every(function (e) { return contrast(color(d, e), hex) >= MIN_CONTRAST; }); }
  function bgOf(d, i) { var s = d.slides[i]; return s && s.bg ? s.bg : THEMES[d.theme].bg; }  // the one place every draw path (editor, Present, PDF) takes the background from
  function bgUsable(d, i, hex) { return !!d.slides[i] && BG_HEX.indexOf(hex) >= 0 && bgReadable(d, i, hex); }
  // slides (0-based) whose own background would be unreadable under a theme; the theme's own background is always readable (checked in the tests)
  function themeConflicts(d, name) {
    if (!has(THEMES, name)) return [];
    var t = copy(d); t.theme = name; var out = [];
    t.slides.forEach(function (s, i) { if (s.bg && !bgReadable(t, i, s.bg)) out.push(i); }); return out;
  }
  var rejected = null;
  function takeReject() { var r = rejected; rejected = null; return r; }
  ops.setBackground = function (d, i, hex) {
    rejected = null; var s = d.slides[i]; if (!s) return null;
    if (hex === "theme") { if (!s.bg) return null; var n0 = copy(d); delete n0.slides[i].bg; return normVersion(n0); }
    if (BG_HEX.indexOf(hex) < 0 || s.bg === hex) return null;
    if (!bgReadable(d, i, hex)) { rejected = "That background was not applied: text on this slide would be hard to read on it (contrast below 4.5). Change the text colour first, or pick another background."; return null; }
    var n = copy(d); n.slides[i].bg = hex; return normVersion(n);
  };
  // Switching theme and, in the same step, putting the listed slides back to the theme background. One Undo step brings back both.
  ops.setThemeResetting = function (d, name) {
    rejected = null;
    if (!has(THEMES, name) || d.theme === name) return null;
    var n = copy(d), c = themeConflicts(d, name); c.forEach(function (i) { delete n.slides[i].bg; }); n.theme = name; return normVersion(n);
  };
  // Every other change is checked as a whole: a change that would leave any slide with a background its text cannot be read on is refused (null), with a reason in takeReject(). The deck passed in is never touched.
  Object.keys(ops).forEach(function (k) {
    var f = ops[k]; if (k === "setBackground" || k === "setThemeResetting") return;
    ops[k] = function () {
      rejected = null; var r = f.apply(null, arguments); if (!r) return r;
      var nd = r.deck || r;
      for (var i = 0; i < nd.slides.length; i++) if (nd.slides[i].bg && !bgReadable(nd, i, nd.slides[i].bg)) {
        rejected = k === "setTheme" ? "Not switched: the background on slide " + (i + 1) + " would be hard to read with this theme's text." : "That change was not made: it would leave the text on slide " + (i + 1) + " hard to read on its background (contrast below 4.5). Change or remove that slide's background first.";
        return null;
      }
      return r;
    };
  });
  function color(deck, e) { var t = THEMES[deck.theme]; return e.fill === "theme" ? (e.type === "text" ? t.fg : t.accent) : e.fill; }
  function lum(hex) { var c = [1, 3, 5].map(function (i) { var v = parseInt(hex.substr(i, 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; }
  function contrast(a, b) { var x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  // text on a shape: whichever of white or near-black reads better on the shape color
  function onFill(fill) { return contrast(fill, "#ffffff") >= contrast(fill, "#111111") ? "#ffffff" : "#111111"; }
  function textColor(deck, e) { return e.type === "text" ? color(deck, e) : onFill(color(deck, e)); }
  // raw is the stored string or null. An unreadable or newer-format value is never treated as empty.
  function loadDeck(raw) {
    if (raw === null || raw === undefined || raw === "") return { deck: newDeck(), unreadable: false, raw: null };
    var v = null; try { v = validate(JSON.parse(raw)); } catch (e) { v = null; }
    return v ? { deck: v, unreadable: false, raw: null } : { deck: newDeck(), unreadable: true, raw: String(raw) };
  }
  function describe(deck, i) { return describeEls(deck.slides[i].elements); }
  function describeEls(els) {
    return els.map(function (e, k) {
      var kind = e.type === "text" ? "Text" : e.type === "rect" ? "Rectangle" : e.type === "image" ? "Picture" : "Ellipse";
      return (k + 1) + ". " + kind + (e.text ? ": " + cut(e.text, 40) : "");
    });
  }
  // store: { save(deck) -> true|false }
  function History(deck, store, limit) {
    var h = this; h.deck = deck; h.past = []; h.future = []; h.limit = limit || HIST; h.store = store; h.saveFailed = false; h.blocked = false; h.note = "";
    var BLOCK = "Editing is paused because your stored deck could not be read. Nothing was changed or saved.";
    h.persist = function (note) {
      if (h.blocked) { h.note = BLOCK; return; }
      var ok = false; try { ok = h.store.save(h.deck) !== false; } catch (e) { ok = false; }
      h.saveFailed = !ok;
      h.note = ok ? (note || "Saved on this device.") : "This browser would not store the deck, so these changes are on screen only and will be lost on reload." + (note ? " " + note : "");
    };
    h.change = function (next, note) {
      if (h.blocked) { h.note = BLOCK; return false; }
      if (!next || JSON.stringify(next) === JSON.stringify(h.deck)) return false;
      var size = JSON.stringify(h.deck).length, cap = Math.min(h.limit, Math.max(5, Math.floor(MAX_SNAPSHOT_BYTES / Math.max(size, 1))));
      h.past.push(copy(h.deck)); while (h.past.length > cap) h.past.shift(); h.future = []; h.deck = next; h.persist(note); return true;
    };
    h.undo = function () { if (h.blocked) { h.note = BLOCK; return false; } if (!h.past.length) return false; h.future.push(copy(h.deck)); h.deck = h.past.pop(); h.persist("Undone."); return true; };
    h.redo = function () { if (h.blocked) { h.note = BLOCK; return false; } if (!h.future.length) return false; h.past.push(copy(h.deck)); h.deck = h.future.pop(); h.persist("Redone."); return true; };
  }
  // Slide show keys: returns the new slide index, or null when the key does nothing here. Escape is handled by the dialog.
  // Find and replace over the text of the slides' own items (not the master, not picture descriptions, not speaker notes). Plain text only: no wildcards, no
  // regex. Occurrences do not overlap, left to right. at = one slide index, or undefined for every slide. Nothing is cut: when any resulting text would pass
  // MAX_TEXT, nothing is replaced and tooLong lists the slide numbers.
  var FIND_MAX = 100;
  function pat(q, mc) { return typeof q === "string" && q.length && Array.from(q).length <= FIND_MAX ? new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), mc === true ? "gu" : "giu") : null; }
  function findText(d, q, mc, at) {
    var re = pat(q, mc), out = { total: 0, slides: [] }; if (!re) return out;
    d.slides.forEach(function (sl, i) { if (at !== undefined && at !== i) return; var c = 0; sl.elements.forEach(function (e) { if (e.type === "image") return; var m = e.text.match(re); if (m) c += m.length; }); if (c) { out.total += c; out.slides.push({ at: i, n: c }); } });
    return out;
  }
  function replaceText(d, q, r, mc, at) {
    var re = pat(q, mc), res = { deck: null, count: 0, tooLong: [], invalid: false }; if (!re || typeof r !== "string" || Array.from(r).length > MAX_TEXT) return res;
    var n = copy(d);
    n.slides.forEach(function (sl, i) { if (at !== undefined && at !== i) return; sl.elements.forEach(function (e) {
      if (e.type === "image") return; var m = e.text.match(re); if (!m) return;
      var t = e.text.replace(re, function () { return r; }); res.count += m.length;
      if (Array.from(t).length > MAX_TEXT) { if (res.tooLong.indexOf(i + 1) < 0) res.tooLong.push(i + 1); return; } e.text = t; }); });
    if (res.tooLong.length || !res.count) { res.count = res.tooLong.length ? res.count : 0; return res; }
    var v = validate(n); var base = validate(copy(d)); if (!v) { res.invalid = true; res.count = 0; } else if (JSON.stringify(v) !== JSON.stringify(base)) res.deck = v; else res.count = 0; return res;
  }
  function showNav(i, n, key) {
    var next = ["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"], prev = ["ArrowLeft", "ArrowUp", "PageUp", "Backspace"];
    if (!(n > 0) || !(i >= 0 && i < n)) return null;
    if (next.indexOf(key) >= 0) return Math.min(n - 1, i + 1);
    if (prev.indexOf(key) >= 0) return Math.max(0, i - 1);
    if (key === "Home") return 0;
    if (key === "End") return n - 1;
    return null;
  }
  // Slide show with animation. step is how many of the slide's animation entries are already shown. Forward keys show the next entry, and only
  // move to the next slide when none is left. Back keys hide the last shown entry first. Returns {at, step} or null when the key does nothing.
  function animCount(d, i) { return d.slides[i] && d.slides[i].anim ? d.slides[i].anim.length : 0; }
  function showStep(d, i, step, key) {
    var n = d.slides.length; if (!(n > 0) || !(i >= 0 && i < n)) return null;
    var next = ["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"], prev = ["ArrowLeft", "ArrowUp", "PageUp", "Backspace"];
    if (next.indexOf(key) >= 0) { if (step < animCount(d, i)) return { at: i, step: step + 1 }; return i < n - 1 ? { at: i + 1, step: 0 } : null; }
    if (prev.indexOf(key) >= 0) { if (step > 0) return { at: i, step: step - 1 }; return i > 0 ? { at: i - 1, step: animCount(d, i - 1) } : null; }
    if (key === "Home") return { at: 0, step: 0 };
    if (key === "End") return { at: n - 1, step: animCount(d, n - 1) };
    return null;
  }
  // Snapping while dragging (editor only, nothing stored). Lines: the slide's edges and middle, and the edges and middle of the other items on the slide
  // (not master items). A moving item snaps its left, middle or right to a vertical line and its top, middle or bottom to a horizontal line, when
  // within SNAP of it; a resized item snaps its right and bottom edge. Returns the shift to apply and the lines that now match.
  var SNAP = 6;
  function snapBox(d, i, id, b, mode, tol) {
    var s = d.slides[i]; if (!s || !b) return { dx: 0, dy: 0, vx: [], hy: [] }; tol = tol === undefined ? SNAP : tol;
    var xs = [0, W / 2, W], ys = [0, H / 2, H];
    s.elements.forEach(function (e) { if (e.id === id) return; xs.push(e.x, e.x + e.w / 2, e.x + e.w); ys.push(e.y, e.y + e.h / 2, e.y + e.h); });
    var cx = mode === "move" ? [b.x, b.x + b.w / 2, b.x + b.w] : [b.x + b.w], cy = mode === "move" ? [b.y, b.y + b.h / 2, b.y + b.h] : [b.y + b.h];
    function best(c, refs) { var k = null; c.forEach(function (v) { refs.forEach(function (r) { var t = r - v; if (Math.abs(t) <= tol && (k === null || Math.abs(t) < Math.abs(k))) k = t; }); }); return k === null ? 0 : k; }
    var dx = best(cx, xs), dy = best(cy, ys), vx = [], hy = [];
    cx.forEach(function (v) { xs.forEach(function (r) { if (Math.abs(v + dx - r) < 0.001 && vx.indexOf(r) < 0) vx.push(r); }); });
    cy.forEach(function (v) { ys.forEach(function (r) { if (Math.abs(v + dy - r) < 0.001 && hy.indexOf(r) < 0) hy.push(r); }); });
    return { dx: dx, dy: dy, vx: dx || vx.length ? vx : [], hy: dy || hy.length ? hy : [] };
  }
  // ids of the items of slide i that are not shown yet at this step
  // true when the next entry (index step) plays by itself, so the show moves on after AUTO_MS without a key
  var AUTO_MS = 1000;
  function autoNext(d, i, step) { var a = d.slides[i] && d.slides[i].anim; return !!(a && step >= 0 && step < a.length && a[step].after === true); }
  function hiddenIds(d, i, step) { var a = d.slides[i] && d.slides[i].anim || [], out = {}; a.forEach(function (x, q) { if (q >= step) out[x.el] = true; }); return out; }
  function fmtClock(sec) { sec = Math.max(0, Math.floor(Number(sec) || 0)); var m = Math.floor(sec / 60), r = sec % 60; return m + ":" + (r < 10 ? "0" : "") + r; }
  // Wrap text to a width. A character is estimated at 0.55 of the font size, and wide scripts and emoji at twice that.
  // This is an approximation, not a measurement of the real font. Runs of white space (including newlines) count as one
  // space, which is how the one-line drawing already treated them. Words longer than a line are cut by code point, so a
  // pair of UTF-16 halves is never split. No text is dropped: item text is capped at 1000 characters.
  function isWide(c) { return (c >= 0x1100 && c <= 0x115f) || (c >= 0x2e80 && c <= 0xa4cf) || (c >= 0xac00 && c <= 0xd7a3) || (c >= 0xf900 && c <= 0xfaff) || (c >= 0xfe30 && c <= 0xfe6f) || (c >= 0xff00 && c <= 0xff60) || (c >= 0xffe0 && c <= 0xffe6) || (c >= 0x1f300 && c <= 0x1faff) || (c >= 0x20000 && c <= 0x3fffd); }
  function units(ch) { return isWide(ch.codePointAt(0)) ? 2 : 1; }
  // bold is wider, so a bold item is wrapped with a wider estimate. Wrapping is approximate either way.
  function wrapLines(text, size, width, bold) {
    var per = Math.max(2, Math.floor(width / (size * (bold ? 0.6 : 0.55)))), out = [], line = [], used = 0;
    function flush() { if (line.length) out.push(line.join("")); line = []; used = 0; }
    String(text).split(/\s+/).forEach(function (word) {
      if (!word) return;
      var cps = Array.from(word), wu = cps.reduce(function (t, ch) { return t + units(ch); }, 0);
      if (line.length && used + 1 + wu <= per) { line.push(" "); used += 1; cps.forEach(function (ch) { line.push(ch); used += units(ch); }); return; }
      flush();
      cps.forEach(function (ch) { var u = units(ch); if (used + u > per && line.length) flush(); line.push(ch); used += u; });
    });
    flush();
    return out;
  }
  function textHeight(lines, size) { return Math.ceil(lines.length * size * 1.2); }
  var api = { findText: findText, replaceText: replaceText, FIND_MAX: FIND_MAX, MAX_TEXT: MAX_TEXT, layoutSlot: function (d, key, slot) { return layKeys().indexOf(key) < 0 || !layOk(slot) || slot < 0 || slot >= LAYOUTS[key].items.length ? null : slotOf(d, key, slot); }, layKeys: layKeys, bgOf: bgOf, bgUsable: bgUsable, bgReadable: bgReadable, themeConflicts: themeConflicts, takeReject: takeReject, BGS: BGS, MIN_CONTRAST: MIN_CONTRAST, snapBox: snapBox, SNAP: SNAP, autoNext: autoNext, AUTO_MS: AUTO_MS, showStep: showStep, hiddenIds: hiddenIds, animCount: animCount, FX: FX, LAYOUTS: LAYOUTS, countCut: countCut, cut: cut, MAX_NOTES: MAX_NOTES, fmtClock: fmtClock, wrapLines: wrapLines, textHeight: textHeight, showNav: showNav, W: W, H: H, THEMES: THEMES, FILLS: FILLS, MAX_SLIDES: MAX_SLIDES, MAX_EL: MAX_EL, newDeck: newDeck, validate: validate, ops: ops, color: color, textColor: textColor, onFill: onFill, contrast: contrast, loadDeck: loadDeck, describe: describe, describeEls: describeEls, masterOf: masterOf, MAX_MASTER: MAX_MASTER, History: History, copy: copy };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSlides = api;
})(typeof self !== "undefined" ? self : this);
