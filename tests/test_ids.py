"""Stable master block IDs and the block index."""

from __future__ import annotations

import copy

import pytest

from lib.errors import ResumeTailorError
from lib.ids import assign_ids, index_blocks, normalize_master
from lib.schemas import MASTER_SKILL_CATEGORY, SUMMARY_ID, MasterDocument


def _code(excinfo) -> str:
    return excinfo.value.code


def _all_ids(doc: dict) -> list[str]:
    ids = []
    for key in ("experience", "projects", "education"):
        for e in doc[key]:
            ids.append(e["id"])
            ids += [b["id"] for b in e["bullets"]]
    for g in doc["skills"]:
        ids.append(g["id"])
        ids += [i["id"] for i in g["items"]]
    ids += [c["id"] for c in doc["certifications"]]
    return ids


# --------------------------------------------------------------------------
# normalize_master
# --------------------------------------------------------------------------

def test_legacy_strings_become_dicts(legacy_master):
    doc = normalize_master(legacy_master, "resume")
    assert doc["skills"][0]["items"][0]["name"] == "Python"
    assert all(isinstance(i, dict) for g in doc["skills"] for i in g["items"])
    assert doc["certifications"][0]["text"] == "AWS Certified Developer (2024)"
    assert isinstance(doc["certifications"][0], dict)


def test_string_bullets_and_skill_groups(legacy_master):
    legacy_master["experience"][0]["bullets"] = ["Did a thing.", {"text": "Did another."}]
    legacy_master["skills"].append("Kubernetes")
    doc = normalize_master(legacy_master, "resume")
    assert [b["text"] for b in doc["experience"][0]["bullets"]] == ["Did a thing.", "Did another."]
    assert doc["skills"][-1]["category"] == "General"
    assert doc["skills"][-1]["items"][0]["name"] == "Kubernetes"


def test_normalized_master_validates(legacy_master):
    for kind in ("resume", "cv"):
        doc = normalize_master(legacy_master, kind)
        assert doc["metadata"]["kind"] == kind
        MasterDocument.model_validate(doc)


def test_normalize_is_pure(legacy_master):
    before = copy.deepcopy(legacy_master)
    normalize_master(legacy_master, "resume")
    assert legacy_master == before


def test_normalize_empty_and_none():
    for raw in ({}, None):
        doc = normalize_master(raw, "cv")
        assert doc["metadata"]["kind"] == "cv"
        assert doc["experience"] == [] and doc["skills"] == []
        MasterDocument.model_validate(doc)


def test_normalize_bad_kind(legacy_master):
    with pytest.raises(ResumeTailorError) as ei:
        normalize_master(legacy_master, "letter")
    assert _code(ei) == "INVALID_KIND"


def test_normalize_overrides_wrong_metadata_kind(legacy_master):
    legacy_master["metadata"] = {"kind": "cv", "career_stage": "3-5"}
    doc = normalize_master(legacy_master, "resume")
    assert doc["metadata"]["kind"] == "resume"
    assert doc["metadata"]["career_stage"] == "3-5"


# --------------------------------------------------------------------------
# Determinism / stability
# --------------------------------------------------------------------------

def test_normalize_deterministic(legacy_master):
    assert normalize_master(legacy_master, "resume") == normalize_master(legacy_master, "resume")


def test_normalize_idempotent(legacy_master):
    once = normalize_master(legacy_master, "resume")
    twice = normalize_master(once, "resume")
    assert once == twice


def test_assign_ids_rerun_changes_nothing(legacy_master):
    doc = normalize_master(legacy_master, "resume")
    before = copy.deepcopy(doc)
    assign_ids(doc)
    assert doc == before


def test_expected_id_shapes(legacy_master):
    doc = normalize_master(legacy_master, "resume")
    assert [e["id"] for e in doc["experience"]] == ["exp-001", "exp-002"]
    assert [b["id"] for b in doc["experience"][0]["bullets"]] == ["exp-001-b01", "exp-001-b02"]
    assert doc["projects"][0]["id"] == "proj-001"
    assert doc["education"][0]["id"] == "edu-001"
    assert [g["id"] for g in doc["skills"]] == ["skg-001", "skg-002"]
    assert doc["skills"][0]["items"][0]["id"] == "skill-python"
    assert doc["certifications"][0]["id"] == "cert-001"


def test_all_ids_unique(legacy_master):
    doc = normalize_master(legacy_master, "resume")
    ids = _all_ids(doc)
    assert len(ids) == len(set(ids))
    assert SUMMARY_ID not in ids


def test_existing_ids_preserved_and_append_gets_next(legacy_master):
    doc = normalize_master(legacy_master, "resume")
    before = {e["id"]: [b["id"] for b in e["bullets"]] for e in doc["experience"]}
    doc["experience"].insert(0, {"title": "Staff Engineer", "company": "New Co", "bullets": [{"text": "x"}]})
    doc["experience"][1]["bullets"].append({"text": "new bullet"})
    doc["skills"][0]["items"].append({"name": "Rust"})
    doc["certifications"].append({"text": "CKA"})
    assign_ids(doc)
    assert doc["experience"][0]["id"] == "exp-003"
    assert doc["experience"][0]["bullets"][0]["id"] == "exp-003-b01"
    for e in doc["experience"][1:]:
        assert [b["id"] for b in e["bullets"]][:len(before[e["id"]])] == before[e["id"]]
    assert doc["experience"][1]["bullets"][-1]["id"] == "exp-001-b03"
    assert doc["skills"][0]["items"][-1]["id"] == "skill-rust"
    assert doc["certifications"][-1]["id"] == "cert-002"
    ids = _all_ids(doc)
    assert len(ids) == len(set(ids))


def test_custom_existing_ids_kept(legacy_master):
    legacy_master["experience"][0]["id"] = "exp-acme"
    legacy_master["experience"][0]["bullets"][0]["id"] = "exp-001"  # collides with the default prefix
    doc = normalize_master(legacy_master, "resume")
    assert doc["experience"][0]["id"] == "exp-acme"
    assert doc["experience"][0]["bullets"][0]["id"] == "exp-001"
    ids = _all_ids(doc)
    assert len(ids) == len(set(ids)), ids
    assert doc["experience"][1]["id"] != "exp-001"


def test_duplicate_skill_names_unique(legacy_master):
    legacy_master["skills"] = [
        {"category": "A", "items": ["Python", "python", "PYTHON"]},
        {"category": "B", "items": ["Python", "C++", "C#", "C", "", "!!!"]},
    ]
    doc = normalize_master(legacy_master, "resume")
    ids = [i["id"] for g in doc["skills"] for i in g["items"]]
    assert len(ids) == len(set(ids)), ids
    assert ids[:4] == ["skill-python", "skill-python-2", "skill-python-3", "skill-python-4"]
    again = normalize_master(legacy_master, "resume")
    assert [i["id"] for g in again["skills"] for i in g["items"]] == ids


def test_skill_ids_do_not_collide_with_existing(legacy_master):
    legacy_master["skills"] = [{"category": "A", "items": [{"name": "Go", "id": "skill-python"}, "Python"]}]
    doc = normalize_master(legacy_master, "resume")
    items = doc["skills"][0]["items"]
    assert items[0]["id"] == "skill-python"
    assert items[1]["id"] == "skill-python-2"


# --------------------------------------------------------------------------
# Categories / index
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,cat", [
    ("Software Engineering Intern", "internship"),
    ("Intern", "internship"),
    ("Summer Internship - Data", "internship"),
    ("ML Interns Program", "internship"),
    ("INTERN, Backend", "internship"),
    ("Software Engineer", "professional"),
    ("Internal Tools Engineer", "professional"),
    ("International Sales Lead", "professional"),
    ("", "professional"),
])
def test_experience_category(title, cat):
    doc = normalize_master({"experience": [{"title": title, "company": "X", "bullets": ["b"]}]}, "resume")
    idx = index_blocks(doc)
    assert idx["exp-001"]["category"] == cat
    assert idx["exp-001-b01"]["category"] == cat


def test_intern_category_on_fixture(legacy_master):
    idx = index_blocks(normalize_master(legacy_master, "resume"))
    assert idx["exp-001"]["category"] == "professional"
    assert idx["exp-002"]["category"] == "internship"
    assert idx["exp-002-b01"]["category"] == "internship"


def test_project_categories(legacy_master):
    legacy_master["projects"].append({"name": "thesis", "academic": True, "bullets": ["Wrote a thesis."]})
    idx = index_blocks(normalize_master(legacy_master, "resume"))
    assert idx["proj-001"]["category"] == "personal_project"
    assert idx["proj-001-b01"]["category"] == "personal_project"
    assert idx["proj-002"]["category"] == "academic"
    assert idx["proj-002-b01"]["category"] == "academic"


def test_index_blocks_types_and_parents(legacy_master):
    legacy_master["education"][0]["bullets"] = ["GPA 3.9"]
    doc = normalize_master(legacy_master, "resume")
    idx = index_blocks(doc)

    assert idx[SUMMARY_ID] == {"type": "summary", "section": "summary", "parent_id": None,
                               "text": doc["summary"], "category": None}

    assert idx["exp-001"]["type"] == "experience" and idx["exp-001"]["parent_id"] is None
    assert idx["exp-001"]["text"] == "Software Engineer Acme Corp"
    assert idx["exp-001-b02"]["type"] == "experience_bullet"
    assert idx["exp-001-b02"]["parent_id"] == "exp-001"
    assert idx["exp-001-b02"]["section"] == "experience"

    assert idx["proj-001"]["type"] == "project"
    assert idx["proj-001"]["text"] == "rag-notes Python, LangChain"
    assert idx["proj-001-b01"]["type"] == "project_bullet"
    assert idx["proj-001-b01"]["parent_id"] == "proj-001"

    assert idx["edu-001"]["type"] == "education" and idx["edu-001"]["category"] == "academic"
    assert idx["edu-001-b01"]["type"] == "education_bullet"
    assert idx["edu-001-b01"]["parent_id"] == "edu-001"
    assert idx["edu-001-b01"]["category"] == "academic"

    assert idx["skg-001"]["type"] == "skill_group" and idx["skg-001"]["category"] is None
    assert idx["skill-python"]["type"] == "skill_item"
    assert idx["skill-python"]["parent_id"] == "skg-001"
    assert idx["skill-python"]["category"] == MASTER_SKILL_CATEGORY
    assert idx["skill-aws"]["parent_id"] == "skg-002"

    assert idx["cert-001"]["type"] == "certification"
    assert idx["cert-001"]["category"] == "certification"
    assert idx["cert-001"]["parent_id"] is None


def test_index_covers_every_id(legacy_master):
    doc = normalize_master(legacy_master, "resume")
    idx = index_blocks(doc)
    assert set(idx) == set(_all_ids(doc)) | {SUMMARY_ID}
    for bid, entry in idx.items():
        if entry["parent_id"] is not None:
            assert entry["parent_id"] in idx, bid


def test_index_summary_present_even_if_empty():
    idx = index_blocks(normalize_master({}, "resume"))
    assert idx == {SUMMARY_ID: {"type": "summary", "section": "summary", "parent_id": None,
                                "text": "", "category": None}}


def test_deleted_ids_are_never_reissued(legacy_master):
    """Provenance safety: an older version citing exp-002 must never end up
    pointing at a different block after exp-002 is deleted and a new
    experience is added."""
    from lib.ids import assign_ids, normalize_master
    doc = normalize_master(legacy_master, "resume")
    assert [e["id"] for e in doc["experience"]] == ["exp-001", "exp-002"]
    del doc["experience"][1]
    doc["experience"].append({"title": "New Role", "company": "Gamma", "bullets": [{"text": "x"}]})
    assign_ids(doc)
    assert doc["experience"][1]["id"] == "exp-003"
    # also after the LAST entry is deleted (no remaining higher ID to infer from)
    del doc["experience"][1]
    doc["experience"].append({"title": "Another", "company": "Delta", "bullets": []})
    assign_ids(doc)
    assert doc["experience"][1]["id"] == "exp-004"
