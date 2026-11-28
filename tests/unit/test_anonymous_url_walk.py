"""Anonymous walk of every URL pattern of both URL configs (config.urls on the app host, config.urls_iam on the IAM host).

What it asserts, per pattern, for GET and POST sent with no session (a fresh client and a fresh client address per
request, so the login throttle does not mask the answer; the production limiter is untouched):
  1. no 5xx;
  2. a route that is not in PUBLIC answers only with a redirect to a sign-in, activation, recovery or IAM page,
     or 401, 403, 404 or 405;
  3. anonymous POSTs to routes outside PUBLIC change no database row (row counts of every model compared);
  4. the 200 bodies of public routes contain none of a seeded person's name, email or employee id.

Coverage limits (stated so nobody reads this as full coverage): dummy path parameters name no real object, so this
shows the anonymous gate in front of each view, not what an authenticated user sees; only the two methods GET and POST
are sent; static and media serving, websocket and other non-URLconf paths are not walked.

PUBLIC is traced to code, not to the observed status:
  login, activate, forgot-password and passkey sign-in endpoints are the sign-in flow itself;
  /verify/ is documented in apps/certificates/views.py as "No authentication required";
  /health, /ready and /sw.js are probes and the service worker;
  IAM /login, /recover and /setup are the sign-in, recovery and token-driven setup entry points.
UNRESOLVED_PUBLIC are routes public today for which no requirement was found; they hold no user data. They are pinned
here so a change shows up, and they are not a decision that they should be public."""

import re
from collections import Counter

import pytest
from django.apps import apps as django_apps
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver

from apps.accounts.models import CustomUser

APP_HOST, IAM_HOST = "app.salowtech.com", "iam.salowtech.com"
DUMMY = {"int": "1", "str": "x", "slug": "x", "uuid": "00000000-0000-0000-0000-000000000000", "path": "x"}
GATE_PREFIXES = ("/auth/login/", "/auth/activate/", "/auth/recovery/forgot-password/", "/iam/login/",
                 f"https://{IAM_HOST}/iam/")  # fmt: skip
REFUSALS = {401, 403, 404, 405}

PUBLIC = {
    APP_HOST: {"/auth/login/", "/auth/activate/", "/auth/recovery/forgot-password/", "/auth/passkeys/auth/options/",
               "/auth/passkeys/auth/verify/", "/auth/recovery/passkey/verify/", "/auth/logout/", "/health",
               "/health/", "/ready", "/ready/", "/sw.js", "/verify/", "/verify/x/"},
    IAM_HOST: {"/login/", "/iam/login/", "/recover/", "/iam/recover/", "/setup/", "/iam/setup/", "/health",
               "/health/", "/ready", "/ready/"},
}  # fmt: skip
UNRESOLVED_PUBLIC = {APP_HOST: {"/styleguide/"}, IAM_HOST: set()}
# tables an anonymous request may write by design: throttle counters, sessions, public verification log
WRITE_OK = {"core.ratelimitbucket", "core.ratelimitrecord", "certificates.certificateverificationlog", "sessions.session", "accounts.loginattempt"}


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern)


def _routes(conf):
    out, regex = [], []
    for route in _walk(get_resolver(conf).url_patterns):
        if "(?P<" in route or route.startswith("^") or "$" in route:
            regex.append(route)
            continue
        out.append("/" + re.sub(r"<(?:(\w+):)?\w+>", lambda m: DUMMY.get(m.group(1) or "str", "x"), route))
    return sorted(set(out)), regex


def _counts():
    return {m._meta.label_lower: m.objects.count() for m in django_apps.get_models() if not m._meta.proxy}


def _walk_host(conf, host):
    urls, regex = _routes(conf)
    res, n = {}, 0
    for url in urls:
        for method in ("get", "post"):
            n += 1
            c = Client(HTTP_HOST=host, REMOTE_ADDR=f"10.9.{n // 250}.{n % 250 + 1}")
            before = _counts() if method == "post" and url not in PUBLIC[host] else None
            r = getattr(c, method)(url)
            res[(method.upper(), url)] = (r.status_code, r.headers.get("Location", ""), r.content.decode("utf-8", "replace"),
                                          None if before is None else {k: v for k, v in _counts().items() if before[k] != v})  # fmt: skip
    return res, regex


@pytest.mark.django_db
@pytest.mark.parametrize("conf,host", [("config.urls", APP_HOST), ("config.urls_iam", IAM_HOST)])
def test_every_url_is_gated_for_an_anonymous_visitor(conf, host):
    CustomUser.objects.create(employee_id="ZQ-LEAK-7", full_name="Zqleak Marker", email="zqleak7@salowtech.com",
                              role="employee", status="active", is_active=True)  # fmt: skip
    res, regex = _walk_host(conf, host)
    assert regex == [], f"regex routes are not walked and must be listed: {regex}"
    seen_public, problems = set(), []
    for (method, url), (status, loc, body, changed) in res.items():
        if status >= 500:
            problems.append(f"5xx {status} {method} {url}")
        if url in PUBLIC[host] or url in UNRESOLVED_PUBLIC[host]:
            seen_public.add(url)
            for marker in ("Zqleak", "zqleak7@", "ZQ-LEAK-7"):
                if status == 200 and marker in body:
                    problems.append(f"leak {marker} in {method} {url}")
            continue
        if status in REFUSALS:
            pass
        elif status in (301, 302) and loc.startswith(GATE_PREFIXES):
            pass
        else:
            problems.append(f"open {status} {method} {url} -> {loc[:60]}")
        if changed:
            wrote = {k for k in changed if k not in WRITE_OK}
            if wrote:
                problems.append(f"anonymous POST {url} changed rows {sorted(wrote)}")
    assert not problems, "\n".join(problems)
    # pinned lists must still match real routes, so a rename cannot silently empty the allowlist
    listed = PUBLIC[host] | UNRESOLVED_PUBLIC[host]
    assert listed <= {u for _m, u in res}, sorted(listed - {u for _m, u in res})
    print(f"WALKED {host}: {len(res)} requests, statuses {dict(Counter(v[0] for v in res.values()))}")
