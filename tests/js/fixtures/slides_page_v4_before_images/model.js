/* Salow Slides model: pure logic, no DOM, so node can test it. A deck lives on this device.
 * Colors are chosen from fixed lists, so nothing from a file or the user reaches a style string
 * unchecked. Elements without their own color inherit the theme, so a theme change restyles them. */
(function (root) {
  "use strict";
  var W = 960, H = 540, MAX_SLIDES = 50, MAX_EL = 30, MAX_MASTER = 10, MAX_TEXT = 1000, MAX_NOTES = 2000, HIST = 100;
  var THEMES = { light: { bg: "#ffffff", fg: "#222222", accent: "#7a2e22" }, dark: { bg: "#1f2430", fg: "#f2f2f2", accent: "#e0a458" }, warm: { bg: "#faf5ec", fg: "#3b2a20", accent: "#a4472f" } };
  var FILLS = ["theme", "#7a2e22", "#2e6f95", "#3b7d4f", "#c28b1d", "#6b6b6b"];
  var TYPES = ["text", "rect", "ellipse"];
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
  var ALIGNS = ["left", "center", "right"];
  function styled(e) { return e.b === true || e.i === true || has(e, "al"); }
  function fixEl(e) {
    if (!e || typeof e !== "object" || TYPES.indexOf(e.type) < 0 || typeof e.id !== "string" || !ID_RE.test(e.id)) return null;
    var text = typeof e.text === "string" ? cut(e.text, MAX_TEXT) : "";
    var w = clamp(e.w, 10, W), h = clamp(e.h, 10, H);
    var o = { id: e.id, type: e.type, x: clamp(e.x, 0, W - w), y: clamp(e.y, 0, H - h), w: w, h: h, fill: FILLS.indexOf(e.fill) >= 0 ? e.fill : "theme", text: text, size: clamp(e.size, 8, 120) };
    // Text style. Only set fields are kept, so an unstyled item serialises exactly as before and an unstyled deck stays version 1 to 3.
    if (e.b === true) o.b = true;
    if (e.i === true) o.i = true;
    if (ALIGNS.indexOf(e.al) >= 0) o.al = e.al;  // unset means left for text items and centre for shapes
    return o;
  }
  // returns a clean deck or null; unknown fields are dropped and numbers are clamped
  function validate(d) {
    if (!d || typeof d !== "object" || Array.isArray(d) || (has(d, "v") && d.v !== 1 && d.v !== 2 && d.v !== 3 && d.v !== 4) || !Array.isArray(d.slides) || !d.slides.length || d.slides.length > MAX_SLIDES) return null;
    var TRANS = ["none", "fade"], ids = Object.create(null), out = { v: 1, theme: has(THEMES, d.theme) ? d.theme : "light", slides: [] }, masterEls = [], anyHide = false, anyTrans = false, anyStyle = false;
    if (has(d, "master")) {
      var m = d.master; if (!m || typeof m !== "object" || Array.isArray(m) || !Array.isArray(m.elements) || m.elements.length > MAX_MASTER) return null;
      for (var k = 0; k < m.elements.length; k++) { var me = fixEl(m.elements[k]); if (!me || ids[me.id]) return null; ids[me.id] = true; masterEls.push(me); if (styled(me)) anyStyle = true; }
    }
    for (var i = 0; i < d.slides.length; i++) {
      var s = d.slides[i];
      if (!s || typeof s !== "object" || typeof s.id !== "string" || !ID_RE.test(s.id) || !Array.isArray(s.elements) || s.elements.length > MAX_EL || ids[s.id]) return null;
      ids[s.id] = true; var els = [];
      for (var j = 0; j < s.elements.length; j++) { var e = fixEl(s.elements[j]); if (!e || ids[e.id]) return null; ids[e.id] = true; els.push(e); if (styled(e)) anyStyle = true; }
      if (has(s, "hideMaster") && typeof s.hideMaster !== "boolean") return null;
      var ns = { id: s.id, elements: els, notes: typeof s.notes === "string" ? cut(s.notes, MAX_NOTES) : "" };
      if (s.hideMaster === true) { ns.hideMaster = true; anyHide = true; }
      if (has(s, "transition")) { if (TRANS.indexOf(s.transition) < 0) return null; if (s.transition === "fade") { ns.transition = "fade"; anyTrans = true; } }
      out.slides.push(ns);
    }
    // Version 2 only when something needs it, so a deck without a master is still a version 1 deck that older code opens.
    if (masterEls.length) out.master = { elements: masterEls };
    // Version 3 when a transition is used: the previous (master) version only knows 1 and 2 and refuses 3, so it never drops a transition silently.
    if (masterEls.length || anyHide) out.v = 2;
    if (anyTrans) out.v = 3;
    // Version 4 when any item is styled: older code only knows 1 to 3 and refuses 4, so it never drops a style silently.
    if (anyStyle) out.v = 4;
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
    addSlide: function (d, at, layout) { if (d.slides.length >= MAX_SLIDES) return null; var n = copy(d), s = newSlide(layout); n.slides.splice(at === undefined ? n.slides.length : at + 1, 0, s); return n; },
    deleteSlide: function (d, i) { if (d.slides.length <= 1 || !d.slides[i]) return null; var n = copy(d); n.slides.splice(i, 1); return n; },
    // copy of slide i placed right after it, with fresh ids so ids stay unique; null at the slide or item caps
    duplicateSlide: function (d, i) {
      var src = d.slides[i]; if (!src || d.slides.length >= MAX_SLIDES) return null;
      var n = copy(d), c = copy(src); c.id = uid(); c.elements.forEach(function (e) { e.id = uid(); }); n.slides.splice(i + 1, 0, c); return n;
    },
    // speaker notes for slide i: plain text, cut at MAX_NOTES (the page also stops typing there, so nothing is cut silently)
    setNotes: function (d, i, text) { if (!d.slides[i] || typeof text !== "string") return null; var n = copy(d); n.slides[i].notes = cut(text, MAX_NOTES); return n; },
    moveSlide: function (d, i, to) { if (!d.slides[i] || to < 0 || to >= d.slides.length || to === i) return null; var n = copy(d), s = n.slides.splice(i, 1)[0]; n.slides.splice(to, 0, s); return n; },
    addElement: function (d, i, type) { var s = d.slides[i]; if (!s || s.elements.length >= MAX_EL || TYPES.indexOf(type) < 0) return null; var n = copy(d), e = newEl(type); n.slides[i].elements.push(e); return { deck: n, id: e.id }; },
    updateElement: function (d, i, id, patch) {
      var s = d.slides[i]; if (!s) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; }); if (k < 0) return null;
      var n = copy(d), merged = Object.assign({}, n.slides[i].elements[k]);
      ["x", "y", "w", "h", "size", "fill", "text", "b", "i", "al"].forEach(function (f) { if (has(patch, f)) merged[f] = patch[f]; });
      if (has(patch, "al") && ALIGNS.indexOf(patch.al) < 0 && patch.al !== "default") return null;
      if ((has(patch, "b") && typeof patch.b !== "boolean") || (has(patch, "i") && typeof patch.i !== "boolean")) return null;
      if (has(patch, "b") && !patch.b) delete merged.b;
      if (has(patch, "i") && !patch.i) delete merged.i;
      if (has(patch, "al") && patch.al === "default") delete merged.al;
      var fixed = fixEl(merged); n.slides[i].elements[k] = fixed; return normVersion(n);
    },
    // dir -1 sends the item one step back (drawn earlier, underneath), +1 brings it one step forward. Null at the ends.
    moveElement: function (d, i, id, dir) {
      var s = d.slides[i]; if (!s || (dir !== 1 && dir !== -1)) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; });
      if (k < 0 || k + dir < 0 || k + dir >= s.elements.length) return null;
      var n = copy(d), a = n.slides[i].elements; var t = a[k]; a[k] = a[k + dir]; a[k + dir] = t; return n;
    },
    deleteElement: function (d, i, id) { var s = d.slides[i]; if (!s) return null; var n = copy(d), l = n.slides[i].elements.length; n.slides[i].elements = n.slides[i].elements.filter(function (e) { return e.id !== id; }); return n.slides[i].elements.length === l ? null : n; },
    setHideMaster: function (d, i, hide) { var s = d.slides[i]; if (!s || typeof hide !== "boolean" || (s.hideMaster === true) === hide) return null; var n = copy(d); if (hide) n.slides[i].hideMaster = true; else delete n.slides[i].hideMaster; return normVersion(n); },
    // how a slide appears in Present: "none" or "fade". The PDF and the editor are not animated.
    setTransition: function (d, i, name) { var s = d.slides[i]; if (!s || (name !== "none" && name !== "fade") || (s.transition === "fade" ? "fade" : "none") === name) return null; var n = copy(d); if (name === "fade") n.slides[i].transition = "fade"; else delete n.slides[i].transition; return normVersion(n); },
    setTheme: function (d, name) { if (!has(THEMES, name) || d.theme === name) return null; var n = copy(d); n.theme = name; return n; }
  };
  // The master is edited through the same item operations as a slide, on a one-slide stand-in deck.
  function normVersion(n) {
    var any = n.slides.some(function (s) { return s.hideMaster === true; }), trans = n.slides.some(function (s) { return s.transition === "fade"; });
    if (n.master && !n.master.elements.length) delete n.master;
    var sty = n.slides.some(function (s) { return s.elements.some(styled); }) || (n.master && n.master.elements.some(styled));
    n.v = sty ? 4 : trans ? 3 : (n.master || any ? 2 : 1); return n;
  }
  function viaMaster(d, fn, adding) {
    var els = d.master ? d.master.elements : [];
    if (adding && els.length >= MAX_MASTER) return null;
    var t = { v: 1, theme: d.theme, slides: [{ id: "master", elements: copy(els), notes: "" }] }, r = fn(t); if (!r) return null;
    var nd = r.deck || r, n = copy(d); n.master = { elements: nd.slides[0].elements }; normVersion(n);
    return r.deck ? { deck: n, id: r.id } : n;
  }
  ops.master_addElement = function (d, type) { return viaMaster(d, function (t) { return ops.addElement(t, 0, type); }, true); };
  ops.master_updateElement = function (d, id, patch) { return viaMaster(d, function (t) { return ops.updateElement(t, 0, id, patch); }); };
  ops.master_moveElement = function (d, id, dir) { return viaMaster(d, function (t) { return ops.moveElement(t, 0, id, dir); }); };
  ops.master_deleteElement = function (d, id) { return viaMaster(d, function (t) { return ops.deleteElement(t, 0, id); }); };
  // the master items drawn under slide i (none when the slide hides the master)
  function masterOf(d, i) { return d.master && d.slides[i] && d.slides[i].hideMaster !== true ? d.master.elements : []; }
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
      var kind = e.type === "text" ? "Text" : e.type === "rect" ? "Rectangle" : "Ellipse";
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
  function showNav(i, n, key) {
    var next = ["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"], prev = ["ArrowLeft", "ArrowUp", "PageUp", "Backspace"];
    if (!(n > 0) || !(i >= 0 && i < n)) return null;
    if (next.indexOf(key) >= 0) return Math.min(n - 1, i + 1);
    if (prev.indexOf(key) >= 0) return Math.max(0, i - 1);
    if (key === "Home") return 0;
    if (key === "End") return n - 1;
    return null;
  }
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
  var api = { LAYOUTS: LAYOUTS, countCut: countCut, cut: cut, MAX_NOTES: MAX_NOTES, fmtClock: fmtClock, wrapLines: wrapLines, textHeight: textHeight, showNav: showNav, W: W, H: H, THEMES: THEMES, FILLS: FILLS, MAX_SLIDES: MAX_SLIDES, MAX_EL: MAX_EL, newDeck: newDeck, validate: validate, ops: ops, color: color, textColor: textColor, onFill: onFill, contrast: contrast, loadDeck: loadDeck, describe: describe, describeEls: describeEls, masterOf: masterOf, MAX_MASTER: MAX_MASTER, History: History, copy: copy };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSlides = api;
})(typeof self !== "undefined" ? self : this);
