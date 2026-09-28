"""lib/validators/structure.py"""

from __future__ import annotations

import copy

from lib.ids import index_blocks, normalize_master
from lib.validators.structure import check_structure
from tests.conftest import SYNTHETIC_LEGACY_MASTER

CONTRACT = {"id": "classic-minimalist", "status": "supported", "version": "1.1.0",
  "page": {"size": "letter", "width_pt": 612, "height_pt": 792, "margins_in": {"top": 0.6, "bottom": 0.5, "left": 0.6, "right": 0.6}},
  "typography": {"font_family": "Latin Modern Roman", "pdf_font_prefixes": ["LMRoman", "LMRomanCaps", "CMSY", "CMMI", "CMR"],
                 "body": {"size_pt": 10}, "name": {"size_pt": 20.7, "weight": "bold"}, "heading": {"size_pt": 12, "weight": "bold"}, "size_tolerance_pt": 0.1},
  "layout": {"columns": 1, "tables_allowed": False, "text_boxes_allowed": False, "graphics_allowed": False},
  "sections": {"order": ["summary", "experience", "projects", "skills", "education", "certifications"],
               "headings": {"summary": "Summary", "experience": "Experience", "projects": "Projects", "skills": "Technical Skills", "education": "Education", "certifications": "Certifications"}},
  "spacing": {"section": "\\vspace{2pt} + titlerule", "bullet": "itemsep=2pt", "line_spacing": "single"},
  "limits": {"min_pages": 1, "max_pages": 2},
  "formatting": {"bullet_style": "itemize", "date_style": "as written in master",
                 "heading_style": "bold small caps + rule", "link_style": "hyperref hidelinks"},
  "latex": {"documentclass_options": ["letterpaper", "11pt"],
            "layout_only_macros": ["resumeSubheading", "resumeProjectHeading"],
            "topmargin_adjust_in": -0.4, "textheight_adjust_in": 0.9, "side_margin_adjust_in": -0.4, "textwidth_adjust_in": 0.8}}

SENTINEL = "ZQXSENTINEL"


def _master() -> dict:
    return normalize_master(copy.deepcopy(SYNTHETIC_LEGACY_MASTER), "resume")


def _version(master: dict | None = None) -> dict:
    """A version whose every claim cites its own master block."""
    v = copy.deepcopy(master or _master())
    ref = lambda i: [{"type": "master", "id": i}]  # noqa: E731
    for section in ("experience", "projects", "education"):
        for e in v[section]:
            for b in e["bullets"]:
                b["source_refs"] = ref(b["id"])
    for g in v["skills"]:
        for item in g["items"]:
            item["source_refs"] = ref(item["id"])
    for c in v["certifications"]:
        c["source_refs"] = ref(c["id"])
    v["metadata"] = {"version_id": "v-1", "workspace_id": "w", "document_kind": "resume", "created_at": "t",
                     "summary_source_refs": ref("sum-001"), "unknown_jd_requirements": []}
    return v


def _by_id(checks):
    return {c.id: c for c in checks}


def _run(doc, contract=CONTRACT, kind="resume", **kw):
    return _by_id(check_structure(doc, contract, kind, **kw))


def test_clean_version_passes():
    m = _master()
    checks = _run(_version(m), master_index=index_blocks(m), evidence={})
    for cid in ("structure.document_kind", "structure.required_sections", "structure.section_order",
                "structure.standard_headings", "structure.provenance_complete",
                "structure.master_refs_resolve", "structure.evidence_refs_resolve",
                "structure.unknown_jd_requirements"):
        assert checks[cid].status == "pass", (cid, checks[cid])


def test_document_kind_mismatch_is_critical():
    c = _run(_version(), kind="cv")["structure.document_kind"]
    assert (c.status, c.severity, c.category) == ("fail", "critical", "SOURCE") and c.blocking


def test_master_kind_is_read_from_metadata_kind():
    assert _run(_master())["structure.document_kind"].status == "pass"


def test_required_sections_resume_and_cv():
    v = _version()
    v["skills"] = []
    c = _run(v)["structure.required_sections"]
    assert (c.status, c.severity) == ("fail", "error") and c.measurement == ["skills"]
    v = _version()
    v["summary"] = ""
    assert _run(v)["structure.required_sections"].severity == "warning"
    cv = _version()
    cv["metadata"]["document_kind"] = "cv"
    cv["experience"] = []
    assert _run(cv, kind="cv")["structure.required_sections"].status == "pass"
    cv["education"] = []
    c = _run(cv, kind="cv")["structure.required_sections"]
    assert (c.status, c.severity) == ("fail", "error")


def test_section_order_fails_for_unrenderable_section_and_bad_order():
    v = _version()
    v["publications"] = [{"text": "x"}]
    c = _run(v)["structure.section_order"]
    assert (c.status, c.severity) == ("fail", "error") and "publications" in c.message
    contract = copy.deepcopy(CONTRACT)
    contract["sections"]["order"] = ["education", "summary", "experience", "projects", "skills", "certifications"]
    assert _run(_version(), contract)["structure.section_order"].status == "fail"


def test_standard_headings_fail():
    contract = copy.deepcopy(CONTRACT)
    contract["sections"]["headings"]["experience"] = "My Journey"
    c = _run(_version(), contract)["structure.standard_headings"]
    assert (c.status, c.severity) == ("fail", "error") and "experience" in c.message


def test_section_order_fails_on_duplicate_entries():
    contract = copy.deepcopy(CONTRACT)
    contract["sections"]["order"] = ["summary", "experience", "experience", "projects", "skills", "education",
                                     "certifications"]
    c = _run(_version(), contract)["structure.section_order"]
    assert c.status == "fail" and "duplicates" in c.message


def test_section_order_fails_on_unknown_section_entry():
    contract = copy.deepcopy(CONTRACT)
    contract["sections"]["order"] = ["summary", "experience", "projects", "skills", "education",
                                     "certifications", "hobbies"]
    c = _run(_version(), contract)["structure.section_order"]
    assert c.status == "fail" and "hobbies" in c.message


def test_standard_headings_fails_for_missing_heading():
    contract = copy.deepcopy(CONTRACT)
    del contract["sections"]["headings"]["experience"]
    c = _run(_version(), contract)["structure.standard_headings"]
    assert (c.status, c.severity) == ("fail", "error")
    assert "no heading for section(s): experience" in c.message


def test_every_structure_check_id_is_emitted():
    from lib.validators.structure import ALL_CHECK_IDS
    v = _version()
    index = index_blocks(_master())
    checks = check_structure(v, CONTRACT, "resume", master_index=index, evidence={})
    assert {c.id for c in checks} == set(ALL_CHECK_IDS)


def test_unknown_contract_sections_not_available():
    contract = copy.deepcopy(CONTRACT)
    contract["sections"] = "unknown"
    checks = _run(_version(), contract)
    assert checks["structure.section_order"].status == "not_available"
    assert checks["structure.standard_headings"].status == "not_available"
    contract = copy.deepcopy(CONTRACT)
    contract["sections"]["order"] = "unknown"
    assert _run(_version(), contract)["structure.section_order"].status == "not_available"


def test_bullet_without_source_refs_fails():
    v = _version()
    v["projects"][0]["bullets"][0]["source_refs"] = []
    c = _run(v)["structure.provenance_complete"]
    assert (c.status, c.severity, c.category) == ("fail", "critical", "FACTUAL") and c.blocking
    assert c.measurement["unsourced"] == [v["projects"][0]["bullets"][0]["id"]]


def test_skill_cert_and_summary_without_refs_fail():
    v = _version()
    v["skills"][0]["items"][0]["source_refs"] = []
    v["certifications"][0]["source_refs"] = []
    v["metadata"]["summary_source_refs"] = []
    c = _run(v)["structure.provenance_complete"]
    assert c.status == "fail"
    assert set(c.measurement["unsourced"]) == {v["skills"][0]["items"][0]["id"], v["certifications"][0]["id"], "sum-001"}


def test_bad_master_ref_fails():
    m = _master()
    v = _version(m)
    v["experience"][0]["bullets"][0]["source_refs"] = [{"type": "master", "id": "exp-999-b01"}]
    c = _run(v, master_index=index_blocks(m))["structure.master_refs_resolve"]
    assert (c.status, c.severity, c.category) == ("fail", "critical", "FACTUAL")
    assert c.measurement == ["exp-001-b01->exp-999-b01"]


def test_bad_evidence_ref_fails_and_good_one_passes():
    v = _version()
    v["experience"][0]["bullets"][0]["source_refs"].append({"type": "evidence", "id": "ev-missing"})
    c = _run(v, evidence={"ev-1": {}})["structure.evidence_refs_resolve"]
    assert (c.status, c.severity) == ("fail", "critical")
    v["experience"][0]["bullets"][0]["source_refs"][-1]["id"] = "ev-1"
    assert _run(v, evidence={"ev-1": {}})["structure.evidence_refs_resolve"].status == "pass"


def test_ref_resolution_not_available_without_index():
    checks = _run(_version())
    assert checks["structure.master_refs_resolve"].status == "not_available"
    assert checks["structure.evidence_refs_resolve"].status == "not_available"


def test_unknown_jd_requirements_is_info_and_never_fails():
    v = _version()
    v["metadata"]["unknown_jd_requirements"] = ["Kubernetes", "Terraform"]
    c = _run(v)["structure.unknown_jd_requirements"]
    assert (c.status, c.severity) == ("pass", "info") and c.measurement["count"] == 2


def test_messages_never_quote_resume_text():
    v = _version()
    v["experience"][0]["bullets"][0]["text"] = SENTINEL
    v["experience"][0]["bullets"][0]["source_refs"] = []
    v["certifications"][0]["text"] = SENTINEL
    v["certifications"][0]["source_refs"] = [{"type": "master", "id": "nope"}]
    v["summary"] = SENTINEL
    checks = check_structure(v, CONTRACT, "cv", master_index=index_blocks(_master()), evidence={})
    assert any(c.status == "fail" for c in checks)
    for c in checks:
        assert SENTINEL not in c.message and SENTINEL not in repr(c.measurement)
