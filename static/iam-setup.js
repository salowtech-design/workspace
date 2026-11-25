// Reads the one-time setup token from the URL fragment (never sent to the server), removes it from the
// address bar and history entry, and posts it once to claim the setup session.
(function () {
  var t = (window.location.hash || "").replace(/^#/, "");
  var msg = document.getElementById("iam-claim-msg");
  if (!t) {
    if (msg) { msg.textContent = "This setup link is incomplete. Use the full link you were given."; }
    return;
  }
  window.history.replaceState(null, "", window.location.pathname);
  document.getElementById("iam-claim-token").value = t;
  document.getElementById("iam-claim-form").submit();
})();
