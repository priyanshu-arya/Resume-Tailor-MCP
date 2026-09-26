"""Masters, versions, JDs: optimistic concurrency, write-once, Resume/CV separation."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from lib import storage
from lib.errors import ResumeTailorError
from lib.ids import normalize_master

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = str(REPO_ROOT / ".venv" / "bin" / "python")
if not Path(PY).exists():
    PY = sys.executable


def _code(excinfo) -> str:
    return excinfo.value.code


def _version(version_id: str, ws, **meta) -> dict:
    m = {"version_id": version_id, "workspace_id": ws.id, "document_kind": "resume",
         "created_at": "2026-01-01T00:00:00Z"}
    m.update(meta)
    return {"summary": "s", "metadata": m}


def _edited(doc: dict, summary: str) -> dict:
    d = copy.deepcopy(doc)
    d["summary"] = summary
    return d


def _history(ws) -> list:
    return storage.yaml_load_file(ws.master_dir / "history.yaml") or []


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

def test_load_master_empty(workspace):
    assert storage.load_master("resume", workspace) == (None, None)
    assert storage.load_master("cv", workspace) == (None, None)


def test_require_master_missing(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        storage.require_master("resume", workspace)
    assert _code(ei) == "MASTER_NOT_FOUND"


def test_saved_hash_matches_loaded_hash(master):
    ws, doc, h = master
    loaded, lh = storage.load_master("resume", ws)
    assert loaded == doc
    assert lh == h


def test_first_save_history_entry(master):
    ws, _, h = master
    hist = _history(ws)
    assert len(hist) == 1
    assert hist[0]["previous_hash"] is None
    assert hist[0]["new_hash"] == h
    assert hist[0]["kind"] == "resume"
    assert list(ws.backups_dir.iterdir()) == []  # nothing to back up on first save


def test_save_none_on_existing_is_master_exists(master):
    ws, doc, h = master
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", _edited(doc, "other"), None, "oops", ws)
    assert _code(ei) == "MASTER_EXISTS"
    assert storage.load_master("resume", ws)[1] == h


def test_stale_hash_is_conflict_and_second_writer_wins(master):
    ws, _, _ = master
    doc1, h1 = storage.load_master("resume", ws)          # writer 1 reads
    doc2, h2 = storage.load_master("resume", ws)          # writer 2 reads
    new2 = storage.save_master("resume", _edited(doc2, "writer two"), h2, "w2", ws)
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", _edited(doc1, "writer one"), h1, "w1", ws)
    assert _code(ei) == "MASTER_CONFLICT"
    on_disk, disk_hash = storage.load_master("resume", ws)
    assert on_disk["summary"] == "writer two"
    assert disk_hash == new2
    assert len(_history(ws)) == 2  # failed write did not append history


def test_expected_hash_when_no_master_is_conflict(workspace, legacy_master):
    doc = normalize_master(legacy_master, "resume")
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", doc, "deadbeef" * 8, "x", workspace)
    assert _code(ei) == "MASTER_CONFLICT"
    assert not workspace.master_path("resume").exists()


def test_garbage_hash_is_conflict(master):
    ws, doc, _ = master
    for bad in ("", "0" * 64, "../../etc"):
        with pytest.raises(ResumeTailorError) as ei:
            storage.save_master("resume", _edited(doc, "x"), bad, "x", ws)
        assert _code(ei) == "MASTER_CONFLICT"


def test_correct_hash_writes_backup_and_history(master):
    ws, doc, h = master
    new = storage.save_master("resume", _edited(doc, "updated"), h, "edit summary", ws)
    assert new != h
    loaded, lh = storage.load_master("resume", ws)
    assert loaded["summary"] == "updated" and lh == new

    backups = list(ws.backups_dir.iterdir())
    assert len(backups) == 1
    assert backups[0].name.startswith("resume-") and h[:8] in backups[0].name
    assert storage.yaml_load_file(backups[0]) == doc

    hist = _history(ws)
    assert hist[-1]["previous_hash"] == h
    assert hist[-1]["new_hash"] == new
    assert hist[-1]["reason"] == "edit summary"


def test_hash_chain_across_many_saves(master):
    ws, doc, h = master
    for i in range(5):
        h = storage.save_master("resume", _edited(doc, f"v{i}"), h, f"r{i}", ws)
    hist = _history(ws)
    assert len(hist) == 6
    for prev, cur in zip(hist, hist[1:]):
        assert cur["previous_hash"] == prev["new_hash"]
    assert hist[-1]["new_hash"] == storage.load_master("resume", ws)[1]


def test_kind_mismatch_is_master_invalid(workspace, legacy_master):
    cv_doc = normalize_master(legacy_master, "cv")
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", cv_doc, None, "x", workspace)
    assert _code(ei) == "MASTER_INVALID"
    assert not workspace.master_path("resume").exists()


@pytest.mark.parametrize("bad", [
    {},
    {"metadata": {}},
    {"metadata": {"kind": "resume"}, "skills": "python"},
    {"metadata": {"kind": "resume"}, "experience": [{"bullets": [{"no_text": 1}]}]},
    {"metadata": {"kind": "letter"}},
])
def test_schema_invalid_master(workspace, bad):
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", bad, None, "x", workspace)
    assert _code(ei) == "MASTER_INVALID"
    assert not workspace.master_path("resume").exists()


def test_master_invalid_details_do_not_echo_input(workspace):
    secret = "SSN-123-45-6789"
    bad = {"metadata": {"kind": "resume"}, "name": {"x": secret}, "skills": [{"items": [{"name": [secret]}]}]}
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("resume", bad, None, "x", workspace)
    assert _code(ei) == "MASTER_INVALID"
    assert secret not in json.dumps(ei.value.details, default=str)


@pytest.mark.parametrize("bad", ["", "master", "RESUME", "../cv", None, "resume "])
def test_bad_kind(workspace, legacy_master, bad):
    doc = normalize_master(legacy_master, "resume")
    for call in (lambda: storage.load_master(bad, workspace),
                 lambda: storage.save_master(bad, doc, None, "x", workspace),
                 lambda: storage.require_master(bad, workspace)):
        with pytest.raises(ResumeTailorError) as ei:
            call()
        assert _code(ei) == "INVALID_KIND"


def test_resume_and_cv_are_separate(master, legacy_master):
    ws, _, resume_hash = master
    assert storage.load_master("cv", ws) == (None, None)

    cv_doc = normalize_master(legacy_master, "cv")
    cv_doc["summary"] = "Academic CV summary"
    cv_hash = storage.save_master("cv", cv_doc, None, "cv", ws)

    assert storage.load_master("resume", ws)[1] == resume_hash
    assert storage.load_master("cv", ws)[1] == cv_hash
    assert ws.master_path("cv") != ws.master_path("resume")

    # editing the CV leaves the resume untouched, and vice versa
    cv_hash2 = storage.save_master("cv", _edited(cv_doc, "cv v2"), cv_hash, "cv2", ws)
    assert storage.load_master("resume", ws)[1] == resume_hash
    resume_doc, _ = storage.load_master("resume", ws)
    storage.save_master("resume", _edited(resume_doc, "r2"), resume_hash, "r2", ws)
    assert storage.load_master("cv", ws)[1] == cv_hash2


def test_resume_hash_cannot_be_used_for_cv(master, legacy_master):
    ws, _, resume_hash = master
    cv_doc = normalize_master(legacy_master, "cv")
    storage.save_master("cv", cv_doc, None, "cv", ws)
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_master("cv", _edited(cv_doc, "x"), resume_hash, "wrong hash", ws)
    assert _code(ei) == "MASTER_CONFLICT"


def test_concurrent_threads_same_hash_only_one_wins(master):
    ws, doc, h = master
    results = []
    barrier = threading.Barrier(6)

    def writer(i):
        barrier.wait()
        try:
            storage.save_master("resume", _edited(doc, f"thread {i}"), h, f"t{i}", ws)
            results.append(("ok", i))
        except ResumeTailorError as e:
            results.append((e.code, i))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [i for s, i in results if s == "ok"]
    assert len(winners) == 1, results
    assert all(s in ("ok", "MASTER_CONFLICT") for s, _ in results)
    assert storage.load_master("resume", ws)[0]["summary"] == f"thread {winners[0]}"
    assert len(_history(ws)) == 2


_PROC_WRITER = r"""
import sys, copy
from lib import storage, workspace
ws = workspace.get_workspace()
doc, h = storage.load_master("resume", ws)
expected = sys.argv[1]
open(sys.argv[3], "w").close()
import time, os
while not os.path.exists(sys.argv[4]):
    time.sleep(0.01)
d = copy.deepcopy(doc); d["summary"] = "proc " + sys.argv[2]
try:
    storage.save_master("resume", d, expected, "p", ws)
    print("OK")
except storage.ResumeTailorError as e:
    print(e.code)
"""


def test_concurrent_processes_same_hash_only_one_wins(master, rt_home, tmp_path):
    ws, _, h = master
    go = tmp_path / "go"
    procs = []
    for i in range(4):
        ready = tmp_path / f"ready{i}"
        procs.append((subprocess.Popen([PY, "-c", _PROC_WRITER, h, str(i), str(ready), str(go)],
                                       cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env={**__import__("os").environ,
                                                       "RESUME_TAILOR_HOME": str(rt_home)}), ready))
    import time
    deadline = time.monotonic() + 30
    while not all(r.exists() for _, r in procs):
        assert time.monotonic() < deadline, "writers never became ready"
        time.sleep(0.02)
    go.touch()
    outs = []
    for p, _ in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        outs.append(out.strip())
    assert sorted(outs) == ["MASTER_CONFLICT"] * 3 + ["OK"], outs
    assert storage.load_master("resume", ws)[0]["summary"].startswith("proc ")
    assert len(_history(ws)) == 2


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

def test_save_and_load_version(workspace):
    doc = _version("v1", workspace)
    path = storage.save_version("v1", doc, workspace)
    assert path == storage.version_path("v1", workspace)
    assert storage.load_version("v1", workspace) == doc
    assert storage.version_exists("v1", workspace)
    assert storage.list_version_ids(workspace) == ["v1"]


def test_version_write_once(workspace):
    storage.save_version("v1", _version("v1", workspace), workspace)
    changed = _version("v1", workspace)
    changed["summary"] = "overwritten"
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_version("v1", changed, workspace)
    assert _code(ei) == "VERSION_EXISTS"
    assert storage.load_version("v1", workspace)["summary"] == "s"


def test_version_write_once_even_without_validation(workspace):
    storage.save_version("v1", _version("v1", workspace), workspace)
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_version("v1", {"junk": 1}, workspace, validate=False)
    assert _code(ei) == "VERSION_EXISTS"


def test_released_version_is_frozen(workspace):
    storage.save_version("v1", _version("v1", workspace), workspace)
    storage.update_version_metadata("v1", {"released": True, "release_report_id": "rr-1"}, workspace)
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_version("v1", _version("v1", workspace), workspace)
    assert _code(ei) == "VERSION_RELEASED"
    with pytest.raises(ResumeTailorError) as ei:
        storage.update_version_metadata("v1", {"released": False}, workspace)
    assert _code(ei) == "VERSION_RELEASED"
    with pytest.raises(ResumeTailorError) as ei:
        storage.update_version_metadata("v1", {"template_id": "other"}, workspace)
    assert _code(ei) == "VERSION_RELEASED"
    meta = storage.load_version("v1", workspace)["metadata"]
    assert meta["released"] is True and meta["release_report_id"] == "rr-1"


def test_update_metadata_on_draft(workspace):
    storage.save_version("v1", _version("v1", workspace), workspace)
    out = storage.update_version_metadata("v1", {"template_id": "classic"}, workspace)
    assert out["metadata"]["template_id"] == "classic"
    reloaded = storage.load_version("v1", workspace)
    assert reloaded["metadata"]["template_id"] == "classic"
    assert reloaded["summary"] == "s"


def test_update_metadata_missing_version(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        storage.update_version_metadata("nope", {"released": True}, workspace)
    assert _code(ei) == "VERSION_NOT_FOUND"


def test_update_metadata_rejects_schema_invalid_update(workspace):
    """A 'metadata-only' update must not be able to write a version that
    no longer matches the TailoredVersion schema."""
    storage.save_version("v1", _version("v1", workspace), workspace)
    with pytest.raises(ResumeTailorError) as ei:
        storage.update_version_metadata("v1", {"document_kind": "banana", "created_at": None}, workspace)
    assert _code(ei) == "PATCH_INVALID"


def test_load_version_does_not_resolve_master(master):
    ws, _, _ = master
    for alias in ("master", "master-resume", "master_cv"):
        assert storage.load_version(alias, ws) is None
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_version("", ws)
    assert _code(ei) == "INVALID_ID"
    with pytest.raises(ResumeTailorError) as ei:
        storage.require_version("master", ws)
    assert _code(ei) == "VERSION_NOT_FOUND"


def test_resolve_master_alias_all():
    assert storage.resolve_master_alias(None) == "resume"
    for alias, kind in storage.MASTER_ALIASES.items():
        assert storage.resolve_master_alias(alias) == kind
        assert storage.resolve_master_alias(f"  {alias}  ") == kind
    expected = {"": "resume", "master": "resume", "master-resume": "resume", "master_resume": "resume",
                "master-cv": "cv", "master_cv": "cv"}
    assert storage.MASTER_ALIASES == expected
    for not_alias in ("v1", "Master", "master-resume-v2", "cv", "resume", "../master"):
        assert storage.resolve_master_alias(not_alias) is None


@pytest.mark.parametrize("bad", [
    {},
    {"metadata": {"version_id": "v1"}},
    {"metadata": {"version_id": "v1", "workspace_id": "w", "document_kind": "letter", "created_at": "x"}},
    {"metadata": {"version_id": "v1", "workspace_id": "w", "document_kind": "resume",
                  "created_at": "x"}, "experience": "nope"},
])
def test_schema_invalid_version(workspace, bad):
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_version("v1", bad, workspace)
    assert _code(ei) == "PATCH_INVALID"
    assert not storage.version_exists("v1", workspace)


def test_version_too_deep_rejected(workspace):
    doc = _version("v1", workspace)
    nested = "leaf"
    for _ in range(25):
        nested = {"n": nested}
    doc["unparsed"] = [nested]
    with pytest.raises(ResumeTailorError) as ei:
        storage.save_version("v1", doc, workspace)
    assert _code(ei) == "YAML_TOO_DEEP"
    assert not storage.version_exists("v1", workspace)


def test_version_traversal_ids(workspace):
    for bad in ("../../etc/passwd", "../x", "a/b"):
        with pytest.raises(ResumeTailorError):
            storage.save_version(bad, _version("v", workspace), workspace)
    assert list(workspace.versions_dir.iterdir()) == []


def test_version_no_temp_files(workspace):
    for i in range(5):
        storage.save_version(f"v{i}", _version(f"v{i}", workspace), workspace)
    assert all(not p.name.endswith(".tmp") for p in workspace.versions_dir.iterdir())


def test_concurrent_version_saves_one_wins(workspace):
    results = []
    barrier = threading.Barrier(6)

    def saver(i):
        doc = _version("race", workspace)
        doc["summary"] = f"t{i}"
        barrier.wait()
        try:
            storage.save_version("race", doc, workspace)
            results.append("ok")
        except ResumeTailorError as e:
            results.append(e.code)

    ts = [threading.Thread(target=saver, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == ["VERSION_EXISTS"] * 5 + ["ok"]


# --------------------------------------------------------------------------
# JDs
# --------------------------------------------------------------------------

def test_jd_round_trip(workspace):
    storage.save_jd("acme-backend", "We need Python.", {"skills": ["Python"]}, workspace)
    assert storage.load_jd("acme-backend", workspace) == {"jd_text": "We need Python.",
                                                          "extracted": {"skills": ["Python"]}}
    assert storage.list_jd_ids(workspace) == ["acme-backend"]
    assert storage.load_jd("missing", workspace) is None


def test_jd_text_with_yaml_payload_is_stored_as_text(workspace, tmp_path):
    marker = tmp_path / "jd-pwned"
    payload = f"!!python/object/apply:os.system ['touch {marker}']"
    storage.save_jd("evil", payload, None, workspace)
    loaded = storage.load_jd("evil", workspace)
    assert loaded["jd_text"] == payload
    assert not marker.exists()


def test_jd_traversal(workspace):
    for bad in ("../x", "../../etc/passwd", ".."):
        with pytest.raises(ResumeTailorError):
            storage.save_jd(bad, "t", None, workspace)
        with pytest.raises(ResumeTailorError):
            storage.load_jd(bad, workspace)
