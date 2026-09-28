"""Phase 5: contract completeness, registry self-validation, the
`template.contract_complete` release check, and `recommend_template`'s
usable/releasable_alternative/warning fields (spec §26-30, D-8).

Verifies the D-8 hole is closed: a hollowed `status: supported` contract
(fields hand-edited to "unknown") must fail to load / block release, not
silently degrade format checks to not_available while release succeeds.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from lib import templates
from lib.errors import ResumeTailorError
from lib.schemas import CONTRACT_REQUIREMENTS, TemplateContract, contract_gaps
from tests.conftest import needs_tectonic

REAL_CLASSIC = templates.get_template("classic-minimalist")


def _classic_dict() -> dict:
    return copy.deepcopy(REAL_CLASSIC)


# --- contract_gaps (pure) ---------------------------------------------------

def test_contract_requirements_is_a_stable_map():
    for block in ("page", "typography", "layout", "sections", "spacing", "limits", "formatting", "latex"):
        assert block in CONTRACT_REQUIREMENTS
        assert CONTRACT_REQUIREMENTS[block]


def test_contract_gaps_empty_for_a_complete_contract():
    assert contract_gaps(_classic_dict()) == []


def test_contract_gaps_whole_block_unknown():
    d = _classic_dict()
    d["page"] = "unknown"
    gaps = contract_gaps(d)
    assert set(gaps) == {f"page.{k}" for k in CONTRACT_REQUIREMENTS["page"]}


def test_contract_gaps_single_field_unknown():
    d = _classic_dict()
    d["typography"]["body"] = "unknown"
    assert contract_gaps(d) == ["typography.body"]


def test_contract_gaps_missing_key_counts_as_unknown():
    d = _classic_dict()
    del d["limits"]["max_pages"]
    assert "limits.max_pages" in contract_gaps(d)


def test_contract_gaps_all_unknown_for_experimental_metadata():
    experimental = templates.get_template("awesome-cv-resume")
    gaps = contract_gaps(experimental)
    # every block this contract leaves unmeasured shows up as a gap
    for block in ("page", "typography", "spacing", "formatting"):
        assert f"{block}." in "".join(g for g in gaps if g.startswith(block))


# --- TemplateContract model validator (load-time programming-error net) ----

def test_supported_status_requires_complete_contract():
    TemplateContract.model_validate(_classic_dict())  # sanity: real contract loads


@pytest.mark.parametrize("path", [("page",), ("typography", "body"), ("spacing", "line_spacing"),
                                  ("formatting", "bullet_style"), ("latex", "layout_only_macros")])
def test_hollowed_supported_contract_fails_to_load(path):
    d = _classic_dict()
    node = d
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = "unknown"
    with pytest.raises(ValidationError):
        TemplateContract.model_validate(d)


def test_experimental_may_be_entirely_unknown():
    d = _classic_dict()
    d["status"] = "experimental"
    d["page"] = "unknown"
    d["typography"] = "unknown"
    TemplateContract.model_validate(d)  # does not raise


def test_get_contract_wraps_validation_error_as_business_error(monkeypatch):
    """A hand-edited hollow templates.yaml must surface as a business error
    (readable message, TEMPLATE_REGISTRY_INVALID), never a bare
    pydantic ValidationError / INTERNAL_ERROR."""
    bad = _classic_dict()
    bad["page"] = "unknown"
    monkeypatch.setattr(templates, "_lookup", lambda tid: bad)
    with pytest.raises(ResumeTailorError) as ei:
        templates.get_contract("classic-minimalist")
    assert ei.value.code == "TEMPLATE_REGISTRY_INVALID"


# --- validate_registry() ----------------------------------------------------

def test_registry_validates():
    assert templates.validate_registry()["ok"] is True


def test_renderers_are_a_subset_of_supported_ids():
    supported = {t["id"] for t in templates.list_templates() if t["status"] == "supported"}
    assert set(templates.RENDERERS) <= supported


def test_registry_detects_hollowed_supported_contract(monkeypatch):
    bad_list = [t if t["id"] != "classic-minimalist" else {**_classic_dict(), "page": "unknown"}
                for t in templates._load_all()]
    monkeypatch.setattr(templates, "_load_all", lambda: bad_list)
    with pytest.raises(ResumeTailorError) as ei:
        templates.validate_registry()
    assert ei.value.code == "TEMPLATE_REGISTRY_INVALID"
    assert any("contract has gaps" in p for p in ei.value.details["problems"])


def test_registry_detects_supported_template_claiming_columns_or_structures(monkeypatch):
    for field, value in (("columns", 2),):
        bad = _classic_dict()
        bad["layout"][field] = value
        bad_list = [t if t["id"] != "classic-minimalist" else bad for t in templates._load_all()]
        monkeypatch.setattr(templates, "_load_all", lambda bl=bad_list: bl)
        with pytest.raises(ResumeTailorError) as ei:
            templates.validate_registry()
        assert ei.value.code == "TEMPLATE_REGISTRY_INVALID"


def test_registry_detects_non_supported_partial_precision(monkeypatch):
    bad = templates.get_template("awesome-cv-resume")
    bad = copy.deepcopy(bad)
    bad["page"] = {"size": "letter"}  # a guess, not the literal "unknown"
    bad_list = [t if t["id"] != "awesome-cv-resume" else bad for t in templates._load_all()]
    monkeypatch.setattr(templates, "_load_all", lambda: bad_list)
    with pytest.raises(ResumeTailorError) as ei:
        templates.validate_registry()
    assert any("literal" in p for p in ei.value.details["problems"])


def test_every_contract_requirement_key_is_in_enforcement():
    required = {f"{block}.{k}" for block, keys in CONTRACT_REQUIREMENTS.items() for k in keys}
    assert required <= set(_classic_dict()["enforcement"])


# --- template.contract_complete release check -------------------------------

@needs_tectonic
def test_hollowed_contract_blocks_release(master, monkeypatch):
    """Headline D-8 test: a supported template whose contract has been
    hollowed at release time must block release with
    template.contract_complete, not silently degrade to not_available while
    release succeeds."""
    import server
    from lib import templates as _templates

    real_get_contract = _templates.get_contract

    def hollow(tid):
        c = real_get_contract(tid)
        if tid == "classic-minimalist":
            c["page"] = "unknown"
            c["typography"] = "unknown"
        return c

    wf = server.analyze_tailoring_requirements("Backend Engineer\nRequirements:\n- Python\n")["workflow_id"]
    out = server.tailor_resume("acme", [], wf, template="classic-minimalist")
    assert out["ok"], out
    monkeypatch.setattr("lib.release._templates.get_contract", hollow)
    rel = server.release_resume(out["version_id"], wf)
    assert rel["released"] is False
    assert "template.contract_complete" in {c["id"] for c in rel["critical_failures"]}


@needs_tectonic
def test_experimental_template_reports_contract_not_available(master):
    import server
    from lib import release as _release
    wf = server.analyze_tailoring_requirements("Backend Engineer\nRequirements:\n- Python\n")["workflow_id"]
    out = server.tailor_resume("acme", [], wf, template="metrics-driven")
    assert out["ok"], out
    result = _release.validate(out["version_id"], wf)
    checks_by_id = {c.id: c for c in result["report"].checks}
    assert checks_by_id["template.contract_complete"].status == "not_available"


@needs_tectonic
def test_invalid_registry_is_a_business_error_at_release(master, monkeypatch):
    import server
    from lib import templates as _templates
    wf = server.analyze_tailoring_requirements("Backend Engineer\nRequirements:\n- Python\n")["workflow_id"]
    out = server.tailor_resume("acme", [], wf, template="classic-minimalist")
    assert out["ok"], out

    def broken(tid):
        raise ResumeTailorError("TEMPLATE_REGISTRY_INVALID", "broken on purpose")
    monkeypatch.setattr("lib.release._templates.get_contract", broken)
    rel = server.release_resume(out["version_id"], wf)
    assert rel["ok"] is False
    assert rel["error"]["code"] == "TEMPLATE_REGISTRY_INVALID"


# --- recommend_template: usable / releasable_alternative / warning ---------

def test_recommend_supported_only_is_always_usable(legacy_master):
    rec = templates.recommend_template(
        "Full-stack web developer. React, Angular, Vue, PHP.", legacy_master, supported_only=True)
    assert rec["usable"] is True
    assert "warning" not in rec
    assert "releasable_alternative" not in rec


def test_recommend_unrestricted_experimental_pick_is_not_usable(legacy_master):
    jd = ("Full-stack web developer. React, Angular, Vue, PHP, REST, MVC, HTML, CSS, "
          "JavaScript, Node.js, Express. 3 years of experience.")
    rec = templates.recommend_template(jd, legacy_master, supported_only=False)
    assert rec["recommended_template"] == "full-stack-modern"
    assert rec["usable"] is False
    assert "classic-minimalist" in rec["warning"]
    assert templates.is_releasable(rec["releasable_alternative"])
    assert "next_step" in rec


def test_recommend_unrestricted_classic_pick_is_usable():
    # A JD/resume combo that scores classic-minimalist highest even unrestricted.
    rec = templates.recommend_template("We need a research-adjacent backend engineer. REST, git, CI/CD.", None,
                                       supported_only=False)
    if rec["recommended_template"] == "classic-minimalist":
        assert rec["usable"] is True
        assert "warning" not in rec


def test_list_templates_exposes_releasable():
    rows = {t["id"]: t for t in templates.list_templates()}
    assert rows["classic-minimalist"]["releasable"] is True
    for tid, row in rows.items():
        if tid != "classic-minimalist":
            assert row["releasable"] is False
