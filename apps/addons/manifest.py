"""Add-on manifest validation (Add-ons step 1, pure code: no models, no network, no delivery, no install).

Blueprint 9.11: a manifest holds name, version, scopes, hooks, cards and a webhook URL; scopes are checked by can() and may
not exceed the installer's rights; cards are a declarative format so an add-on cannot inject script.
What this module decides, and what it does not:
  - SCOPE_REGISTRY and HOOK_REGISTRY are EMPTY. The list of scopes and events an add-on may name is a product and security
    choice that has not been made, so today any manifest that names a scope or a hook is refused. Callers (tests) may pass
    their own registry; no setting or environment variable can.
  - Cards allow only plain text, key/value and divider blocks: the card FORMAT has no HTML, link, script or image block, and
    stored text refuses < and > and control, format (including bidi and zero-width), surrogate, private-use, unassigned and
    line or paragraph separator characters. The text can still contain things that look like links or Markdown. The future
    renderer MUST escape it as plain text, with no Markdown and no auto-linking; that renderer does not exist yet.
  - The webhook URL check is SYNTACTIC only: https, a host name, no credentials, no IP literal in a private, loopback or
    link-local range. A host name can still resolve to an internal address, so a delivery-time resolve-and-pin check is
    still required and is NOT built here. Nothing in this module sends a request.
Validation returns the cleaned manifest or raises ManifestError with every problem found."""

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

SCOPE_REGISTRY: frozenset = frozenset()
HOOK_REGISTRY: frozenset = frozenset()

NAME_MAX = 80
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")  # build default: plain three-part version, at most 20 chars
MAX_SCOPES = 20
MAX_HOOKS = 20
MAX_CARDS = 10
MAX_BLOCKS = 20
TEXT_MAX = 500
URL_MAX = 300
TOP_KEYS = {"name", "version", "scopes", "hooks", "cards", "webhook_url"}
BLOCK_KEYS = {"text": {"type", "text"}, "keyvalue": {"type", "key", "value"}, "divider": {"type"}}


class ManifestError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _plain(value, limit, label, problems, allow_empty=False):
    if not isinstance(value, str):
        problems.append(f"{label} must be text.")
        return ""
    value = unicodedata.normalize("NFC", value).strip()  # normalise FIRST: some characters grow (U+0958), so length is checked after
    if not value and not allow_empty:
        problems.append(f"{label} must not be empty.")
    if len(value) > limit:
        problems.append(f"{label} is longer than {limit} characters.")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") for c in value):
        problems.append(f"{label} contains control, invisible or direction-changing characters.")
    if re.search(r"[<>]", value):
        problems.append(f"{label} must be plain text (no markup).")
    return value


def _url_problem(url):
    """Syntactic check only. It is never an SSRF defence: a public-looking name can still resolve to an internal address.

    Implementation limits, not user policy: percent-encoding, backslashes and spaces are refused outright (simpler than
    decoding), and the numeric-host and private-suffix rules are heuristics. Real delivery will additionally need DNS
    resolve-and-pin, validation of every redirect, and awareness of NAT64 and other embedded-IPv4 address forms; none of
    that exists here."""
    if not isinstance(url, str) or len(url) > URL_MAX or not url.isascii():
        return "webhook_url must be ASCII text within the length limit."
    if any(ord(c) <= 32 or ord(c) == 127 or c in "\\%" for c in url):
        return "webhook_url must not contain spaces, control characters, backslashes or percent signs."
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return "webhook_url is not a valid URL."
    if parts.scheme != "https":
        return "webhook_url must use https."
    if not host or parts.username or parts.password:
        return "webhook_url needs a host name and no credentials."
    if port not in (None, 443):
        return "webhook_url may only use the default https port."
    host = host.lower().rstrip(".")  # a trailing dot names the same host
    if not host or ".." in host:
        return "webhook_url host is not valid."
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            return "webhook_url must not point at a private, loopback or link-local address."
        return None
    labels = host.split(".")
    if labels[-1].isdigit() or labels[-1].startswith("0x"):  # old inet_aton forms end in a numeric label; names like cafe.be do not
        return "webhook_url host looks like a number (short, hex or octal address forms are refused)."
    if len(labels) < 2 or host == "localhost" or labels[-1] in ("local", "internal", "localhost", "lan", "home", "corp"):
        return "webhook_url host is not a public host name."
    return None


def validate_manifest(raw, scope_registry=SCOPE_REGISTRY, hook_registry=HOOK_REGISTRY):
    problems = []
    if not isinstance(raw, dict):
        raise ManifestError(["The manifest must be an object."])
    for k in sorted(map(repr, set(raw) - TOP_KEYS)):
        problems.append(f"Unknown manifest field {k}.")
    out = {"name": _plain(raw.get("name"), NAME_MAX, "name", problems)}
    version = raw.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        problems.append("version must look like 1.0.0.")
    out["version"] = version if isinstance(version, str) else ""
    for key, registry, cap in (("scopes", scope_registry, MAX_SCOPES), ("hooks", hook_registry, MAX_HOOKS)):
        items = raw.get(key, [])
        if not isinstance(items, list) or len(items) > cap or not all(isinstance(i, str) for i in items):
            problems.append(f"{key} must be a list of at most {cap} names.")
            items = []
        for i in items:
            if i not in registry:
                problems.append(f"{key[:-1].capitalize()} {i!r} is not in the reviewed list.")
        if len(set(items)) != len(items):
            problems.append(f"{key} has duplicates.")
        out[key] = list(items)
    cards = raw.get("cards", [])
    out["cards"] = []
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        problems.append(f"cards must be a list of at most {MAX_CARDS}.")
        cards = []
    for ci, card in enumerate(cards):
        if not isinstance(card, dict) or set(card) - {"title", "blocks"}:
            problems.append(f"Card {ci + 1} must have only a title and blocks.")
            continue
        blocks = card.get("blocks", [])
        if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
            problems.append(f"Card {ci + 1} blocks must be a list of at most {MAX_BLOCKS}.")
            blocks = []
        clean_blocks = []
        for bi, b in enumerate(blocks):
            label = f"Card {ci + 1} block {bi + 1}"
            if not isinstance(b, dict) or not isinstance(b.get("type"), str) or b["type"] not in BLOCK_KEYS or set(b) - BLOCK_KEYS[b["type"]]:
                problems.append(f"{label} must be a text, keyvalue or divider block with only its own fields.")
                continue
            clean = {"type": b["type"]}
            for f in BLOCK_KEYS[b["type"]] - {"type"}:
                clean[f] = _plain(b.get(f), TEXT_MAX, f"{label} {f}", problems, allow_empty=(f == "value"))
            clean_blocks.append(clean)
        out["cards"].append({"title": _plain(card.get("title"), NAME_MAX, f"Card {ci + 1} title", problems), "blocks": clean_blocks})
    url = raw.get("webhook_url")
    if url not in (None, ""):
        p = _url_problem(url)
        if p:
            problems.append(p)
        out["webhook_url"] = url
    else:
        out["webhook_url"] = ""
    if problems:
        raise ManifestError(problems)
    return out
