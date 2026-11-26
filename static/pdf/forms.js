// Lists and fills the standard form fields (AcroForm) of an open PDF and builds a separate filled copy (browser only).
// The original bytes are never changed and nothing is stored. Nothing inside the file is run: no scripts, no actions, no submit.
(function (root) {
  "use strict";
  var MAX_BYTES = 20 * 1024 * 1024, MAX_FIELDS = 300, MAX_NAME = 200, MAX_OPTIONS = 200;
  function fail(code, msg) { var e = new Error(msg); e.code = code; return e; }
  function kindOf(L, f) {
    if (f instanceof L.PDFTextField) return "text";
    if (f instanceof L.PDFCheckBox) return "check";
    if (f instanceof L.PDFRadioGroup) return "radio";
    if (f instanceof L.PDFDropdown) return "dropdown";
    if (f instanceof L.PDFOptionList) return "list";
    if (f instanceof L.PDFSignature) return "signature";
    if (f instanceof L.PDFButton) return "button";
    return "other";
  }
  function hasXfa(L, pdf) {
    try { var af = pdf.catalog.lookup(L.PDFName.of("AcroForm")); return af instanceof L.PDFDict && af.get(L.PDFName.of("XFA")) !== undefined; } catch (e) { return false; }
  }
  // pdf-lib drops XFA data on load unless told to keep it. Listing keeps it (to see it); filling lets it go, so the copy is a plain form that agrees with the values filled.
  function load(L, bytes, keepXfa) {
    return L.PDFDocument.load(bytes, { ignoreEncryption: false, updateMetadata: false, preserveXFA: !!keepXfa }).catch(function (e) { throw fail(/encrypt/i.test(String(e && e.message)) ? "encrypted" : "damaged", "could not read"); });
  }
  // What the file has: fields this page can fill, fields it lists but cannot fill, and flags for the person.
  function list(L, bytes, looksSigned) {
    return load(L, bytes, true).then(function (pdf) {
      var xfa = hasXfa(L, pdf), form = pdf.getForm(), all = form.getFields(), out = { fields: [], skipped: [], truncated: false, signed: looksSigned(L, pdf, bytes), xfa: xfa, total: all.length };
      all.slice(0, MAX_FIELDS).forEach(function (f) {
        var full = String(f.getName()), name = full.slice(0, MAX_NAME), k = kindOf(L, f);
        try {
          if (k === "signature" || k === "button" || k === "other") { out.skipped.push({ name: name, kind: k }); return; }
          var o = { name: full, label: name, kind: k, readOnly: !!f.isReadOnly(), required: !!f.isRequired() };
          if (k === "text") { o.value = f.getText() || ""; o.max = f.getMaxLength() || 0; o.multiline = !!f.isMultiline(); o.password = !!f.isPassword(); if (o.password) { out.skipped.push({ name: name, kind: "password text" }); return; } }
          else if (k === "check") o.value = !!f.isChecked();
          else {
            var opts = f.getOptions(); if (opts.length > MAX_OPTIONS) { out.skipped.push({ name: name, kind: k + " with too many choices" }); return; }
            o.options = opts.map(String);
            if (k === "radio") o.value = f.getSelected() || "";
            else { var s = f.getSelected(); o.value = Array.isArray(s) ? s.map(String) : []; o.multi = !!f.isMultiselect(); }
          }
          out.fields.push(o);
        } catch (e) { out.skipped.push({ name: name, kind: "unreadable " + k }); }
      });
      if (all.length > MAX_FIELDS) out.truncated = true;
      return out;
    });
  }
  // values: a null-prototype map of full field name to value for the fields the person changed. Returns the new file; fields not named are left exactly as they were.
  function fill(L, bytes, values) {
    // XFA data cannot be kept by this writer, and dropping it silently would change the document, so a file that has it is refused.
    return load(L, bytes, true).then(function (pdf) {
      if (hasXfa(L, pdf)) throw fail("xfa", "xfa form");
      var form = pdf.getForm(), done = 0, names = Object.keys(values);
      names.forEach(function (name) {
        var f = form.getFieldMaybe(name), k, v = values[name]; // exact full name only; the shortened label is for display
        if (!f) throw fail("missing", "field not found");
        k = kindOf(L, f);
        if (f.isReadOnly()) throw fail("readonly", "read only");
        try {
          if (k === "radio" || k === "dropdown" || k === "list") {
            var offered = f.getOptions().map(String), want = k === "radio" ? [].concat(v) : [].concat(v), bad = want.some(function (x) { return x !== "" && offered.indexOf(String(x)) < 0; });
            if (bad) throw fail("value", "not offered");
          }
          if (k === "text" && /[^\n\r\t\x20-\x7e\xa0-\xff]/.test(String(v))) throw fail("value", "character the form font cannot draw");
          if (k === "text" && f.isMultiline()) f.setFontSize(11); // pdf-lib's automatic size for a multi-line box draws one huge line
          if (k === "text") f.setText(v === "" ? undefined : String(v));
          else if (k === "check") { if (v) f.check(); else f.uncheck(); }
          else if (k === "radio") { if (v === "") f.clear(); else f.select(String(v)); }
          else if (k === "dropdown" || k === "list") { var a = [].concat(v).map(String).filter(function (x) { return x !== ""; }); if (!a.length) f.clear(); else f.select(a.length === 1 ? a[0] : a); }
          else throw fail("kind", "unsupported");
        } catch (e) { if (e && e.code) throw e; throw fail("value", "value not accepted for " + name); }
        done++;
      });
      if (done) { try { form.updateFieldAppearances(); } catch (e) { throw fail("value", "text could not be drawn"); } }
      return pdf.save({ useObjectStreams: false }).then(function (out) { if (out.length > MAX_BYTES) throw fail("size", "copy too large"); return { bytes: out, filled: done }; });
    });
  }
  var api = { MAX_BYTES: MAX_BYTES, MAX_FIELDS: MAX_FIELDS, list: list, fill: fill };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.PdfForms = api;
})(typeof self !== "undefined" ? self : this);
