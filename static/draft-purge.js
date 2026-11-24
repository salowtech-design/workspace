// Runs on the sign-in page: drop any saved form drafts so a different person on a shared browser
// can never see a previous user's drafts.
(function () {
  try {
    Object.keys(sessionStorage).forEach(function (k) {
      if (k.indexOf('_salow_draft_') === 0 || k === '_salow_minimized_at') sessionStorage.removeItem(k);
    });
  } catch (e) {}
})();
