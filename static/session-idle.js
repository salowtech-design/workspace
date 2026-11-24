// Workspace idle timeout warning. The server decides when to sign out; this script only
// shows how much time is left and lets the user stay signed in. Status polling does not
// count as activity on the server. Clicks and key presses send a keep-alive at most once a minute.
(function () {
  'use strict';
  var root = document.getElementById('idle-timeout-root');
  if (!root) return;
  var statusUrl = root.getAttribute('data-status-url');
  var keepaliveUrl = root.getAttribute('data-keepalive-url');
  var loginUrl = root.getAttribute('data-login-url');
  var banner = document.getElementById('idle-timeout-banner');
  var countdown = document.getElementById('idle-timeout-countdown');
  var stay = document.getElementById('idle-timeout-stay');
  var remaining = null, warnAt = 120, lastSync = 0, lastKeepalive = Date.now();

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var i = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return i ? i.value : '';
  }
  function snapshotDrafts() {
    try {
      var data = {};
      document.querySelectorAll('form input:not([type="password"]):not([type="hidden"]), form textarea, form select').forEach(function (el) {
        if (el.name && el.value) data[el.name] = el.value;
      });
      if (Object.keys(data).length) sessionStorage.setItem('_salow_draft_' + location.pathname, JSON.stringify(data));
    } catch (e) {}
  }
  function signOut() {
    snapshotDrafts();
    location.href = loginUrl + '?reason=idle&next=' + encodeURIComponent(location.pathname + location.search);
  }
  function show(secs) {
    var m = Math.floor(secs / 60), s = secs % 60;
    countdown.textContent = m + ':' + (s < 10 ? '0' : '') + s;
    banner.style.display = 'flex';
  }
  function render() {
    if (remaining === null) return;
    if (remaining <= 0) return signOut();
    if (remaining <= warnAt) show(remaining); else banner.style.display = 'none';
  }
  function sync() {
    fetch(statusUrl, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { if (r.status === 401 || r.redirected) { signOut(); return null; } return r.json(); })
      .then(function (d) { if (!d) return; remaining = d.remaining_seconds; warnAt = d.warning_seconds; lastSync = Date.now(); render(); })
      .catch(function () {});
  }
  function keepalive() {
    lastKeepalive = Date.now();
    fetch(keepaliveUrl, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { remaining = d.remaining_seconds; render(); } })
      .catch(function () {});
  }
  stay.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); keepalive(); });
  ['keydown', 'click'].forEach(function (ev) {
    document.addEventListener(ev, function () {
      if (Date.now() - lastKeepalive > 60000 && (remaining === null || remaining > 0)) keepalive();
    }, true);
  });
  setInterval(function () { if (remaining !== null) { remaining -= 1; render(); } }, 1000);
  setInterval(sync, 30000);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') sync(); });
  sync();
})();
