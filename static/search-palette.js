/* Quick search: Ctrl+K or Cmd+K, or the Quick search button. It asks the same /search/ view for JSON, so the
 * permission checks, the rate limit and the result shape are the page's own. Results are drawn with textContent
 * only, and a link is followed only when it is a path on this site. */
(function () {
  "use strict";
  var btn = document.getElementById("palette-open");
  if (!btn) return;
  var dlg = null, input, list, status, items = [], idx = -1, seq = 0, timer = null, ctl = null, opener = null;
  function safe(u) {
    if (typeof u !== "string" || u.charAt(0) !== "/" || u.charAt(1) === "/" || u.charAt(1) === "\\") return false;
    for (var i = 0; i < u.length; i++) { var c = u.charCodeAt(i); if (c <= 32 || c === 127 || u.charAt(i) === "\\") return false; }  // no control characters, spaces or backslashes anywhere
    try { var r = new URL(u, window.location.origin); return r.origin === window.location.origin; } catch (e) { return false; }
  }
  function build() {
    dlg = document.createElement("div"); dlg.id = "palette"; dlg.setAttribute("role", "dialog"); dlg.setAttribute("aria-modal", "true"); dlg.setAttribute("aria-label", "Quick search"); dlg.hidden = true;
    var box = document.createElement("div"); box.className = "palette-box";
    input = document.createElement("input"); input.type = "text"; input.maxLength = 100; input.setAttribute("aria-label", "Search your workspace"); input.setAttribute("role", "combobox"); input.setAttribute("aria-controls", "palette-list"); input.setAttribute("aria-expanded", "false"); input.autocomplete = "off";
    status = document.createElement("p"); status.className = "cal-meta"; status.setAttribute("role", "status"); status.textContent = "Type at least 2 characters. Searches Drive, Tasks, Calendar, People, Work Logs and Chat. Sheets, Slides and PDF are not searched.";
    list = document.createElement("ul"); list.id = "palette-list"; list.setAttribute("role", "listbox");
    box.appendChild(input); box.appendChild(status); box.appendChild(list); dlg.appendChild(box); document.body.appendChild(dlg);
    dlg.addEventListener("mousedown", function (e) { if (e.target === dlg) close(); });
    input.addEventListener("input", function () { schedule(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); mark(idx + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); mark(idx - 1); }
      else if (e.key === "Enter") { if (e.isComposing || e.keyCode === 229) return; e.preventDefault(); if (idx >= 0 && items[idx]) go(items[idx].url); }
      else if (e.key === "Tab") { e.preventDefault(); }  // focus stays in the dialog
    });
  }
  function clear() { while (list.firstChild) list.removeChild(list.firstChild); items = []; idx = -1; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
  function mark(i) {
    if (!items.length) return; idx = (i + items.length) % items.length;
    items.forEach(function (it, k) { it.el.setAttribute("aria-selected", k === idx ? "true" : "false"); it.el.className = k === idx ? "palette-on" : ""; });
    input.setAttribute("aria-activedescendant", items[idx].el.id); items[idx].el.scrollIntoView({ block: "nearest" });
  }
  function go(u) { if (safe(u)) { window.location.assign(u); } }
  function draw(groups) {
    clear(); var n = 0;
    groups.forEach(function (g) {
      var h = document.createElement("li"); h.setAttribute("role", "presentation"); h.className = "palette-group"; h.textContent = String(g.name); list.appendChild(h);
      g.items.forEach(function (x) {
        if (!safe(x.url)) return;
        var li = document.createElement("li"); li.setAttribute("role", "option"); li.id = "palette-o" + n; li.setAttribute("aria-selected", "false"); li.textContent = String(x.title);
        (function (u) { li.addEventListener("mousedown", function (e) { e.preventDefault(); go(u); }); })(x.url);
        list.appendChild(li); items.push({ el: li, url: x.url }); n++;
      });
      if (g.more) { var m = document.createElement("li"); m.setAttribute("role", "presentation"); m.className = "palette-more"; m.textContent = "More in " + g.name + ". Narrow your search."; list.appendChild(m); }
    });
    input.setAttribute("aria-expanded", n ? "true" : "false");
    status.textContent = n ? n + (n === 1 ? " result" : " results") + ". Up and Down to choose, Enter to open, Esc to close." : "No results.";
    if (n) mark(0);
  }
  function run() {
    var q = input.value.trim(), my = ++seq;
    if (ctl) { try { ctl.abort(); } catch (e) { /* already done */ } ctl = null; }
    if (q.length < 2) { clear(); status.textContent = "Type at least 2 characters."; return; }
    ctl = (typeof AbortController === "function") ? new AbortController() : null;
    status.textContent = "Searching.";
    fetch("/search/?format=json&q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { "Accept": "application/json" }, signal: ctl ? ctl.signal : undefined })
      .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; }); })
      .then(function (res) {
        if (my !== seq) return;  // a newer search started: this answer is stale
        if (!res.ok || !res.body || !Array.isArray(res.body.groups)) { clear(); status.textContent = res.status === 429 ? "Too many searches. Wait a moment." : "Search did not work. Try again."; return; }
        draw(res.body.groups);
      })
      .catch(function (e) { if (my !== seq || (e && e.name === "AbortError")) return; clear(); status.textContent = "Search did not work. Try again."; });
  }
  function schedule() { if (timer) clearTimeout(timer); timer = setTimeout(run, 400); }
  function open() { if (!dlg) build(); opener = document.activeElement; dlg.hidden = false; input.value = ""; clear(); status.textContent = "Type at least 2 characters. Searches Drive, Tasks, Calendar, People, Work Logs and Chat. Sheets, Slides and PDF are not searched."; input.focus(); }
  function close() { if (!dlg || dlg.hidden) return; seq++; if (timer) clearTimeout(timer); if (ctl) { try { ctl.abort(); } catch (e) { /* done */ } } dlg.hidden = true; var back = (opener && opener !== document.body && opener.focus) ? opener : btn; back.focus(); }
  btn.addEventListener("click", open);
  document.addEventListener("keydown", function (e) { if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === "k" || e.key === "K")) { e.preventDefault(); if (dlg && !dlg.hidden) close(); else open(); } });
})();
