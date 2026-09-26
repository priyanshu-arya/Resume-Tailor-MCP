"""Tests for lib/patches.py -- structured, untrusted patches against master block IDs."""

from __future__ import annotations

import copy

import pytest

from lib.errors import ResumeTailorError
from lib.ids import normalize_master
from lib.patches import MAX_PATCHES, derive_claim_strength, parse_patches, validate_and_apply
from lib.schemas import ReplaceBlock

WF = "wf-001"
SENTINEL = "ZZ-SECRET-SENTINEL-4242"


@pytest.fixture
def m(legacy_master):
    return normalize_master(legacy_master, "resume")


def _evidence(eid="ev-001", *, workflow_id=WF, confirmed=True, category="professional", metrics=None):
    return {"id": eid, "workflow_id": workflow_id, "workspace_id": "ws-1", "term": "Kubernetes",
            "category": category, "evidence_text": "Ran Kubernetes deployments at Acme.",
            "confirmed": confirmed, "metrics": metrics or [], "created_at": "2026-01-01T00:00:00Z"}


def _mref(i):
    return {"type": "master", "id": i}


def _eref(i):
    return {"type": "evidence", "id": i}


def replace(target, text="Rewrote the thing.", refs=None, **nc):
    return {"operation": "replace_block", "target": {"id": target},
            "new_content": {"text": text, "source_refs": refs if refs is not None else [_mref(target)], **nc}}


def drop(target):
    return {"operation": "drop_block", "target": {"id": target}}


def reorder(order, *, parent_id=None, section=None):
    p = {"operation": "reorder", "order": order}
    if parent_id is not None:
        p["parent_id"] = parent_id
    if section is not None:
        p["section"] = section
    return p


def add_block(parent, text="Added bullet.", refs=None, **nc):
    return {"operation": "add_block", "parent_id": parent,
            "new_content": {"text": text, "source_refs": refs if refs is not None else [_mref("exp-001-b01")], **nc}}


def add_skill(category, name, refs=None, **kw):
    return {"operation": "add_skill_item", "category": category, "name": name,
            "source_refs": refs if refs is not None else [_mref("exp-001-b01")], **kw}


def _rejections(exc):
    return exc.value.details["rejections"]


def _rules(exc):
    return [r["rule"] for r in _rejections(exc)]


def _bullet(body, section, entry_id, bullet_id):
    entry = next(e for e in body[section] if e["id"] == entry_id)
    return next(b for b in entry["bullets"] if b["id"] == bullet_id)


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------

def test_master_not_mutated(m):
    snapshot = copy.deepcopy(m)
    validate_and_apply(m, [replace("exp-001-b01"), drop("exp-002"), add_block("proj-001"),
                           add_skill("Languages", "Rust"), reorder(["skg-002", "skg-001"], section="skills")],
                       workflow_id=WF)
    assert m == snapshot


def test_master_not_mutated_on_rejection(m):
    snapshot = copy.deepcopy(m)
    with pytest.raises(ResumeTailorError):
        validate_and_apply(m, [drop("exp-001-b01"), replace("nope")], workflow_id=WF)
    assert m == snapshot


def test_noop_equals_master_content_plus_self_refs(m):
    body, report = validate_and_apply(m, [], workflow_id=WF)
    assert "metadata" not in body and "unparsed" not in body
    expected = {k: copy.deepcopy(m[k]) for k in
                ("name", "contact", "summary", "skills", "experience", "education", "projects", "certifications")}
    cats = {"exp-001": "professional", "exp-002": "internship", "proj-001": "personal_project", "edu-001": "academic"}
    for section in ("experience", "projects", "education"):
        for e in expected[section]:
            for b in e["bullets"]:
                b["source_refs"] = [_mref(b["id"])]
                b["claim_strength"] = cats[e["id"]]
    for g in expected["skills"]:
        for i in g["items"]:
            i["source_refs"] = [_mref(i["id"])]
            i["claim_strength"] = None
    for c in expected["certifications"]:
        c["source_refs"] = [_mref(c["id"])]
        c["claim_strength"] = "certification"
    assert body == expected
    assert report == {"applied": [], "new_block_ids": [], "changed_block_ids": [], "dropped_block_ids": [],
                      "summary": {"source_refs": [_mref("sum-001")], "claim_strength": None}}


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------

def test_replace_bullet(m):
    body, report = validate_and_apply(
        m, [replace("exp-001-b01", "Built REST endpoints in Django.", metadata={"note": "x"})], workflow_id=WF)
    b = _bullet(body, "experience", "exp-001", "exp-001-b01")
    assert b == {"id": "exp-001-b01", "text": "Built REST endpoints in Django.",
                 "source_refs": [_mref("exp-001-b01")], "claim_strength": "professional", "metadata": {"note": "x"}}
    assert report["changed_block_ids"] == ["exp-001-b01"]
    assert report["applied"] == [{"patch_index": 0, "operation": "replace_block", "block_id": "exp-001-b01"}]


def test_replace_project_and_education_bullets(m):
    m["education"][0]["bullets"] = [{"id": "edu-001-b01", "text": "GPA 3.8"}]
    body, _ = validate_and_apply(m, [replace("proj-001-b01"), replace("edu-001-b01")], workflow_id=WF)
    assert _bullet(body, "projects", "proj-001", "proj-001-b01")["claim_strength"] == "personal_project"
    assert _bullet(body, "education", "edu-001", "edu-001-b01")["claim_strength"] == "academic"


def test_replace_summary(m):
    body, report = validate_and_apply(
        m, [replace("sum-001", "Backend engineer.", refs=[_mref("exp-001-b01"), _mref("exp-002-b01")])],
        workflow_id=WF)
    assert body["summary"] == "Backend engineer."
    assert report["summary"] == {"source_refs": [_mref("exp-001-b01"), _mref("exp-002-b01")],
                                 "claim_strength": "professional"}


def test_explicit_claim_strength_kept(m):
    body, _ = validate_and_apply(m, [replace("exp-001-b01", claim_strength="internship")], workflow_id=WF)
    assert _bullet(body, "experience", "exp-001", "exp-001-b01")["claim_strength"] == "internship"


def test_derived_claim_strength_uses_strongest_ref(m):
    ev = {"ev-001": _evidence(category="coursework")}
    body, _ = validate_and_apply(
        m, [replace("exp-001-b01", refs=[_eref("ev-001"), _mref("exp-002-b01")])], workflow_id=WF, evidence=ev)
    assert _bullet(body, "experience", "exp-001", "exp-001-b01")["claim_strength"] == "internship"


def test_derive_claim_strength_master_skill_is_none():
    assert derive_claim_strength([{"category": "master_skill"}]) is None
    assert derive_claim_strength([{"category": "master_skill"}, {"category": "professional"}]) == "professional"
    assert derive_claim_strength([]) is None


def test_drop_every_kind_of_block(m):
    body, report = validate_and_apply(
        m, [drop("sum-001"), drop("exp-001-b02"), drop("exp-002"), drop("skill-go"), drop("skg-002"),
            drop("cert-001")], workflow_id=WF)
    assert body["summary"] == ""
    assert [e["id"] for e in body["experience"]] == ["exp-001"]
    assert [b["id"] for b in body["experience"][0]["bullets"]] == ["exp-001-b01"]
    assert [g["id"] for g in body["skills"]] == ["skg-001"]
    assert [i["id"] for i in body["skills"][0]["items"]] == ["skill-python", "skill-sql"]
    assert body["certifications"] == []
    assert report["dropped_block_ids"] == ["sum-001", "exp-001-b02", "exp-002", "skill-go", "skg-002", "cert-001"]
    assert report["summary"] == {"source_refs": [], "claim_strength": None}


def test_drop_summary_twice_is_unknown(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("sum-001"), drop("sum-001")], workflow_id=WF)
    assert _rules(exc) == ["patch.unknown_target"]
    assert _rejections(exc)[0]["patch_index"] == 1


def test_reorder_section_entries_and_skill_groups(m):
    body, report = validate_and_apply(
        m, [reorder(["exp-002", "exp-001"], section="experience"), reorder(["skg-002", "skg-001"], section="skills")],
        workflow_id=WF)
    assert [e["id"] for e in body["experience"]] == ["exp-002", "exp-001"]
    assert [g["id"] for g in body["skills"]] == ["skg-002", "skg-001"]
    assert [a["block_id"] for a in report["applied"]] == ["experience", "skills"]


def test_reorder_bullets_and_skill_items(m):
    body, _ = validate_and_apply(
        m, [reorder(["exp-001-b02", "exp-001-b01"], parent_id="exp-001"),
            reorder(["skill-go", "skill-python", "skill-sql"], parent_id="skg-001")], workflow_id=WF)
    assert [b["id"] for b in body["experience"][0]["bullets"]] == ["exp-001-b02", "exp-001-b01"]
    assert [i["id"] for i in body["skills"][0]["items"]] == ["skill-go", "skill-python", "skill-sql"]


def test_reorder_certifications_and_projects(m):
    m["certifications"].append({"id": "cert-002", "text": "CKA"})
    body, _ = validate_and_apply(m, [reorder(["cert-002", "cert-001"], section="certifications"),
                                     reorder(["proj-001"], section="projects")], workflow_id=WF)
    assert [c["id"] for c in body["certifications"]] == ["cert-002", "cert-001"]


def test_add_block_ids_and_fields(m):
    ev = {"ev-001": _evidence()}
    body, report = validate_and_apply(
        m, [add_block("exp-001", "Deployed services on Kubernetes.", refs=[_eref("ev-001")], metadata={"k": 1}),
            add_block("proj-001")], workflow_id=WF, evidence=ev)
    new = body["experience"][0]["bullets"][-1]
    assert new == {"id": "vb-001", "text": "Deployed services on Kubernetes.", "source_refs": [_eref("ev-001")],
                   "claim_strength": "professional", "metadata": {"k": 1}}
    assert body["projects"][0]["bullets"][-1]["id"] == "vb-002"
    assert report["new_block_ids"] == ["vb-001", "vb-002"]


def test_add_block_numbering_after_highest_vb(m):
    m["experience"][0]["bullets"].append({"id": "vb-007", "text": "x"})
    body, report = validate_and_apply(m, [add_block("exp-002")], workflow_id=WF)
    assert report["new_block_ids"] == ["vb-008"]


def test_add_block_to_education(m):
    body, _ = validate_and_apply(m, [add_block("edu-001", refs=[_mref("proj-001-b01")])], workflow_id=WF)
    assert body["education"][0]["bullets"][0]["id"] == "vb-001"
    assert body["education"][0]["bullets"][0]["claim_strength"] == "personal_project"


def test_add_skill_to_existing_group_case_insensitive(m):
    body, report = validate_and_apply(m, [add_skill("  languages ", " Rust ")], workflow_id=WF)
    group = body["skills"][0]
    assert group["category"] == "Languages"
    assert group["items"][-1] == {"id": "vs-001", "name": "Rust", "source_refs": [_mref("exp-001-b01")],
                                  "claim_strength": "professional"}
    assert report["new_block_ids"] == ["vs-001"]


def test_add_skill_creates_new_group(m):
    body, report = validate_and_apply(
        m, [add_skill("Orchestration", "Kubernetes"), add_skill("orchestration", "Helm")], workflow_id=WF)
    assert body["skills"][-1]["id"] == "vg-001"
    assert body["skills"][-1]["category"] == "Orchestration"
    assert [i["id"] for i in body["skills"][-1]["items"]] == ["vs-001", "vs-002"]
    assert report["new_block_ids"] == ["vg-001", "vs-001", "vs-002"]


def test_add_skill_citing_master_skill_has_none_strength(m):
    body, _ = validate_and_apply(m, [add_skill("Languages", "Python 3", refs=[_mref("skill-python")])],
                                 workflow_id=WF)
    assert body["skills"][0]["items"][-1]["claim_strength"] is None


def test_cert_can_be_cited(m):
    body, _ = validate_and_apply(m, [add_skill("Cloud", "AWS Lambda", refs=[_mref("cert-001")])], workflow_id=WF)
    assert body["skills"][1]["items"][-1]["claim_strength"] == "certification"


def test_added_then_dropped_block_not_reported_as_new(m):
    _, report = validate_and_apply(m, [add_block("exp-001"), drop("vb-001")], workflow_id=WF)
    assert report["new_block_ids"] == []


# --------------------------------------------------------------------------
# Structural rejections
# --------------------------------------------------------------------------

@pytest.mark.parametrize("target", ["exp-001", "proj-001", "edu-001", "skg-001", "skill-python", "cert-001"])
def test_replace_target_type(m, target):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace(target, refs=[_mref("exp-001-b01")])], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    assert _rules(exc) == ["patch.replace_target_type"]


@pytest.mark.parametrize("patch", [replace("exp-001-b99", refs=[_mref("exp-001-b01")]), drop("nope"),
                                   add_block("exp-099"), reorder([], parent_id="exp-099")])
def test_unknown_target(m, patch):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [patch], workflow_id=WF)
    assert _rules(exc) == ["patch.unknown_target"]


@pytest.mark.parametrize("text", ["", "   \n", "x" * 601])
def test_text_length_replace_and_add(m, text):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", text), add_block("exp-001", text)], workflow_id=WF)
    assert _rules(exc) == ["patch.text_length", "patch.text_length"]


def test_text_length_boundary_ok(m):
    validate_and_apply(m, [replace("exp-001-b01", "x" * 600)], workflow_id=WF)


@pytest.mark.parametrize("name", ["", "  ", "y" * 61])
def test_skill_name_length(m, name):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_skill("Languages", name)], workflow_id=WF)
    assert _rules(exc) == ["patch.text_length"]


def test_skill_name_boundary_ok(m):
    validate_and_apply(m, [add_skill("Languages", "y" * 60)], workflow_id=WF)


def test_duplicate_skill(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_skill("LANGUAGES", " python ")], workflow_id=WF)
    assert _rules(exc) == ["patch.duplicate_skill"]


def test_duplicate_skill_within_same_patch_list(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_skill("DevOps", "Terraform"), add_skill("devops", "terraform")], workflow_id=WF)
    assert _rules(exc) == ["patch.duplicate_skill"]
    assert _rejections(exc)[0]["patch_index"] == 1


def test_add_block_parent_must_be_entry(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("skg-001"), add_block("exp-001-b01"), add_block("sum-001")],
                           workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    assert len(_rejections(exc)) == 3


@pytest.mark.parametrize("patch", [
    reorder(["exp-001"]),  # neither
    reorder(["exp-001", "exp-002"], parent_id="exp-001", section="experience"),  # both
    reorder(["sum-001"], section="summary"),  # bad section
    reorder([], section="contact"),
    reorder([], parent_id="exp-001-b01"),  # a bullet has no children
])
def test_reorder_scope(m, patch):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [patch], workflow_id=WF)
    assert _rules(exc) == ["patch.reorder_scope"]


@pytest.mark.parametrize("order", [
    ["exp-001"],  # missing
    ["exp-001", "exp-002", "exp-003"],  # extra
    ["exp-001", "exp-001"],  # duplicate (and missing)
    ["exp-001", "exp-002", "exp-002"],  # duplicate
    ["exp-001-b01", "exp-002"],  # wrong scope
])
def test_reorder_not_permutation(m, order):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [reorder(order, section="experience")], workflow_id=WF)
    assert _rules(exc) == ["patch.reorder_not_permutation"]


# --------------------------------------------------------------------------
# Ordering semantics / all-or-nothing
# --------------------------------------------------------------------------

def test_drop_then_reorder_uses_remaining_ids(m):
    body, _ = validate_and_apply(
        m, [drop("exp-001-b01"), reorder(["exp-001-b02"], parent_id="exp-001")], workflow_id=WF)
    assert [b["id"] for b in body["experience"][0]["bullets"]] == ["exp-001-b02"]


def test_reorder_including_dropped_id_fails(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("exp-002"), reorder(["exp-002", "exp-001"], section="experience")],
                           workflow_id=WF)
    assert _rules(exc) == ["patch.reorder_not_permutation"]


def test_replace_after_drop_fails(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("exp-001"), replace("exp-001-b01")], workflow_id=WF)
    assert _rules(exc) == ["patch.unknown_target"]


def test_reorder_sees_added_block(m):
    body, _ = validate_and_apply(
        m, [add_block("exp-002"), reorder(["vb-001", "exp-002-b01"], parent_id="exp-002")], workflow_id=WF)
    assert [b["id"] for b in body["experience"][1]["bullets"]] == ["vb-001", "exp-002-b01"]


def test_third_patch_invalid_rejects_all(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01"), drop("cert-001"), replace("exp-001-b77")], workflow_id=WF)
    assert _rejections(exc) == [{"patch_index": 2, "operation": "replace_block", "rule": "patch.unknown_target",
                                 "message": _rejections(exc)[0]["message"]}]


def test_all_rejections_reported(m):
    ev = {"ev-x": _evidence("ev-x", workflow_id="other")}
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [
            replace("exp-001-b01"),  # ok
            drop("ghost"),  # 1
            replace("skg-001"),  # 2
            add_block("exp-001", refs=[]),  # 3
            add_skill("Languages", "Rust", refs=[_eref("ev-x")]),  # 4
            reorder(["exp-001"], section="experience"),  # 5
        ], workflow_id=WF, evidence=ev)
    assert exc.value.code == "PROVENANCE_VIOLATION"
    assert [(r["patch_index"], r["rule"]) for r in _rejections(exc)] == [
        (1, "patch.unknown_target"), (2, "patch.replace_target_type"), (3, "provenance.missing_refs"),
        (4, "provenance.evidence_wrong_workflow"), (5, "patch.reorder_not_permutation")]


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

@pytest.mark.parametrize("patch", [replace("exp-001-b01", refs=[]), add_block("exp-001", refs=[]),
                                   add_skill("Languages", "Rust", refs=[]), replace("sum-001", refs=[])])
def test_missing_refs_never_inferred(m, patch):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [patch], workflow_id=WF)
    assert exc.value.code == "PROVENANCE_VIOLATION"
    assert _rules(exc) == ["provenance.missing_refs"]


@pytest.mark.parametrize("ref", ["exp-099-b01", "vb-001", "vs-001", "vg-001", "v-20260101-abc", ""])
def test_fabricated_master_ref(m, ref):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs=[_mref(ref)])], workflow_id=WF)
    assert exc.value.code == "PROVENANCE_VIOLATION"
    assert _rules(exc) == ["provenance.unknown_master_ref"]


def test_vb_block_added_in_same_list_is_not_citable(m):
    """Refs resolve against the ORIGINAL master, not the evolving body."""
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001"), add_block("exp-002", refs=[_mref("vb-001")])], workflow_id=WF)
    assert _rules(exc) == ["provenance.unknown_master_ref"]


def test_ref_to_block_dropped_in_body_is_still_citable(m):
    validate_and_apply(m, [drop("exp-002"), add_block("exp-001", refs=[_mref("exp-002-b01")])], workflow_id=WF)


@pytest.mark.parametrize("ref", ["sum-001", "skg-001"])
def test_uncitable_ref(m, ref):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs=[_mref(ref)])], workflow_id=WF)
    assert _rules(exc) == ["provenance.uncitable_ref"]


def test_unknown_evidence_ref(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs=[_eref("ev-404")])], workflow_id=WF,
                           evidence={"ev-001": _evidence()})
    assert _rules(exc) == ["provenance.unknown_evidence_ref"]


def test_master_id_as_evidence_ref_is_unknown(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs=[_eref("exp-001-b01")])], workflow_id=WF)
    assert _rules(exc) == ["provenance.unknown_evidence_ref"]


def test_evidence_from_other_workflow(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001", refs=[_eref("ev-001")])], workflow_id=WF,
                           evidence={"ev-001": _evidence(workflow_id="wf-other")})
    assert exc.value.code == "PROVENANCE_VIOLATION"
    assert _rules(exc) == ["provenance.evidence_wrong_workflow"]


def test_unconfirmed_evidence(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001", refs=[_eref("ev-001")])], workflow_id=WF,
                           evidence={"ev-001": _evidence(confirmed=False)})
    assert _rules(exc) == ["provenance.evidence_unconfirmed"]


def test_truthy_but_not_true_confirmed_is_unconfirmed(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001", refs=[_eref("ev-001")])], workflow_id=WF,
                           evidence={"ev-001": _evidence(confirmed="yes")})
    assert _rules(exc) == ["provenance.evidence_unconfirmed"]


def test_evidence_none_category(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001", refs=[_eref("ev-001")])], workflow_id=WF,
                           evidence={"ev-001": _evidence(category="none")})
    assert _rules(exc) == ["provenance.evidence_none"]


def test_evidence_several_failures_reported(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001", refs=[_eref("ev-001")])], workflow_id=WF,
                           evidence={"ev-001": _evidence(workflow_id="x", confirmed=False)})
    assert set(_rules(exc)) == {"provenance.evidence_wrong_workflow", "provenance.evidence_unconfirmed"}


def test_one_bad_ref_among_good_rejects_patch(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01", refs=[_mref("exp-001-b01"), _mref("exp-001-b99")])],
                           workflow_id=WF)
    assert _rules(exc) == ["provenance.unknown_master_ref"]


def test_mixed_structural_and_provenance_is_provenance_violation(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("ghost"), replace("exp-001-b01", refs=[])], workflow_id=WF)
    assert exc.value.code == "PROVENANCE_VIOLATION"


# --------------------------------------------------------------------------
# Schema-level rejections (parse_patches)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("patch", [
    {**replace("exp-001-b01"), "title": "CTO"},
    {**drop("exp-001"), "company": "Google"},
    {"operation": "replace_block", "target": {"id": "exp-001", "title": "CTO"},
     "new_content": {"text": "x", "source_refs": [_mref("exp-001-b01")]}},
    {"operation": "replace_block", "target": {"id": "exp-001-b01"},
     "new_content": {"text": "x", "source_refs": [_mref("exp-001-b01")], "dates": "2010-2026"}},
    {**add_skill("Languages", "Rust"), "github": "github.com/x/y"},
    {**reorder(["exp-001", "exp-002"], section="experience"), "end": "Present"},
])
def test_extra_fields_rejected(m, patch):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [patch], workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"
    assert exc.value.details["errors"]


@pytest.mark.parametrize("raw", [
    [{"operation": "rename_company", "target": {"id": "exp-001"}}],
    [{"operation": "replace_block"}],
    [{"operation": "replace_block", "target": {"id": "exp-001-b01"},
      "new_content": {"text": "x", "source_refs": [{"type": "guess", "id": "a"}]}}],
    [{"operation": "add_skill_item", "category": "x", "name": "y", "claim_strength": "godlike"}],
    ["not a dict"],
    {"operation": "drop_block"},
    "drop everything",
])
def test_parse_rejects_malformed(raw):
    with pytest.raises(ResumeTailorError) as exc:
        parse_patches(raw)
    assert exc.value.code == "PATCH_INVALID"
    for e in exc.value.details["errors"]:
        assert set(e) == {"loc", "msg"}


def test_parse_error_does_not_echo_values():
    raw = [{"operation": SENTINEL, "target": {"id": "x"}},
           {"operation": "replace_block", "target": {"id": "x"},
            "new_content": {"text": 5, "source_refs": [{"type": SENTINEL, "id": SENTINEL}]}},
           {**drop("x"), "title": SENTINEL}]
    with pytest.raises(ResumeTailorError) as exc:
        parse_patches(raw)
    assert SENTINEL not in str(exc.value.details)
    assert SENTINEL not in exc.value.message


def test_too_many_patches(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("cert-001")] + [reorder(["cert-001"], section="certifications")] * MAX_PATCHES,
                           workflow_id=WF)
    assert exc.value.code == "PATCH_INVALID"


def test_too_many_parsed_patches(m):
    parsed = parse_patches([reorder(["exp-001", "exp-002"], section="experience")] * MAX_PATCHES)
    with pytest.raises(ResumeTailorError):
        validate_and_apply(m, parsed + parsed[:1], workflow_id=WF)


def test_exactly_max_patches_ok(m):
    validate_and_apply(m, [reorder(["exp-001", "exp-002"], section="experience")] * MAX_PATCHES, workflow_id=WF)


def test_accepts_parsed_models(m):
    patches = parse_patches([replace("exp-001-b01"), drop("cert-001")])
    assert isinstance(patches[0], ReplaceBlock)
    body, _ = validate_and_apply(m, patches, workflow_id=WF)
    assert body["certifications"] == []


# --------------------------------------------------------------------------
# Header fields
# --------------------------------------------------------------------------

HEADER_FIELDS = {
    "experience": ("id", "title", "company", "location", "start", "end"),
    "education": ("id", "degree", "school", "year"),
    "projects": ("id", "name", "stack", "dates", "github"),
}


def test_header_fields_unchanged_after_big_patch_set(m):
    m["projects"][0]["dates"] = "2024"
    m["projects"][0]["github"] = "github.com/alexexample/rag-notes"
    ev = {"ev-001": _evidence()}
    patches = [
        replace("sum-001", "Engineer.", refs=[_mref("exp-001-b01")]),
        replace("exp-001-b01", "A."), replace("exp-001-b02", "B."), replace("exp-002-b01", "C."),
        replace("proj-001-b01", "D."),
        add_block("exp-001", "E.", refs=[_eref("ev-001")]), add_block("edu-001", "F."), add_block("proj-001", "G."),
        reorder(["vb-001", "exp-001-b02", "exp-001-b01"], parent_id="exp-001"),
        reorder(["exp-002", "exp-001"], section="experience"),
        add_skill("Cloud", "Kubernetes", refs=[_eref("ev-001")]), drop("skill-go"), drop("cert-001"),
    ]
    body, _ = validate_and_apply(m, patches, workflow_id=WF, evidence=ev)
    assert body["name"] == m["name"] and body["contact"] == m["contact"]
    for section, fields in HEADER_FIELDS.items():
        before = {e["id"]: {f: e.get(f) for f in fields} for e in m[section]}
        after = {e["id"]: {f: e.get(f) for f in fields} for e in body[section]}
        assert after == before
    assert [g["category"] for g in body["skills"]] == ["Languages", "Cloud"]


# --------------------------------------------------------------------------
# Repair mode
# --------------------------------------------------------------------------

def test_repair_mode_allows_drop_and_reorder(m):
    body, _ = validate_and_apply(m, [drop("exp-001-b02"), reorder(["exp-002", "exp-001"], section="experience")],
                                 workflow_id=WF, repair_mode=True)
    assert [e["id"] for e in body["experience"]] == ["exp-002", "exp-001"]


def test_repair_mode_forbids_content_operations(m):
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [drop("cert-001"), replace("exp-001-b01"), add_block("exp-001"),
                               add_skill("Languages", "Rust")], workflow_id=WF, repair_mode=True)
    assert exc.value.code == "PATCH_INVALID"
    assert [(r["patch_index"], r["rule"]) for r in _rejections(exc)] == [
        (1, "repair.forbidden_operation"), (2, "repair.forbidden_operation"), (3, "repair.forbidden_operation")]


# --------------------------------------------------------------------------
# Provenance hook
# --------------------------------------------------------------------------

def test_hook_ctx_contents(m):
    ev = {"ev-001": _evidence(metrics=["40%"])}
    seen = []
    patches = [
        drop("cert-001"),  # hook not called for drop
        replace("exp-001-b01", "Did X.", refs=[_mref("exp-001-b01"), _eref("ev-001")]),
        add_block("proj-001", "Did Y.", refs=[_mref("proj-001-b01")], claim_strength="academic"),
        add_skill("languages", "Rust", refs=[_mref("skill-go")]),
        replace("sum-001", "Summary.", refs=[_mref("exp-002-b01")]),
        add_skill("New Group", "Helm", refs=[_eref("ev-001")]),
    ]
    validate_and_apply(m, patches, workflow_id=WF, evidence=ev, provenance_hook=lambda c: seen.append(c) or [])
    assert [c["patch_index"] for c in seen] == [1, 2, 3, 4, 5]
    c = seen[0]
    assert c == {
        "patch_index": 1, "operation": "replace_block", "target_type": "experience_bullet",
        "target_section": "experience", "skill_group_category": None, "text": "Did X.",
        "source_refs": [_mref("exp-001-b01"), _eref("ev-001")], "claim_strength": "professional",
        "ref_infos": [
            {"type": "master", "id": "exp-001-b01", "category": "professional",
             "text": m["experience"][0]["bullets"][0]["text"], "section": "experience"},
            {"type": "evidence", "id": "ev-001", "category": "professional",
             "text": "Ran Kubernetes deployments at Acme.", "section": None, "term": "Kubernetes",
             "metrics": ["40%"]},
        ],
    }
    assert (seen[1]["target_type"], seen[1]["target_section"], seen[1]["claim_strength"]) == (
        "project_bullet", "projects", "academic")
    assert (seen[2]["target_type"], seen[2]["skill_group_category"], seen[2]["text"], seen[2]["claim_strength"]) == (
        "skill_item", "Languages", "Rust", None)
    assert seen[2]["ref_infos"][0]["category"] == "master_skill"
    assert (seen[3]["target_type"], seen[3]["target_section"], seen[3]["claim_strength"]) == (
        "summary", "summary", "internship")
    assert seen[4]["skill_group_category"] == "New Group"


def test_hook_not_called_when_structural_or_ref_checks_fail(m):
    calls = []
    with pytest.raises(ResumeTailorError):
        validate_and_apply(m, [replace("exp-001-b01", refs=[]), replace("skg-001"), add_block("exp-001", "")],
                           workflow_id=WF, provenance_hook=lambda c: calls.append(c) or [])
    assert calls == []


def test_hook_rejections_propagate(m):
    def hook(ctx):
        if ctx["patch_index"] == 1:
            return [{"rule": "provenance.claim_strength", "message": "claim exceeds evidence"}]
        return []

    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [replace("exp-001-b01"), add_block("exp-001")], workflow_id=WF, provenance_hook=hook)
    assert exc.value.code == "PROVENANCE_VIOLATION"
    assert _rejections(exc) == [{"patch_index": 1, "operation": "add_block", "rule": "provenance.claim_strength",
                                 "message": "claim exceeds evidence"}]


def test_hook_rejected_patch_is_not_applied_for_later_patches(m):
    hook = lambda c: [{"rule": "provenance.x", "message": "no"}] if c["patch_index"] == 0 else []  # noqa: E731
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, [add_block("exp-001"), drop("vb-001")], workflow_id=WF, provenance_hook=hook)
    assert _rules(exc) == ["provenance.x", "patch.unknown_target"]


def test_hook_ctx_mutation_does_not_affect_body(m):
    def hook(ctx):
        ctx["ref_infos"][0]["category"] = "none"
        ctx["source_refs"].clear()
        return []

    body, _ = validate_and_apply(m, [replace("exp-001-b01")], workflow_id=WF, provenance_hook=hook)
    assert _bullet(body, "experience", "exp-001", "exp-001-b01")["source_refs"] == [_mref("exp-001-b01")]


# --------------------------------------------------------------------------
# No personal data in errors
# --------------------------------------------------------------------------

def test_rejection_details_never_quote_patch_text(m):
    ev = {"ev-001": _evidence(workflow_id="other", confirmed=False)}
    patches = [
        replace("exp-001-b01", SENTINEL, refs=[]),
        replace("exp-001-b02", SENTINEL * 50),
        replace("skg-001", SENTINEL),
        add_block("exp-001", SENTINEL, refs=[_eref("ev-001")]),
        add_block("ghost", SENTINEL),
        add_skill("Languages", "Python", refs=[_mref("sum-001")]),
        add_skill(SENTINEL, SENTINEL * 5),
        add_skill("Languages", SENTINEL, refs=[_mref("nope")]),
    ]
    with pytest.raises(ResumeTailorError) as exc:
        validate_and_apply(m, patches, workflow_id=WF, evidence=ev, repair_mode=False)
    assert len(_rejections(exc)) >= len(patches)
    assert SENTINEL not in str(exc.value.details)
    assert SENTINEL not in exc.value.message
