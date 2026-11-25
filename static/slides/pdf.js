/* Salow Slides PDF: builds a PDF from the deck, in the browser. Nothing leaves the device.
 * One 960 x 540 point page (13.33 x 7.5 in, 16:9) per slide. Rectangles, ellipses and text are drawn as vector shapes and
 * text in Helvetica (a standard PDF font, so the file embeds no font). Line breaks use the same estimate as the screen
 * (SalowSlides.wrapLines), so they match the editor, not a measurement of Helvetica. Speaker notes are not written.
 * Characters Helvetica's encoding cannot draw are refused before any file is made: nothing is dropped silently. */
(function (root) {
  "use strict";
  function rgbOf(L, hex) { return L.rgb(parseInt(hex.substr(1, 2), 16) / 255, parseInt(hex.substr(3, 2), 16) / 255, parseInt(hex.substr(5, 2), 16) / 255); }
  // Every item with text the font cannot encode: [{ slide, item, chars }] (1-based), chars as a de-duplicated list.
  function unsupported(font, deck) {
    // encodeText quietly turns an unknown character into "?", so membership in the font's own character set is the test.
    var bad = [], set = Object.create(null);
    font.getCharacterSet().forEach(function (cp) { set[cp] = true; });
    function can(ch) { return set[ch.codePointAt(0)] === true; }
    deck.slides.forEach(function (s, i) {
      s.elements.forEach(function (e, j) {
        if (!e.text) return;
        var seen = [];
        Array.from(e.text).forEach(function (ch) { if (!/\s/.test(ch) && !can(ch) && seen.indexOf(ch) < 0) seen.push(ch); });
        if (seen.length) bad.push({ slide: i + 1, item: j + 1, chars: seen });
      });
    });
    return bad;
  }
  function refusal(bad) {
    var parts = bad.slice(0, 5).map(function (b) { return "slide " + b.slide + " item " + b.item + " (" + b.chars.slice(0, 6).join(" ") + (b.chars.length > 6 ? " ..." : "") + ")"; });
    return "The PDF fonts cannot draw some characters, so no PDF was made: " + parts.join(", ") + (bad.length > 5 ? ", and " + (bad.length - 5) + " more items" : "") + ". Change or remove them and try again.";
  }
  // L is the pdf-lib global, S the SalowSlides model. Returns Promise<Uint8Array>, or rejects with an Error whose code is "chars".
  function build(L, S, deck) {
    return L.PDFDocument.create().then(function (doc) {
      return doc.embedFont(L.StandardFonts.Helvetica).then(function (font) {
        var bad = unsupported(font, deck);
        if (bad.length) { var err = new Error(refusal(bad)); err.code = "chars"; err.items = bad; throw err; }
        var th = S.THEMES[deck.theme], W = S.W, H = S.H;
        doc.setTitle("Salow Slides deck"); doc.setProducer("Salow Slides"); doc.setCreator("Salow Slides");
        deck.slides.forEach(function (s) {
          var pg = doc.addPage([W, H]);
          pg.drawRectangle({ x: 0, y: 0, width: W, height: H, color: rgbOf(L, th.bg) });
          s.elements.forEach(function (e) {
            var c = S.color(deck, e);
            if (e.type === "rect") pg.drawRectangle({ x: e.x, y: H - e.y - e.h, width: e.w, height: e.h, color: rgbOf(L, c) });
            else if (e.type === "ellipse") pg.drawEllipse({ x: e.x + e.w / 2, y: H - e.y - e.h / 2, xScale: e.w / 2, yScale: e.h / 2, color: rgbOf(L, c) });
            if (!e.text) return;
            var shape = e.type !== "text", lines = S.wrapLines(e.text, e.size, shape ? e.w - 8 : e.w), lh = e.size * 1.2;
            var top = shape ? e.y + e.h / 2 - (lines.length * lh) / 2 : e.y, col = rgbOf(L, S.textColor(deck, e));
            lines.forEach(function (ln, k) {
              var x = shape ? e.x + e.w / 2 - font.widthOfTextAtSize(ln, e.size) / 2 : e.x;
              pg.drawText(ln, { x: x, y: H - (top + e.size + k * lh), size: e.size, font: font, color: col });
            });
          });
        });
        return doc.save();
      });
    });
  }
  var api = { build: build, unsupported: unsupported, refusal: refusal };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowSlidesPdf = api;
})(typeof self !== "undefined" ? self : this);
