"""The CI workflow only listened to a branch named main, but the repository's default branch is
final-iam-workspace-multidevice, so CI never ran on pushes or pull requests (GitHub reports 0 runs)."""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yaml"
DEFAULT_BRANCH = "final-iam-workspace-multidevice"


def _branches(event):
    m = re.search(r"^  %s:\n    branches: \[([^\]]*)\]" % event, WORKFLOW.read_text(), re.M)
    assert m, event
    return [b.strip() for b in m.group(1).split(",")]


def test_ci_runs_for_pushes_and_pull_requests_on_the_default_branch():
    assert DEFAULT_BRANCH in _branches("push")
    assert DEFAULT_BRANCH in _branches("pull_request")


def test_ci_workflow_has_no_deploy_or_publish_step():
    text = WORKFLOW.read_text().lower()
    for word in ("deploy", "docker push", "ssh ", "secrets."):
        assert word not in text
