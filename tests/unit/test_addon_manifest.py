"""ADAPTER LOGIC ONLY: manifest validation in apps/addons/manifest.py (pure code, no database, no network, nothing installed).
The scope and hook registries are empty by design, so the tests that need a name pass their own registry. The webhook URL
check is syntactic only (no DNS) and is never an SSRF defence; a delivery-time resolve check is not built. One machine, one run."""

import pytest

from apps.addons.manifest import ManifestError, validate_manifest

REG = frozenset({"calendar.read"})
HOOKS = frozenset({"task.completed"})


def good(**over):
    m = {"name": "Demo", "version": "1.0.0", "scopes": ["calendar.read"], "hooks": ["task.completed"],
         "cards": [{"title": "Hello", "blocks": [{"type": "text", "text": "Plain words"},
                                                 {"type": "keyvalue", "key": "Owner", "value": "Ops"}, {"type": "divider"}]}],
         "webhook_url": "https://example.org/hook"}  # fmt: skip
    m.update(over)
    return m


def problems(m, **kw):
    with pytest.raises(ManifestError) as e:
        validate_manifest(m, REG, HOOKS, **kw)
    return " | ".join(e.value.problems)


def test_good_manifest_is_accepted_and_empty_registries_refuse_any_scope_or_hook():
    assert validate_manifest(good(), REG, HOOKS)["webhook_url"] == "https://example.org/hook"
    assert "not in the reviewed list" in problems(good(scopes=["calendar.write"]))
    with pytest.raises(ManifestError) as e:  # shipped registries are empty
        validate_manifest(good())
    assert "Scope 'calendar.read' is not in the reviewed list" in str(e.value) and "Hook" in str(e.value)
    assert validate_manifest(good(scopes=[], hooks=[]))["scopes"] == []


@pytest.mark.parametrize("url", ["http://example.org/h", "https://localhost/h", "https://127.0.0.1/h", "https://10.0.0.5/h",
                                 "https://169.254.169.254/latest", "https://[::1]/h", "https://u:p@example.org/h",
                                 "https://example.org:8443/h", "https://intranet/h", "https://svc.internal/h", "ftp://example.org/h",
                                 "https://127.1/h", "https://2130706433/h", "https://0x7f000001/h", "https://0177.0.0.1/h", "https://017700000001/h",
                                 "https://localhost./h", "https://foo.internal./h", "https://[::ffff:127.0.0.1]/h",
                                 "https://exa mple.org/h", "https://example.org/h\n", "https://example.org\\@evil.test/", "https://example.org/%0d%0a",
                                 "https://10.1/h", "https://1.2.3.4.5/h"])
def test_webhook_urls_that_are_not_plain_public_https_are_refused(url):
    assert "webhook_url" in problems(good(webhook_url=url))


def test_letter_only_and_hex_letter_host_names_are_not_mistaken_for_numbers():
    for host in ("cafe.be", "dad.be", "facade.cc", "face1.be", "a.example.org"):
        assert validate_manifest(good(webhook_url=f"https://{host}/h"), REG, HOOKS)["webhook_url"].endswith(f"{host}/h"), host


def test_text_length_is_checked_after_normalisation():
    grown = "\u0958" * 300  # one character each, two after NFC: 600 > 500
    assert "longer than 500" in problems(good(cards=[{"title": "T", "blocks": [{"type": "text", "text": grown}]}]))


def test_a_trailing_dot_host_is_normalised_for_the_public_name_check():
    assert validate_manifest(good(webhook_url="https://example.org./hook"), REG, HOOKS)["webhook_url"] == "https://example.org./hook"


def test_wrong_types_never_crash_and_unicode_format_characters_are_refused():
    for bad in ([], {}, 5, None, ["text"]):
        problems(good(cards=[{"title": "T", "blocks": [{"type": bad, "text": "x"}]}]))
        problems(good(scopes=[bad]))
    problems(good(**{"cards": [{"title": "T", "blocks": [["x"]]}]}))
    with pytest.raises(ManifestError):
        validate_manifest({1: "a", "name": "n"}, REG, HOOKS)  # a non-text key must not crash the unknown-field report
    for ch in ("\u202e", "\u200b", "\u2028", "\ufeff", "\ue000", "\ud800"):
        assert "direction-changing" in problems(good(cards=[{"title": "T", "blocks": [{"type": "text", "text": f"a{ch}b"}]}])), repr(ch)
    assert validate_manifest(good(name="Caf\u00e9 \u65e5\u672c"), REG, HOOKS)["name"] == "Caf\u00e9 \u65e5\u672c"


def test_card_blocks_are_declarative_plain_text_only():
    for bad in ({"type": "html", "text": "x"}, {"type": "text", "text": "<script>alert(1)</script>"},
                {"type": "text", "text": "x", "onclick": "go()"}, {"type": "text", "text": "a\x00b"}, {"type": "text", "text": "x" * 501}):  # fmt: skip
        assert problems(good(cards=[{"title": "T", "blocks": [bad]}]))
    assert "Unknown manifest field 'script'" in problems(good(script="alert(1)"))
    assert "version" in problems(good(version="1.0"))
    assert "must be an object" in problems("not a dict")
