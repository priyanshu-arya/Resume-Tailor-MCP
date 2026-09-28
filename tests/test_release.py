"""Phase 4 acceptance: template contract + release gate, end to end.

[ ] only registered templates work          [ ] unsupported templates cannot be released
[ ] requested template is actually rendered [ ] no silent fallback
[ ] formatting contract is validated        [ ] production PDF verification is mandatory
[ ] released versions are immutable
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

import server
from lib import audit, storage
from lib.errors import ResumeTailorError
from tests.conftest import needs_tectonic

pytestmark = needs_tectonic

JD = "Backend Engineer\nRequirements:\n- Python\n- AWS\n"


def _tailor(save_as="acme", patches=None, template="auto"):
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    out = server.tailor_resume(save_as, patches or [], wf, template=template)
    assert out["ok"], out
    return wf, out["version_id"]


def test_unregistered_template_rejected(master):
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    for bad in ("fancy-two-column", "../../templates/x", "Template 1.pdf"):
        assert server.tailor_resume("x", [], wf, template=bad)["error"]["code"] == "TEMPLATE_UNKNOWN"


def test_experimental_template_is_not_substituted_and_not_released(master):
    wf, vid = _tailor(template="metrics-driven")
    assert storage.require_version(vid)["metadata"]["template_id"] == "metrics-driven"
    rel = server.release_resume(vid, wf)
    assert rel["ok"] and rel["released"] is False
    assert "template.releasable" in {c["id"] for c in rel["critical_failures"]}
    draft = server.export_resume(vid, wf, mode="draft")
    assert draft["error"]["code"] == "TEMPLATE_NO_RENDERER"  # never a classic PDF in disguise


def test_release_happy_path_and_exact_identity(master):
    ws, _, _ = master
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"], rel["critical_failures"]
    assert {"page_count", "margins_in", "body_font_pt_modal"} <= set(rel["measured_properties"])
    pdf_events = [e for e in audit.read_events(ws) if e["event"] == "pdf_compiled"]
    assert pdf_events and pdf_events[-1]["status"] == "pass"
    assert pdf_events[-1]["page_count"] == rel["measured_properties"]["page_count"]
    exported = server.export_resume(vid, wf)
    text, blob = exported[0].text, exported[1].resource.blob
    tex = text.split("```latex\n", 1)[1].rsplit("\n```", 1)[0]
    assert hashlib.sha256(tex.encode()).hexdigest() == rel["tex_sha256"]
    import base64
    assert hashlib.sha256(base64.b64decode(blob)).hexdigest() == rel["pdf_sha256"]
    assert "RELEASED" in text
    assert r"\documentclass[letterpaper,11pt]{article}" in tex  # classic-minimalist actually rendered
    ws = storage.get_workspace()
    report_files = list(ws.releases_dir.glob(f"{vid}-rel-*.json"))
    assert len(report_files) == 1
    report = json.loads(report_files[0].read_text())
    assert report["released"] and report["version_id"] == vid and report["workspace_id"] == ws.id
    assert "Alex Example" not in report_files[0].read_text()  # no resume content in the report
    assert Path(json.loads(text.split("```json\n")[1].split("\n```")[0])["pdf_path"]).name.startswith("Alex_Example_")


def test_lifecycle_state_is_derived_not_persisted(master):
    """Phase 6 lifecycle decision: no stored 'validated' flag -- a version is
    only ever draft or released, and every surface agrees."""
    wf, vid = _tailor()
    assert server.validate_version(vid, wf)["lifecycle_state"] == "draft"
    assert {v["version_id"]: v["lifecycle_state"] for v in server.list_versions()["versions"]}[vid] == "draft"
    status = server.get_workflow_status(wf)
    assert {v["version_id"]: v["lifecycle_state"] for v in status["versions"]}[vid] == "draft"

    rel = server.release_resume(vid, wf)
    assert rel["lifecycle_state"] == "released"
    assert server.validate_version(vid, wf)["lifecycle_state"] == "released"
    assert {v["version_id"]: v["lifecycle_state"] for v in server.list_versions()["versions"]}[vid] == "released"
    status = server.get_workflow_status(wf)
    assert {v["version_id"]: v["lifecycle_state"] for v in status["versions"]}[vid] == "released"

    again = server.release_resume(vid, wf)
    assert again["lifecycle_state"] == "released"


def test_flipped_pdf_byte_is_caught_on_export(master):
    ws, _, _ = master
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"]
    base = rel["export_basename"]
    pdf_path = ws.exports_dir / f"{base}.pdf"
    data = bytearray(pdf_path.read_bytes())
    data[-1] ^= 0xFF  # flip the last byte
    pdf_path.write_bytes(bytes(data))
    out = server.export_resume(vid, wf, mode="release")
    assert out["error"]["code"] == "NOT_RELEASED"


def test_flipped_tex_byte_is_caught_on_export(master):
    ws, _, _ = master
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"]
    base = rel["export_basename"]
    tex_path = ws.exports_dir / f"{base}.tex"
    tex_path.write_text(tex_path.read_text(encoding="utf-8") + "% tampered\n", encoding="utf-8")
    out = server.export_resume(vid, wf, mode="release")
    assert out["error"]["code"] == "NOT_RELEASED"


def test_second_release_writes_no_second_report(master):
    ws, _, _ = master
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"]
    before = sorted((ws.releases_dir).glob(f"{vid}-rel-*.json"))
    again = server.release_resume(vid, wf)
    assert again["already_released"]
    after = sorted((ws.releases_dir).glob(f"{vid}-rel-*.json"))
    assert before == after and len(after) == 1


def test_released_version_is_immutable(master):
    wf, vid = _tailor()
    assert server.release_resume(vid, wf)["released"]
    with pytest.raises(ResumeTailorError) as e:
        storage.update_version_metadata(vid, {"template_id": "metrics-driven"})
    assert e.value.code == "VERSION_RELEASED"
    with pytest.raises(ResumeTailorError) as e:
        storage.save_version(vid, storage.require_version(vid))
    assert e.value.code == "VERSION_RELEASED"
    again = server.release_resume(vid, wf)
    assert again["already_released"]


def test_export_requires_release(master):
    wf, vid = _tailor()
    assert server.export_resume(vid, wf)["error"]["code"] == "NOT_RELEASED"
    draft = server.export_resume(vid, wf, mode="draft")
    assert "DRAFT / UNVERIFIED" in draft[0].text


def test_pdf_verification_unavailable_blocks_release(master, monkeypatch):
    from lib.validators import pdf as pdfmod
    monkeypatch.setattr(pdfmod, "detect_pdf_backend", lambda: {
        "available": False, "backends": [], "poppler": False,
        "message": "PDF validation unavailable. Production release blocked."})
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"] is False
    assert any("PDF validation unavailable" in c["message"] for c in rel["critical_failures"])
    assert not storage.require_version(vid)["metadata"]["released"]


def test_page_cap_from_career_stage_blocks(workspace, legacy_master):
    from lib.ids import normalize_master
    long_master = copy.deepcopy(legacy_master)
    for i in range(12):
        long_master["experience"].append({
            "title": "Engineer", "company": f"Company {i}", "start": "Jan 2010", "end": "Dec 2011",
            "bullets": [{"text": f"Built service {i}-{j} handling billing, search and reporting workloads."}
                        for j in range(5)]})
    doc = normalize_master(long_master, "resume")
    doc["metadata"]["career_stage"] = "fresher"
    storage.save_master("resume", doc, None, "test", ws=workspace)
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"] is False
    assert "pdf.page_count" in {c["id"] for c in rel["critical_failures"]}
    assert rel["measured_properties"]["page_count"] > 1


def test_tampered_version_on_disk_is_caught(master):
    ws = storage.get_workspace()
    wf, vid = _tailor()
    path = storage.version_path(vid)
    doc = yaml.safe_load(path.read_text())
    doc["experience"][0]["bullets"][0]["text"] = "Architected a global platform serving 50M users."
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    rel = server.release_resume(vid, wf)
    assert rel["released"] is False
    assert "provenance.replay" in {c["id"] for c in rel["critical_failures"]}


def test_master_change_after_tailoring_blocks_release(master):
    ws, doc, h = master
    wf, vid = _tailor()
    storage.save_master("resume", dict(doc, summary="Changed summary text for a new master revision."), h, "edit")
    rel = server.release_resume(vid, wf)
    assert rel["released"] is False
    assert "source.master_hash" in {c["id"] for c in rel["critical_failures"]}


def test_source_master_hash_mismatch_is_critical_and_skips_replay(master):
    """When the master hash differs, provenance.replay is not appended at
    all (not run against a master that no longer matches) -- assert both
    halves, so its absence is never misread as a pass."""
    from lib import release as _release
    ws, doc, h = master
    wf, vid = _tailor()
    storage.save_master("resume", dict(doc, summary="Changed summary text for a new master revision."), h, "edit")
    result = _release.validate(vid, wf)
    ids = [c.id for c in result["report"].checks]
    c = {c.id: c for c in result["report"].checks}["source.master_hash"]
    assert (c.status, c.severity) == ("fail", "critical")
    assert "provenance.replay" not in ids


def test_master_preview_is_draft_only(master):
    assert server.export_resume("master-resume", mode="release")["error"]["code"] == "NOT_RELEASED"
    draft = server.export_resume("master-resume", mode="draft")
    assert "DRAFT / UNVERIFIED" in draft[0].text


def test_missing_released_file_is_a_business_error(master):
    """D-10: a released file deleted from disk must surface as NOT_RELEASED,
    not an unhandled OSError turned into INTERNAL_ERROR."""
    ws, _, _ = master
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"]
    base = rel["export_basename"]
    (ws.exports_dir / f"{base}.pdf").unlink()
    out = server.export_resume(vid, wf, mode="release")
    assert out["error"]["code"] == "NOT_RELEASED"


def test_unknown_template_on_export_is_a_business_error(master):
    """D-11: lib/export.py's internal template lookup must raise
    TEMPLATE_UNKNOWN, not a bare ValueError surfacing as INTERNAL_ERROR."""
    from lib import export as _export
    from lib.errors import ResumeTailorError
    with pytest.raises(ResumeTailorError) as e:
        _export._section_order("not-a-real-template")
    assert e.value.code == "TEMPLATE_UNKNOWN"


def test_repair_path_after_blocked_release(workspace, legacy_master):
    """A too-long version is repaired by dropping blocks, then releases."""
    from lib.ids import normalize_master
    m = copy.deepcopy(legacy_master)
    for i in range(12):
        m["experience"].append({"title": "Engineer", "company": f"Company {i}", "start": "2010", "end": "2011",
                                "bullets": [{"text": f"Built service {i}-{j} for billing and search."}
                                            for j in range(5)]})
    doc = normalize_master(m, "resume")
    doc["metadata"]["career_stage"] = "fresher"
    storage.save_master("resume", doc, None, "test", ws=workspace)
    wf, vid = _tailor()
    assert server.release_resume(vid, wf)["released"] is False
    drops = [{"operation": "drop_block", "target": {"id": e["id"]}} for e in doc["experience"][2:]]
    fixed = server.tailor_resume("acme-fixed", drops, wf, repair_of=vid)
    assert fixed["ok"], fixed
    rel = server.release_resume(fixed["version_id"], wf)
    assert rel["released"], rel["critical_failures"]


def test_replay_with_an_added_project_entry(master):
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    saved = server.save_tailoring_evidence(
        wf, "Kubernetes", "personal_project", "Built a personal Kubernetes operator for fun.", confirmed=True)
    eid = saved["evidence"]["id"]
    out = server.tailor_resume("acme-proj", [{
        "operation": "add_project_entry", "name": "K8s Operator", "stack": "Kubernetes", "academic": False,
        "source_refs": [{"type": "evidence", "id": eid}], "claim_strength": "personal_project",
        "bullets": [{"text": "Built a Kubernetes operator to automate deployments.",
                    "source_refs": [{"type": "evidence", "id": eid}]}],
    }], wf, evidence_ids=[eid])
    assert out["ok"], out
    assert out["new_entry_ids"]
    rel = server.release_resume(out["version_id"], wf)
    assert rel["released"], rel["critical_failures"]
    assert "provenance.replay" not in {c["id"] for c in rel.get("critical_failures", [])}


def test_hand_added_project_fails_replay(master):
    wf, vid = _tailor()
    path = storage.version_path(vid)
    doc = yaml.safe_load(path.read_text())
    doc["projects"].append({"id": "vp-fake", "name": "Fabricated Project", "stack": "Kubernetes",
                            "academic": False, "source_refs": [], "claim_strength": None, "bullets": []})
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    rel = server.release_resume(vid, wf)
    assert rel["released"] is False
    assert "provenance.replay" in {c["id"] for c in rel["critical_failures"]}
