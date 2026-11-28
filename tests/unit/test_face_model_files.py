"""ADAPTER LOGIC ONLY: the reviewed model-file gate in apps/face/engine/model_files.py. No model is loaded and no face is
matched or checked for liveness; the files here are small fake byte strings. Limits: one machine, one run; the gate is not
called by the pipeline yet."""

import hashlib

import pytest

from apps.face.engine import model_files as mf


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def test_shipped_allowlist_is_empty_so_every_file_is_refused(tmp_path):
    assert all(not mf.REVIEWED_MODEL_SHA256[r] for r in mf.ROLES) and set(mf.REVIEWED_MODEL_SHA256) == set(mf.ROLES)
    f = tmp_path / "m.onnx"
    f.write_bytes(b"fake")
    for role in mf.ROLES:
        with pytest.raises(mf.ModelFileNotReviewed, match="No reviewed model file"):
            mf.verified_model_path(role, str(f))


def test_pinned_hash_accepts_only_that_exact_content_and_role(tmp_path):
    good, bad = tmp_path / "good.bin", tmp_path / "bad.bin"
    good.write_bytes(b"reviewed-bytes")
    bad.write_bytes(b"reviewed-bytes!")
    allow = {"detector": frozenset({_sha(b"reviewed-bytes")}), "embedder": frozenset(), "liveness": frozenset()}
    assert mf.verified_model_path("detector", str(good), allow) == str(good.resolve())
    with pytest.raises(mf.ModelFileNotReviewed, match="not in the reviewed list"):
        mf.verified_model_path("detector", str(bad), allow)
    with pytest.raises(mf.ModelFileNotReviewed, match="No reviewed model file"):
        mf.verified_model_path("embedder", str(good), allow)  # a hash pinned for one role does not open another
    with pytest.raises(mf.ModelFileNotReviewed, match="Unknown model role"):
        mf.verified_model_path("other", str(good), allow)


def test_missing_directory_and_oversize_are_refused_and_symlink_resolves_to_hashed_target(tmp_path, monkeypatch):
    allow = {"detector": frozenset({_sha(b"x")}), "embedder": frozenset(), "liveness": frozenset()}
    with pytest.raises(mf.ModelFileNotReviewed, match="not readable"):
        mf.verified_model_path("detector", str(tmp_path / "absent"), allow)
    with pytest.raises(mf.ModelFileNotReviewed, match="not a regular file"):
        mf.verified_model_path("detector", str(tmp_path), allow)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 10)
    monkeypatch.setattr(mf, "MAX_MODEL_BYTES", 5)
    with pytest.raises(mf.ModelFileNotReviewed, match="size limit"):
        mf.verified_model_path("detector", str(big), allow)
    monkeypatch.setattr(mf, "MAX_MODEL_BYTES", 1024)
    target, link = tmp_path / "t.bin", tmp_path / "l.bin"
    target.write_bytes(b"y")
    link.symlink_to(target)
    with pytest.raises(mf.ModelFileNotReviewed, match="not in the reviewed list"):
        mf.verified_model_path("detector", str(link), allow)  # the link's target is hashed, not the link
    pinned = {"detector": frozenset({_sha(b"y")}), "embedder": frozenset(), "liveness": frozenset()}
    assert mf.verified_model_path("detector", str(link), pinned) == str(target.resolve()), "positive: returns the TARGET real path"
    with pytest.raises(mf.ModelFileNotReviewed, match="not readable"):
        mf.verified_model_path("detector", "bad\x00path", pinned)  # ValueError from the OS layer is normalised


def test_read_is_capped_even_if_the_file_grows_after_the_size_check(tmp_path, monkeypatch):
    f = tmp_path / "grow.bin"
    f.write_bytes(b"abc")
    allow = {"detector": frozenset({_sha(b"abc")}), "embedder": frozenset(), "liveness": frozenset()}
    real_stat = mf.os.stat

    class Small:  # claims a small, regular file; the real content is larger than the cap
        st_mode = real_stat(str(f)).st_mode
        st_size = 1

    monkeypatch.setattr(mf, "MAX_MODEL_BYTES", 2)
    monkeypatch.setattr(mf.os, "stat", lambda p: Small)
    with pytest.raises(mf.ModelFileNotReviewed, match="size limit"):
        mf.verified_model_path("detector", str(f), allow)
