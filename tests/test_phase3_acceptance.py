"""Phase 3 acceptance, end to end through the MCP tool functions.

[ ] missing skill triggers evidence workflow
[ ] unsupported skill not added
[ ] confirmed project skill can be used in project context
[ ] learning-only skill cannot become professional experience
[ ] generated blocks have valid source refs
[ ] unsupported metrics are rejected (and so can never reach release)
"""

from __future__ import annotations

import server
from lib import storage

JD = """Backend Engineer
Requirements:
- Python
- FastAPI
- Kubernetes
- AWS
"""


def _start(master):
    analysis = server.analyze_tailoring_requirements(JD)
    assert analysis["ok"], analysis
    return analysis


def _evidence(wf, term, category, text, metrics=None):
    out = server.save_tailoring_evidence(wf, term, category, text, confirmed=True, metrics=metrics)
    assert out["ok"], out
    return out["evidence"]["id"]


def test_missing_skill_triggers_evidence_prompt(master):
    analysis = _start(master)
    prompted = {p["term"] for p in analysis["evidence_prompts"]}
    assert {"fastapi", "kubernetes"} <= prompted
    assert "python" in analysis["confirmed"]


def test_unconfirmed_evidence_cannot_be_saved(master):
    wf = _start(master)["workflow_id"]
    out = server.save_tailoring_evidence(wf, "Kubernetes", "professional", "ran clusters", confirmed=False)
    assert out["error"]["code"] == "EVIDENCE_INVALID"


def test_unsupported_skill_is_not_added_and_is_reported(master):
    wf = _start(master)["workflow_id"]
    ev_none = _evidence(wf, "Kubernetes", "none", "")
    add = {"operation": "add_skill_item", "category": "Cloud", "name": "Kubernetes",
           "source_refs": [{"type": "evidence", "id": ev_none}]}
    assert server.tailor_resume("k8s", [add], wf, evidence_ids=[ev_none])["error"]["code"] == "PROVENANCE_VIOLATION"
    # without any source it is rejected too -- no inferred provenance
    add["source_refs"] = []
    assert server.tailor_resume("k8s", [add], wf)["error"]["code"] == "PROVENANCE_VIOLATION"
    ok = server.tailor_resume("plain", [], wf, evidence_ids=[ev_none])
    assert ok["ok"]
    assert any(n["term"].lower() == "kubernetes" for n in ok["not_added"])


def test_confirmed_project_skill_used_in_project_context_only(master):
    wf = _start(master)["workflow_id"]
    ev = _evidence(wf, "FastAPI", "personal_project",
                   "Built REST APIs using FastAPI for my personal RAG project.")
    project_bullet = {"operation": "add_block", "parent_id": "proj-001", "new_content": {
        "text": "Built REST APIs with FastAPI for the note search backend.",
        "source_refs": [{"type": "evidence", "id": ev}], "claim_strength": "personal_project"}}
    skill = {"operation": "add_skill_item", "category": "Frameworks", "name": "FastAPI",
             "source_refs": [{"type": "evidence", "id": ev}], "claim_strength": "personal_project"}
    ok = server.tailor_resume("fastapi-proj", [project_bullet, skill], wf, evidence_ids=[ev])
    assert ok["ok"], ok
    placed = {(a["term"].lower(), a["section"]) for a in ok["added_terms"]}
    assert placed == {("fastapi", "Projects"), ("fastapi", "Skills")}

    experience_bullet = {"operation": "add_block", "parent_id": "exp-001", "new_content": {
        "text": "Built REST APIs with FastAPI.", "source_refs": [{"type": "evidence", "id": ev}]}}
    bad = server.tailor_resume("fastapi-exp", [experience_bullet], wf, evidence_ids=[ev])
    assert bad["error"]["code"] == "PROVENANCE_VIOLATION"


def test_learning_only_cannot_become_professional_experience(master):
    wf = _start(master)["workflow_id"]
    ev = _evidence(wf, "Kubernetes", "learning_only", "I am learning Kubernetes through an online course.")
    expand = {"operation": "replace_block", "target": {"id": "exp-001-b02"}, "new_content": {
        "text": "Implemented Kubernetes infrastructure for 3 product teams.",
        "source_refs": [{"type": "master", "id": "exp-001-b02"}, {"type": "evidence", "id": ev}],
        "claim_strength": "professional"}}
    assert server.tailor_resume("k8s-exp", [expand], wf, evidence_ids=[ev])["error"]["code"] == "PROVENANCE_VIOLATION"
    familiar = {"operation": "add_skill_item", "category": "Familiar With", "name": "Kubernetes",
                "source_refs": [{"type": "evidence", "id": ev}], "claim_strength": "learning_only"}
    assert server.tailor_resume("k8s-familiar", [familiar], wf, evidence_ids=[ev])["ok"]


def test_evidence_from_other_workflow_rejected(master):
    wf_a = _start(master)["workflow_id"]
    wf_b = _start(master)["workflow_id"]
    ev_a = _evidence(wf_a, "FastAPI", "personal_project", "Built REST APIs using FastAPI for my RAG project.")
    skill = {"operation": "add_skill_item", "category": "Frameworks", "name": "FastAPI",
             "source_refs": [{"type": "evidence", "id": ev_a}]}
    out = server.tailor_resume("cross", [skill], wf_b, evidence_ids=[ev_a])
    assert out["ok"] is False and out["error"]["code"] in ("EVIDENCE_NOT_FOUND", "PROVENANCE_VIOLATION")


def test_every_generated_block_has_valid_source_refs(master):
    ws, doc, _ = master
    wf = _start(master)["workflow_id"]
    ev = _evidence(wf, "FastAPI", "personal_project", "Built REST APIs using FastAPI for my RAG project.")
    out = server.tailor_resume("refs", [
        {"operation": "replace_block", "target": {"id": "sum-001"}, "new_content": {
            "text": "Backend engineer with 4 years building Python services and data pipelines on AWS.",
            "source_refs": [{"type": "master", "id": "sum-001"}]}},
        {"operation": "add_skill_item", "category": "Frameworks", "name": "FastAPI",
         "source_refs": [{"type": "evidence", "id": ev}]},
    ], wf, evidence_ids=[ev])
    assert out["ok"], out
    version = storage.require_version(out["version_id"], ws)
    from lib.ids import index_blocks
    master_ids = set(index_blocks(doc))
    blocks = [b for s in ("experience", "projects", "education") for e in version[s] for b in e["bullets"]]
    blocks += [i for g in version["skills"] for i in g["items"]] + version["certifications"]
    for block in blocks:
        assert block["source_refs"], block["id"]
        for ref in block["source_refs"]:
            assert (ref["type"] == "master" and ref["id"] in master_ids) or \
                   (ref["type"] == "evidence" and ref["id"] == ev)
    assert version["metadata"]["summary_source_refs"]
    assert version["metadata"]["evidence_ids"] == [ev]


def test_unsupported_metric_rejected(master):
    wf = _start(master)["workflow_id"]
    invented = {"operation": "replace_block", "target": {"id": "exp-002-b01"}, "new_content": {
        "text": "Implemented unit tests with pytest for a billing service, raising coverage 95%.",
        "source_refs": [{"type": "master", "id": "exp-002-b01"}]}}
    assert server.tailor_resume("metric", [invented], wf)["error"]["code"] == "PROVENANCE_VIOLATION"
    kept = {"operation": "replace_block", "target": {"id": "exp-001-b01"}, "new_content": {
        "text": "Reduced median API latency 42% by building 18 Python/Django REST endpoints.",
        "source_refs": [{"type": "master", "id": "exp-001-b01"}]}}
    assert server.tailor_resume("metric-ok", [kept], wf)["ok"]


def test_evidence_metric_must_be_user_stated(master):
    wf = _start(master)["workflow_id"]
    out = server.save_tailoring_evidence(wf, "FastAPI", "personal_project", "Built FastAPI APIs.",
                                         confirmed=True, metrics=["10K users"])
    assert out["error"]["code"] == "EVIDENCE_INVALID"


def test_internship_evidence_stays_under_internship_role(master):
    wf = _start(master)["workflow_id"]
    ev = _evidence(wf, "AWS", "internship", "Deployed billing jobs on AWS Lambda during my internship.")
    under_fulltime = {"operation": "add_block", "parent_id": "exp-001", "new_content": {
        "text": "Deployed billing jobs on AWS Lambda.", "source_refs": [{"type": "evidence", "id": ev}]}}
    assert server.tailor_resume("i1", [under_fulltime], wf, evidence_ids=[ev])["error"]["code"] == "PROVENANCE_VIOLATION"
    under_intern = dict(under_fulltime, parent_id="exp-002")
    assert server.tailor_resume("i2", [under_intern], wf, evidence_ids=[ev])["ok"]
    # master internship bullet cannot be moved/cited into the full-time role either
    moved = {"operation": "add_block", "parent_id": "exp-001", "new_content": {
        "text": "Implemented unit tests with pytest for a billing service.",
        "source_refs": [{"type": "master", "id": "exp-002-b01"}]}}
    assert server.tailor_resume("i3", [moved], wf)["error"]["code"] == "PROVENANCE_VIOLATION"


def test_benefits_section_is_not_a_requirement(master):
    jd = "Engineer\nRequirements:\n- Python\nBenefits:\n- Free Kubernetes training\n"
    analysis = server.analyze_tailoring_requirements(jd)
    assert "kubernetes" not in analysis["missing"] + analysis["confirmed"] + analysis["priority_missing"]
