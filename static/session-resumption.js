// Workspace Resumption & Visibility Preservation Policy (Section 10 & Defect 8)
// Return <= 30s: resume UI context seamlessly without prompt.
// Return > 30s: require fresh authentication while strictly preserving permitted form drafts and work state.
(function() {
  'use strict';

  var DRAFT_PREFIX = '_salow_draft_';
  var SENSITIVE = /pass|pwd|pin|code|token|otp|secret|recovery|csrf|biometric|face|key|totp|card|ssn/i;

  function snapshotFormData() {
    var data = {};
    var forms = document.querySelectorAll('form');
    forms.forEach(function(form, fIdx) {
      // Only free-text areas are kept. Inputs (employee id, one-time codes, recovery codes, tokens),
      // selects and anything that looks sensitive are never written to browser storage.
      var elements = form.querySelectorAll('textarea');
      elements.forEach(function(el) {
        if (el.name && el.value && !SENSITIVE.test(el.name) && el.autocomplete !== 'one-time-code') {
          data[el.name] = el.value;
        }
      });
    });
    if (Object.keys(data).length > 0) {
      try {
        var key = DRAFT_PREFIX + window.location.pathname;
        sessionStorage.setItem(key, JSON.stringify(data));
      } catch (e) {}
    }
    return data;
  }

  function restoreFormData() {
    try {
      var key = DRAFT_PREFIX + window.location.pathname;
      var raw = sessionStorage.getItem(key);
      if (!raw) return;
      var data = JSON.parse(raw);
      var restored = false;
      Object.keys(data).forEach(function(name) {
        var el = document.querySelector('[name="' + CSS.escape(name) + '"]');
        if (el && !el.value) {
          el.value = data[name];
          restored = true;
        }
      });
      if (restored) {
        sessionStorage.removeItem(key);
      }
    } catch (e) {}
  }

  document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'hidden') {
      sessionStorage.setItem('_salow_minimized_at', Date.now().toString());
      snapshotFormData();
    } else if (document.visibilityState === 'visible') {
      var minimizedAt = sessionStorage.getItem('_salow_minimized_at');
      if (minimizedAt) {
        var elapsed = Date.now() - parseInt(minimizedAt, 10);
        sessionStorage.removeItem('_salow_minimized_at');
        if (elapsed > 30000) {
          snapshotFormData();
          var nextRoute = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.href = '/auth/login/?flow=signin&next=' + nextRoute;
        }
      }
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', restoreFormData);
  } else {
    restoreFormData();
  }
})();
