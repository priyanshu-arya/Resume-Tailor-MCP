"""Workspace manager: initialization, isolation, path safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import storage
from lib import workspace as wsmod
from lib.errors import ResumeTailorError
from lib.ids import normalize_master

REPO_ROOT = Path(__file__).resolve().parent.parent

BAD_IDS = [
    "../x",
    "a/b",
    "..",
    ".",
    "",
    ".hidden",
    "a\\b",
    "a\x00b",
    "a" * 200,
    "/etc/passwd",
    "/abs",
    "a..b",
    " a",
    "a b",
    "abc\n",  # `$` in the ID regex matches before a trailing newline
    "\nabc",
]


def _code(excinfo) -> str:
    return excinfo.value.code


def _home(monkeypatch, path: Path) -> None:
    monkeypatch.setenv(wsmod.ENV_HOME, str(path))


# --------------------------------------------------------------------------
# Initialization
# --------------------------------------------------------------------------

def test_no_workspace_raises_not_initialized(rt_home):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.get_workspace()
    assert _code(ei) == "WORKSPACE_NOT_INITIALIZED"


def test_get_workspace_never_auto_creates(rt_home):
    for _ in range(3):
        with pytest.raises(ResumeTailorError):
            wsmod.get_workspace()
    assert not rt_home.exists() or not any(rt_home.iterdir())
    assert not wsmod.config_path().exists()


def test_storage_calls_without_workspace_do_not_create_one(rt_home):
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_master("resume")
    assert _code(ei) == "WORKSPACE_NOT_INITIALIZED"
    assert not wsmod.config_path().exists()


def test_init_creates_tree_and_config_but_no_masters(rt_home):
    info = wsmod.initialize_workspace()
    assert info["created"] is True
    ws = wsmod.get_workspace()
    assert ws.id == info["workspace_id"]
    for d in (ws.master_dir, ws.backups_dir, ws.legacy_dir, ws.monitoring_dir, ws.versions_dir,
              ws.jd_dir, ws.exports_dir, ws.sessions_dir, ws.releases_dir, ws.evidence_dir):
        assert d.is_dir(), d
    assert wsmod.config_path().is_file()
    assert not ws.master_path("resume").exists()
    assert not ws.master_path("cv").exists()
    assert info["masters_present"] == {"resume": False, "cv": False}
    # nothing but directories under master/
    assert [p for p in ws.master_dir.rglob("*") if p.is_file()] == []


def test_second_init_is_idempotent(rt_home):
    first = wsmod.initialize_workspace()
    second = wsmod.initialize_workspace()
    assert second["created"] is False
    assert second["workspace_id"] == first["workspace_id"]
    assert len(list((rt_home / "workspaces").iterdir())) == 1


def test_second_init_preserves_master(master):
    ws, doc, h = master
    wsmod.initialize_workspace()
    assert storage.load_master("resume", ws) == (doc, h)


def test_second_init_repairs_missing_subdir(workspace):
    workspace.exports_dir.rmdir()
    wsmod.initialize_workspace()
    assert workspace.exports_dir.is_dir()


def test_deleted_root_is_error_not_new_workspace(workspace):
    import shutil
    shutil.rmtree(workspace.root)
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.get_workspace()
    assert _code(ei) == "WORKSPACE_NOT_INITIALIZED"
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.initialize_workspace()
    assert _code(ei) == "WORKSPACE_NOT_INITIALIZED"
    assert not workspace.root.exists()
    assert not (workspace.root.parent.exists() and any(workspace.root.parent.iterdir()))


def test_tampered_config_workspace_id_rejected(rt_home):
    rt_home.mkdir(parents=True)
    wsmod.config_path().write_text("active_workspace_id: ../../etc\n", encoding="utf-8")
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.get_workspace()
    assert _code(ei) == "INVALID_ID"


def test_config_with_python_tag_does_not_execute(rt_home, tmp_path):
    marker = tmp_path / "pwned-config"
    rt_home.mkdir(parents=True)
    wsmod.config_path().write_text(
        f"active_workspace_id: !!python/object/apply:os.system ['touch {marker}']\n", encoding="utf-8")
    with pytest.raises(Exception):
        wsmod.get_workspace()
    assert not marker.exists()


# --------------------------------------------------------------------------
# Isolation / location
# --------------------------------------------------------------------------

def test_two_homes_are_isolated(tmp_path, monkeypatch, legacy_master):
    home_a, home_b = tmp_path / "home-a", tmp_path / "home-b"

    _home(monkeypatch, home_a)
    id_a = wsmod.initialize_workspace()["workspace_id"]
    ws_a = wsmod.get_workspace()
    storage.save_master("resume", normalize_master(legacy_master, "resume"), None, "a")
    storage.save_version("v1", {"metadata": {"version_id": "v1", "workspace_id": id_a,
                                             "document_kind": "resume", "created_at": "2026-01-01T00:00:00Z"}})

    _home(monkeypatch, home_b)
    id_b = wsmod.initialize_workspace()["workspace_id"]
    ws_b = wsmod.get_workspace()

    assert id_a != id_b
    assert ws_a.root != ws_b.root
    assert home_a not in ws_b.root.parents and home_b not in ws_a.root.parents
    assert storage.load_master("resume") == (None, None)
    assert storage.load_version("v1") is None
    assert storage.list_version_ids() == []

    _home(monkeypatch, home_a)
    assert wsmod.get_workspace().id == id_a
    assert storage.load_master("resume")[0] is not None


def test_workspace_b_cannot_reach_a_by_id(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path / "a")
    id_a = wsmod.initialize_workspace()["workspace_id"]
    _home(monkeypatch, tmp_path / "b")
    wsmod.initialize_workspace()
    ws_b = wsmod.get_workspace()
    for attempt in (f"../../../a/workspaces/{id_a}/master/resume", f"../{id_a}", id_a + "/x"):
        with pytest.raises(ResumeTailorError):
            storage.version_path(attempt, ws_b)


def test_master_path_outside_repo(workspace):
    for kind in ("resume", "cv"):
        p = workspace.master_path(kind).resolve()
        assert REPO_ROOT not in p.parents
    assert REPO_ROOT not in workspace.root.resolve().parents


def test_default_app_root_is_under_home_not_repo(tmp_path, monkeypatch):
    monkeypatch.delenv(wsmod.ENV_HOME, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = wsmod.app_root()
    assert root == (tmp_path / ".resume-tailor").resolve()
    assert REPO_ROOT not in root.parents


def test_app_root_read_at_call_time(tmp_path, monkeypatch):
    _home(monkeypatch, tmp_path / "one")
    assert wsmod.app_root() == (tmp_path / "one").resolve()
    _home(monkeypatch, tmp_path / "two")
    assert wsmod.app_root() == (tmp_path / "two").resolve()


def test_master_path_rejects_bad_kind(workspace):
    for bad in ("../resume", "master", "RESUME", "", "cv/../../x"):
        with pytest.raises(ResumeTailorError) as ei:
            workspace.master_path(bad)
        assert _code(ei) == "INVALID_KIND"


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_validate_id_rejects(bad):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.validate_id(bad)
    assert _code(ei) == "INVALID_ID"


@pytest.mark.parametrize("bad", [None, 123, b"abc", ["a"]], ids=repr)
def test_validate_id_rejects_non_strings(bad):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.validate_id(bad)
    assert _code(ei) == "INVALID_ID"


@pytest.mark.parametrize("good", ["a", "A1", "job-2026_v1.final", "x" * 81, "RT-DEADBEEF"])
def test_validate_id_accepts(good):
    assert wsmod.validate_id(good) == good


def test_validate_id_length_boundary():
    with pytest.raises(ResumeTailorError):
        wsmod.validate_id("x" * 82)


@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_safe_child_rejects(tmp_path, bad):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.safe_child(tmp_path, bad, ".yaml")
    assert _code(ei) in ("INVALID_ID", "PATH_TRAVERSAL")


def test_safe_child_stays_in_base(tmp_path):
    p = wsmod.safe_child(tmp_path, "job-1", ".yaml")
    assert p.parent == tmp_path.resolve()
    assert p.name == "job-1.yaml"


def test_safe_child_rejects_symlink_escape(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("secret: 1\n")
    (base / "evil.yaml").symlink_to(outside)
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.safe_child(base, "evil", ".yaml")
    assert _code(ei) == "PATH_TRAVERSAL"


def test_storage_paths_reject_traversal(workspace):
    for fn in (storage.version_path, storage.jd_path):
        for bad in ("../../etc/passwd", "../x", "/etc/passwd", "..", ""):
            with pytest.raises(ResumeTailorError) as ei:
                fn(bad, workspace)
            assert _code(ei) in ("INVALID_ID", "PATH_TRAVERSAL")


def test_version_path_rejects_trailing_newline(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        storage.version_path("v1\n", workspace)
    assert _code(ei) == "INVALID_ID"


def test_storage_version_via_symlink_rejected(workspace, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("metadata: {released: false}\n")
    (workspace.versions_dir / "leak.yaml").symlink_to(outside)
    with pytest.raises(ResumeTailorError) as ei:
        storage.load_version("leak", workspace)
    assert _code(ei) == "PATH_TRAVERSAL"


# --------------------------------------------------------------------------
# slugify
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Senior Backend Engineer @ Acme!", "senior-backend-engineer-acme"),
    ("  --Hello   World--  ", "hello-world"),
    ("Café Résumé", "cafe-resume"),
    ("../../etc/passwd", "etc-passwd"),
    ("A/B\\C", "a-b-c"),
    (12345, "12345"),
])
def test_slugify(text, expected):
    out = wsmod.slugify(text)
    assert out == expected
    assert wsmod.validate_id(out) == out


@pytest.mark.parametrize("garbage", ["", "   ", "!!!@@@", "---", "日本語", "\x00\n\t"])
def test_slugify_garbage_raises(garbage):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.slugify(garbage)
    assert _code(ei) == "INVALID_ID"


def test_slugify_truncates_without_trailing_dash():
    out = wsmod.slugify("word " * 50, max_len=20)
    assert len(out) <= 20
    assert not out.endswith("-") and not out.startswith("-")
    wsmod.validate_id(out)


def test_slugify_long_input_is_valid_id():
    wsmod.validate_id(wsmod.slugify("x" * 500))


# --------------------------------------------------------------------------
# list_workspace_size
# --------------------------------------------------------------------------

def test_list_workspace_size_empty(workspace):
    report = wsmod.list_workspace_size(workspace)
    assert report["workspace_id"] == workspace.id
    assert report["total_bytes"] == 0
    assert all(a["files"] == 0 for a in report["areas"].values())


def test_list_workspace_size_counts_files(master):
    ws, _, _ = master
    (ws.exports_dir / "a.txt").write_bytes(b"12345")
    nested = ws.exports_dir / "sub"
    nested.mkdir()
    (nested / "b.txt").write_bytes(b"xyz")
    report = wsmod.list_workspace_size(ws)
    assert report["areas"]["data/exports"] == {"files": 2, "bytes": 8}
    master_files = [p for p in ws.master_dir.rglob("*") if p.is_file()]
    assert report["areas"]["master"]["files"] == len(master_files) >= 2  # resume.yaml + history.yaml
    assert report["total_bytes"] == sum(a["bytes"] for a in report["areas"].values())
    # deletes nothing
    assert (ws.exports_dir / "a.txt").exists()
