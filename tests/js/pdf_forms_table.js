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
  const by = Object.assign(Object.create(null), Object.fromEntries(info.fields.map((x) => [x.name, x])));
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
  // Exact identifiers: two names that share their first 200 characters stay distinct, and the display label is the only shortened part.
  const pre = "p".repeat(210), twin = await (async () => { const d = await P.PDFDocument.create(), pg = d.addPage([300, 300]), f = d.getForm(); f.createTextField(pre + "A").addToPage(pg, { x: 10, y: 10, width: 50, height: 15 }); f.createTextField(pre + "B").addToPage(pg, { x: 10, y: 40, width: 50, height: 15 }); return d.save({ useObjectStreams: false }); })();
  const ti = await F.list(P, twin, E.looksSigned);
  ok("long names keep their full identifier and a shortened label", ti.fields.length === 2 && ti.fields[0].name.length === 211 && ti.fields[0].label.length === 200 && ti.fields[0].name !== ti.fields[1].name);
  const tw2 = await F.fill(P, twin, Object.assign(Object.create(null), { [pre + "B"]: "second" })), tl = await F.list(P, tw2.bytes, E.looksSigned);
  ok("filling one of two names with the same first 200 characters changes only that field", tl.fields.find((x) => x.name === pre + "B").value === "second" && tl.fields.find((x) => x.name === pre + "A").value === "");
  ok("a shortened label does not match a field", (await code({ [pre.slice(0, 200)]: "x" }, twin)) === "missing");
  // Names that are also object keys.
  const odd = await (async () => { const d = await P.PDFDocument.create(), pg = d.addPage([300, 300]), f = d.getForm(); ["__proto__", "constructor", "toString", "hasOwnProperty"].forEach((nm, i) => f.createTextField(nm).addToPage(pg, { x: 10, y: 10 + 30 * i, width: 50, height: 15 })); return d.save({ useObjectStreams: false }); })();
  const ov = Object.create(null); ov["__proto__"] = "a"; ov["constructor"] = "b"; ov["toString"] = "c"; ov["hasOwnProperty"] = "d";
  const of = await F.fill(P, odd, ov), ol = await F.list(P, of.bytes, E.looksSigned), om = new Map(ol.fields.map((x) => [x.name, x.value]));
  ok("fields named like object keys are filled and read back exactly", om.get("__proto__") === "a" && om.get("constructor") === "b" && om.get("toString") === "c" && om.get("hasOwnProperty") === "d" && ({}).polluted === undefined && Object.getPrototypeOf({}) === Object.prototype);
  // Untouched fields are not redrawn: a marker appearance on an untouched field survives a fill of another field.
  const mk = await makeForm(async (d, pg, f) => { const w = f.getTextField("code").acroField.getWidgets()[0], st = d.context.stream("q 1 0 0 rg 0 0 5 5 re f Q % marker"); const ap = d.context.obj({ N: d.context.register(st) }); w.dict.set(P.PDFName.of("AP"), ap); f.getTextField("code").acroField.dict.set(P.PDFName.of("V"), P.PDFHexString.fromText("\u0905")); });
  const apBytes = async (b) => { const dd = await P.PDFDocument.load(b), w = dd.getForm().getTextField("code").acroField.getWidgets()[0], n = w.dict.lookup(P.PDFName.of("AP")).lookup(P.PDFName.of("N")); return Buffer.from(n.getContents()).toString("latin1"); };
  const mf = await F.fill(P, mk, { full_name: "x" });
  ok("an untouched field keeps its own appearance and value (nothing redrawn)", (await apBytes(mf.bytes)) === (await apBytes(mk)) && (await F.list(P, mf.bytes, E.looksSigned)).fields.find((x) => x.name === "code").value === "\u0905");
  ok("control: filling that same field does redraw its appearance", (await apBytes((await F.fill(P, mk, { code: "ab" })).bytes)) !== (await apBytes(mk)));
  // Output cap.
  const big = await (async () => { const d = await P.PDFDocument.create(), pg = d.addPage([100, 100]); d.getForm().createTextField("t").addToPage(pg, { x: 1, y: 1, width: 50, height: 15 }); d.context.register(d.context.stream(require("crypto").randomBytes(F.MAX_BYTES + 1000))); return d.save({ useObjectStreams: false }); })();
  ok("a filled copy over the size limit is refused", (await code({ t: "x" }, big)) === "size");
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
  ok("filling an XFA form is refused, not written without the XFA data", (await code({ full_name: "x" }, xf)) === "xfa");
  ok("a form without XFA is not refused", (await code({ full_name: "x" })) === "none");
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
