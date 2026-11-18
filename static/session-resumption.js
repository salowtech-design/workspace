// Workspace Resumption & Minimize Policy (Section 10)
// Return <= 30s: resume UI context without prompt.
// Return > 30s: require fresh sign-in and restore permitted task.
(function() {
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
})();
