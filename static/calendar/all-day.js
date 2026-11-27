// All day box: the start and end boxes become plain date boxes (no meaningless times) and go back when it is unticked.
// Without script the date and time boxes stay and the server ignores the time part of an all-day event.
(function () {
  var box = document.getElementById("cal-all-day");
  var s = document.getElementById("cal-start"), e = document.getElementById("cal-end");
  if (!box || !s || !e) return;
  var sl = document.getElementById("cal-start-label"), el = document.getElementById("cal-end-label");
  function apply() {
    var day = box.checked;
    [s, e].forEach(function (f) {
      var v = f.value;
      f.type = day ? "date" : "datetime-local";
      if (day) f.value = v ? v.slice(0, 10) : "";
      else f.value = v ? v.slice(0, 10) + "T09:00" : "";
    });
    sl.textContent = day ? "First day" : "Starts";
    el.textContent = day ? "Last day" : "Ends";
  }
  box.addEventListener("change", apply);
})();
