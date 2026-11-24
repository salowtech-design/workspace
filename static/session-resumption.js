// Workspace Resumption & Visibility Preservation Policy (Section 10 & Defect 8)
// Return <= 30s: resume UI context seamlessly without prompt.
// Return > 30s: require fresh authentication without saving any form content.
(function() {
  'use strict';

  var DRAFT_PREFIX = '_salow_draft_';
  var SENSITIVE = /pass|pwd|pin|code|token|otp|secret|recovery|csrf|biometric|face|key|totp|card|ssn/i;

  // No form content is ever written to browser storage. Removing any draft left by older versions.
  function purgeLegacyDrafts() {
    try {
      Object.keys(sessionStorage).forEach(function (k) {
        if (k.indexOf(DRAFT_PREFIX) === 0) sessionStorage.removeItem(k);
      });
    } catch (e) {}
  }

  document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'hidden') {
      sessionStorage.setItem('_salow_minimized_at', Date.now().toString());
    } else if (document.visibilityState === 'visible') {
      var minimizedAt = sessionStorage.getItem('_salow_minimized_at');
      if (minimizedAt) {
        var elapsed = Date.now() - parseInt(minimizedAt, 10);
        sessionStorage.removeItem('_salow_minimized_at');
        if (elapsed > 30000) {
          var nextRoute = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.href = '/auth/login/?flow=signin&next=' + nextRoute;
        }
      }
    }
  });

  purgeLegacyDrafts();
})();
