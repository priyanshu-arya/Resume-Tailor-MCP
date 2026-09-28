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
# Multi-workspace: list / select / switch (R-USER-14, R-USER-15)
# --------------------------------------------------------------------------

def test_list_workspaces_empty_home(rt_home):
    r = wsmod.list_workspaces()
    assert r == {"workspaces": [], "active_workspace_id": None, "count": 0, "truncated": False,
                "note": r["note"]}


def test_list_workspaces_marks_active(workspace):
    r = wsmod.list_workspaces()
    assert r["count"] == 1
    assert r["active_workspace_id"] == workspace.id
    row = r["workspaces"][0]
    assert row["workspace_id"] == workspace.id
    assert row["active"] is True
    assert row["masters_present"] == {"resume": False, "cv": False}
    assert row["id_format_ok"] is True


def test_list_workspaces_skips_symlinked_entry(workspace, rt_home):
    real = rt_home / "workspaces" / "RT-DEADBEEF"
    real.mkdir(parents=True)
    link = rt_home / "workspaces" / "RT-C0FFEE00"
    link.symlink_to(real, target_is_directory=True)
    ids = wsmod.existing_workspace_ids()
    assert "RT-DEADBEEF" in ids
    assert "RT-C0FFEE00" not in ids


def test_list_workspaces_skips_invalid_names(workspace, rt_home):
    # ".hidden" fails validate_id (must start with an alnum), so a directory
    # with that name must never become a selectable workspace id.
    bad_dir = rt_home / "workspaces" / ".hidden"
    bad_dir.mkdir(parents=True)
    ids = wsmod.existing_workspace_ids()
    assert ".hidden" not in ids
    assert workspace.id in ids


def test_select_workspace_switches_binding(two_workspaces):
    id_a, id_b = two_workspaces
    assert wsmod.get_workspace().id == id_b  # b is active per the fixture
    r = wsmod.select_workspace(id_a)
    assert r["switched"] is True
    assert r["previous_workspace_id"] == id_b
    assert wsmod.get_workspace().id == id_a
    # master saved in A is reachable again after switching back
    assert storage.load_master("resume")[0] is not None


def test_select_workspace_logs_the_switch_in_both_workspaces(two_workspaces):
    """The destination side is logged by server.py's generic post-call audit
    hook (config.yaml has already moved by the time it runs, so ws=None
    there resolves to the new workspace); the source side has no such hook
    once the call returns, so lib.workspace.select_workspace logs it
    directly, from inside the call, into the workspace being LEFT. Going
    through server.select_workspace (as a real MCP call would) exercises
    both halves together."""
    import json
    import server as _server
    id_a, id_b = two_workspaces  # b is active
    _server.select_workspace(id_a)

    def last_event(wid):
        ws = wsmod._workspace_from_id(wid)
        path = ws.monitoring_dir / "audit.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return json.loads(lines[-1]) if lines else None

    ev_a = last_event(id_a)  # destination: gets it from the generic server-level hook
    ev_b = last_event(id_b)  # source: gets it from select_workspace itself

    assert ev_a is not None and ev_a["event"] == "workspace_switched"
    assert ev_a["workspace_id"] == id_a
    assert ev_a.get("from_workspace_id") == id_b

    assert ev_b is not None and ev_b["event"] == "workspace_switched"
    assert ev_b["workspace_id"] == id_b
    assert ev_b.get("to_workspace_id") == id_a


def test_select_workspace_with_no_prior_binding_is_not_a_switch(two_workspaces, rt_home):
    # recovering from an ambiguous/missing binding (no active_workspace_id at
    # all) is a first activation, not a switch -- there is no "from" side
    id_a, id_b = two_workspaces
    wsmod.config_path().unlink()
    r = wsmod.select_workspace(id_a)
    assert r["switched"] is False
    assert r["previous_workspace_id"] is None


def test_select_workspace_missing_dir_is_not_found(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.select_workspace("RT-DEADBEEF")
    assert _code(ei) == "WORKSPACE_NOT_FOUND"
    # the binding is untouched
    assert wsmod.get_workspace().id == workspace.id


@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_select_workspace_rejects_traversal_ids(workspace, bad):
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.select_workspace(bad)
    assert _code(ei) in ("INVALID_ID", "PATH_TRAVERSAL")


def test_select_workspace_creates_no_workspace(workspace, rt_home):
    before = set((rt_home / "workspaces").iterdir())
    with pytest.raises(ResumeTailorError):
        wsmod.select_workspace("RT-DEADBEEF")
    after = set((rt_home / "workspaces").iterdir())
    assert before == after


def test_create_new_makes_second_workspace_and_binds(workspace):
    first_id = workspace.id
    r = wsmod.initialize_workspace(create_new=True, label="second one!!")
    assert r["created"] is True
    assert r["workspace_id"] != first_id
    assert wsmod.get_workspace().id == r["workspace_id"]
    row = next(w for w in wsmod.list_workspaces()["workspaces"] if w["workspace_id"] == r["workspace_id"])
    assert row["label"] == "second-one"  # slugified


def test_default_init_never_makes_second_workspace(workspace):
    first_id = workspace.id
    for _ in range(3):
        r = wsmod.initialize_workspace()
        assert r["created"] is False
        assert r["workspace_id"] == first_id
    ids = wsmod.existing_workspace_ids()
    assert ids == [first_id]


def test_create_new_refused_when_bound_workspace_missing(workspace):
    import shutil
    shutil.rmtree(workspace.root)
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.initialize_workspace(create_new=True)
    assert _code(ei) == "WORKSPACE_NOT_INITIALIZED"
    assert wsmod.existing_workspace_ids() == []


def test_missing_config_with_existing_workspaces_is_ambiguous(master):
    """Regression for defect D-1: deleting config.yaml used to silently mint
    a brand-new empty workspace and orphan the master that was already
    there."""
    ws, doc, h = master
    wsmod.config_path().unlink()
    with pytest.raises(ResumeTailorError) as ei:
        wsmod.get_workspace()
    assert _code(ei) == "WORKSPACE_AMBIGUOUS"
    assert ei.value.details["available_workspace_ids"] == [ws.id]
    with pytest.raises(ResumeTailorError) as ei2:
        wsmod.initialize_workspace()
    assert _code(ei2) == "WORKSPACE_AMBIGUOUS"
    # still exactly one workspace on disk -- nothing new was minted
    assert wsmod.existing_workspace_ids() == [ws.id]
    # recovery: explicit select brings the master back
    wsmod.select_workspace(ws.id)
    assert storage.load_master("resume", wsmod.get_workspace()) == (doc, h)


def test_ambiguous_does_not_create_anything(master, rt_home):
    ws, doc, h = master
    wsmod.config_path().unlink()
    before = {p: p.stat().st_mtime_ns for p in rt_home.rglob("*") if p.is_file()}
    for _ in range(2):
        with pytest.raises(ResumeTailorError):
            wsmod.get_workspace()
        with pytest.raises(ResumeTailorError):
            wsmod.initialize_workspace()
    after = {p: p.stat().st_mtime_ns for p in rt_home.rglob("*") if p.is_file()}
    assert before == after
    assert not wsmod.config_path().exists()


def test_config_preserves_unknown_keys_across_select(two_workspaces, rt_home):
    id_a, id_b = two_workspaces
    config_path = wsmod.config_path()
    from lib import safe_yaml
    from lib.locking import atomic_write_yaml
    cfg = safe_yaml.load_file(config_path)
    cfg["a_future_field_this_version_does_not_know_about"] = "keep-me"
    atomic_write_yaml(config_path, cfg)
    wsmod.select_workspace(id_a)
    cfg2 = safe_yaml.load_file(config_path)
    assert cfg2["a_future_field_this_version_does_not_know_about"] == "keep-me"
    assert cfg2["active_workspace_id"] == id_a


def test_concurrent_select_does_not_lose_config(two_workspaces):
    import threading
    id_a, id_b = two_workspaces
    errors = []

    def flip(target):
        try:
            for _ in range(20):
                wsmod.select_workspace(target)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=flip, args=(id_a,))
    t2 = threading.Thread(target=flip, args=(id_b,))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not errors
    # config is still valid YAML naming one of the two ids -- no corruption/interleaving
    assert wsmod.get_workspace().id in (id_a, id_b)


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
