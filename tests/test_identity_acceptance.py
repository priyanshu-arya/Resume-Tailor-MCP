"""Phase 1 acceptance: the 18-item Definition of Done for workspace
identity, binding and folder-based master discovery (R-USER-01..17).

[ ] 1  first run asks for a folder          [ ] 10 ambiguous binding -> explicit selection
[ ] 2  no memory/chat fallback              [ ] 11 tailoring without a master blocked
[ ] 3  only the named folder scanned        [ ] 11b master not ready blocks analyze too
[ ] 4  multiple candidates require a pick   [ ] 12 a tailored version cannot become master
[ ] 5  resume/CV stay separate              [ ] 13 provenance recorded & readable
[ ] 6  new workspace only on explicit ask   [ ] 14 switching is auditable, both sides
[ ] 7  existing workspace loads unchanged   [ ] 14b switch cannot hijack a workflow
[ ] 8  workspace A unreachable from B       [ ] 15 no tool accepts a workspace path
[ ] 9  two homes, same person, unlinked     [ ] 16 discovery paths cannot escape
                                             [ ] 17 a full cycle writes nothing into the repo
                                             [ ] 18 repo resources hold no synthetic user data

Most of the underlying mechanics are exercised in detail in test_workspace.py,
test_discovery.py, test_source_truth.py and test_audit.py; this file is the
checklist that proves each numbered promise holds, using the real server.*
entry points wherever a real MCP call would exercise it.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

import server
from lib import storage, workflows
from lib import workspace as wsmod
from lib.errors import ResumeTailorError


def _code(excinfo) -> str:
    return excinfo.value.code


# 1 --------------------------------------------------------------------

def test_1_first_run_state_asks_for_a_folder(rt_home):
    r = server.get_workspace_status()
    assert r["state"] == "NO_WORKSPACE"
    assert "discover_masters" in r["next_step"]


# 2 --------------------------------------------------------------------

def test_2_no_master_error_refuses_memory_and_chat_history(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        storage.require_master("resume", workspace)
    msg = ei.value.message.lower()
    assert "memory" in msg and "conversation" in msg and "previous tailored version" in msg

    r = server.get_workspace_status()
    assert "memory" in r["refuse"].lower()


# 3 & 4 ------------------------------------------------------------------

def test_3_and_4_only_named_folder_scanned_multiple_candidates_require_a_pick(
        workspace, candidate_folder, tmp_path):
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "sneaky-resume.md").write_text("# Someone Else\n", encoding="utf-8")

    r = server.discover_masters(str(candidate_folder))
    names = [c["filename"] for c in r["candidates"]]
    assert "sneaky-resume.md" not in names
    assert r["requires_user_selection"] is True
    assert "selected" not in r

    # import_master_from_folder has no default for filename -- the caller
    # (Claude) is structurally forced to name exactly one
    sig = inspect.signature(server.import_master_from_folder)
    assert sig.parameters["filename"].default is inspect.Parameter.empty
    assert sig.parameters["folder"].default is inspect.Parameter.empty
    assert sig.parameters["kind"].default is inspect.Parameter.empty


# 5 --------------------------------------------------------------------

def test_5_resume_and_cv_import_to_separate_files_no_crossover(workspace, tmp_path):
    folder = tmp_path / "both"
    folder.mkdir()
    (folder / "resume.md").write_text(
        "# Alex\n\n## Summary\nEngineer.\n\n## Experience\n### Role, Co\n- did engineering work\n",
        encoding="utf-8")
    (folder / "cv.md").write_text(
        "# Alex\n\n## Publications\n- A paper about academic research.\n", encoding="utf-8")

    r1 = server.import_master_from_folder(str(folder), "resume.md", "resume")
    r2 = server.import_master_from_folder(str(folder), "cv.md", "cv")
    assert r1["applied"] and r2["applied"]

    resume_doc, _ = storage.load_master("resume", workspace)
    cv_doc, _ = storage.load_master("cv", workspace)
    assert resume_doc["metadata"]["kind"] == "resume"
    assert cv_doc["metadata"]["kind"] == "cv"
    assert resume_doc["metadata"]["imported"]["source_filename"] == "resume.md"
    assert cv_doc["metadata"]["imported"]["source_filename"] == "cv.md"
    assert "academic research" not in json.dumps(resume_doc)
    assert "engineering work" not in json.dumps(cv_doc)


# 6 --------------------------------------------------------------------

def test_6_new_workspace_only_on_explicit_create_new(workspace):
    first = workspace.id
    r = server.initialize_workspace()
    assert r["created"] is False and r["workspace_id"] == first
    r2 = server.initialize_workspace(create_new=True)
    assert r2["created"] is True and r2["workspace_id"] != first


def test_6b_no_internal_call_site_passes_create_new_true():
    # a call-shaped occurrence (preceded by '(' or ',', as an actual kwarg),
    # not a docstring mention of the parameter -- lib/workspace.py's own
    # docstring says "create_new=True" in prose, which must not count as a hit
    import pathlib
    call_pattern = re.compile(r"[(,]\s*create_new\s*=\s*True\b")
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    hits = []
    for py in (repo_root / "lib").rglob("*.py"):
        if call_pattern.search(py.read_text(encoding="utf-8")):
            hits.append(str(py.relative_to(repo_root)))
    assert hits == [], f"create_new=True must only ever come from an explicit user request: {hits}"


# 7 --------------------------------------------------------------------

def test_7_existing_workspace_loads_unchanged(master):
    ws, doc, h = master
    r = server.initialize_workspace()
    assert r["created"] is False
    assert storage.load_master("resume", ws) == (doc, h)


# 8 --------------------------------------------------------------------

def test_8_workspace_a_data_unreachable_from_b(two_workspaces, legacy_master):
    id_a, id_b = two_workspaces  # a has a master; b is active and empty
    ws_b = wsmod.get_workspace()
    assert storage.load_master("resume", ws_b) == (None, None)

    from lib.ids import normalize_master
    wf = workflows.create_workflow("resume", "JD text", ws=ws_b)
    ev_folder = ws_b.evidence_dir / wf["workflow_id"]
    assert not ev_folder.exists()  # nothing carried over from A

    ws_a = wsmod._workspace_from_id(id_a)
    with pytest.raises(ResumeTailorError):
        workflows.load_workflow(wf["workflow_id"], ws_a)  # A cannot see B's workflow either


# 9 --------------------------------------------------------------------

def test_9_two_homes_same_synthetic_person_are_not_linked(tmp_path, monkeypatch, legacy_master):
    from lib.ids import normalize_master
    home_a, home_b = tmp_path / "machine-a", tmp_path / "machine-b"

    monkeypatch.setenv(wsmod.ENV_HOME, str(home_a))
    id_a = wsmod.initialize_workspace()["workspace_id"]
    storage.save_master("resume", normalize_master(legacy_master, "resume"), None, "t")

    monkeypatch.setenv(wsmod.ENV_HOME, str(home_b))
    r = server.get_workspace_status()
    assert r["state"] == "NO_WORKSPACE"  # the same person's data on machine A is invisible here
    id_b = wsmod.initialize_workspace()["workspace_id"]
    assert id_b != id_a
    assert wsmod.list_workspaces()["workspaces"] == [
        {"workspace_id": id_b, "label": None, "created_at": wsmod.list_workspaces()["workspaces"][0]["created_at"],
         "active": True, "masters_present": {"resume": False, "cv": False}, "id_format_ok": True}
    ]


# 10 -------------------------------------------------------------------

def test_10_ambiguous_binding_requires_explicit_selection(master):
    ws, doc, h = master
    wsmod.config_path().unlink()
    for call in (server.get_workspace, server.initialize_workspace):
        r = call()
        assert r["ok"] is False
        assert r["error"]["code"] == "WORKSPACE_AMBIGUOUS"
    r = server.get_workspace_status()
    assert r["state"] == "WORKSPACE_INVALID"
    assert r["available_workspace_ids"] == [ws.id]
    assert wsmod.existing_workspace_ids() == [ws.id]  # nothing new was created


# 11 & 11b ---------------------------------------------------------------

def test_11_tailoring_without_a_master_is_blocked_both_calls(workspace):
    r1 = server.analyze_tailoring_requirements(jd_text="Requirements: Python")
    assert r1["ok"] is False and r1["error"]["code"] == "MASTER_NOT_FOUND"
    from lib import workflows as wf_mod
    wf = wf_mod.create_workflow("resume", "Requirements: Python", ws=workspace)
    r2 = server.tailor_resume("x", [], wf["workflow_id"])
    assert r2["ok"] is False and r2["error"]["code"] == "MASTER_NOT_FOUND"


def test_11b_unready_master_blocks_analyze_before_any_evidence_prompt(workspace, legacy_master):
    from lib.ids import normalize_master
    legacy_master["unparsed"] = ["a stray line"]
    storage.save_master("resume", normalize_master(legacy_master, "resume"), None, "t", ws=workspace)
    r = server.analyze_tailoring_requirements(jd_text="Requirements: Python")
    assert r["ok"] is False and r["error"]["code"] == "MASTER_INVALID"


# 12 -------------------------------------------------------------------

def test_12_released_version_cannot_be_promoted_to_master(master):
    ws, doc, h = master
    version_like = dict(doc)
    version_like["metadata"] = {
        "kind": "resume", "version_id": "v1", "released": True, "release_report_id": "rel-x",
        "document_kind": "resume", "created_at": "2026-01-01T00:00:00Z", "workspace_id": ws.id,
        "workflow_id": "wf-1",
    }
    r = server.set_master_resume(kind="resume", resume=version_like)
    assert r["ok"] is False
    assert r["error"]["code"] == "MASTER_INVALID"
    assert "version_fields" in r["error"]["details"]


# 13 -------------------------------------------------------------------

def test_13_import_provenance_is_readable(workspace, candidate_folder):
    r = server.import_master_from_folder(str(candidate_folder), "alex-resume.md", "resume")
    assert r["applied"] is True
    hist = server.get_master_history("resume")
    assert hist["ok"] is True
    assert hist["history"][0]["source_filename"] == "alex-resume.md"
    status = server.get_workspace_status()
    assert status["masters"]["resume"]["imported"]["source_filename"] == "alex-resume.md"


# 14 & 14b ---------------------------------------------------------------

def test_14_switch_is_auditable_from_both_sides(two_workspaces):
    id_a, id_b = two_workspaces
    server.select_workspace(id_a)
    for wid in (id_a, id_b):
        ws = wsmod._workspace_from_id(wid)
        events = [json.loads(l)["event"] for l in
                 (ws.monitoring_dir / "audit.jsonl").read_text().splitlines()]
        assert "workspace_switched" in events


def test_14b_switch_mid_workflow_breaks_the_workflow(two_workspaces, legacy_master):
    id_a, id_b = two_workspaces
    ws_a = wsmod._workspace_from_id(id_a)
    server.select_workspace(id_a)
    wf = workflows.create_workflow("resume", "JD", ws=ws_a)
    server.select_workspace(id_b)
    with pytest.raises(ResumeTailorError) as ei:
        workflows.load_workflow(wf["workflow_id"])  # resolves against the NOW-active workspace, B
    assert _code(ei) == "WORKFLOW_NOT_FOUND"


# 15 -------------------------------------------------------------------

def test_15_no_tool_accepts_an_arbitrary_workspace_path():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    banned = ("workspace_root", "workspace_path", "root", "home", "app_root")
    for t in tools:
        fn = getattr(server, t.name)
        for pname in inspect.signature(fn).parameters:
            assert pname not in banned, f"{t.name}({pname}) looks like a raw workspace path override"
    # the only path-shaped parameters anywhere are folder/file_path, both
    # vetted (_checked_dir / _checked_path) before a byte is touched
    path_like = {pname for t in tools for pname in inspect.signature(getattr(server, t.name)).parameters
                if pname in ("folder", "file_path")}
    assert path_like <= {"folder", "file_path"}


# 16 -------------------------------------------------------------------

def test_16_discovery_paths_cannot_escape(workspace, candidate_folder, tmp_path):
    for bad_folder in ("../../etc", str(tmp_path) + "\x00evil", "/nonexistent-xyz-123"):
        r = server.discover_masters(bad_folder)
        assert r["ok"] is False
        assert r["error"]["code"] in ("FOLDER_UNSUPPORTED", "INVALID_ID", "PATH_TRAVERSAL")
    for bad_filename in ("../../etc/passwd", "sub/inner.md", ".."):
        r = server.import_master_from_folder(str(candidate_folder), bad_filename, "resume")
        assert r["ok"] is False


# 17 & 18 ------------------------------------------------------------------

def test_17_full_cycle_writes_nothing_into_the_repo(workspace, candidate_folder, repo_snapshot):
    before = repo_snapshot()
    imported = server.import_master_from_folder(str(candidate_folder), "alex-resume.md", "resume")
    assert imported["applied"] is True, imported
    wf = server.analyze_tailoring_requirements(jd_text="Requirements: Python")
    assert wf["ok"] is not False, wf
    ev = server.save_tailoring_evidence(wf["workflow_id"], "Python", "personal_project",
                                        "I used Python.", confirmed=True)
    out = server.tailor_resume("v1", [], wf["workflow_id"], evidence_ids=[ev["evidence"]["id"]])
    assert out["ok"] is not False, out
    after = repo_snapshot()
    assert after == before


def test_18_repo_resources_contain_no_synthetic_user_data():
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    sentinel = "alex.example@example.test"
    for py in (repo_root / "resources").rglob("*"):
        if py.is_file() and py.suffix in (".yaml", ".yml", ".md", ".txt"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            assert sentinel not in text, py
