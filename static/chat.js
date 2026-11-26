(function () {
  var list = document.getElementById("chat-list");
  if (!list || !list.dataset.poll) return;
  var status = document.getElementById("chat-status");
  var failures = 0, busy = false, timer = null, stopped = false, slow = 0, notBefore = 0;
  var EVERY = 10000, SLOW_FIRST = 20000, SLOW_MAX = 120000;
  function say(t) { if (status) status.textContent = t; }
  function stop(t) { stopped = true; clearTimeout(timer); say(t); }
  function later(ms) { clearTimeout(timer); if (!stopped) timer = setTimeout(tick, ms); }
  // 429 means too many polls from this person in a minute (for example many open windows). It is not a failure of the page:
  // wait longer (the server's Retry-After when it sends one, else 20 s doubling to 2 minutes), say so, and go back to
  // the normal pace after the next answer. It never counts toward the five failures that pause live updates.
  function slowDown(r) {
    var ra = parseInt(r.headers.get("Retry-After"), 10);
    slow = slow ? Math.min(slow * 2, SLOW_MAX) : SLOW_FIRST;
    var wait = ra > 0 ? Math.min(ra * 1000, SLOW_MAX) : slow;
    notBefore = Date.now() + wait;
    say("Updates are slower for a moment because Chat is open in many places. Trying again in " + Math.round(wait / 1000) + " seconds.");
    return wait;
  }
  function tick() {
    if (stopped || document.visibilityState !== "visible" || busy) { later(EVERY); return; } // hidden tabs neither fetch nor read
    if (Date.now() < notBefore) { later(notBefore - Date.now()); return; }
    busy = true;
    fetch(list.dataset.poll + "?after=" + encodeURIComponent(list.dataset.last), {
      credentials: "same-origin", redirect: "manual", headers: { Accept: "text/html" }
    }).then(function (r) {
      if (r.status === 429) { later(slowDown(r)); return null; }
      if (r.status === 401 || r.type === "opaqueredirect") { stop("Your session has ended. Reload the page to sign in again."); return null; }
      if (r.status === 404) { stop("This conversation is no longer available to you."); return null; }
      if (!r.ok) { failures += 1; if (failures >= 5) stop("Live updates paused. Reload the page to continue."); return null; }
      failures = 0;
      if (slow) { slow = 0; notBefore = 0; say(""); }
      var last = r.headers.get("X-Chat-Last"), more = r.headers.get("X-Chat-More") === "1";
      return r.text().then(function (html) {
        if (html.trim()) { list.insertAdjacentHTML("beforeend", html); list.scrollTop = list.scrollHeight; }
        if (last) list.dataset.last = last;
        if (more) say("More new messages. Reload to see them.");
      });
    }).catch(function () {
      failures += 1; if (failures >= 5) stop("Live updates paused. Reload the page to continue.");
    }).then(function () { busy = false; if (!stopped && Date.now() >= notBefore) later(EVERY); });
  }
  later(EVERY);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible" && Date.now() >= notBefore) tick(); });
})();
