// Live IAM action feed. Push (SSE) when the server supports it, else a one-second cursor poll.
// Each event is shown once (deduplicated by id).
(function () {
  var box = document.getElementById("iam-live-events");
  if (!box) return;
  var body = document.getElementById("iam-live-events-body");
  var status = document.getElementById("iam-live-events-status");
  var url = box.getAttribute("data-url");
  var streamUrl = box.getAttribute("data-stream-url");
  var cursor = null;
  var ready = false; // false while the first backlog is shown: only later events refresh the tab
  var mode = "polling";
  var seen = {};
  function cell(text, cls) {
    var td = document.createElement("td");
    td.className = cls || "px-4 py-2";
    td.textContent = text; // textContent only: event text is never parsed as HTML
    return td;
  }
  function render(e) {
    if (seen[e.id]) return;
    seen[e.id] = true;
    var tr = document.createElement("tr");
    tr.className = "iam-live-row";
    tr.appendChild(cell(new Date(e.time).toLocaleTimeString()));
    tr.appendChild(cell(e.domain.toUpperCase()));
    if (e.actor_state === "claimed" && e.claimed_identity) {
      tr.appendChild(cell("unverified claim: " + e.claimed_identity, "px-4 py-2 iam-actor-claimed"));
    } else {
      tr.appendChild(cell(e.actor || "-"));
    }
    tr.appendChild(cell(e.event));
    tr.appendChild(cell(e.subject || e.reference || "-"));
    tr.appendChild(cell(e.message));
    tr.setAttribute("data-event-id", String(e.id));
    tr.setAttribute("data-received-ms", String(Date.now()));
    tr.setAttribute("data-event-time", e.time);
    tr.setAttribute("data-mode", mode);
    if (e.id > (cursor || 0)) cursor = e.id;
    if (ready) scheduleRegionRefresh();
    body.insertBefore(tr, body.firstChild);
    while (body.children.length > 50) body.removeChild(body.lastChild);
  }
  // Refresh the current tab's own data (tables, counts, queues) when a new event arrives.
  // Authorization is the normal page GET (session, role and step-up are checked by the server);
  // the X-Live-Refresh header marks it as a background read that does not extend IAM idle time.
  var refreshTimer = null, lastRefresh = 0, refreshes = 0;
  function regionBusy(region) {
    var a = document.activeElement;
    if (a && region.contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) return true;
    var fields = region.querySelectorAll("input:not([type=hidden]):not([type=submit]), textarea");
    for (var i = 0; i < fields.length; i++) { if (fields[i].value && fields[i].value !== fields[i].defaultValue) return true; }
    return false;
  }
  function scheduleRegionRefresh() {
    var region = document.getElementById("iam-tab-live-region");
    if (!region || refreshTimer) return;
    var wait = Math.max(400, 2000 - (Date.now() - lastRefresh));
    refreshTimer = setTimeout(function () {
      refreshTimer = null;
      if (regionBusy(region)) { scheduleRegionRefresh(); return; }
      lastRefresh = Date.now();
      fetch(location.href, { credentials: "same-origin", headers: { "X-Live-Refresh": "1" } })
        .then(function (r) { if (r.redirected || !r.ok) throw new Error("auth"); return r.text(); })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          var fresh = doc.getElementById("iam-tab-live-region");
          if (!fresh || regionBusy(region)) return;
          region.innerHTML = fresh.innerHTML;
          refreshes += 1;
          region.setAttribute("data-live-refreshes", String(refreshes));
          region.setAttribute("data-live-refreshed-ms", String(Date.now()));
        })
        .catch(function () { /* signed out or offline: the status line already says so */ });
    }, wait);
  }
  function poll() {
    var q = cursor === null ? "?limit=20" : "?after=" + cursor;
    fetch(url + q, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 401 || r.redirected) { status.textContent = "Signed out. Reload to sign in again."; throw new Error("auth"); }
        return r.json();
      })
      .then(function (d) {
        d.events.forEach(render);
        ready = true;
        cursor = d.cursor;
        status.textContent = "Live (polling), updated " + new Date().toLocaleTimeString();
        setTimeout(poll, 1000);
      })
      .catch(function (err) {
        if (err.message === "auth") return;
        status.textContent = "Reconnecting...";
        setTimeout(poll, 3000);
      });
  }
  function startPolling() {
    mode = "polling";
    poll();
  }
  function startPush() {
    if (!streamUrl || !window.EventSource) return startPolling();
    var failures = 0;
    var es = new EventSource(streamUrl + (cursor ? "?after=" + cursor : ""));
    es.onopen = function () {
      failures = 0;
      setTimeout(function () { ready = true; }, 1500); // after the replayed backlog
      mode = "push";
      status.textContent = "Live (push), connected " + new Date().toLocaleTimeString();
    };
    es.addEventListener("audit", function (m) {
      try { render(JSON.parse(m.data)); } catch (x) { /* ignore a malformed frame */ }
      status.textContent = "Live (push), last event " + new Date().toLocaleTimeString();
    });
    es.addEventListener("end", function (m) {
      es.close();
      var reason = "";
      try { reason = JSON.parse(m.data).reason; } catch (x) { reason = ""; }
      if (reason === "reconnect") return startPush();
      status.textContent = "Stream closed (" + reason + "). Reload to sign in again.";
    });
    es.onerror = function () {
      failures += 1;
      // 404 (no ASGI), 401, 503 or repeated drops: EventSource gives no status, so after
      // two failures close it and use the poll, which reports the sign-out state itself.
      if (failures >= 2 || es.readyState === 2) {
        es.close();
        startPolling();
      } else {
        status.textContent = "Reconnecting...";
      }
    };
  }
  startPush();
})();
