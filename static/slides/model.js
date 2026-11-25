/* Salow Slides model: pure logic, no DOM, so node can test it. A deck lives on this device.
 * Colors are chosen from fixed lists, so nothing from a file or the user reaches a style string
 * unchecked. Elements without their own color inherit the theme, so a theme change restyles them. */
(function (root) {
  "use strict";
  var W = 960, H = 540, MAX_SLIDES = 50, MAX_EL = 30, MAX_TEXT = 1000, HIST = 100;
  var THEMES = { light: { bg: "#ffffff", fg: "#222222", accent: "#7a2e22" }, dark: { bg: "#1f2430", fg: "#f2f2f2", accent: "#e0a458" }, warm: { bg: "#faf5ec", fg: "#3b2a20", accent: "#a4472f" } };
  var FILLS = ["theme", "#7a2e22", "#2e6f95", "#3b7d4f", "#c28b1d", "#6b6b6b"];
  var TYPES = ["text", "rect", "ellipse"];
  var has = function (o, k) { return Object.prototype.hasOwnProperty.call(o, k); };
  var ID_RE = /^[A-Za-z0-9_-]{1,40}$/, MAX_SNAPSHOT_BYTES = 5000000;
  var seq = 0;
  function uid() { return "e" + Date.now().toString(36) + (seq++).toString(36); }
  function clamp(v, lo, hi) { v = Math.round(Number(v)); if (!isFinite(v)) v = lo; return Math.min(hi, Math.max(lo, v)); }
  function newEl(type) {
    var e = { id: uid(), type: type, x: 80, y: 80, w: type === "text" ? 400 : 200, h: type === "text" ? 80 : 120, fill: "theme", text: "", size: 32 };
    if (type === "text") e.text = "Text";
    return e;
  }
  function newSlide() { return { id: uid(), elements: [] }; }
  function newDeck() { return { v: 1, theme: "light", slides: [newSlide()] }; }
  function fixEl(e) {
    if (!e || typeof e !== "object" || TYPES.indexOf(e.type) < 0 || typeof e.id !== "string" || !ID_RE.test(e.id)) return null;
    var text = typeof e.text === "string" ? e.text.slice(0, MAX_TEXT) : "";
    var w = clamp(e.w, 10, W), h = clamp(e.h, 10, H);
    return { id: e.id, type: e.type, x: clamp(e.x, 0, W - w), y: clamp(e.y, 0, H - h), w: w, h: h, fill: FILLS.indexOf(e.fill) >= 0 ? e.fill : "theme", text: text, size: clamp(e.size, 8, 120) };
  }
  // returns a clean deck or null; unknown fields are dropped and numbers are clamped
  function validate(d) {
    if (!d || typeof d !== "object" || Array.isArray(d) || (has(d, "v") && d.v !== 1) || !Array.isArray(d.slides) || !d.slides.length || d.slides.length > MAX_SLIDES) return null;
    var ids = Object.create(null), out = { v: 1, theme: has(THEMES, d.theme) ? d.theme : "light", slides: [] };
    for (var i = 0; i < d.slides.length; i++) {
      var s = d.slides[i];
      if (!s || typeof s !== "object" || typeof s.id !== "string" || !ID_RE.test(s.id) || !Array.isArray(s.elements) || s.elements.length > MAX_EL || ids[s.id]) return null;
      ids[s.id] = true; var els = [];
      for (var j = 0; j < s.elements.length; j++) { var e = fixEl(s.elements[j]); if (!e || ids[e.id]) return null; ids[e.id] = true; els.push(e); }
      out.slides.push({ id: s.id, elements: els });
    }
    return out;
  }
  function copy(d) { return JSON.parse(JSON.stringify(d)); }
  var ops = {
    addSlide: function (d, at) { if (d.slides.length >= MAX_SLIDES) return null; var n = copy(d), s = newSlide(); n.slides.splice(at === undefined ? n.slides.length : at + 1, 0, s); return n; },
    deleteSlide: function (d, i) { if (d.slides.length <= 1 || !d.slides[i]) return null; var n = copy(d); n.slides.splice(i, 1); return n; },
    moveSlide: function (d, i, to) { if (!d.slides[i] || to < 0 || to >= d.slides.length || to === i) return null; var n = copy(d), s = n.slides.splice(i, 1)[0]; n.slides.splice(to, 0, s); return n; },
    addElement: function (d, i, type) { var s = d.slides[i]; if (!s || s.elements.length >= MAX_EL || TYPES.indexOf(type) < 0) return null; var n = copy(d), e = newEl(type); n.slides[i].elements.push(e); return { deck: n, id: e.id }; },
    updateElement: function (d, i, id, patch) {
      var s = d.slides[i]; if (!s) return null; var k = -1; s.elements.forEach(function (e, x) { if (e.id === id) k = x; }); if (k < 0) return null;
      var n = copy(d), merged = Object.assign({}, n.slides[i].elements[k]);
      ["x", "y", "w", "h", "size", "fill", "text"].forEach(function (f) { if (has(patch, f)) merged[f] = patch[f]; });
      var fixed = fixEl(merged); n.slides[i].elements[k] = fixed; return n;
    },
    deleteElement: function (d, i, id) { var s = d.slides[i]; if (!s) return null; var n = copy(d), l = n.slides[i].elements.length; n.slides[i].elements = n.slides[i].elements.filter(function (e) { return e.id !== id; }); return n.slides[i].elements.length === l ? null : n; },
    setTheme: function (d, name) { if (!has(THEMES, name) || d.theme === name) return null; var n = copy(d); n.theme = name; return n; }
  };
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
  function describe(deck, i) {
    return deck.slides[i].elements.map(function (e, k) {
      var kind = e.type === "text" ? "Text" : e.type === "rect" ? "Rectangle" : "Ellipse";
      return (k + 1) + ". " + kind + (e.text ? ": " + e.text.slice(0, 40) : "");
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
  var api = { W: W, H: H, THEMES: THEMES, FILLS: FILLS, MAX_SLIDES: MAX_SLIDES, MAX_EL: MAX_EL, newDeck: newDeck, validate: validate, ops: ops, color: color, textColor: textColor, onFill: onFill, contrast: contrast, loadDeck: loadDeck, describe: describe, History: History, copy: copy };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSlides = api;
})(typeof self !== "undefined" ? self : this);
