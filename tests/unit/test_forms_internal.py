"""Salow Forms, internal first slice: private owner creation, edit, preview and own-response flow.
Collection (publish and submit) is OFF by default (FORMS_COLLECTION_APPROVED False); tests that exercise the response flow
turn it on with the settings fixture to pin today's working defaults. Those defaults (owner sees all results, audience member
sees only their own answers, one response per person) are PROPOSED, not owner-approved. Not an authorization proof.
Real TiDB runner, one machine, one run; test client without CSRF."""

import json

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import Client

from apps.accounts.models import CustomUser
from apps.forms import services as fs
from apps.forms.models import Answer, Form, Response

HOST = "app.salowtech.com"
EXCLUDED = {"accounts.authevent", "accounts.loginattempt", "core.ratelimitbucket", "core.ratelimitrecord", "sessions.session",
            "certificates.certificateverificationlog", "face.faceverificationlog"}  # fmt: skip


def _u(eid):
    return CustomUser.objects.create(employee_id=eid, full_name=f"{eid} P", email=f"{eid.lower()}@salowtech.com",
                                     role="employee", status="active", is_active=True)  # fmt: skip


def _snap():
    s = {}
    for m in django_apps.get_models():
        if m._meta.proxy or m._meta.label_lower in EXCLUDED:
            continue
        for r in m.objects.all().values():
            r.pop("last_login", None)
            r.pop("last_activity", None)
            s[(m._meta.label_lower, str(r.get("id")))] = repr(sorted(r.items()))
    return s


def _client(u, n):
    c = Client(HTTP_HOST=HOST, REMOTE_ADDR=f"10.4.0.{n}")
    c.force_login(u)
    return c


@pytest.fixture
def w(db):
    o, a, x = _u("FM-O"), _u("FM-A"), _u("FM-X")
    f = fs.create_form(o, "Lunch poll", "desc")
    fs.add_question(o, f.public_id, "short_text", "Name", True)
    fs.add_question(o, f.public_id, "single_choice", "Meal", False, ["Rice", "Roti"])
    fs.add_question(o, f.public_id, "number", "Guests", False)
    fs.set_audience(o, f.public_id, ["fm-a"])
    return {"o": o, "a": a, "x": x, "f": Form.objects.get(pk=f.pk)}


def _qs(f):
    return {q.text: str(q.pk) for q in f.questions.all()}


def test_collection_is_off_by_default(w):
    o, a, f = w["o"], w["a"], w["f"]
    before = _snap()
    with pytest.raises(ValidationError) as e:
        fs.publish(o, f.public_id)
    assert "switched off" in str(e.value)
    r = _client(o, 1).post(f"/forms/{f.public_id}/", {"do": "publish"})
    assert r.status_code == 302 and Form.objects.get(pk=f.pk).status == "draft"  # the owner is sent back to the editor
    assert _changed(before, _snap()) == []
    for st in ("draft", "open", "closed"):  # an audience member sees nothing of the form while collection is off
        Form.objects.filter(pk=f.pk).update(status=st)
        ca = _client(a, 11)
        for path in ("", "fill/", "preview/", "results/", "results.csv"):
            r = ca.get(f"/forms/{f.public_id}/{path}")
            assert r.status_code == 404 and "Lunch poll" not in r.content.decode(), (st, path)
    Form.objects.filter(pk=f.pk).update(status="open")  # even a form forced open takes no answers while collection is off
    before = _snap()
    with pytest.raises(Http404):
        fs.submit_response(a, f.public_id, {_qs(f)["Name"]: "x"})
    assert _client(a, 2).post(f"/forms/{f.public_id}/fill/", {f"q{_qs(f)['Name']}": "x"}).status_code == 404
    assert Response.objects.count() == 0 and Answer.objects.count() == 0
    assert [k for k in _changed(before, _snap()) if not k[0].startswith("core.")] == []


def _changed(b, a):
    return sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))


def test_owner_preview_stores_nothing_and_outsider_gets_404(w):
    o, x, f = w["o"], w["x"], w["f"]
    q = _qs(f)
    before = _snap()
    c = _client(o, 3)
    ok = c.post(f"/forms/{f.public_id}/preview/", {f"q{q['Name']}": "Asha", f"q{q['Meal']}": "Rice", f"q{q['Guests']}": "3"})
    assert ok.status_code == 200 and "Nothing was saved" in ok.content.decode() and "would be accepted" in ok.content.decode()
    bad = c.post(f"/forms/{f.public_id}/preview/", {f"q{q['Name']}": "", f"q{q['Meal']}": "Pizza"})
    assert "Question 1 needs an answer." in bad.content.decode() and "would be accepted" not in bad.content.decode()
    assert [k for k in _changed(before, _snap()) if not k[0].startswith("core.")] == []
    cx = _client(x, 4)
    assert cx.get(f"/forms/{f.public_id}/preview/").status_code == 404
    assert cx.get(f"/forms/{f.public_id}/").status_code == 404
    assert cx.get(f"/forms/{f.public_id}/results/").status_code == 404
    assert cx.get(f"/forms/{f.public_id}/results.csv").status_code == 404


def test_draft_editing_swap_remove_and_freeze(w, settings):
    o, f = w["o"], w["f"]
    ids = [q.pk for q in f.questions.order_by("position")]
    fs.change_question(o, f.public_id, ids[0], "down")
    assert [q.pk for q in f.questions.order_by("position")] == [ids[1], ids[0], ids[2]]
    fs.change_question(o, f.public_id, ids[1], "up")  # already first: no change
    fs.change_question(o, f.public_id, ids[1], "remove")
    assert [(q.pk, q.position) for q in f.questions.order_by("position")] == [(ids[0], 1), (ids[2], 2)]
    with pytest.raises(Http404):
        fs.change_question(o, f.public_id, 999999, "remove")
    settings.FORMS_COLLECTION_APPROVED = True
    fs.publish(o, f.public_id)
    for call in (lambda: fs.add_question(o, f.public_id, "date", "When"), lambda: fs.set_audience(o, f.public_id, ["FM-X"]),
                 lambda: fs.update_details(o, f.public_id, "t", ""), lambda: fs.change_question(o, f.public_id, ids[0], "remove")):
        with pytest.raises(ValidationError) as e:
            call()
        assert "Only a draft form can be changed." in str(e.value)
    assert f.audience.count() == 1 and f.questions.count() == 2


def test_response_flow_with_collection_on(w, settings):
    settings.FORMS_COLLECTION_APPROVED = True
    o, a, x, f = w["o"], w["a"], w["x"], w["f"]
    fs.publish(o, f.public_id)
    q = _qs(f)
    payload = {q["Name"]: "Asha", q["Meal"]: "Roti", q["Guests"]: "007.50"}
    before = _snap()
    with pytest.raises(Http404):
        fs.submit_response(x, f.public_id, payload)  # outsider: same payload as the audience control below
    assert _changed(before, _snap()) == []
    with pytest.raises(Http404):
        fs.submit_response(o, f.public_id, payload)  # the owner is not on the audience
    with pytest.raises(ValidationError) as e:
        fs.submit_response(a, f.public_id, {**payload, "999999": "x"})
    assert "The form has changed" in str(e.value)
    with pytest.raises(ValidationError) as e:
        fs.submit_response(a, f.public_id, {**payload, q["Meal"]: "Pizza"})
    assert "Pick one of the choices." in str(e.value)
    assert Response.objects.count() == 0
    fs.submit_response(a, f.public_id, payload)
    stored = {x_.question.text: x_.value for x_ in Answer.objects.all()}
    assert stored == {"Name": "Asha", "Meal": "Roti", "Guests": "7.5"}
    with pytest.raises(ValidationError) as e:
        fs.submit_response(a, f.public_id, payload)
    assert "already answered" in str(e.value) and Response.objects.count() == 1
    ca, cx, co = _client(a, 5), _client(x, 6), _client(o, 7)
    assert "Asha" in ca.get(f"/forms/{f.public_id}/fill/").content.decode()
    assert cx.get(f"/forms/{f.public_id}/fill/").status_code == 404
    assert ca.get(f"/forms/{f.public_id}/results/").status_code == 404  # a respondent never sees results
    assert "Asha" in co.get(f"/forms/{f.public_id}/results/").content.decode()
    fs.close(o, f.public_id)
    with pytest.raises(ValidationError):
        fs.submit_response(a, f.public_id, payload)  # closed (and already answered)


def test_closed_form_takes_no_answer_from_new_audience_member(w, settings):
    settings.FORMS_COLLECTION_APPROVED = True
    o, a, f = w["o"], w["a"], w["f"]
    fs.publish(o, f.public_id)
    fs.close(o, f.public_id)
    with pytest.raises(Http404):
        fs.submit_response(a, f.public_id, {_qs(f)["Name"]: "x"})  # closed: the policy no longer lets the audience fill
    assert Response.objects.count() == 0


def test_answer_normalisation_and_csv_safety(w, settings):
    o, f = w["o"], w["f"]
    fs.add_question(o, f.public_id, "date", "When")
    fs.add_question(o, f.public_id, "scale_1_5", "Rate")
    fs.add_question(o, f.public_id, "multi_choice", "Sides", False, ["a", "b", "c"])
    qs = list(f.questions.order_by("position"))
    by = {q.text: str(q.pk) for q in qs}
    good = fs.clean_answers(qs, {by["Name"]: " =Asha ", by["Guests"]: "-0", by["When"]: "2026-12-03", by["Rate"]: "5",
                                 by["Sides"]: ["c", "a"]})  # fmt: skip
    assert good[by["Guests"]] == "0" and good[by["Sides"]] == json.dumps(["a", "c"]) and good[by["Name"]] == "=Asha"
    for field, bad in (("Guests", "1e5"), ("Guests", "１２"), ("When", "2026-13-01"), ("Rate", "6"), ("Sides", ["a", "a"]), ("Name", "x\x00y")):
        with pytest.raises(ValidationError):
            fs.clean_answers(qs, {by["Name"]: "ok", by[field]: bad})
    assert [fs.csv_safe(c) for c in ("=1+1", "+x", "-x", "@x", "\tx", "plain", "")] == ["'=1+1", "'+x", "'-x", "'@x", "'\tx", "plain", ""]
    settings.FORMS_COLLECTION_APPROVED = True
    a = w["a"]
    fs.publish(o, f.public_id)
    fs.submit_response(a, f.public_id, {by["Name"]: "=HYPERLINK(1)", by["Guests"]: "-2", by["Sides"]: ["b", "a"],
                                        by["When"]: "2026-12-03", by["Rate"]: "4"})  # fmt: skip
    text = fs.csv_text(o, f.public_id)
    assert "'=HYPERLINK(1)" in text and ",=HYPERLINK" not in text
    assert ",-2," in text and "'-2" not in text and "2026-12-03" in text and "'2026" not in text  # validated numbers keep their minus
    assert "a, b" in text and "[" not in text.split("\n", 1)[1]
    assert "a, b" in _client(o, 9).get(f"/forms/{f.public_id}/results/").content.decode()
    c = _client(o, 8).get(f"/forms/{f.public_id}/results.csv")
    assert c.status_code == 200 and c["Content-Type"].startswith("text/csv") and c["X-Content-Type-Options"] == "nosniff"


def test_visibility_matrix_owner_self_answer_and_blank_optional(w, settings):
    o, a, x, f = w["o"], w["a"], w["x"], w["f"]
    q = _qs(f)
    ca = _client(a, 12)
    settings.FORMS_COLLECTION_APPROVED = True
    assert ca.get(f"/forms/{f.public_id}/fill/").status_code == 404  # draft, flag on: still hidden
    fs.publish(o, f.public_id)
    page = ca.get(f"/forms/{f.public_id}/fill/")
    assert page.status_code == 200 and "sees your name and your answers" in page.content.decode()
    assert _client(x, 13).get(f"/forms/{f.public_id}/fill/").status_code == 404
    fs.submit_response(a, f.public_id, {q["Name"]: "A", q["Meal"]: "   ", q["Guests"]: "  "})  # optional blanks are skipped
    assert {x_.question.text for x_ in Answer.objects.all()} == {"Name"}
    fs.close(o, f.public_id)
    assert ca.get(f"/forms/{f.public_id}/fill/").status_code == 200  # a closed form shows a respondent only their own answers
    assert _client(x, 14).get(f"/forms/{f.public_id}/fill/").status_code == 404
    with pytest.raises(ValidationError) as e:
        fs.set_audience(o, f.public_id, ["FM-O"])
    assert "Answering your own form is not enabled" in str(e.value)
    co = _client(o, 15)
    r = co.get(f"/forms/{f.public_id}/fill/")
    assert r.status_code == 302 and r["Location"].endswith("/preview/")
    f2 = fs.create_form(o, "Second")
    with pytest.raises(ValidationError) as e:
        fs.set_audience(o, f2.public_id, ["FM-O"])
    assert "not enabled" in str(e.value)


def test_forms_not_served_on_the_iam_host(w):
    f = w["f"]
    for path in ("/forms/", f"/forms/{f.public_id}/", f"/forms/{f.public_id}/preview/"):
        c = Client(HTTP_HOST="iam.salowtech.com", REMOTE_ADDR="10.4.0.50")
        c.force_login(w["o"])
        assert c.get(path).status_code == 404, path
