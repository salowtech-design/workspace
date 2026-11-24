"""Production compose keeps backups and audit checkpoints on named volumes, and says a volume is not off-site."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "docker-compose.prod.yml").read_text()


def _service(name: str) -> str:
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \w|^\w|\Z)", TEXT, re.S | re.M)
    assert m, f"service {name} missing"
    return m.group(1)


def test_backup_worker_writes_and_web_reads_only():
    assert "- salow_backups:/backups\n" in _service("backup_worker")
    assert "process_backup_jobs --loop" in _service("backup_worker")
    assert "- salow_backups:/backups:ro" in _service("web")


def test_audit_checkpoints_are_on_a_named_volume_for_the_writer():
    sched = _service("security_scheduler")
    assert "- salow_audit_checkpoints:/audit_checkpoints" in sched
    assert "- AUDIT_CHECKPOINT_DIR=/audit_checkpoints" in sched
    assert re.search(r"^volumes:\n(?:  .*\n)*  salow_audit_checkpoints:", TEXT, re.M)
    assert re.search(r"^volumes:\n(?:  .*\n)*  salow_backups:", TEXT, re.M)


def test_compose_states_a_volume_is_not_offsite():
    assert "NOT an off-site copy" in TEXT
