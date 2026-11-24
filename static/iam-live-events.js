// Live IAM action feed: polls once a second with a cursor, shows each event once.
(function () {
  var box = document.getElementById("iam-live-events");
  if (!box) return;
  var body = document.getElementById("iam-live-events-body");
  var status = document.getElementById("iam-live-events-status");
  var url = box.getAttribute("data-url");
  var cursor = null;
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
    tr.className = "border-b border-gray-800/60 text-xs font-mono";
    tr.appendChild(cell(new Date(e.time).toLocaleTimeString()));
    tr.appendChild(cell(e.domain.toUpperCase()));
    tr.appendChild(cell(e.actor || "-"));
    tr.appendChild(cell(e.event));
    tr.appendChild(cell(e.subject || e.reference || "-"));
    tr.appendChild(cell(e.message));
    tr.setAttribute("data-event-id", String(e.id));
    body.insertBefore(tr, body.firstChild);
    while (body.children.length > 50) body.removeChild(body.lastChild);
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
        cursor = d.cursor;
        status.textContent = "Live, updated " + new Date().toLocaleTimeString();
        setTimeout(poll, 1000);
      })
      .catch(function (err) {
        if (err.message === "auth") return;
        status.textContent = "Reconnecting...";
        setTimeout(poll, 3000);
      });
  }
  poll();
})();
