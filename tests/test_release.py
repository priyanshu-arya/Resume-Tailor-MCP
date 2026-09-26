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
import shutil
from pathlib import Path

import pytest
import yaml

import server
from lib import storage
from lib.errors import ResumeTailorError

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    not ((REPO / "bin" / "tectonic").exists() or shutil.which("tectonic")), reason="tectonic not available")

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
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"], rel["critical_failures"]
    assert {"page_count", "margins_in", "body_font_pt_modal"} <= set(rel["measured_properties"])
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


def test_master_preview_is_draft_only(master):
    assert server.export_resume("master-resume", mode="release")["error"]["code"] == "NOT_RELEASED"
    draft = server.export_resume("master-resume", mode="draft")
    assert "DRAFT / UNVERIFIED" in draft[0].text


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
