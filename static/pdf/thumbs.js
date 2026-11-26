// Page thumbnails strip: pure helpers. The strip draws only the thumbnails near what is on screen and lets go of far ones.
(function (root) {
  "use strict";
  var MAX = 400, KEEP = 8, AHEAD = 3;
  // Indexes (0 based) of the items to have drawn: those in view plus AHEAD on each side. Empty for a strip with no width.
  function want(scrollLeft, viewW, itemW, count) {
    if (!(itemW > 0) || !(viewW > 0) || count < 1) return [0, -1];
    var first = Math.max(0, Math.floor(scrollLeft / itemW) - AHEAD), last = Math.min(count - 1, Math.ceil((scrollLeft + viewW) / itemW) + AHEAD - 1);
    return last < first ? [0, -1] : [first, last];
  }
  // Whether a drawn item should be let go of: further than KEEP items outside the wanted range.
  function drop(i, range) { return i < range[0] - KEEP || i > range[1] + KEEP; }
  var api = { MAX: MAX, KEEP: KEEP, AHEAD: AHEAD, want: want, drop: drop };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowPdfThumbs = api;
})(typeof self !== "undefined" ? self : this);
