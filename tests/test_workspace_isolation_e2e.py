"""Phase 8.6 Gate 3: two-workspace isolation, end to end.

Two homes, two masters, interleaved workflows -- everything below operates
on explicit `ws=` objects (like the rest of this suite's lib-level tests),
not the server's single active-binding model, because the point is to prove
isolation between two workspaces that exist *at the same time*, not to
exercise `select_workspace`.

[ ] A's workflow/evidence/version ids are not found in B
[ ] each audit log contains only its own workspace_id
[ ] each exports/ dir holds only its own basenames
[ ] release reports do not cross-reference
[ ] no path under A appears under B
[ ] two threads, one workspace_lock each, do not deadlock
"""

from __future__ import annotations

import copy
import json
import shutil
import threading
from pathlib import Path

import pytest

from lib import audit, evidence, release, storage, tailoring, workflows
from lib import workspace as wsmod
from lib.errors import ResumeTailorError
from lib.ids import normalize_master

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    not ((REPO / "bin" / "tectonic").exists() or shutil.which("tectonic")), reason="tectonic not available")

JD = "Backend Engineer\nRequirements:\n- Python\n- AWS\n"


def _master_for(name: str) -> dict:
    from tests.conftest import SYNTHETIC_LEGACY_MASTER
    m = copy.deepcopy(SYNTHETIC_LEGACY_MASTER)
    m["name"] = name
    m["contact"]["email"] = f"{name.split()[0].lower()}@example.test"
    return m


@pytest.fixture
def two_live_workspaces(rt_home):
    """Two initialized, independently-mastered workspaces in one
    RESUME_TAILOR_HOME, each captured as its own live Workspace object."""
    a = wsmod.initialize_workspace()
    ws_a = wsmod.get_workspace()
    storage.save_master("resume", normalize_master(_master_for("Workspace Alpha"), "resume"),
                        None, "test", ws=ws_a)

    b = wsmod.initialize_workspace(create_new=True)
    ws_b = wsmod.get_workspace()
    storage.save_master("resume", normalize_master(_master_for("Workspace Beta"), "resume"),
                        None, "test", ws=ws_b)

    assert ws_a.id != ws_b.id
    return ws_a, ws_b


def _run_workflow(ws, save_as: str):
    """analyze -> evidence -> tailor -> validate -> release, all against one
    explicit workspace. Returns (workflow_id, version_id, release_result)."""
    result = evidence.analyze_requirements(JD, "resume", ws=ws)
    wf = result["workflow_id"]
    ev_res = evidence.save_evidence(wf, "aws", "personal_project",
                                    "Ran a side project's infra on AWS.", confirmed=True, ws=ws)
    eid = ev_res["evidence"]["id"]
    tailor_out = tailoring.tailor(save_as, [], workflow_id=wf, evidence_ids=[eid], ws=ws)
    vid = tailor_out["version_id"]
    rel = release.release_resume(vid, wf, ws=ws)
    return wf, vid, eid, rel


def test_workflow_evidence_and_version_ids_not_found_across_workspaces(two_live_workspaces):
    ws_a, ws_b = two_live_workspaces
    wf_a, vid_a, eid_a, rel_a = _run_workflow(ws_a, "iso-a")
    wf_b, vid_b, eid_b, rel_b = _run_workflow(ws_b, "iso-b")

    assert rel_a["released"] and rel_b["released"]
    assert wf_a != wf_b and vid_a != vid_b and eid_a != eid_b

    with pytest.raises(ResumeTailorError) as e:
        workflows.load_workflow(wf_a, ws=ws_b)
    assert e.value.code == "WORKFLOW_NOT_FOUND"

    with pytest.raises(ResumeTailorError) as e:
        workflows.load_workflow(wf_b, ws=ws_a)
    assert e.value.code == "WORKFLOW_NOT_FOUND"

    assert not (ws_b.evidence_dir / wf_a).exists()
    assert not (ws_a.evidence_dir / wf_b).exists()

    with pytest.raises(ResumeTailorError):
        storage.require_version(vid_a, ws_b)
    with pytest.raises(ResumeTailorError):
        storage.require_version(vid_b, ws_a)


def test_each_audit_log_contains_only_its_own_workspace_id(two_live_workspaces):
    ws_a, ws_b = two_live_workspaces
    _run_workflow(ws_a, "iso-a2")
    _run_workflow(ws_b, "iso-b2")

    events_a = audit.read_events(ws_a)
    events_b = audit.read_events(ws_b)
    assert events_a and events_b
    assert {e["workspace_id"] for e in events_a} == {ws_a.id}
    assert {e["workspace_id"] for e in events_b} == {ws_b.id}


def test_exports_and_release_reports_do_not_cross_reference(two_live_workspaces):
    ws_a, ws_b = two_live_workspaces
    wf_a, vid_a, _eid_a, rel_a = _run_workflow(ws_a, "iso-a3")
    wf_b, vid_b, _eid_b, rel_b = _run_workflow(ws_b, "iso-b3")

    basenames_a = {p.stem for p in ws_a.exports_dir.glob("*.pdf")}
    basenames_b = {p.stem for p in ws_b.exports_dir.glob("*.pdf")}
    assert basenames_a and basenames_b
    assert basenames_a.isdisjoint(basenames_b)
    assert rel_a["export_basename"] in basenames_a
    assert rel_a["export_basename"] not in basenames_b
    assert rel_b["export_basename"] in basenames_b
    assert rel_b["export_basename"] not in basenames_a

    report_a = release.load_release_report(vid_a, rel_a["release_report_id"], ws_a)
    report_b = release.load_release_report(vid_b, rel_b["release_report_id"], ws_b)
    assert report_a["workspace_id"] == ws_a.id and report_b["workspace_id"] == ws_b.id
    assert report_a["version_id"] != report_b["version_id"]
    assert report_a["workflow_id"] == wf_a and report_b["workflow_id"] == wf_b
    blob_a, blob_b = json.dumps(report_a), json.dumps(report_b)
    assert wf_b not in blob_a and vid_b not in blob_a
    assert wf_a not in blob_b and vid_a not in blob_b


def test_no_path_under_a_appears_under_b(two_live_workspaces):
    ws_a, ws_b = two_live_workspaces
    _run_workflow(ws_a, "iso-a4")
    _run_workflow(ws_b, "iso-b4")

    a_root_str = str(ws_a.root)
    for p in ws_b.root.rglob("*"):
        if p.is_file():
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            assert a_root_str not in text, f"{ws_a.id}'s path leaked into {p}"
    assert not (ws_b.root / ws_a.id).exists()


def test_concurrent_workflows_in_separate_workspaces_do_not_deadlock(two_live_workspaces):
    """Two threads, one workspace_lock each (via a full tailor+release run) --
    proves the workspace lock and the audit lock do not deadlock across
    workspaces. tests/test_locking.py only covers single-workspace
    contention."""
    ws_a, ws_b = two_live_workspaces
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def run(ws, key, save_as):
        try:
            results[key] = _run_workflow(ws, save_as)
        except BaseException as e:  # noqa: BLE001 - surfaced to the main thread below
            errors.append(e)

    t_a = threading.Thread(target=run, args=(ws_a, "a", "iso-conc-a"))
    t_b = threading.Thread(target=run, args=(ws_b, "b", "iso-conc-b"))
    t_a.start()
    t_b.start()
    t_a.join(timeout=60)
    t_b.join(timeout=60)

    assert not t_a.is_alive(), "workspace A's thread did not finish -- possible deadlock"
    assert not t_b.is_alive(), "workspace B's thread did not finish -- possible deadlock"
    assert not errors, errors
    assert results["a"][3]["released"] and results["b"][3]["released"]
    assert results["a"][0] != results["b"][0]
