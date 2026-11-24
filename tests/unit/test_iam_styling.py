"""IAM console styling: Tailwind-style utility classes used by admin templates need local CSS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_iam_utilities_css_exists_and_covers_sizing():
    css = (ROOT / "static/css/iam-utilities.css").read_text()
    assert ".w-8" in css and ".h-8" in css  # the oversized sessions SVG
    assert "http://" not in css and "https://" not in css and "@import" not in css


def test_base_template_links_local_css_only_on_iam_host():
    base = (ROOT / "templates/base.html").read_text()
    assert "/static/css/iam-utilities.css" in base
    assert "request.is_iam_host" in base
    assert "cdn" not in base.lower()


def test_local_review_settings_allow_both_dedicated_hosts_and_stay_http():
    from config.settings import local_review

    assert {"iam.salowtech.com", "app.salowtech.com", "localhost"} <= set(
        local_review.ALLOWED_HOSTS
    )
    assert local_review.SESSION_COOKIE_SECURE is False
