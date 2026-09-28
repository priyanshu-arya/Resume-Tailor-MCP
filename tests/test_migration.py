"""Legacy -> workspace migration: explicit, verified, never destructive, idempotent."""

from __future__ import annotations

import pytest
import yaml

from lib import migration, storage
from lib.errors import ResumeTailorError
from lib.ids import normalize_master
from lib.locking import sha256_bytes, sha256_file
from lib.migration import migrate_legacy


def _write(path, data) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data if isinstance(data, bytes) else yaml.safe_dump(data, sort_keys=False).encode("utf-8")
    path.write_bytes(raw)
    return raw


@pytest.fixture
def legacy_root(tmp_path, legacy_master):
    root = tmp_path / "legacy-repo"
    _write(root / "resources" / "master_resume.yaml", legacy_master)
    return root


def _backups(ws):
    return sorted(ws.backups_dir.glob("*.yaml")) if ws.backups_dir.exists() else []


def _snapshots(ws):
    return sorted(ws.legacy_dir.glob("*.yaml"))


def _seed_other_master(ws, legacy_master) -> str:
    other = dict(legacy_master, name="Someone Else")
    return storage.save_master("resume", normalize_master(other, "resume"), None, "seed", ws=ws)


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

def test_migrates_resume_with_verified_snapshot_and_ids(workspace, legacy_root):
    src = legacy_root / "resources" / "master_resume.yaml"
    source_hash = sha256_bytes(src.read_bytes())

    result = migrate_legacy(legacy_root, ws=workspace)

    assert result["ok"] is True
    assert result["workspace_id"] == workspace.id
    assert result["masters"]["resume"]["status"] == "migrated"
    assert result["masters"]["cv"]["status"] == "no_legacy"
    snap = workspace.legacy_dir / f"resume-{source_hash[:12]}.yaml"
    assert sha256_file(snap) == source_hash
    doc, h = storage.load_master("resume", workspace)
    assert doc["metadata"]["migration"]["source_hash"] == source_hash
    assert doc["metadata"]["migration"]["source"] == "legacy_repository"
    assert doc["metadata"]["kind"] == "resume"
    assert doc["experience"][0]["id"] and doc["experience"][0]["bullets"][0]["id"]
    assert h == result["masters"]["resume"]["master_hash"]
    log = migration.load_log(workspace)
    assert len(log) == 1 and log[0]["decision"] == "migrated" and log[0]["master_hash"] == h


def test_legacy_file_is_byte_identical_afterwards(workspace, legacy_root):
    src = legacy_root / "resources" / "master_resume.yaml"
    before = src.read_bytes()
    migrate_legacy(legacy_root, ws=workspace)
    assert src.read_bytes() == before


def test_second_run_is_noop(workspace, legacy_root):
    migrate_legacy(legacy_root, ws=workspace)
    _, h1 = storage.load_master("resume", workspace)
    records, backups, snaps = len(migration.load_log(workspace)), _backups(workspace), _snapshots(workspace)
    history = (workspace.master_dir / "history.yaml").read_bytes()

    result = migrate_legacy(legacy_root, ws=workspace)

    assert result["masters"]["resume"]["status"] == "already_migrated"
    assert len(migration.load_log(workspace)) == records
    assert _backups(workspace) == backups
    assert _snapshots(workspace) == snaps
    assert (workspace.master_dir / "history.yaml").read_bytes() == history
    assert storage.load_master("resume", workspace)[1] == h1


def test_no_legacy(workspace, tmp_path):
    result = migrate_legacy(tmp_path / "empty-repo", ws=workspace)
    assert result["masters"] == {"resume": {"status": "no_legacy"}, "cv": {"status": "no_legacy"}}
    assert result["ok"] is True
    assert not workspace.master_path("resume").exists()
    assert not migration.log_path(workspace).exists()


def test_conflict_without_choice_writes_nothing(workspace, legacy_root, legacy_master):
    h = _seed_other_master(workspace, legacy_master)
    before = workspace.master_path("resume").read_bytes()

    result = migrate_legacy(legacy_root, ws=workspace)

    status = result["masters"]["resume"]
    assert result["ok"] is False
    assert status["status"] == "conflict"
    assert status["details"]["workspace_master_hash"] == h
    assert status["details"]["workspace_master_from_migration"] is False
    assert status["details"]["legacy_source_hash"] == sha256_file(legacy_root / "resources" / "master_resume.yaml")
    assert workspace.master_path("resume").read_bytes() == before
    assert _backups(workspace) == []
    assert _snapshots(workspace) == []
    assert not migration.log_path(workspace).exists()


def test_keep_workspace_is_remembered(workspace, legacy_root, legacy_master):
    h = _seed_other_master(workspace, legacy_master)

    first = migrate_legacy(legacy_root, conflict_choice="keep_workspace", ws=workspace)
    assert first["masters"]["resume"]["status"] == "kept_workspace"
    assert first["ok"] is True

    later = migrate_legacy(legacy_root, ws=workspace)
    assert later["masters"]["resume"]["status"] == "already_resolved"
    assert later["ok"] is True
    assert storage.load_master("resume", workspace)[1] == h
    assert len(migration.load_log(workspace)) == 1
    assert _backups(workspace) == []


def test_replace_with_legacy_backs_up_previous(workspace, legacy_root, legacy_master):
    old_hash = _seed_other_master(workspace, legacy_master)

    result = migrate_legacy(legacy_root, conflict_choice="replace_with_legacy", ws=workspace)

    assert result["masters"]["resume"]["status"] == "replaced"
    backups = _backups(workspace)
    assert len(backups) == 1 and old_hash[:8] in backups[0].name
    assert storage.yaml_load_file(backups[0])["name"] == "Someone Else"
    doc, _ = storage.load_master("resume", workspace)
    assert doc["name"] == legacy_master["name"]
    assert "migration" in doc["metadata"]

    again = migrate_legacy(legacy_root, conflict_choice="replace_with_legacy", ws=workspace)
    assert again["masters"]["resume"]["status"] == "already_migrated"
    assert len(_backups(workspace)) == 1


def test_unknown_conflict_choice_rejected(workspace, legacy_root, legacy_master):
    _seed_other_master(workspace, legacy_master)
    with pytest.raises(ResumeTailorError) as exc:
        migrate_legacy(legacy_root, conflict_choice="merge", ws=workspace)
    assert exc.value.code == "MIGRATION_CONFLICT"


def test_cv_and_resume_are_independent(workspace, tmp_path, legacy_master):
    root = tmp_path / "cv-only"
    _write(root / "resources" / "master_cv.yaml", legacy_master)

    result = migrate_legacy(root, ws=workspace)

    assert result["masters"]["cv"]["status"] == "migrated"
    assert result["masters"]["resume"]["status"] == "no_legacy"
    assert not workspace.master_path("resume").exists()
    doc, _ = storage.load_master("cv", workspace)
    assert doc["metadata"]["kind"] == "cv"


def test_snapshot_hash_mismatch_writes_no_master(workspace, legacy_root, monkeypatch):
    monkeypatch.setattr(migration, "sha256_file", lambda p: "0" * 64)
    with pytest.raises(ResumeTailorError) as exc:
        migrate_legacy(legacy_root, ws=workspace)
    assert exc.value.code == "MIGRATION_CONFLICT"
    assert not workspace.master_path("resume").exists()
    assert not migration.log_path(workspace).exists()


def test_unsafe_yaml_rejected(workspace, tmp_path):
    root = tmp_path / "evil"
    _write(root / "resources" / "master_resume.yaml",
           b"name: !!python/object/apply:os.system ['echo pwned']\n")
    with pytest.raises(ResumeTailorError) as exc:
        migrate_legacy(root, ws=workspace)
    assert exc.value.code == "YAML_UNSAFE"
    assert not workspace.master_path("resume").exists()
    assert _snapshots(workspace) == []


def test_no_workspace(rt_home, legacy_root):
    with pytest.raises(ResumeTailorError) as exc:
        migrate_legacy(legacy_root)
    assert exc.value.code == "WORKSPACE_NOT_INITIALIZED"
    assert not rt_home.exists() or not (rt_home / "workspaces").exists()


# --------------------------------------------------------------------------
# Versions, JDs, exports
# --------------------------------------------------------------------------

def _version(title: str) -> dict:
    return {"name": "Alex Example", "summary": title, "experience": []}


def test_versions_only_when_asked(workspace, legacy_root):
    _write(legacy_root / "data" / "versions" / "acme-swe.yaml", _version("v1"))

    result = migrate_legacy(legacy_root, ws=workspace)

    assert result["versions"] is None
    assert storage.list_version_ids(workspace) == []


def test_versions_migrated_with_legacy_flag_and_idempotent(workspace, legacy_root):
    raw = _write(legacy_root / "data" / "versions" / "acme-swe.yaml", _version("v1"))
    _write(legacy_root / "data" / "versions" / "bad id!.yaml", _version("bad"))

    first = migrate_legacy(legacy_root, include_versions=True, ws=workspace)
    statuses = {v["version_id"]: v["status"] for v in first["versions"]}
    assert statuses == {"acme-swe": "migrated", "bad id!": "skipped_invalid_id"}
    doc = storage.load_version("acme-swe", workspace)
    meta = doc["metadata"]
    assert meta["legacy"] is True and meta["released"] is False
    assert meta["version_id"] == "acme-swe" and meta["document_kind"] == "resume"
    assert meta["legacy_source_hash"] == sha256_bytes(raw)
    content = storage.version_path("acme-swe", workspace).read_bytes()

    second = migrate_legacy(legacy_root, include_versions=True, ws=workspace)
    assert {v["version_id"]: v["status"] for v in second["versions"]}["acme-swe"] == "already_migrated"
    assert storage.list_version_ids(workspace) == ["acme-swe"]
    assert storage.version_path("acme-swe", workspace).read_bytes() == content


def test_differing_existing_version_is_conflict(workspace, legacy_root):
    _write(legacy_root / "data" / "versions" / "acme-swe.yaml", _version("legacy"))
    storage.save_version("acme-swe", _version("workspace"), ws=workspace, validate=False)
    before = storage.version_path("acme-swe", workspace).read_bytes()

    result = migrate_legacy(legacy_root, include_versions=True, ws=workspace)

    assert result["versions"] == [{"version_id": "acme-swe", "status": "conflict"}]
    assert result["ok"] is False
    assert storage.version_path("acme-swe", workspace).read_bytes() == before


def test_jds_only_when_asked_and_existing_skipped(workspace, legacy_root):
    jd_src = legacy_root / "data" / "jd_history"
    raw = _write(jd_src / "acme-jd.yaml", {"jd_text": "Python engineer", "extracted": {}})
    _write(jd_src / "old-jd.yaml", {"jd_text": "legacy text", "extracted": {}})
    storage.save_jd("old-jd", "workspace text", ws=workspace)

    assert migrate_legacy(legacy_root, ws=workspace)["jds"] is None
    assert not storage.jd_path("acme-jd", workspace).exists()

    result = migrate_legacy(legacy_root, include_jds=True, ws=workspace)
    assert {j["jd_id"]: j["status"] for j in result["jds"]} == {"acme-jd": "copied", "old-jd": "skipped_existing"}
    assert storage.jd_path("acme-jd", workspace).read_bytes() == raw
    assert storage.load_jd("old-jd", workspace)["jd_text"] == "workspace text"


def test_exports_counted_and_left_in_place(workspace, legacy_root):
    exports = legacy_root / "data" / "exports"
    exports.mkdir(parents=True)
    (exports / "a.pdf").write_bytes(b"%PDF-1.4")
    (exports / "a.tex").write_text("\\documentclass{article}")

    result = migrate_legacy(legacy_root, ws=workspace)

    assert result["legacy_exports_left_in_place"] == 2
    assert result["note"] == "Legacy files were not deleted."
    assert sorted(p.name for p in exports.iterdir()) == ["a.pdf", "a.tex"]
    assert list(workspace.exports_dir.iterdir()) == []
