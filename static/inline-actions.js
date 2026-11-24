// Replaces inline event handlers so the CSP can forbid them (script-src-attr 'none').
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-action],[data-copy],[data-confirm]') : null;
    if (!el) return;
    if (el.hasAttribute('data-confirm') && el.tagName !== 'FORM' && !window.confirm(el.getAttribute('data-confirm'))) {
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (el.getAttribute('data-action') === 'print') window.print();
    if (el.hasAttribute('data-copy')) {
      var text = el.getAttribute('data-copy');
      if (el.hasAttribute('data-copy-strip-space')) text = text.replace(/\s+/g, '');
      var label = el.getAttribute('data-copied-label') || 'Copied!';
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { el.textContent = label; });
    }
  }, true);
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f && f.tagName === 'FORM' && f.hasAttribute('data-confirm') && !window.confirm(f.getAttribute('data-confirm'))) {
      e.preventDefault();
    }
  }, true);
})();
