"""Workspace + master resolution: the 9-row state table, and the "never
guess, never create, always report" invariants (R-USER-01..13)."""

from __future__ import annotations

import shutil

import pytest

from lib import resolve
from lib import storage
from lib import workspace as wsmod
from lib.ids import normalize_master
from lib.locking import atomic_write_yaml


def _state():
    return resolve.resolve_workspace_state()


# --------------------------------------------------------------------------
# The 9-row state table
# --------------------------------------------------------------------------

def test_row_no_active_id_no_dirs(rt_home):
    r = _state()
    assert r["state"] == resolve.NO_WORKSPACE
    assert r["reason"] is None
    assert r["masters"] == {"resume": None, "cv": None}


def test_row_no_active_id_workspaces_exist(workspace):
    wsmod.config_path().unlink()
    r = _state()
    assert r["state"] == resolve.WORKSPACE_INVALID
    assert r["reason"] == "no_active_binding"
    assert r["available_workspace_ids"] == [workspace.id]


def test_row_unreadable_config(rt_home):
    rt_home.mkdir(parents=True)
    wsmod.config_path().write_text("active_workspace_id: [1, 2\n", encoding="utf-8")  # bad YAML
    r = _state()
    assert r["state"] == resolve.WORKSPACE_INVALID
    assert r["reason"] == "unreadable_config"


def test_row_invalid_workspace_id(rt_home):
    rt_home.mkdir(parents=True)
    wsmod.config_path().write_text("active_workspace_id: ../../etc\n", encoding="utf-8")
    r = _state()
    assert r["state"] == resolve.WORKSPACE_INVALID
    assert r["reason"] == "invalid_workspace_id"


def test_row_bound_but_missing_dir(workspace):
    shutil.rmtree(workspace.root)
    r = _state()
    assert r["state"] == resolve.WORKSPACE_INVALID
    assert r["reason"] == "missing_workspace_dir"


def test_row_bound_dir_ok_no_master(workspace):
    r = _state()
    assert r["state"] == resolve.WORKSPACE_NEEDS_SETUP
    assert r["reason"] == "no_master"
    assert r["masters"]["resume"]["present"] is False


def test_row_master_present_fails_schema(workspace):
    atomic_write_yaml(workspace.master_path("resume"), {"name": "no metadata at all"})
    r = _state()
    assert r["state"] == resolve.WORKSPACE_NEEDS_SETUP
    assert r["reason"] == "master_invalid"
    assert r["masters"]["resume"]["present"] is True
    assert r["masters"]["resume"]["valid"] is False
    assert r["masters"]["resume"]["invalid_reason"] == "schema_validation_failed"


def test_row_master_present_unreadable_yaml(workspace):
    workspace.master_path("resume").write_text("name: [unterminated\n", encoding="utf-8")
    r = _state()
    assert r["state"] == resolve.WORKSPACE_NEEDS_SETUP
    assert r["reason"] == "master_invalid"
    assert r["masters"]["resume"]["invalid_reason"] == "unreadable_file"


def test_row_valid_master_unparsed_outstanding(master):
    ws, doc, h = master
    doc2 = dict(doc)
    doc2["unparsed"] = ["a stray line the parser could not place"]
    storage.save_master("resume", doc2, h, "test", ws=ws)
    r = _state()
    assert r["state"] == resolve.WORKSPACE_FOUND  # unparsed is still FOUND, not NEEDS_SETUP
    assert r["reason"] is None
    assert r["masters"]["resume"]["ready"] is False
    assert r["masters"]["resume"]["unparsed_items"] == 1


def test_row_valid_ready_master(master):
    r = _state()
    assert r["state"] == resolve.WORKSPACE_FOUND
    assert r["reason"] is None
    assert r["masters"]["resume"]["ready"] is True
    assert r["masters"]["resume"]["present"] is True


# --------------------------------------------------------------------------
# Cross-cutting invariants
# --------------------------------------------------------------------------

def test_resolve_never_raises_for_broken_binding(rt_home):
    # every scenario above is exercised through _state() without pytest.raises
    # -- this test asserts the general property with a battery of bad configs
    rt_home.mkdir(parents=True)
    bad_configs = [
        "active_workspace_id: ../../x\n",
        "active_workspace_id: [1,2\n",
        "{}\n",
        "active_workspace_id:\n",
    ]
    for text in bad_configs:
        wsmod.config_path().write_text(text, encoding="utf-8")
        r = _state()  # must not raise
        assert r["state"] in resolve.RESOLUTION_STATES


def test_resolve_creates_nothing(rt_home):
    before_exists = wsmod.config_path().exists()
    for _ in range(3):
        _state()
    assert wsmod.config_path().exists() == before_exists
    assert wsmod.existing_workspace_ids() == []


def test_unparsed_master_is_found_not_needs_setup(master):
    ws, doc, h = master
    doc2 = dict(doc)
    doc2["unparsed"] = ["x"]
    storage.save_master("resume", doc2, h, "test", ws=ws)
    r = _state()
    assert r["state"] == resolve.WORKSPACE_FOUND


def test_next_step_text_refuses_memory_substitute(rt_home):
    r = _state()
    assert "memory" in r["next_step"].lower() or "memory" in r["refuse"].lower()
    assert "previous tailored version" in r["refuse"]
    assert "another workspace" in r["refuse"]


def test_next_step_names_discover_masters_on_no_workspace(rt_home):
    r = _state()
    assert "discover_masters" in r["next_step"]


def test_next_step_names_select_workspace_on_ambiguous(workspace):
    wsmod.config_path().unlink()
    r = _state()
    assert "select_workspace" in r["next_step"]
    assert "list_workspaces" in r["next_step"]


def test_next_step_ready_names_analyze(master):
    r = _state()
    assert "analyze_tailoring_requirements" in r["next_step"]


# --------------------------------------------------------------------------
# require_tailorable_master
# --------------------------------------------------------------------------

def test_require_tailorable_master_no_master_raises_not_found(workspace):
    from lib.errors import ResumeTailorError
    with pytest.raises(ResumeTailorError) as ei:
        resolve.require_tailorable_master("resume", workspace)
    assert ei.value.code == "MASTER_NOT_FOUND"


def test_require_tailorable_master_unparsed_raises_master_invalid(master):
    from lib.errors import ResumeTailorError
    ws, doc, h = master
    doc2 = dict(doc)
    doc2["unparsed"] = ["x"]
    storage.save_master("resume", doc2, h, "test", ws=ws)
    with pytest.raises(ResumeTailorError) as ei:
        resolve.require_tailorable_master("resume", ws)
    assert ei.value.code == "MASTER_INVALID"


def test_require_tailorable_master_ready_returns_doc(master):
    ws, doc, h = master
    got_doc, got_hash = resolve.require_tailorable_master("resume", ws)
    assert got_hash == h
    assert got_doc["name"] == doc["name"]


def test_require_tailorable_master_accepted_unparsed_is_ready(master):
    ws, doc, h = master
    doc2 = dict(doc)
    doc2["unparsed"] = ["x"]
    doc2["metadata"] = dict(doc["metadata"])
    doc2["metadata"]["unparsed_accepted"] = True
    storage.save_master("resume", doc2, h, "test", ws=ws)
    got_doc, got_hash = resolve.require_tailorable_master("resume", ws)
    assert got_doc["unparsed"] == ["x"]
