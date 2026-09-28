"""Claim-strength / placement / technology / metric / verb rules (lib/validators/provenance.py).

Driven end-to-end through lib.patches.validate_and_apply with the real hook.
"""

from __future__ import annotations

import pytest

from lib import rules
from lib.errors import ResumeTailorError
from lib.ids import normalize_master
from lib.patches import validate_and_apply
from lib.safe_yaml import load_file
from lib.validators.provenance import extract_metrics, make_hook

WF = "wf-prov"
SENTINEL = "ZZ-SECRET-SENTINEL-4242"

# Synthetic master IDs (tests/conftest.py SYNTHETIC_LEGACY_MASTER):
B_BUILT = "exp-001-b01"   # "Built 18 Python/Django REST endpoints, reducing median API latency 42%."
B_LED = "exp-001-b02"     # "Led migration of nightly ETL jobs to Airflow on AWS, serving 3 product teams."
EXP = "exp-001"
PROJ = "proj-001"
PROJ_B = "proj-001-b01"   # "Created a retrieval-augmented note search tool over 2,000 personal notes."
SUMMARY = "sum-001"       # "Backend engineer with 4 years building Python services and data pipelines on AWS."


@pytest.fixture
def m(legacy_master):
    return normalize_master(legacy_master, "resume")


def ev(eid, term, category, text, metrics=None, *, workflow_id=WF, confirmed=True):
    return {"id": eid, "workflow_id": workflow_id, "workspace_id": "ws-1", "term": term, "category": category,
            "evidence_text": text, "confirmed": confirmed, "metrics": metrics or [],
            "created_at": "2026-09-01T00:00:00Z"}


EV = {
    "ev-fastapi": ev("ev-fastapi", "FastAPI", "personal_project",
                     "Built REST APIs using FastAPI for my personal RAG project"),
    "ev-k8s": ev("ev-k8s", "Kubernetes", "learning_only", "Completed a Kubernetes tutorial course"),
    "ev-aws": ev("ev-aws", "AWS", "personal_project", "deployed RAG app on AWS EC2"),
    "ev-metric": ev("ev-metric", "Redis", "professional", "Added Redis caching, cut p95 latency by 30%",
                    metrics=["30%"]),
    "ev-nometric": ev("ev-nometric", "Redis", "professional", "Added Redis caching, cut p95 latency by 30%"),
    "ev-course": ev("ev-course", "Airflow", "coursework", "Used Airflow in a data engineering course"),
}


def mref(i):
    return {"type": "master", "id": i}


def eref(i):
    return {"type": "evidence", "id": i}


def replace(target, text, refs, **nc):
    return {"operation": "replace_block", "target": {"id": target},
            "new_content": {"text": text, "source_refs": refs, **nc}}


def add_block(parent, text, refs, **nc):
    return {"operation": "add_block", "parent_id": parent,
            "new_content": {"text": text, "source_refs": refs, **nc}}


def add_skill(category, name, refs, **kw):
    return {"operation": "add_skill_item", "category": category, "name": name, "source_refs": refs, **kw}


def run(master, patches, evidence=EV):
    return validate_and_apply(master, patches, workflow_id=WF, evidence=evidence,
                              provenance_hook=make_hook(master, evidence))


def rejected(master, patches, evidence=EV) -> list[dict]:
    with pytest.raises(ResumeTailorError) as exc:
        run(master, patches, evidence)
    assert exc.value.code == "PROVENANCE_VIOLATION"
    return exc.value.details["rejections"]


def rule_ids(rejs) -> set[str]:
    return {r["rule"] for r in rejs}


# --------------------------------------------------------------------------
# lib/rules.py
# --------------------------------------------------------------------------

def test_rules_version_matches_yaml():
    data = load_file(rules.ETIQUETTE_PATH)
    assert rules.rules_version() == data["rules_version"] == "2026.10-v2.1"


def test_placement_matrix_and_fail_closed():
    assert rules.placement("professional", "experience") == "yes"
    assert rules.placement("personal_project", "summary") == "limited"
    assert rules.placement("learning_only", "skills") == "limited"
    assert rules.placement("personal_project", "experience") == "no"
    assert rules.placement("master_summary", "summary") == "yes"
    assert rules.placement("master_summary", "experience") == "no"
    # unknown category / section -> "no"
    assert rules.placement("wizardry", "skills") == "no"
    assert rules.placement("professional", "hobbies") == "no"
    assert rules.placement(None, "skills") == "no"  # type: ignore[arg-type]


def test_rule_tables_load():
    assert set(rules.evidence_categories()) >= {"professional", "learning_only", "none"}
    assert "familiar" in rules.familiarity_markers()
    assert "built" in rules.safe_verbs()
    assert any({"led", "lead", "leading"} <= fam for fam in rules.high_scope_families())


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------

def test_unchanged_master_replay_has_no_rejections(m):
    body, report = run(m, [])
    assert report["applied"] == []


def test_rephrasing_same_facts_passes(m):
    body, _ = run(m, [replace(B_BUILT, "Reduced median API latency 42% by building 18 Python/Django REST endpoints.",
                              [mref(B_BUILT)])])
    assert body["experience"][0]["bullets"][0]["text"].startswith("Reduced median API latency 42%")


def test_high_scope_verb_kept_from_source_passes(m):
    run(m, [replace(B_LED, "Led the Airflow migration of nightly ETL jobs on AWS for 3 product teams.",
                    [mref(B_LED)])])


# --------------------------------------------------------------------------
# Spec acceptance: personal_project FastAPI evidence
# --------------------------------------------------------------------------

def test_personal_project_evidence_supports_project_bullet_and_skill(m):
    body, report = run(m, [
        add_block(PROJ, "Built REST APIs with FastAPI for the RAG project.", [eref("ev-fastapi")]),
        add_skill("Frameworks", "FastAPI", [eref("ev-fastapi")]),
    ])
    assert len(report["applied"]) == 2
    proj = body["projects"][0]
    assert proj["bullets"][-1]["claim_strength"] == "personal_project"


def test_personal_project_evidence_in_experience_is_placement_violation(m):
    rejs = rejected(m, [add_block(EXP, "Built REST APIs with FastAPI.", [eref("ev-fastapi")])])
    assert "provenance.placement" in rule_ids(rejs)


# --------------------------------------------------------------------------
# Spec acceptance: learning_only Kubernetes
# --------------------------------------------------------------------------

def test_learning_only_in_experience_rejected(m):
    rejs = rejected(m, [add_block(EXP, "Implemented Kubernetes infrastructure.", [eref("ev-k8s")])])
    ids = rule_ids(rejs)
    assert "provenance.placement" in ids
    assert "provenance.scope_expansion" in ids


def test_learning_only_skill_outside_familiarity_group_rejected(m):
    rejs = rejected(m, [add_skill("Frameworks", "Kubernetes", [eref("ev-k8s")])])
    assert rule_ids(rejs) == {"provenance.familiarity_group"}


def test_learning_only_skill_in_familiarity_group_passes(m):
    body, _ = run(m, [add_skill("Familiar With", "Kubernetes", [eref("ev-k8s")])])
    group = next(g for g in body["skills"] if g["category"] == "Familiar With")
    assert group["items"][0]["claim_strength"] == "learning_only"


def test_learning_only_plus_master_skill_still_needs_familiarity_group(m):
    rejs = rejected(m, [add_skill("Cloud", "Kubernetes", [mref("skill-docker"), eref("ev-k8s")])])
    assert "provenance.familiarity_group" in rule_ids(rejs)


# --------------------------------------------------------------------------
# Spec §24: deployed vs architected/led
# --------------------------------------------------------------------------

def test_spec24_deployed_on_aws_in_projects_passes(m):
    run(m, [add_block(PROJ, "Deployed a RAG application on AWS EC2.", [eref("ev-aws")])])


def test_spec24_architected_and_led_rejected(m):
    rejs = rejected(m, [add_block(PROJ, "Architected and led enterprise AWS infrastructure.", [eref("ev-aws")])])
    verb = [r for r in rejs if r["rule"] == "provenance.high_scope_verb"]
    assert verb and "architected" in verb[0]["message"] and "led" in verb[0]["message"]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def test_invented_metric_rejected(m):
    rejs = rejected(m, [replace(B_LED, "Led migration of nightly ETL jobs to Airflow on AWS, reducing latency 42%.",
                                [mref(B_LED)])])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_master_own_metric_passes(m):
    run(m, [replace(B_BUILT, "Built 18 Django REST endpoints in Python, reducing median API latency 42%.",
                    [mref(B_BUILT)])])


def test_evidence_metric_only_from_metrics_list(m):
    ok_text = "Added Redis caching that cut p95 latency 30%."
    run(m, [add_block(EXP, ok_text, [eref("ev-metric")])])
    rejs = rejected(m, [add_block(EXP, ok_text, [eref("ev-nometric")])])
    assert rule_ids(rejs) == {"provenance.unsupported_metric"}


def test_metric_suffix_and_separator_normalization(m):
    # "2,000" in the master matches "2000"; "3" alone does not become "3%".
    run(m, [replace(PROJ_B, "Created a retrieval-augmented search tool over 2000 personal notes.", [mref(PROJ_B)])])
    rejs = rejected(m, [replace(B_LED, "Led migration of nightly ETL jobs to Airflow on AWS, serving 3% of teams.",
                                [mref(B_LED)])])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_extract_metrics_shapes():
    got = extract_metrics("Cut costs $1,200 and 2.5x faster for 100K users, 42 % less, on EC2 with GPT-4 and 3 teams")
    assert ("$", "1200", "") in got
    assert ("", "2.5", "x") in got
    assert ("", "100", "k") in got
    assert ("", "42", "%") in got
    assert ("", "3", "") in got
    assert all(num not in ("2", "4") for _c, num, _s in got)  # EC2 / GPT-4 are names


# --------------------------------------------------------------------------
# Technologies
# --------------------------------------------------------------------------

def test_unsupported_technology_rejected(m):
    rejs = rejected(m, [replace(B_BUILT, "Built Django and Kubernetes services.", [mref(B_BUILT)])])
    tech = [r for r in rejs if r["rule"] == "provenance.unsupported_technology"]
    assert tech and "kubernetes" in tech[0]["message"].lower()


def test_skill_item_needs_supporting_source(m):
    rejs = rejected(m, [add_skill("Frameworks", "Kubernetes", [mref(B_BUILT)])])
    assert "provenance.unsupported_technology" in rule_ids(rejs)
    run(m, [add_skill("Frameworks", "Django", [mref(B_BUILT)])])


# --------------------------------------------------------------------------
# Claim strength and mixed refs
# --------------------------------------------------------------------------

def test_claim_strength_above_evidence_rejected(m):
    rejs = rejected(m, [add_block(PROJ, "Deployed a RAG application on AWS EC2.", [eref("ev-aws")],
                                  claim_strength="professional")])
    assert "provenance.claim_strength" in rule_ids(rejs)


def test_master_skill_item_claim_up_to_professional_passes(m):
    run(m, [add_skill("Platforms", "Python", [mref("skill-python")], claim_strength="professional")])


def test_one_ref_with_no_placement_rejects_whole_patch(m):
    rejs = rejected(m, [add_block(EXP, "Built REST APIs with FastAPI and Django.",
                                  [mref(B_BUILT), eref("ev-fastapi")])])
    placement = [r for r in rejs if r["rule"] == "provenance.placement"]
    assert len(placement) == 1 and "ev-fastapi" in placement[0]["message"]


def test_coursework_only_cannot_expand_into_experience(m):
    rejs = rejected(m, [add_block(EXP, "Used Airflow for data pipelines.", [eref("ev-course")])])
    assert "provenance.scope_expansion" in rule_ids(rejs)


# --------------------------------------------------------------------------
# Master summary (master_summary pseudo-category)
# --------------------------------------------------------------------------

def test_summary_rewrite_citing_master_summary_passes(m):
    body, _ = run(m, [replace(SUMMARY, "Python backend engineer with 4 years of services and data pipelines on AWS.",
                              [mref(SUMMARY)])])
    assert "4 years" in body["summary"]


def test_master_summary_cited_from_experience_rejected(m):
    rejs = rejected(m, [add_block(EXP, "Built Python services and data pipelines on AWS.", [mref(SUMMARY)])])
    assert "provenance.placement" in rule_ids(rejs)


# --------------------------------------------------------------------------
# No patch text in messages
# --------------------------------------------------------------------------

def test_messages_do_not_quote_patch_text(m):
    text = f"Architected {SENTINEL} Kubernetes platform, cutting costs 77%."
    rejs = rejected(m, [
        add_block(EXP, text, [eref("ev-k8s")]),
        add_block(PROJ, text, [eref("ev-aws")], claim_strength="professional"),
        add_skill(f"Frameworks {SENTINEL}"[:60], f"K8s {SENTINEL}"[:60], [eref("ev-k8s")]),
    ])
    assert rejs
    for r in rejs:
        assert SENTINEL not in r["message"]
        assert "77" not in r["message"]


# --------------------------------------------------------------------------
# 4.3 -- skill-item provenance confirmation tests
# --------------------------------------------------------------------------

def test_one_evidence_cannot_justify_two_unrelated_skills(m):
    # ev-fastapi's text/term is FastAPI; a second, unrelated skill name
    # citing the same record is rejected on its own patch (each add_skill_item
    # is its own patch with its own refs -- one source cannot justify a group).
    body, _ = run(m, [add_skill("Frameworks", "FastAPI", [eref("ev-fastapi")])])
    assert body["skills"][-1]["items"][-1]["name"] == "FastAPI"
    rejs = rejected(m, [add_skill("Frameworks", "Django", [eref("ev-fastapi")])])
    assert "provenance.unsupported_technology" in rule_ids(rejs)


def test_group_id_cannot_be_used_as_a_source_ref(m):
    rejs = rejected(m, [add_skill("Tools", "Terraform", [mref("skg-001")])])
    assert "provenance.uncitable_ref" in rule_ids(rejs)


# --------------------------------------------------------------------------
# 4.4 -- scope inflation (verb swapped, inflation kept) + rules_version drift
# --------------------------------------------------------------------------

def test_scope_inflation_marker_without_matching_source_rejected(m):
    text = "Deployed enterprise-grade AWS infrastructure serving thousands of users across multiple regions."
    rejs = rejected(m, [add_block(PROJ, text, [eref("ev-aws")], claim_strength="personal_project")])
    assert "provenance.scope_inflation" in rule_ids(rejs)


def test_scope_inflation_marker_present_in_cited_source_passes(m):
    evd = dict(EV, **{"ev-aws-enterprise": ev("ev-aws-enterprise", "AWS", "personal_project",
                    "Deployed an enterprise-grade AWS setup for my personal project across multiple regions")})
    body, _ = run(m, [add_block(PROJ, "Deployed enterprise-grade AWS infrastructure across multiple regions.",
                                [eref("ev-aws-enterprise")], claim_strength="personal_project")], evidence=evd)
    assert body  # no rejection


# --------------------------------------------------------------------------
# 4.5 -- metric provenance, adversarial coverage
# --------------------------------------------------------------------------

def test_rounded_up_metric_rejected(m):
    rejs = rejected(m, [replace(B_BUILT, "Built 18 Python/Django REST endpoints, cutting latency 45%.",
                                [mref(B_BUILT)])])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_metric_unit_swapped_rejected(m):
    rejs = rejected(m, [replace(B_BUILT, "Built 18 Python/Django REST endpoints, cutting latency 42x.",
                                [mref(B_BUILT)])])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_metric_unit_swapped_to_ms_rejected(m):
    rejs = rejected(m, [replace(B_BUILT, "Built 18 Python/Django REST endpoints, cutting latency 42ms.",
                                [mref(B_BUILT)])])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_percentage_invented_from_a_ratio_rejected(m):
    rejs = rejected(m, [replace(PROJ_B, "Created a retrieval tool over 2,000 notes, improving recall by 15%.",
                                [mref(PROJ_B)])])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_currency_changed_rejected(m):
    evd = dict(EV, **{"ev-cost": ev("ev-cost", "AWS", "personal_project", "Cut my AWS bill by $500 a month",
                                    metrics=["$500"])})
    rejs = rejected(m, [add_block(PROJ, "Cut AWS spend by ₹500 a month.", [eref("ev-cost")],
                                  claim_strength="personal_project")], evidence=evd)
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_currency_dropped_is_allowed_known_looseness(m):
    # Deliberate asymmetry: a currency sign may be dropped from new text
    # (never added or changed) -- see lib/validators/provenance._metric_supported.
    evd = dict(EV, **{"ev-cost": ev("ev-cost", "AWS", "personal_project", "Cut my AWS bill by $500 a month",
                                    metrics=["$500"])})
    body, _ = run(m, [add_block(PROJ, "Cut AWS spend by 500 a month.", [eref("ev-cost")],
                                claim_strength="personal_project")], evidence=evd)
    assert body


def test_metric_present_only_in_jd_is_rejected(m):
    # A number that only appears in the JD (never cited by any ref) is not
    # in the metric pool at all, regardless of context.
    rejs = rejected(m, [add_block(PROJ, "Reduced latency by 99%.", [eref("ev-aws")],
                                  claim_strength="personal_project")])
    assert "provenance.unsupported_metric" in rule_ids(rejs)


def test_metric_pool_shared_across_refs_is_a_known_looseness(m):
    # Known looseness (documented in README): the metric pool is the UNION of
    # every cited ref's metrics, so a number from one ref can back a claim
    # about a different ref's subject as long as both are cited together.
    evd = dict(EV, **{
        "ev-a": ev("ev-a", "AWS", "personal_project", "Deployed on AWS", metrics=[]),
        "ev-b": ev("ev-b", "Kubernetes", "personal_project", "Cut costs by 30% using Kubernetes", metrics=["30%"]),
    })
    body, _ = run(m, [add_block(PROJ, "Deployed on AWS, cutting costs by 30%.", [eref("ev-a"), eref("ev-b")],
                                claim_strength="personal_project")], evidence=evd)
    assert body
