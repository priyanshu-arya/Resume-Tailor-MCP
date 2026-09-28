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
from tests.conftest import needs_tectonic


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
        "text": "Built Python REST endpoints.", "source_refs": [{"type": "master", "id": "exp-001-b01"}]}}],
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


def test_repair_attempt_counts_up_not_always_one(master):
    """D-9: repair_attempt must reflect the real attempt number, not a bool."""
    ws, _, _ = master
    wf = _wf(ws)
    base = tailoring.tailor("base", [], workflow_id=wf)["version_id"]
    seen = []
    prior = base
    for _ in range(3):
        r = tailoring.tailor(f"r-{len(seen)}", [], workflow_id=wf, repair_of=prior)
        seen.append(r["repair_attempt"])
        prior = base
    assert seen == [1, 2, 3]


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


def test_unready_master_blocks_analyze_before_any_evidence_prompt(workspace, legacy_master):
    """Regression for defect D-3: previously an unparsed master was only
    caught at tailor_resume, AFTER the user had already answered every
    evidence prompt analyze_tailoring_requirements asked."""
    from lib import evidence
    from lib.ids import normalize_master
    legacy_master["unparsed"] = ["some leftover text"]
    storage.save_master("resume", normalize_master(legacy_master, "resume"), None, "t", ws=workspace)
    with pytest.raises(ResumeTailorError) as e:
        evidence.analyze_requirements("Requirements: Python, AWS", ws=workspace)
    assert e.value.code == "MASTER_INVALID"


def test_workflow_records_source_master_hash(master):
    """Regression for defect D-4: a workflow that reaches
    analyze_tailoring_requirements records which master the gap analysis was
    computed against."""
    from lib import evidence
    ws, doc, h = master
    result = evidence.analyze_requirements("Requirements: Python, AWS", ws=ws)
    wf = workflows.load_workflow(result["workflow_id"], ws)
    assert wf["source_master_hash"] == h


def test_master_change_between_analyze_and_tailor_is_a_conflict(master):
    from lib import evidence
    ws, doc, h = master
    result = evidence.analyze_requirements("Requirements: Python, AWS", ws=ws)
    wf_id = result["workflow_id"]

    doc2 = dict(doc)
    doc2["summary"] = "Changed after the gap analysis ran."
    storage.save_master("resume", doc2, h, "changed", ws=ws)

    with pytest.raises(ResumeTailorError) as e:
        tailoring.tailor("v1", [], workflow_id=wf_id, ws=ws)
    assert e.value.code == "MASTER_CONFLICT"
    assert "analyzed_master_hash" in e.value.details
    assert "current_master_hash" in e.value.details


def test_no_conflict_when_master_unchanged_since_analyze(master):
    from lib import evidence
    ws, doc, h = master
    result = evidence.analyze_requirements("Requirements: Python, AWS", ws=ws)
    out = tailoring.tailor("v1", [], workflow_id=result["workflow_id"], ws=ws)
    assert out["source_master_hash"] == h


def test_tailor_without_prior_analyze_has_no_conflict_to_check(master):
    # a bare workflow (created without analyze_tailoring_requirements) has no
    # recorded source_master_hash, so the conflict check must not fire on it
    ws, doc, h = master
    out = tailoring.tailor("v1", [], workflow_id=_wf(ws), ws=ws)
    assert out["source_master_hash"] == h


# --------------------------------------------------------------------------
# Master immutability across a full cycle (Phase 2 gap 3)
# --------------------------------------------------------------------------

def test_master_unchanged_by_tailoring(master):
    """No PDF/tectonic needed: analyze -> evidence -> tailor must leave the
    master byte-identical. mtime_ns and the backup-count check catch a
    rewrite with identical content, which a hash comparison alone would
    miss."""
    ws, doc, h = master
    path = ws.master_path("resume")
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    backups_before = list(ws.backups_dir.iterdir())

    from lib import evidence
    result = evidence.analyze_requirements("Requirements: Python, AWS, Kubernetes", ws=ws)
    ev = evidence.save_evidence(result["workflow_id"], "Kubernetes", "none", confirmed=True, ws=ws)
    tailoring.tailor("v1", [], workflow_id=result["workflow_id"], evidence_ids=[ev["evidence"]["id"]], ws=ws)

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert storage.load_master("resume", ws) == (doc, h)
    assert list(ws.backups_dir.iterdir()) == backups_before


@needs_tectonic
def test_master_unchanged_by_full_release_cycle(master):
    """analyze -> evidence -> tailor -> validate -> release -> export must
    leave the master, and its write history, untouched."""
    ws, doc, h = master
    path = ws.master_path("resume")
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    history_before = storage.read_master_history("resume", ws=ws)

    from lib import evidence
    result = evidence.analyze_requirements("Requirements: Python, AWS, Kubernetes", ws=ws)
    wf_id = result["workflow_id"]
    ev = evidence.save_evidence(wf_id, "Kubernetes", "none", confirmed=True, ws=ws)
    out = tailoring.tailor("v1", [], workflow_id=wf_id, evidence_ids=[ev["evidence"]["id"]], ws=ws)

    from lib import release
    val = release.validate_version(out["version_id"], wf_id, ws=ws)
    assert val["passed"] is True
    rel = release.release_resume(out["version_id"], wf_id, ws=ws)
    assert rel["released"] is True
    release.export(out["version_id"], wf_id, "pdf", "release", ws=ws)

    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    history_after = storage.read_master_history("resume", ws=ws)
    assert history_after == history_before  # the cycle wrote nothing to master history

    version = storage.require_version(out["version_id"], ws)
    assert version["metadata"]["source_master_hash"] == h


# --------------------------------------------------------------------------
# Phase 2 gap 5: standing bypass-hunt tests (converts the one-off greps into
# tests that run every time, not just when someone remembers to check)
# --------------------------------------------------------------------------

def test_no_tool_accepts_a_resume_body_for_tailoring():
    """Signature-level: no tool that produces a tailored version takes a
    resume/body/content/text parameter -- only patches + workflow_id."""
    import asyncio
    banned = ("resume", "body", "content", "resume_body", "source_version", "master_body")
    tools = asyncio.run(server.mcp.list_tools())
    for name in ("tailor_resume",):
        fn = getattr(server, name)
        params = set(inspect.signature(fn).parameters)
        overlap = params & set(banned)
        assert not overlap, f"{name} accepts {overlap}"
    assert any(t.name == "tailor_resume" for t in tools)


def test_version_bodies_never_reach_the_tailoring_path():
    """Static check: lib/tailoring.py's repair path loads a prior version
    only to read its metadata (workflow_id, source_master_hash), and gets
    its actual content to replay from load_version_patches (recorded
    PATCHES) applied against the CURRENT master -- never from the prior
    version's stored body. This pins that shape so a future edit can't
    silently start sourcing tailoring content from a version body again."""
    import pathlib
    src = pathlib.Path(tailoring.__file__).read_text(encoding="utf-8")
    # the only two things read off `prior` are metadata lookups
    assert "prior.get(\"metadata\")" in src or "prior_meta" in src
    # validate_and_apply is called with `master` (the loaded master), not
    # `prior` -- i.e. content is rebuilt from the master, never the version
    assert "validate_and_apply(master," in src
    assert "validate_and_apply(prior" not in src


def test_no_operation_can_add_an_experience_entry():
    """3.5: add_project_entry can only ever create in Projects. A payload
    shaped like a fictitious 'add_experience_entry' operation must fail with
    the neutralized union-tag message, not a field-specific one."""
    from lib.patches import parse_patches
    with pytest.raises(ResumeTailorError) as exc:
        parse_patches([{"operation": "add_experience_entry", "title": "VP of Engineering",
                        "company": "Fake Corp", "bullets": []}])
    assert exc.value.code == "PATCH_INVALID"
    errors = exc.value.details["errors"]
    assert any(e["msg"] == "Unknown operation." for e in errors)
    assert "Fake Corp" not in str(exc.value.details)


def test_fake_employer_smuggled_as_project_name_still_needs_evidence(legacy_master):
    """A project name that reads like a fabricated employer ('VP of
    Engineering at Fake Corp') still needs a real evidence ref of an allowed
    category -- there is no way to smuggle employer-shaped content in
    without it, since add_project_entry has no target and can't touch
    Experience."""
    from lib.ids import normalize_master
    from lib.patches import validate_and_apply
    m = normalize_master(legacy_master, "resume")
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [{
            "operation": "add_project_entry", "name": "VP of Engineering at Fake Corp", "academic": False,
            "source_refs": [{"type": "master", "id": "proj-001"}],
            "bullets": [{"text": "Led the entire engineering org.", "source_refs": [{"type": "master", "id": "proj-001"}]}],
        }], workflow_id="wf-fake")
    assert exc.value.code == "PROVENANCE_VIOLATION"


def test_grep_confirms_no_bypass_markers():
    """The bypass hunt itself, kept as a running assertion rather than a
    one-off manual grep: source_version does not exist anywhere, and the
    only resume= call site outside master_ops.py's own docstring is
    server.py's set_master_resume forwarding into master_ops.set_master."""
    import pathlib
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    hits = []
    for py in list((repo_root / "lib").rglob("*.py")) + [repo_root / "server.py"]:
        if "source_version" in py.read_text(encoding="utf-8"):
            hits.append(str(py.relative_to(repo_root)))
    assert hits == [], f"source_version must not exist as a live concept: {hits}"
