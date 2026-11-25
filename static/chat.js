(function () {
  var list = document.getElementById("chat-list");
  if (!list || !list.dataset.poll) return;
  var status = document.getElementById("chat-status");
  var failures = 0, busy = false, timer = null;
  function say(t) { if (status) status.textContent = t; }
  function stop(t) { clearInterval(timer); say(t); }
  function tick() {
    if (document.visibilityState !== "visible" || busy) return; // hidden tabs neither fetch nor read
    busy = true;
    fetch(list.dataset.poll + "?after=" + encodeURIComponent(list.dataset.last), {
      credentials: "same-origin", redirect: "manual", headers: { Accept: "text/html" }
    }).then(function (r) {
      if (r.status === 401 || r.type === "opaqueredirect") { stop("Your session has ended. Reload the page to sign in again."); return null; }
      if (r.status === 404) { stop("This conversation is no longer available to you."); return null; }
      if (!r.ok) { failures += 1; if (failures >= 5) stop("Live updates paused. Reload the page to continue."); return null; }
      failures = 0;
      var last = r.headers.get("X-Chat-Last"), more = r.headers.get("X-Chat-More") === "1";
      return r.text().then(function (html) {
        if (html.trim()) { list.insertAdjacentHTML("beforeend", html); list.scrollTop = list.scrollHeight; }
        if (last) list.dataset.last = last;
        if (more) say("More new messages. Reload to see them.");
      });
    }).catch(function () {
      failures += 1; if (failures >= 5) stop("Live updates paused. Reload the page to continue.");
    }).then(function () { busy = false; });
  }
  timer = setInterval(tick, 10000);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible") tick(); });
})();
