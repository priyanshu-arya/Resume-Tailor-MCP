"""Phase 8.1: the server-assembled completion block and checklist.

[ ] every check id in every validator maps into a checklist group
[ ] all seven groups always present, including not_run
[ ] worst status wins
[ ] added_after_confirmation only references real evidence/block ids
[ ] no resume text leaks into the completion block (section names/ids are ok)
[ ] blocked-release completion shape
[ ] why sentences come from the placement table
"""

from __future__ import annotations

import pytest

import server
from lib import reporting
from lib.validators import content, format_tex, pdf, structure
from lib.release import ALL_CHECK_IDS as RELEASE_CHECK_IDS
from tests.conftest import needs_tectonic

pytestmark = needs_tectonic

JD = "Backend Engineer\nRequirements:\n- Python\n- AWS\n"


def _tailor(save_as="acme", patches=None, template="auto"):
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    out = server.tailor_resume(save_as, patches or [], wf, template=template)
    assert out["ok"], out
    return wf, out["version_id"]


ALL_MODULE_CHECK_IDS = (
    content.ALL_CHECK_IDS + structure.ALL_CHECK_IDS + format_tex.ALL_CHECK_IDS
    + pdf.ALL_CHECK_IDS + RELEASE_CHECK_IDS
)


def test_checklist_covers_every_check_id():
    """The union of all five ALL_CHECK_IDS tuples must map into GROUP_ORDER --
    an id a validator can emit but the checklist can't place would make a
    new check silently invisible to the completion message."""
    for check_id in ALL_MODULE_CHECK_IDS:
        assert reporting.group_of(check_id) in reporting.GROUP_ORDER, check_id


def test_group_of_raises_on_unmapped_id():
    with pytest.raises(ValueError):
        reporting.group_of("nonsense.not_a_real_check")


def test_checklist_always_has_all_seven_groups_including_not_run():
    out = reporting.checklist([])
    assert set(out) == set(reporting.GROUP_ORDER)
    assert all(v["status"] == "not_run" for v in out.values())


def test_checklist_worst_status_wins():
    checks = [
        {"id": "content.dates_present", "status": "pass", "severity": "warning"},
        {"id": "content.bullet_length", "status": "warning", "severity": "warning"},
        {"id": "content.required_sections", "status": "fail", "severity": "critical"},
    ]
    out = reporting.checklist(checks)
    assert out["content"]["status"] == "fail"
    assert out["content"]["failed"] == ["content.required_sections"]
    assert out["content"]["warnings"] == ["content.bullet_length"]


def test_checklist_not_available_status():
    checks = [{"id": "template.page_size", "status": "not_available", "severity": "error"}]
    out = reporting.checklist(checks)
    assert out["latex"]["status"] == "not_available"


def test_completion_block_on_release_happy_path(master):
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    assert rel["released"]
    c = rel["completion"]
    assert c["lifecycle_state"] == "released"
    assert c["released"] is True
    assert c["source"] == "workspace master resume"
    assert c["template"]["id"] and c["template"]["releasable"] is True
    assert c["display_order"][0] == "source"
    assert set(reporting.GROUP_ORDER) == set(c["validation"]["checklist"])
    assert c["tex_sha256"] and c["pdf_sha256"]
    assert c["export_basename"]


def test_completion_block_on_blocked_release_is_truthful(master):
    wf, vid = _tailor(template="metrics-driven")  # experimental: cannot release
    rel = server.release_resume(vid, wf)
    assert rel["released"] is False
    c = rel["completion"]
    assert c["released"] is False
    assert c["lifecycle_state"] == "blocked"
    assert c["validation"]["critical_failures"]
    assert "template.releasable" in c["validation"]["critical_failures"]


def test_added_after_confirmation_only_references_real_ids(master):
    from lib import evidence as ev, storage
    from lib.ids import index_blocks
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    res = ev.save_evidence(wf, "docker", "personal_project", "Containerized a side project with Docker.",
                           confirmed=True)
    eid = res["evidence"]["id"]
    out = server.tailor_resume(
        "acme", [{"operation": "add_skill_item", "category": "Tools", "name": "Docker",
                 "source_refs": [{"type": "evidence", "id": eid}], "claim_strength": "personal_project"}],
        wf, evidence_ids=[eid])
    assert out["ok"], out
    rel = server.release_resume(out["version_id"], wf)
    c = rel["completion"]
    block_ids = set(index_blocks(storage.require_version(out["version_id"])))
    for item in c["added_after_confirmation"]:
        assert item["evidence_id"] == eid
        assert item["block_id"] in block_ids
        assert "Docker" in item["term"] or item["term"].lower() == "docker"


def test_no_resume_text_leaks_into_completion(master):
    wf, vid = _tailor()
    rel = server.release_resume(vid, wf)
    c = rel["completion"]
    blob = str(c)
    assert "alex.example@example.test" not in blob


def test_why_sentences_come_from_placement_table(master):
    from lib import evidence as ev
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    res = ev.save_evidence(wf, "figma", "personal_project", "Designed UI mockups in Figma for a hobby app.",
                           confirmed=True)
    eid = res["evidence"]["id"]
    out = server.tailor_resume(
        "acme2", [{"operation": "add_skill_item", "category": "Tools", "name": "Figma",
                  "source_refs": [{"type": "evidence", "id": eid}], "claim_strength": "personal_project"}],
        wf, evidence_ids=[eid])
    assert out["ok"], out
    rel = server.release_resume(out["version_id"], wf)
    added = rel["completion"]["added_after_confirmation"]
    assert added
    for item in added:
        assert item["evidence_id"] in item["why"]
        assert item["category"] in item["why"]
