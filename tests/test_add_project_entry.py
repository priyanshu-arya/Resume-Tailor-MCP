"""Tests for `add_project_entry` (spec 3.5) -- the one new patch operation
that can create structure, always in Projects, never Experience/Education.
"""

from __future__ import annotations

import copy

import pytest

from lib.errors import ResumeTailorError
from lib.ids import index_blocks, normalize_master
from lib.patches import MAX_NEW_PROJECT_ENTRIES_PER_CALL, validate_and_apply
from lib.validators.provenance import make_hook
from lib.validators.structure import check_structure

WF = "wf-proj"
SENTINEL = "ZZ-SECRET-SENTINEL-4242"


@pytest.fixture
def m(legacy_master):
    return normalize_master(legacy_master, "resume")


def ev(eid, term, category, text, metrics=None, *, workflow_id=WF, confirmed=True):
    return {"id": eid, "workflow_id": workflow_id, "workspace_id": "ws-1", "term": term, "category": category,
            "evidence_text": text, "confirmed": confirmed, "metrics": metrics or [],
            "created_at": "2026-01-01T00:00:00Z"}


def eref(i):
    return {"type": "evidence", "id": i}


def mref(i):
    return {"type": "master", "id": i}


def add_project(name="K8s Operator", stack="Kubernetes", academic=False, refs=None, bullets=None, **kw):
    return {
        "operation": "add_project_entry", "name": name, "stack": stack, "academic": academic,
        "source_refs": refs if refs is not None else [eref("ev-k8s")],
        "bullets": bullets if bullets is not None else [
            {"text": "Built a Kubernetes operator to automate deployments.",
             "source_refs": [eref("ev-k8s")]},
        ],
        **kw,
    }


EV = {
    "ev-k8s": ev("ev-k8s", "Kubernetes", "personal_project", "Built a personal Kubernetes operator for fun."),
    "ev-k8s-academic": ev("ev-k8s-academic", "Kubernetes", "academic",
                          "Built a Kubernetes operator for my university capstone project."),
    "ev-k8s-course": ev("ev-k8s-course", "Kubernetes", "coursework", "Used Kubernetes in a DevOps course."),
    "ev-k8s-cert": ev("ev-k8s-cert", "Kubernetes", "certification", "CKA certified."),
    "ev-k8s-learning": ev("ev-k8s-learning", "Kubernetes", "learning_only", "Completed a Kubernetes tutorial."),
    "ev-k8s-pro": ev("ev-k8s-pro", "Kubernetes", "professional", "Ran Kubernetes deployments at Acme Corp."),
}


def run(master, patches, evidence=EV):
    return validate_and_apply(master, patches, workflow_id=WF, evidence=evidence,
                              provenance_hook=make_hook(master, evidence))


def rejected(master, patches, evidence=EV) -> list[dict]:
    with pytest.raises(ResumeTailorError) as exc:
        run(master, patches, evidence)
    return exc.value.details["rejections"]


def rule_ids(rejs) -> set[str]:
    return {r["rule"] for r in rejs}


# --------------------------------------------------------------------------
# Structural: ids, cap, forbidden fields, duplicates
# --------------------------------------------------------------------------

def test_creates_a_new_project_with_sequential_bullet_ids(m):
    body, report = run(m, [add_project(bullets=[
        {"text": "Built a Kubernetes operator to automate deployments.", "source_refs": [eref("ev-k8s")]},
        {"text": "Wrote automated tests for the Kubernetes operator.", "source_refs": [eref("ev-k8s")]},
    ])])
    entry = body["projects"][-1]
    assert entry["id"] == "vp-001"
    assert [b["id"] for b in entry["bullets"]] == ["vb-001", "vb-002"]
    assert report["new_entry_ids"] == ["vp-001"]
    assert report["new_block_ids"] == ["vp-001", "vb-001", "vb-002"]


def test_bullet_ids_do_not_collide_with_a_sibling_add_block_in_the_same_patch_set(m):
    # Regression for "mint the entry before minting bullet IDs": if bullet
    # IDs were minted before the entry was in the body, every bullet in this
    # patch (and any add_block in the same call) could collide on "vb-001".
    patches = [
        add_project(bullets=[
            {"text": "Built a Kubernetes operator to automate deployments.", "source_refs": [eref("ev-k8s")]},
            {"text": "Wrote automated tests for the Kubernetes operator.", "source_refs": [eref("ev-k8s")]},
        ]),
        {"operation": "add_block", "parent_id": "exp-001",
         "new_content": {"text": "Ran Kubernetes deployments at Acme.", "source_refs": [eref("ev-k8s-pro")]}},
    ]
    body, report = run(m, patches)
    all_ids = report["new_block_ids"]
    assert len(all_ids) == len(set(all_ids)) == 4


def test_bullet_cap_enforced_by_schema(m):
    from pydantic import ValidationError
    from lib.patches import parse_patches
    too_many = add_project(bullets=[{"text": f"Did thing {i}.", "source_refs": [eref("ev-k8s")]} for i in range(5)])
    with pytest.raises((ValidationError, ResumeTailorError)):
        parse_patches([too_many])


def test_zero_bullets_rejected_by_schema(m):
    from lib.patches import parse_patches
    from lib.errors import ResumeTailorError as RTE
    with pytest.raises(RTE):
        parse_patches([add_project(bullets=[])])


@pytest.mark.parametrize("forbidden_field,value", [("github", "https://github.com/x/y"),
                                                    ("dates", "2024-2025"), ("id", "proj-999")])
def test_forbidden_fields_rejected_by_schema(m, forbidden_field, value):
    from lib.patches import parse_patches
    with pytest.raises(ResumeTailorError) as exc:
        parse_patches([{**add_project(), forbidden_field: value}])
    assert exc.value.code == "PATCH_INVALID"


def test_duplicate_project_name_case_insensitive_rejected(m):
    rejs = rejected(m, [add_project(name="rag search"), add_project(name="RAG Search")])
    assert "patch.duplicate_project" in rule_ids(rejs)


def test_too_many_new_entries_in_one_call_rejected(m):
    patches = [add_project(name=f"Project {i}") for i in range(MAX_NEW_PROJECT_ENTRIES_PER_CALL + 1)]
    rejs = rejected(m, patches)
    assert "patch.too_many_new_entries" in rule_ids(rejs)


def test_new_project_then_add_block_then_reorder(m):
    patches = [
        add_project(),
        {"operation": "reorder", "section": "projects", "order": ["proj-001", "vp-001"]},
    ]
    body, report = run(m, patches)
    assert [p["id"] for p in body["projects"]] == ["proj-001", "vp-001"]


def test_id_outside_master_space_in_reorder_before_add_rejected(m):
    rejs = rejected(m, [{"operation": "reorder", "section": "projects", "order": ["proj-001", "vp-001"]},
                        add_project()])
    assert "patch.reorder_not_permutation" in rule_ids(rejs)


# --------------------------------------------------------------------------
# Semantic / provenance
# --------------------------------------------------------------------------

def test_master_ref_alone_rejected(m):
    rejs = rejected(m, [add_project(refs=[mref("proj-001")])])
    assert "provenance.new_entry_requires_evidence" in rule_ids(rejs)


def test_learning_only_evidence_alone_rejected(m):
    rejs = rejected(m, [add_project(refs=[eref("ev-k8s-learning")],
                                    bullets=[{"text": "Explored Kubernetes basics.",
                                             "source_refs": [eref("ev-k8s-learning")]}])])
    assert "provenance.new_entry_placement" in rule_ids(rejs)


def test_coursework_evidence_alone_rejected(m):
    rejs = rejected(m, [add_project(refs=[eref("ev-k8s-course")],
                                    bullets=[{"text": "Used Kubernetes in coursework.",
                                             "source_refs": [eref("ev-k8s-course")]}])])
    assert "provenance.new_entry_placement" in rule_ids(rejs)


def test_certification_evidence_alone_rejected(m):
    rejs = rejected(m, [add_project(refs=[eref("ev-k8s-cert")],
                                    bullets=[{"text": "Applied Kubernetes per certification training.",
                                             "source_refs": [eref("ev-k8s-cert")]}])])
    assert "provenance.new_entry_placement" in rule_ids(rejs)


def test_personal_project_evidence_accepted(m):
    body, _ = run(m, [add_project()])
    assert body["projects"][-1]["name"] == "K8s Operator"


def test_professional_evidence_accepted_as_downgrade(m):
    body, _ = run(m, [add_project(refs=[eref("ev-k8s-pro")],
                                  bullets=[{"text": "Ran Kubernetes deployments.",
                                           "source_refs": [eref("ev-k8s-pro")]}])])
    assert body["projects"][-1]["name"] == "K8s Operator"


def test_academic_evidence_requires_academic_flag(m):
    rejs = rejected(m, [add_project(academic=False, refs=[eref("ev-k8s-academic")],
                                    bullets=[{"text": "Built a Kubernetes operator for my capstone.",
                                             "source_refs": [eref("ev-k8s-academic")]}])])
    assert "provenance.project_academic_context" in rule_ids(rejs)


def test_academic_evidence_with_academic_flag_accepted(m):
    body, _ = run(m, [add_project(academic=True, refs=[eref("ev-k8s-academic")],
                                  bullets=[{"text": "Built a Kubernetes operator for my capstone.",
                                           "source_refs": [eref("ev-k8s-academic")]}])])
    assert body["projects"][-1]["academic"] is True


def test_technologies_in_name_and_stack_must_be_cited(m):
    ev_no_go = dict(EV)
    rejs = rejected(m, [add_project(name="Go Operator", stack="Go, Kubernetes")], evidence=ev_no_go)
    assert "provenance.unsupported_technology" in rule_ids(rejs)


def test_bullets_need_their_own_refs(m):
    rejs = rejected(m, [add_project(bullets=[{"text": "Built a Kubernetes operator.", "source_refs": []}])])
    assert "provenance.missing_refs" in rule_ids(rejs)


def test_metric_in_project_name_rejected(m):
    rejs = rejected(m, [add_project(name="K8s Operator (99% uptime)")])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_high_scope_verb_in_bullet_rejected(m):
    rejs = rejected(m, [add_project(bullets=[{"text": "Led a Kubernetes operator project.",
                                              "source_refs": [eref("ev-k8s")]}])])
    assert "provenance.high_scope_verb" in rule_ids(rejs)


def test_messages_never_quote_name_or_stack(m):
    rejs = rejected(m, [add_project(name=f"{SENTINEL} Project", stack=SENTINEL,
                                    refs=[mref("proj-001")])])
    assert rejs
    for r in rejs:
        assert SENTINEL not in r["message"]


# --------------------------------------------------------------------------
# Repair safety
# --------------------------------------------------------------------------

def test_not_repair_safe(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_project()], workflow_id=WF, evidence=EV, repair_mode=True,
                           provenance_hook=make_hook(m, EV))
    assert exc.value.details["rejections"][0]["rule"] == "repair.forbidden_operation"


# --------------------------------------------------------------------------
# structure.py: new_entry_provenance / unknown_entry
# --------------------------------------------------------------------------

CONTRACT = {"sections": {"order": ["summary", "experience", "projects", "skills", "education", "certifications"],
                        "headings": {"summary": "Summary", "experience": "Experience", "projects": "Projects",
                                     "skills": "Skills", "education": "Education",
                                     "certifications": "Certifications"}}}


def test_structure_new_entry_provenance_passes_for_legitimately_added_entry(m):
    body, _ = run(m, [add_project()])
    body["metadata"] = {"kind": "resume"}
    checks = check_structure(body, CONTRACT, "resume", master_index=index_blocks(m), evidence=EV)
    by_id = {c.id: c for c in checks}
    assert by_id["structure.new_entry_provenance"].status == "pass"
    assert by_id["structure.unknown_entry"].status == "pass"


def test_structure_unknown_entry_fails_for_hand_edited_fake_employer(m):
    body, _ = run(m, [])
    body["metadata"] = {"kind": "resume"}
    body["experience"].append({"id": "exp-fake", "title": "VP of Engineering", "company": "Fake Corp",
                               "bullets": []})
    checks = check_structure(body, CONTRACT, "resume", master_index=index_blocks(m), evidence=EV)
    by_id = {c.id: c for c in checks}
    assert by_id["structure.unknown_entry"].status == "fail"
    assert "exp-fake" in by_id["structure.unknown_entry"].measurement


def test_structure_new_entry_provenance_fails_for_hand_edited_project_with_no_refs(m):
    body, _ = run(m, [])
    body["metadata"] = {"kind": "resume"}
    body["projects"].append({"id": "vp-fake", "name": "Fabricated Project", "bullets": []})
    checks = check_structure(body, CONTRACT, "resume", master_index=index_blocks(m), evidence=EV)
    by_id = {c.id: c for c in checks}
    assert by_id["structure.new_entry_provenance"].status == "fail"
