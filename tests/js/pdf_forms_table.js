const F = require("../../static/pdf/forms.js"), E = require("../../static/pdf/export.js"), P = require("../../static/pdf/vendor/pdf-lib.min.js"), crypto = require("crypto");
let n = 0, bad = 0;
function ok(name, c) { n++; if (!c) { bad++; console.log("FAIL " + name); } }
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
async function makeForm(extra) {
  const d = await P.PDFDocument.create(), pg = d.addPage([400, 500]), f = d.getForm();
  const t = f.createTextField("full_name"); t.addToPage(pg, { x: 20, y: 440, width: 200, height: 20 });
  const lim = f.createTextField("code"); lim.setMaxLength(4); lim.addToPage(pg, { x: 20, y: 410, width: 80, height: 20 });
  const ml = f.createTextField("notes"); ml.enableMultiline(); ml.addToPage(pg, { x: 20, y: 340, width: 200, height: 60 });
  const ro = f.createTextField("fixed"); ro.setText("locked"); ro.enableReadOnly(); ro.addToPage(pg, { x: 20, y: 310, width: 100, height: 20 });
  const c = f.createCheckBox("agree"); c.addToPage(pg, { x: 20, y: 280, width: 15, height: 15 });
  const r = f.createRadioGroup("size"); r.addOptionToPage("small", pg, { x: 20, y: 250, width: 15, height: 15 }); r.addOptionToPage("large", pg, { x: 60, y: 250, width: 15, height: 15 });
  const dd = f.createDropdown("country"); dd.addOptions(["India", "France"]); dd.addToPage(pg, { x: 20, y: 220, width: 120, height: 20 });
  const ol = f.createOptionList("tags"); ol.addOptions(["a", "b", "c"]); ol.enableMultiselect(); ol.addToPage(pg, { x: 20, y: 150, width: 100, height: 60 });
  const b = f.createButton("go"); b.addToPage("Go", pg, { x: 200, y: 280, width: 40, height: 20 });
  if (extra) await extra(d, pg, f);
  return d.save({ useObjectStreams: false });
}
const done = (async () => {
  const bytes = await makeForm(), before = sha(bytes);
  const info = await F.list(P, bytes, E.looksSigned);
  const by = Object.fromEntries(info.fields.map((x) => [x.name, x]));
  ok("every supported field type is listed with its kind", ["full_name:text", "code:text", "notes:text", "fixed:text", "agree:check", "size:radio", "country:dropdown", "tags:list"].every((s) => by[s.split(":")[0]] && by[s.split(":")[0]].kind === s.split(":")[1]));
  ok("a push button is listed as not supported, not as a field", info.skipped.some((s) => s.name === "go") && !by.go);
  ok("text limit, multiline and read only are carried", by.code.max === 4 && by.notes.multiline === true && by.fixed.readOnly === true && by.fixed.value === "locked");
  ok("choices are carried", by.size.options.join() === "small,large" && by.country.options.join() === "India,France" && by.tags.multi === true);
  ok("listing does not change the input bytes and an unsigned file is not flagged", sha(bytes) === before && info.signed === false && info.xfa === false);
  const res = await F.fill(P, bytes, { full_name: "Akif Hussain", agree: true, size: "large", country: "France", tags: ["a", "c"], notes: "line1\nline2" });
  ok("input bytes are untouched by a fill", sha(bytes) === before);
  const back = await F.list(P, res.bytes, E.looksSigned), bb = Object.fromEntries(back.fields.map((x) => [x.name, x]));
  ok("filled values read back", bb.full_name.value === "Akif Hussain" && bb.agree.value === true && bb.size.value === "large" && bb.country.value.join() === "France" && bb.tags.value.join() === "a,c" && bb.notes.value === "line1\nline2");
  ok("fields that were not named keep their values", bb.fixed.value === "locked" && bb.code.value === "" && back.fields.length === info.fields.length && res.filled === 6);
  const pdf2 = await P.PDFDocument.load(res.bytes), tf = pdf2.getForm().getTextField("full_name");
  ok("the text field widget has a normal appearance (so a viewer that ignores values still shows it)", tf.acroField.getWidgets()[0].getNormalAppearance() !== undefined);
  ok("a filled multi-line box gets a fixed readable font size, not the automatic one", /11\s+Tf/.test(String(pdf2.getForm().getTextField("notes").acroField.getDefaultAppearance())));
  const clr = await F.fill(P, res.bytes, { agree: false, size: "", country: "", full_name: "" });
  const cb = Object.fromEntries((await F.list(P, clr.bytes, E.looksSigned)).fields.map((x) => [x.name, x]));
  ok("a check box, a choice and a text can be cleared", cb.agree.value === false && cb.size.value === "" && cb.country.value.length === 0 && cb.full_name.value === "");
  const code = async (v, b) => { try { await F.fill(P, b || bytes, v); return "none"; } catch (e) { return e.code || "other"; } };
  ok("text over the field limit is refused (value)", (await code({ code: "12345" })) === "value");
  ok("a choice that is not offered is refused", (await code({ size: "medium" })) === "value" && (await code({ country: "Mars" })) === "value");
  ok("a read only field is refused", (await code({ fixed: "x" })) === "readonly");
  ok("a push button cannot be filled", (await code({ go: "x" })) === "kind");
  ok("an unknown field is refused", (await code({ nope: "x" })) === "missing");
  ok("text the form font cannot draw is refused as a value, not written broken", (await code({ full_name: "\u0905\u0915\u0940\u092b" })) === "value");
  ok("damaged input gets the damaged code", (await code({ full_name: "x" }, new Uint8Array([1, 2, 3]))) === "damaged");
  // no form at all
  const plain = await P.PDFDocument.create(); plain.addPage([100, 100]);
  const pi = await F.list(P, await plain.save(), E.looksSigned);
  ok("a file with no form lists nothing", pi.fields.length === 0 && pi.skipped.length === 0);
  // signed file
  const sg = await makeForm(async (d, pg, f) => { const sigDict = d.context.obj({ Type: "Sig", Filter: "Adobe.PPKLite" }); const field = d.context.obj({ FT: "Sig", T: P.PDFHexString.fromText("sig"), V: d.context.register(sigDict), Kids: [] }); f.acroForm.dict.get(P.PDFName.of("Fields")).push(d.context.register(field)); });
  const si = await F.list(P, sg, E.looksSigned);
  ok("a file with a filled signature field is flagged signed and the other fields stay listed", si.signed === true && si.fields.length === info.fields.length);
  // XFA flag
  const xfBase = Buffer.from(await makeForm()).toString("latin1"); const xf = Buffer.from(xfBase.replace("/Fields", "/XFA [(template) (<xdp/>)] /Fields"), "latin1");
  ok("an XFA form is flagged", (await F.list(P, xf, E.looksSigned)).xfa === true);
  // embedded actions are carried, not run: JavaScript in a field stays in the file and nothing here evaluates it
  const js = await makeForm(async (d, pg, f) => { f.getTextField("full_name").acroField.dict.set(P.PDFName.of("AA"), d.context.obj({ K: { S: "JavaScript", JS: P.PDFString.of("globalThis.__ran = 1") } })); });
  globalThis.__ran = 0; const jr = await F.fill(P, js, { full_name: "x" });
  ok("a field script is not run by listing or filling", globalThis.__ran === 0 && (await F.list(P, js, E.looksSigned)).fields.length === info.fields.length && jr.filled === 1);
  const many = await P.PDFDocument.create(), mp = many.addPage([100, 100]); for (let i = 0; i < F.MAX_FIELDS + 5; i++) many.getForm().createTextField("f" + i).addToPage(mp, { x: 1, y: 1, width: 10, height: 10 });
  const mi = await F.list(P, await many.save(), E.looksSigned);
  ok("a form with too many fields lists the first ones and says so", mi.fields.length === F.MAX_FIELDS && mi.truncated === true && mi.total === F.MAX_FIELDS + 5);
  console.log(`${n - bad}/${n} pdf forms rows passed`);
  process.exit(bad ? 1 : 0);
})();
done.catch((e) => { console.log("CRASH", e); process.exit(1); });
