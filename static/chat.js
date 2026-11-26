(function () {
  var list = document.getElementById("chat-list");
  if (!list || !list.dataset.poll) return;
  var status = document.getElementById("chat-status");
  var failures = 0, busy = false, timer = null, stopped = false, slow = 0, notBefore = 0;
  var EVERY = 10000, SLOW_FIRST = 20000, SLOW_MAX = 120000;
  function say(t) { if (status) status.textContent = t; }
  function stop(t) { stopped = true; clearTimeout(timer); say(t); }
  function later(ms) { clearTimeout(timer); if (!stopped) timer = setTimeout(tick, Math.min(ms, 2147483647)); } // a longer wait is rechecked by tick() against notBefore, never shortened
  // 429 means the server is rate limiting this person's polls. It is not a failure of the page. When the server sends Retry-After the
  // page waits at least that long (never earlier, with no upper limit). Without it the wait is 20 s doubling to 2 minutes. The notice says the limit is
  // active and has no countdown (nothing here ticks). The next good answer returns to the normal pace and clears it. It never counts
  // toward the five failures that pause live updates.
  function slowDown(r) {
    var raw = r.headers.get("Retry-After"), ra = parseInt(raw, 10), wait = 0;
    if (/^\s*\d+\s*$/.test(raw || "")) wait = ra * 1000; // seconds, no cap
    else if (raw) { var d = Date.parse(raw) - Date.now(); if (d > 0) wait = d; } // an HTTP date
    slow = slow ? Math.min(slow * 2, SLOW_MAX) : SLOW_FIRST;
    // With no usable Retry-After the wait is the fallback, spread by up to 25 percent so tabs do not retry together. A server
    // instruction is only ever lengthened (by up to 10 percent, same reason), never shortened, with no upper limit.
    wait = wait ? Math.round(wait * (1 + Math.random() * 0.1)) : Math.round(slow * (1 + Math.random() * 0.25));
    notBefore = Date.now() + wait;
    say("Chat is being rate limited right now, so updates are slower. It will try again by itself.");
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
