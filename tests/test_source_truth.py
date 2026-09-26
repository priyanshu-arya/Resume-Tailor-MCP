"""Phase 2 acceptance: the master is the only source of truth.

[ ] arbitrary resume blobs rejected      [ ] master loaded by server
[ ] previous versions cannot become source
[ ] master cannot change during tailoring [ ] source references validated
"""

from __future__ import annotations

import inspect

import pytest

import server
from lib import storage, tailoring, workflows
from lib.errors import ResumeTailorError


def _wf(ws, kind="resume"):
    return workflows.create_workflow(kind, "Requirements: Python, AWS, Kubernetes", ws=ws)["workflow_id"]


def _replace(bid, text, refs):
    return {"operation": "replace_block", "target": {"id": bid},
            "new_content": {"text": text, "source_refs": [{"type": "master", "id": r} for r in refs]}}


def test_tailor_tool_has_no_resume_or_source_version_argument():
    params = inspect.signature(server.tailor_resume).parameters
    assert "resume" not in params and "source_version" not in params
    assert "patches" in params and "workflow_id" in params


def test_resume_blob_is_rejected_as_a_patch(master):
    ws, doc, _ = master
    blob = {k: v for k, v in doc.items() if k != "metadata"}
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("blob", [blob], workflow_id=_wf(ws))
    assert e.value.code == "PATCH_INVALID"
    assert server.tailor_resume("blob", [blob], _wf(ws))["error"]["code"] == "PATCH_INVALID"


def test_master_is_loaded_by_server_and_hash_recorded(master):
    ws, doc, h = master
    out = tailoring.tailor("acme", [_replace("exp-001-b01", "Built 18 Python REST endpoints, reducing median "
                                            "API latency 42%.", ["exp-001-b01"])], workflow_id=_wf(ws))
    version = storage.require_version(out["version_id"], ws)
    assert version["metadata"]["source_master_hash"] == h
    assert version["name"] == doc["name"]
    assert version["experience"][0]["bullets"][0]["source_refs"] == [{"type": "master", "id": "exp-001-b01"}]


def test_previous_version_cannot_be_cited_or_used_as_source(master):
    ws, _, _ = master
    wf = _wf(ws)
    first = tailoring.tailor("v1", [{"operation": "add_block", "parent_id": "exp-001", "new_content": {
        "text": "Implemented unit tests with pytest.", "source_refs": [{"type": "master", "id": "exp-002-b01"}]}}],
        workflow_id=wf)
    new_id = first["new_block_ids"][0]
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("v2", [_replace("exp-001-b01", "x", [new_id])], workflow_id=wf)
    assert e.value.code == "PROVENANCE_VIOLATION"
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("v3", [_replace("exp-001-b01", "x", [first["version_id"]])], workflow_id=wf)
    assert e.value.code == "PROVENANCE_VIOLATION"


def test_fabricated_and_missing_refs_rejected(master):
    ws, _, _ = master
    wf = _wf(ws)
    for refs in (["exp-099-b01"], []):
        with pytest.raises(ResumeTailorError) as e:
            tailoring.tailor("bad", [_replace("exp-001-b01", "Architected everything.", refs)], workflow_id=wf)
        assert e.value.code == "PROVENANCE_VIOLATION"
    assert storage.list_version_ids(ws) == []


def test_master_change_during_tailoring_aborts(master, monkeypatch):
    ws, doc, h = master
    wf = _wf(ws)
    real_apply = tailoring.validate_and_apply

    def apply_then_edit_master(*a, **k):
        result = real_apply(*a, **k)
        edited = dict(doc, summary="Edited concurrently.")
        storage.save_master("resume", edited, h, "concurrent edit", ws=ws)
        return result

    monkeypatch.setattr(tailoring, "validate_and_apply", apply_then_edit_master)
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("race", [], workflow_id=wf)
    assert e.value.code == "MASTER_CONFLICT"
    assert storage.list_version_ids(ws) == []


def test_header_fields_cannot_be_patched(master):
    ws, _, _ = master
    patch = {"operation": "replace_block", "target": {"id": "exp-001"}, "title": "CTO",
             "new_content": {"text": "x", "source_refs": [{"type": "master", "id": "exp-001"}]}}
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("hdr", [patch], workflow_id=_wf(ws))
    assert e.value.code == "PATCH_INVALID"


def test_workflow_kind_must_match_source_kind(master):
    ws, _, _ = master
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("x", [], workflow_id=_wf(ws, "cv"), source_kind="resume")
    assert e.value.code == "INVALID_KIND"


def test_workflow_id_required_and_scoped(master):
    ws, _, _ = master
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("x", [], workflow_id="")
    assert e.value.code == "WORKFLOW_REQUIRED"
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("x", [], workflow_id="wf-20260101-abcdef")
    assert e.value.code == "WORKFLOW_NOT_FOUND"


def test_same_save_as_gets_collision_safe_suffix_then_refuses(master):
    ws, _, _ = master
    wf = _wf(ws)
    a = tailoring.tailor("Acme SWE", [], workflow_id=wf)["version_id"]
    b = tailoring.tailor("Acme SWE", [], workflow_id=wf)["version_id"]
    assert a == "acme-swe" and b.startswith("acme-swe-") and a != b
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("Acme SWE", [], workflow_id=wf)
    assert e.value.code == "VERSION_EXISTS"


def test_repair_only_drops_or_reorders_and_keeps_provenance(master):
    ws, _, _ = master
    wf = _wf(ws)
    base = tailoring.tailor("base", [_replace("exp-001-b01", "Built 18 Python REST endpoints.", ["exp-001-b01"])],
                            workflow_id=wf)["version_id"]
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("r1", [_replace("exp-001-b02", "x", ["exp-001-b02"])], workflow_id=wf, repair_of=base)
    assert e.value.code == "PATCH_INVALID"
    fixed = tailoring.tailor("r1", [{"operation": "drop_block", "target": {"id": "proj-001"}}],
                             workflow_id=wf, repair_of=base)["version_id"]
    v = storage.require_version(fixed, ws)
    assert v["projects"] == []
    assert v["experience"][0]["bullets"][0]["text"] == "Built 18 Python REST endpoints."
    assert v["metadata"]["repair_of"] == base


def test_repair_limit(master):
    ws, _, _ = master
    wf = _wf(ws)
    base = tailoring.tailor("base", [], workflow_id=wf)["version_id"]
    for i in range(workflows.MAX_REPAIR_ATTEMPTS):
        tailoring.tailor(f"r{i}", [], workflow_id=wf, repair_of=base)
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("r-last", [], workflow_id=wf, repair_of=base)
    assert e.value.code == "REPAIR_LIMIT"


def test_unready_master_blocks_tailoring(workspace, legacy_master):
    from lib.ids import normalize_master
    legacy_master["unparsed"] = ["some leftover text"]
    storage.save_master("resume", normalize_master(legacy_master, "resume"), None, "t", ws=workspace)
    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("x", [], workflow_id=_wf(workspace))
    assert e.value.code == "MASTER_INVALID"
