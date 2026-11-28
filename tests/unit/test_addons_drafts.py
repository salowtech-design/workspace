"""Add-ons private drafts (catalogue, draft editor, view-only review). Not a completed module: no submit, approve, install or
delivery exists and perform() still refuses everyone. Real TiDB runner, one machine, one run, test client without CSRF.
Draft visibility (maker only) is an implementation default, not an approved policy. Not an authorization proof."""

import html
import json

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from apps.accounts.models import CustomUser
from apps.addons import drafts as ds
from apps.addons import services as svc
from apps.addons.models import Addon, AddonInstall, AddonVersion


def _u(eid):
    return CustomUser.objects.create(employee_id=eid, full_name=f"{eid} P", email=f"{eid.lower()}@salowtech.com",
                                     role="employee", status="active", is_active=True)  # fmt: skip


def _c(u, n, host="app.salowtech.com"):
    c = Client(HTTP_HOST=host, REMOTE_ADDR=f"10.5.0.{n}")
    c.force_login(u)
    return c


def _m(**kw):
    base = {"name": "Lunch", "version": "1.0.0", "cards": [{"title": "Menu", "blocks": [{"type": "text", "text": "Rice & dal"}]}]}
    base.update(kw)
    return json.dumps(base)


@pytest.fixture
def w(db):
    return {"o": _u("AD-O"), "x": _u("AD-X")}


def test_maker_flow_and_exact_refusals(w):
    o, x = w["o"], w["x"]
    co = _c(o, 1)
    r = co.post("/addons/", {"name": "Lunch"})
    assert r.status_code == 302
    a = Addon.objects.get()
    assert a.status == "draft" and a.owner_id == o.pk
    bad = {"{nope": "not valid JSON", _m(scopes=["files.read"]): "Scope 'files.read' is not in the reviewed list.",
           _m(name="Other"): "The manifest name must match the add-on name.", _m(version="1"): "version must look like 1.0.0.",
           _m(webhook_url="http://x.example/h"): None}  # fmt: skip
    for text, reason in bad.items():
        before = AddonVersion.objects.count()
        page = co.post(f"/addons/{a.public_id}/", {"manifest": text})
        assert AddonVersion.objects.count() == before == 0
        if reason:
            assert reason in html.unescape(page.content.decode())
    ok = co.post(f"/addons/{a.public_id}/", {"manifest": _m()})
    assert ok.status_code == 302 and AddonVersion.objects.get().review_state == "draft"
    co.post(f"/addons/{a.public_id}/", {"manifest": _m(cards=[])})  # same version number replaces the draft
    assert AddonVersion.objects.count() == 1 and AddonVersion.objects.get().manifest["cards"] == []
    co.post(f"/addons/{a.public_id}/", {"manifest": _m()})
    page = co.get(f"/addons/{a.public_id}/v/1.0.0/").content.decode()
    assert "Rice &amp; dal" in page and "View only" in page and "Submit, approve and install are not available" in page
    card = page.split('class="card frm"', 1)[1].split('</div>', 1)[0]
    assert "<button" not in card and "<form" not in card  # no action control in the review card (the site header has its own)
    assert AddonInstall.objects.count() == 0 and Addon.objects.get().status == "draft"


def test_other_person_and_iam_host_get_404_and_nothing_changes(w):
    o, x = w["o"], w["x"]
    a = ds.create_addon(o, "Lunch")
    ds.save_version(o, a.public_id, _m())
    cx = _c(x, 2)
    snap = (list(AddonVersion.objects.values()), list(Addon.objects.values()))
    for path in ("", "v/1.0.0/"):
        r = cx.get(f"/addons/{a.public_id}/{path}")
        assert r.status_code == 404 and "Lunch" not in r.content.decode()
    assert cx.post(f"/addons/{a.public_id}/", {"manifest": _m(cards=[])}).status_code == 404
    assert (list(AddonVersion.objects.values()), list(Addon.objects.values())) == snap
    assert "Lunch" not in cx.get("/addons/").content.decode()
    co = _c(o, 3)  # control: the maker's same payload works
    assert co.post(f"/addons/{a.public_id}/", {"manifest": _m(cards=[])}).status_code == 302
    for path in ("/addons/", f"/addons/{a.public_id}/"):
        assert _c(o, 4, "iam.salowtech.com").get(path).status_code == 404


def test_limits_and_deny_all_entry_point_still_refuses(w):
    o = w["o"]
    for i in range(ds.MAX_ADDONS):
        ds.create_addon(o, f"A{i}")
    with pytest.raises(ValidationError):
        ds.create_addon(o, "one more")
    a = Addon.objects.first()
    with pytest.raises(ValidationError):
        ds.save_version(o, a.public_id, "x" * (ds.MANIFEST_TEXT_MAX + 1))
    v = AddonVersion.objects.create(addon=a, version="1.0.0", manifest={})
    for act in svc.ACTIONS:
        with pytest.raises(PermissionDenied):
            svc.perform(o, act, v)
    v.refresh_from_db()
    assert v.review_state == "draft"


def test_deep_nesting_and_name_rules_align(w):
    o = w["o"]
    a = ds.create_addon(o, "Lunch")
    deep = "[" * 10000 + "]" * 10000  # exactly the 20000 character limit; CPython 3.12.16 raises RecursionError for it
    assert len(deep) == ds.MANIFEST_TEXT_MAX
    with pytest.raises(ValidationError) as e:
        ds.save_version(o, a.public_id, deep)
    msg = " ".join(e.value.messages)
    assert msg == "The manifest is nested too deeply."
    assert AddonVersion.objects.count() == 0
    for bad in ("A<b", "x\u200by", "  ", "", "n" * 81):  # names the manifest check refuses are refused at creation too
        with pytest.raises(ValidationError):
            ds.create_addon(o, bad)
    assert Addon.objects.count() == 1
    nm = "Cafe\u0301"  # decomposed e-acute: stored composed, and the same name then saves through the manifest check
    b = ds.create_addon(o, nm)
    assert b.name == "Caf\u00e9"
    ds.save_version(o, b.public_id, json.dumps({"name": nm, "version": "1.0.0"}))
    assert AddonVersion.objects.filter(addon=b).count() == 1


def test_editor_reloads_the_saved_manifest_not_the_starter(w):
    o = w["o"]
    co = _c(o, 20)
    a = ds.create_addon(o, "Lunch")
    url = f"/addons/{a.public_id}/"
    assert "A plain text card." in co.get(url).content.decode()  # nothing saved: the starter
    mine = _m(cards=[{"title": "Mine", "blocks": [{"type": "text", "text": "Saved words"}]}])
    assert co.post(url, {"manifest": mine}).status_code == 302
    for page in (co.get(url), co.get(url + "?v=1.0.0")):
        body = html.unescape(page.content.decode())
        assert "Saved words" in body and "A plain text card." not in body
    co.post(url, {"manifest": _m(version="1.1.0", cards=[])})
    assert "Saved words" in html.unescape(co.get(url + "?v=1.0.0").content.decode())  # the chosen version, not the newest
    assert '"version": "1.1.0"' in html.unescape(co.get(url).content.decode())
    r = co.post(url, {"manifest": "{bad"})  # failed input stays in the box, saved rows unchanged
    assert "{bad" in r.content.decode() and AddonVersion.objects.get(version="1.0.0").manifest["cards"][0]["title"] == "Mine"
    assert co.post(url, {"manifest": _m()}).status_code == 302  # the same version again replaces it, from text the user sees
    assert AddonVersion.objects.filter(addon=a).count() == 2
