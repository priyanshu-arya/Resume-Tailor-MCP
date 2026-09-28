"""Term matching, aliases, ambiguous-term case rules and unknown JD
requirements (spec §33, §34)."""

import pytest

from lib.keywords import (
    ALIASES,
    AMBIGUOUS_TERMS,
    KNOWN_SKILLS,
    detect_terms,
    extract_jd_keywords,
    normalize_term,
    term_in_text,
)
from lib.matching import match_resume_to_jd


# ---------------------------------------------------------------- tables

def test_aliases_point_at_known_canonicals_and_no_duplicate_concepts():
    for alias, canon in ALIASES.items():
        assert canon in KNOWN_SKILLS
        assert alias not in KNOWN_SKILLS
    for gone in ("golang", "postgres", "google cloud"):
        assert gone not in KNOWN_SKILLS
    assert "tf" not in ALIASES and "py" not in ALIASES


# ---------------------------------------------------------------- go / r / rest

@pytest.mark.parametrize("text", [
    "Experience with Google Cloud",
    "Worked at Google",
    "two years ago",
    "our go-to person",
    "Go to market strategy",
    "you will go above and beyond",
])
def test_go_negatives(text):
    assert not term_in_text("go", text)
    assert "go" not in detect_terms(text)


def test_google_cloud_yields_gcp_not_go():
    found = detect_terms("Experience with Google Cloud")
    assert "gcp" in found and "go" not in found


@pytest.mark.parametrize("text", [
    "Backend services in Go and Python",
    "Go, Rust",
    "Golang microservices",
    "wrote golang daily",
])
def test_go_positives(text):
    assert term_in_text("go", text)
    assert term_in_text("golang", text)


@pytest.mark.parametrize("text", [
    "Statistical modelling in R programming",
    "Proficient in R, Python and SQL",
    "R/RStudio",
    "R",
])
def test_r_positives(text):
    assert term_in_text("r", text)


@pytest.mark.parametrize("text", [
    "Signed, R. Smith",
    "R&D department",
    "a random word, rarely relevant",
    "r programming",           # lowercase r is not the language
    "RStudio only",            # R inside a larger token
    "R-squared of 0.9",
    "worked with J.R. on it",
])
def test_r_negatives(text):
    assert not term_in_text("r", text)


def test_rest_case_rules():
    assert term_in_text("rest", "Designed REST APIs")
    assert term_in_text("rest", "RESTful services")
    assert term_in_text("rest", "restful services")
    assert not term_in_text("rest", "worked with the rest of the team")
    assert not term_in_text("rest", "a restaurant booking app")
    assert not term_in_text("rest", "Rest assured")


# ---------------------------------------------------------------- punctuation terms

def test_punctuation_terms():
    assert term_in_text("c++", "Systems work in C++ and Python")
    assert term_in_text("c#", "Built tools in C#.")
    assert not term_in_text("c#", "c#m chord")
    assert term_in_text(".net", "Services on .NET Core")
    assert term_in_text(".net", "ASP.NET MVC")
    assert not term_in_text(".net", "asp.networking guide")
    assert term_in_text("node.js", "APIs in Node.js")
    assert term_in_text("node.js", "nodejs backend")
    assert term_in_text("ci/cd", "Owned CI/CD pipelines")
    assert term_in_text("ci/cd", "CICD pipelines")
    assert term_in_text("ci/cd", "CI-CD")
    assert not term_in_text("java", "JavaScript only")
    assert not term_in_text("sql", "NoSQL stores")
    assert not term_in_text("sql", "PostgreSQL")


def test_c_family_do_not_cross_match():
    found = detect_terms("C++ and C# developer")
    assert {"c++", "c#"} <= found


# ---------------------------------------------------------------- word-boundary cases

def test_excellent_is_not_excel():
    assert not term_in_text("excel", "excellent communication skills")
    assert not term_in_text("excel", "you will excel in a fast-paced team")
    assert term_in_text("excel", "Advanced Excel and SQL")
    assert term_in_text("excel", "microsoft excel")


def test_digital_is_not_git():
    assert not term_in_text("git", "digital marketing")
    assert term_in_text("git", "version control with git")


def test_github_is_not_git():
    found = detect_terms("Open-source work on GitHub")
    assert "github" in found and "git" not in found
    assert not term_in_text("git", "GitHub Actions")


def test_sap_requires_uppercase():
    assert term_in_text("sap", "SAP ERP rollouts")
    assert not term_in_text("sap", "meetings sap your energy")


def test_spring_semester_is_not_framework():
    assert not term_in_text("spring", "B.S., Spring 2023")
    assert term_in_text("spring", "Spring and Spring Boot services")


# ---------------------------------------------------------------- aliases

def test_alias_js_and_k8s():
    assert term_in_text("javascript", "Frontend in JS")
    assert term_in_text("kubernetes", "Deployed on k8s")
    assert term_in_text("k8s", "Kubernetes clusters")
    assert detect_terms("JS and K8s") == {"javascript", "kubernetes"}


def test_no_semantic_inference():
    # plausible synonyms that are not in ALIASES must not match
    assert not term_in_text("kubernetes", "container orchestration")
    assert not term_in_text("postgresql", "relational databases")
    assert not term_in_text("machine learning", "statistical modelling")


def test_detect_terms_returns_canonical_names():
    found = detect_terms("Golang, Postgres, sklearn, ReactJS, Google Cloud, TypeScript")
    assert found == {"go", "postgresql", "scikit-learn", "react", "gcp", "typescript"}
    assert all(t in KNOWN_SKILLS for t in found)


def test_ts_sci_clearance_is_not_typescript():
    assert not term_in_text("typescript", "Active TS/SCI clearance")
    assert term_in_text("typescript", "React + TS")


def test_normalize_term():
    assert normalize_term("  JS ") == "javascript"
    assert normalize_term("K8s") == "kubernetes"
    assert normalize_term("Golang") == "go"
    assert normalize_term("Postgres") == "postgresql"
    assert normalize_term("google cloud") == "gcp"
    assert normalize_term("Scikit Learn") == "scikit-learn"
    assert normalize_term("Machine-Learning") == "machine learning"
    assert normalize_term("CI-CD") == "ci/cd"
    assert normalize_term("Python") == "python"
    assert normalize_term("  LangGraph  ") == "langgraph"
    assert normalize_term("Some   Tool") == "some tool"


def test_term_in_text_unknown_term_uses_boundary():
    assert term_in_text("LangGraph", "Agents built with LangGraph.")
    assert not term_in_text("graph", "LangGraph agents")


# ---------------------------------------------------------------- unknown requirements

JD_WITH_SECTIONS = """Senior ML Engineer
About us: We are a US-based HealthTech startup backed by YCombinator. EEO employer.
Benefits: PTO, 401K, WFH, AcmeCorpPerks

Requirements:
- 5+ years of Python and PyTorch
- Experience with LangGraph and Nuxt.js
- Familiarity with HIPAA and SOC2 compliance
- BS/MS in CS or PhD; GPA not required
- Ship with Next.js, CI/CD and Google Cloud

Nice to have:
- Kubernetes, dbt, F#
"""


def test_unknown_requirements_detects_new_terms():
    unknown = extract_jd_keywords(JD_WITH_SECTIONS)["unknown_requirements"]
    for term in ("LangGraph", "HIPAA", "SOC2", "Nuxt.js", "F#"):
        assert term in unknown


def test_unknown_requirements_excludes_known_and_stoplist():
    unknown = extract_jd_keywords(JD_WITH_SECTIONS)["unknown_requirements"]
    for term in ("PyTorch", "Next.js", "CI", "CD", "BS", "MS", "PhD", "GPA", "CS",
                 "Python", "Kubernetes", "Google"):
        assert term not in unknown


def test_unknown_requirements_ignores_non_requirement_sections():
    unknown = extract_jd_keywords(JD_WITH_SECTIONS)["unknown_requirements"]
    # only appear in "About us" / "Benefits" text
    for term in ("HealthTech", "YCombinator", "AcmeCorpPerks", "EEO", "US", "PTO", "WFH"):
        assert term not in unknown


def test_unknown_requirements_whole_jd_when_no_sections():
    jd = "Backend Engineer\nWe use LangGraph, FastAPI and Temporal.io with HIPAA data. EEO."
    unknown = extract_jd_keywords(jd)["unknown_requirements"]
    assert unknown == ["LangGraph", "Temporal.io", "HIPAA"]


def test_unknown_requirements_deterministic_dedup_and_capped():
    jd = "Requirements:\n" + " ".join(f"Qx{chr(97 + i // 26)}{chr(97 + i % 26)}Kit" for i in range(40)) + " QxaaKit"
    a = extract_jd_keywords(jd)["unknown_requirements"]
    b = extract_jd_keywords(jd)["unknown_requirements"]
    assert a == b
    assert len(a) == 25
    assert a[0] == "QxaaKit" and a[24] == "QxayKit" and len(set(x.lower() for x in a)) == len(a)
    assert extract_jd_keywords(JD_WITH_SECTIONS) == extract_jd_keywords(JD_WITH_SECTIONS)


def test_extract_jd_keywords_keys_and_canonical_terms():
    kw = extract_jd_keywords(JD_WITH_SECTIONS)
    assert set(kw) == {"title", "seniority", "years_experience", "must_have",
                       "nice_to_have", "unknown_requirements"}
    assert {"python", "pytorch", "next.js", "ci/cd", "gcp"} <= set(kw["must_have"])
    assert "kubernetes" in kw["nice_to_have"]
    assert "go" not in kw["must_have"]  # "Google Cloud" must not yield go
    assert "machine learning" in kw["must_have"]  # "ML" in the title -> alias


# ---------------------------------------------------------------- match_resume_to_jd

JD = """Platform Engineer
Requirements:
- Go and Kubernetes
- PostgreSQL, REST APIs
- Terraform
Nice to have:
- Excel
"""

DICT_RESUME = {
    "summary": "Platform engineer building Golang services.",
    "skills": [{"category": "Infra", "items": [{"name": "Terraform"}, {"name": "Postgres"}]}],
    "experience": [{"title": "Engineer", "bullets": [
        {"text": "Ran workloads on k8s clusters for 3 teams"},
        {"text": "Designed RESTful APIs"},
    ]}],
    "projects": [],
    "certifications": [{"text": "Excellent attendance award"}],
}

LEGACY_RESUME = {
    "summary": "Platform engineer building Golang services.",
    "skills": [{"category": "Infra", "items": ["Terraform", "Postgres"]}],
    "experience": [{"title": "Engineer", "bullets": [
        "Ran workloads on k8s clusters for 3 teams",
        "Designed RESTful APIs",
    ]}],
    "certifications": ["Excellent attendance award"],
}


@pytest.mark.parametrize("resume", [DICT_RESUME, LEGACY_RESUME], ids=["dict", "legacy"])
def test_match_buckets(resume):
    r = match_resume_to_jd(resume, JD)
    assert sorted(r["matched"]) == ["go", "kubernetes", "rest"]
    assert sorted(r["weak"]) == ["postgresql", "terraform"]
    assert r["missing"] == ["excel"]  # "Excellent" is not Excel
    assert r["score"] == round(100 * 3 / 6)
    assert r["ats_visible_score"] == round(100 * 5 / 6)
    for key in ("jd_title", "jd_seniority", "jd_years_experience", "must_have",
                "nice_to_have", "unknown_requirements"):
        assert key in r
    assert r["nice_to_have"] == ["excel"]


def test_match_no_substring_false_positives():
    resume = {
        "summary": "Digital marketer at Google who loves a good restaurant.",
        "skills": [{"category": "x", "items": [{"name": "Excellent writing"}]}],
        "experience": [{"title": "Marketer", "bullets": [{"text": "Led the rest of the team"}]}],
    }
    jd = "Requirements: Go, git, REST, Excel, R"
    r = match_resume_to_jd(resume, jd)
    assert r["matched"] == [] and r["weak"] == []
    assert sorted(r["missing"]) == ["excel", "git", "go", "r", "rest"]


def test_match_k8s_resume_against_kubernetes_jd():
    resume = {"summary": "", "skills": [{"category": "x", "items": [{"name": "k8s"}]}], "experience": []}
    r = match_resume_to_jd(resume, "Requirements: Kubernetes")
    assert r["weak"] == ["kubernetes"]


def test_ambiguous_table_shape():
    for term, rule in AMBIGUOUS_TERMS.items():
        assert rule["forms"], term
        assert term in KNOWN_SKILLS or term in ALIASES


# --------------------------------------------------------------------------
# Phase 3.1: stack-listed terms are weak, not missing (defect fix)
# --------------------------------------------------------------------------

def test_stack_only_term_is_weak_not_missing():
    resume = {"summary": "", "skills": [], "experience": [],
             "projects": [{"name": "p", "stack": "Kubernetes", "bullets": []}]}
    r = match_resume_to_jd(resume, "Requirements: Kubernetes")
    assert r["weak"] == ["kubernetes"]
    assert r["missing"] == []


def test_stack_term_used_in_a_bullet_is_matched_not_weak():
    resume = {"summary": "", "skills": [], "experience": [],
             "projects": [{"name": "p", "stack": "Kubernetes",
                          "bullets": [{"text": "Deployed the service on Kubernetes."}]}]}
    r = match_resume_to_jd(resume, "Requirements: Kubernetes")
    assert r["matched"] == ["kubernetes"]


# --------------------------------------------------------------------------
# classify_term / requirement_view (Phase 3.1)
# --------------------------------------------------------------------------

def test_classify_term_supported_weak_missing():
    from lib.matching import classify_term
    resume = {"summary": "Built things with Python.",
             "skills": [{"category": "x", "items": [{"name": "AWS"}]}],
             "experience": [], "projects": []}
    assert classify_term("python", resume) == "supported"
    assert classify_term("aws", resume) == "weak"
    assert classify_term("kubernetes", resume) == "missing"


def test_requirement_view_covers_every_axis_once():
    from lib.matching import requirement_view
    resume = {"summary": "Built things with Python.",
             "skills": [{"category": "x", "items": [{"name": "AWS"}]}],
             "experience": [], "projects": [{"name": "p", "stack": "LangChain", "bullets": []}]}
    result = match_resume_to_jd(resume, "Requirements: Python, AWS, Kubernetes, LangChain")
    rows = requirement_view(result, resume)
    by_term = {r["term"].lower(): r for r in rows}
    assert by_term["python"]["status"] == "supported"
    assert by_term["aws"]["status"] == "weak"
    assert by_term["kubernetes"]["status"] == "missing"
    assert by_term["langchain"]["status"] == "weak"  # reclassified via classify_term, not "unknown"
    assert by_term["langchain"]["importance"] == "unrecognized"
    assert len(rows) == len(set(r["term"].lower() for r in rows))  # no duplicates


def test_requirement_view_unknown_term_not_in_master_stays_unknown():
    from lib.matching import requirement_view
    resume = {"summary": "", "skills": [], "experience": [], "projects": []}
    # "ZorbaFlux" is CamelCase, so find_unknown_requirements flags it; a
    # plain lowercase word like "zorbaflux" would not be technical-looking
    # and would never reach the unknown_requirements axis at all
    result = match_resume_to_jd(resume, "Requirements: ZorbaFlux")
    assert "ZorbaFlux" in result["unknown_requirements"]
    rows = requirement_view(result, resume)
    zf = next(r for r in rows if r["term"] == "ZorbaFlux")
    assert zf["status"] == "unknown"


def test_requirement_view_priority_ordering():
    from lib.matching import requirement_view
    resume = {"summary": "", "skills": [], "experience": [], "projects": []}
    jd = "Requirements: Python\nNice to have: Rust"
    result = match_resume_to_jd(resume, jd)
    rows = requirement_view(result, resume)
    importances = [r["importance"] for r in rows]
    # must_have rows must all come before nice_to_have rows
    if "must_have" in importances and "nice_to_have" in importances:
        assert importances.index("must_have") < importances.index("nice_to_have") or \
              importances.count("must_have") == len(importances)


# --------------------------------------------------------------------------
# term_type / certification_requirements (Phase 3.2 foundation)
# --------------------------------------------------------------------------

def test_term_type_covers_every_known_skill():
    from lib.keywords import KNOWN_SKILLS, TERM_TYPES, REQUIREMENT_TYPES
    assert set(KNOWN_SKILLS) == set(TERM_TYPES)
    assert set(TERM_TYPES.values()) <= set(REQUIREMENT_TYPES)


def test_term_type_unknown_term_gets_default():
    from lib.keywords import term_type, DEFAULT_TERM_TYPE
    assert term_type("SomeRandomThing") == DEFAULT_TERM_TYPE


def test_certification_requirements_line_scoped():
    from lib.keywords import certification_requirements
    jd = ("Requirements:\n"
         "- AWS Certified Solutions Architect required\n"
         "- Strong Python skills\n"
         "Nice to have: Kubernetes\n")
    assert certification_requirements(jd) == ["aws"]


def test_certification_requirements_empty_when_no_cert_line():
    from lib.keywords import certification_requirements
    assert certification_requirements("Requirements: Python, AWS") == []


def test_certification_requirements_empty_text():
    from lib.keywords import certification_requirements
    assert certification_requirements("") == []
    assert certification_requirements(None) == []
