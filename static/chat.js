(function () {
  var list = document.getElementById("chat-list");
  if (!list) return;
  var status = document.getElementById("chat-status");
  var pressing = 0, gen = 0, pressId = 0, painted = {}; // gen changes at the start and end of every press; painted[seq] is the newest press answer shown for that message
  // Reactions: a press is sent in place (the compose box and the scroll position are untouched). The page asks for the
  // wanted state (the opposite of what it shows), so a stale tab cannot flip a reaction back. Without this script the form posts normally.
  function paint(seq, reacts) {
    var msg = list.querySelector("#m" + seq);
    if (!msg) return;
    reacts.forEach(function (r) {
      var b = msg.querySelector('.chat-react-btn[data-kind="' + r[0] + '"]');
      if (!b) return;
      var n = r[1], mine = !!r[2], label = b.dataset.label;
      b.classList.toggle("on", mine);
      b.setAttribute("aria-pressed", mine ? "true" : "false");
      b.setAttribute("aria-label", label + (n ? ", " + n : "") + (mine ? ", you reacted" : ""));
      var c = b.querySelector(".chat-react-n");
      if (n && !c) { c = document.createElement("span"); c.className = "chat-react-n"; c.setAttribute("aria-hidden", "true"); b.appendChild(c); }
      if (c) { if (n) c.textContent = String(n); else c.remove(); }
    });
  }
  function say(t) { if (status) status.textContent = t; }
  list.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest(".chat-react-btn");
    if (!b || !b.form || b.disabled || !window.fetch || !window.FormData) return;
    ev.preventDefault();
    var d = new FormData(b.form);
    d.set("kind", b.dataset.kind);
    d.set("want", b.getAttribute("aria-pressed") === "true" ? "0" : "1");
    var mine = ++pressId; b.disabled = true; pressing += 1; gen += 1;
    fetch(b.form.action, { method: "POST", body: d, credentials: "same-origin", redirect: "manual", headers: { "X-Chat-Fetch": "1", Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 429) { say("Too many reactions in a minute. Wait and try again."); return null; }
        if (r.status === 404) { say("This conversation is no longer available to you."); return null; }
        if (!r.ok) { say(r.type === "opaqueredirect" || r.status === 401 || r.status === 403 ? "Your session has ended. Reload the page to sign in again." : "The reaction was not saved."); return null; }
        return r.json().then(function (j) {
          say("");
          if ((painted[j.seq] || 0) > mine) return; // a newer press on this message was already answered: this answer is older
          painted[j.seq] = mine; paint(j.seq, j.reacts);
        });
      })
      .catch(function () { say("The reaction was not saved."); })
      .then(function () { b.disabled = false; pressing -= 1; gen += 1; });
  });
  if (!list.dataset.poll) return;
  var failures = 0, busy = false, timer = null, stopped = false, slow = 0, notBefore = 0;
  var EVERY = 10000, SLOW_FIRST = 20000, SLOW_MAX = 120000;
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
  // Peers' reactions on messages already on screen: one small JSON call per poll, skipped while one of my own presses is in flight.
  function refresh() {
    var first = parseInt(list.dataset.first, 10), last = parseInt(list.dataset.last, 10);
    if (!list.dataset.reactions || !first || !last || pressing) return null;
    first = Math.max(first, last - 199); // the server covers at most 200 messages: the newest 200 on screen
    var g = gen;
    return fetch(list.dataset.reactions + "?first=" + first + "&last=" + last, { credentials: "same-origin", redirect: "manual", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 429) { later(slowDown(r)); return null; }
        if (!r.ok) return null;
        return r.json().then(function (j) {
          if (pressing || g !== gen) return; // a press started or finished while this was in flight: its answer may be older than the page
          Object.keys(j.reacts).forEach(function (k) { paint(k, j.reacts[k]); });
        });
      })
      .catch(function () { return null; }); // a failed refresh never counts toward pausing the message poll
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
        return refresh();
      });
    }).catch(function () {
      failures += 1; if (failures >= 5) stop("Live updates paused. Reload the page to continue.");
    }).then(function () { busy = false; if (!stopped && Date.now() >= notBefore) later(EVERY); });
  }
  later(EVERY);
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "visible" && Date.now() >= notBefore) tick(); });
})();

// Mentions: a button inserts "@EMPLOYEEID " at the cursor of the message box. Without this script the person types the ID.
(function () {
  var box = document.getElementById("chat-mentions");
  var ta = document.querySelector(".chat-compose textarea[name=body]");
  if (!box || !ta) return;
  box.addEventListener("click", function (ev) {
    var b = ev.target.closest && ev.target.closest(".chat-mention-btn");
    if (!b) return;
    var t = b.getAttribute("data-mention") + " ", a = ta.selectionStart, z = ta.selectionEnd;
    if (typeof a !== "number") { a = z = ta.value.length; }
    var pre = ta.value.slice(0, a), need = pre && !/\s$/.test(pre) ? " " : "";
    ta.value = pre + need + t + ta.value.slice(z);
    var pos = (pre + need + t).length;
    ta.focus();
    ta.setSelectionRange(pos, pos);
  });
})();
