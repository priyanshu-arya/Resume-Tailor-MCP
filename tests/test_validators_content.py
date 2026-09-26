"""lib/validators/content.py and the lib/ats.score_ats wrapper."""

from __future__ import annotations

import copy

import pytest

from lib.ats import score_ats
from lib.ids import normalize_master
from lib.schemas import Check
from lib.validators.content import check_content, summarize, weak_openers
from tests.conftest import SYNTHETIC_LEGACY_MASTER

SENTINEL = "ZQXSENTINEL"


def _doc(**overrides) -> dict:
    doc = normalize_master(copy.deepcopy(SYNTHETIC_LEGACY_MASTER), "resume")
    doc.update(overrides)
    return doc


def _by_id(checks: list[Check]) -> dict[str, Check]:
    ids = [c.id for c in checks]
    assert len(ids) == len(set(ids)), "check ids must be unique"
    return {c.id: c for c in checks}


def test_synthetic_master_passes_everything():
    checks = _by_id(check_content(_doc()))
    assert set(checks) == {
        "content.contact_email_phone", "content.required_sections", "content.summary_length",
        "content.generic_summary", "content.bullet_length", "content.banned_opener",
        "content.quantified_ratio", "content.bullets_per_role", "content.dates_present",
        "content.personal_identifiers",
    }
    assert all(c.status == "pass" for c in checks.values()), [c for c in checks.values() if c.status != "pass"]
    assert all(c.source == "etiquette" and c.category == "FORMAT" for c in checks.values())


def test_weak_openers_come_from_etiquette_yaml():
    assert "responsible for" in weak_openers()
    assert "worked on" in weak_openers()


def test_missing_email_is_error_missing_phone_is_warning():
    doc = _doc()
    doc["contact"].pop("email")
    c = _by_id(check_content(doc))["content.contact_email_phone"]
    assert (c.status, c.severity) == ("fail", "error") and c.blocking
    doc = _doc()
    doc["contact"].pop("phone")
    c = _by_id(check_content(doc))["content.contact_email_phone"]
    assert (c.status, c.severity) == ("fail", "warning") and not c.blocking


def test_required_sections():
    c = _by_id(check_content(_doc(education=[])))["content.required_sections"]
    assert (c.status, c.severity) == ("fail", "error") and c.measurement == ["education"]
    c = _by_id(check_content(_doc(summary="")))["content.required_sections"]
    assert (c.status, c.severity) == ("fail", "warning")
    # a CV only requires education
    cv = _doc(experience=[], skills=[], summary="")
    cv["metadata"]["kind"] = "cv"
    assert _by_id(check_content(cv))["content.required_sections"].status == "pass"


def test_summary_length():
    c = _by_id(check_content(_doc(summary="Too short.")))["content.summary_length"]
    assert (c.status, c.severity) == ("fail", "warning")
    c = _by_id(check_content(_doc(summary="x" * 601)))["content.summary_length"]
    assert c.status == "fail"
    assert _by_id(check_content(_doc(summary="")))["content.summary_length"].status == "not_available"


def test_generic_summary():
    c = _by_id(check_content(_doc(summary="Highly motivated team player building Python services on AWS.")))
    c = c["content.generic_summary"]
    assert (c.status, c.severity) == ("fail", "warning")
    assert set(c.measurement) == {"highly motivated", "team player"}


def test_bullet_length_covers_projects():
    doc = _doc()
    doc["projects"][0]["bullets"][0]["text"] = "Built " + "x" * 230
    c = _by_id(check_content(doc))["content.bullet_length"]
    assert (c.status, c.severity) == ("fail", "warning")
    assert c.measurement["over_limit"] == [doc["projects"][0]["bullets"][0]["id"]]


def test_banned_opener_experience_and_projects():
    doc = _doc()
    doc["experience"][0]["bullets"][0]["text"] = "Responsible for 18 endpoints."
    c = _by_id(check_content(doc))["content.banned_opener"]
    assert (c.status, c.severity) == ("fail", "warning")
    assert c.measurement["bullets"] == ["exp-001-b01"]

    doc = _doc()
    doc["projects"][0]["bullets"][0]["text"] = "Worked on a note search tool over 2,000 notes."
    c = _by_id(check_content(doc))["content.banned_opener"]
    assert c.status == "fail"
    assert c.measurement["bullets"] == [doc["projects"][0]["bullets"][0]["id"]]
    # the legacy score keeps its experience-only semantics
    assert score_ats(doc)["score"] == 100


def test_banned_opener_on_legacy_string_bullets_uses_positional_labels():
    raw = copy.deepcopy(SYNTHETIC_LEGACY_MASTER)
    raw["projects"][0]["bullets"] = ["Helped with things"]
    c = _by_id(check_content(raw))["content.banned_opener"]
    assert c.measurement["bullets"] == ["projects[0].bullets[0]"]


def test_quantified_ratio_is_info():
    doc = _doc()
    for section in ("experience", "projects"):
        for e in doc[section]:
            for b in e["bullets"]:
                b["text"] = "Implemented unit tests for a billing service."
    c = _by_id(check_content(doc))["content.quantified_ratio"]
    assert (c.status, c.severity) == ("fail", "info") and not c.blocking
    assert c.measurement["ratio"] == 0


def test_no_bullets_is_not_available():
    doc = _doc()
    for section in ("experience", "projects"):
        for e in doc[section]:
            e["bullets"] = []
    checks = _by_id(check_content(doc))
    for cid in ("content.bullet_length", "content.banned_opener", "content.quantified_ratio"):
        assert checks[cid].status == "not_available"


def test_bullets_per_role_covers_projects():
    doc = _doc()
    doc["experience"][0]["bullets"] = doc["experience"][0]["bullets"] * 4  # 8 bullets
    c = _by_id(check_content(doc))["content.bullets_per_role"]
    assert (c.status, c.severity) == ("fail", "warning") and c.measurement == ["exp-001"]
    doc = _doc()
    doc["projects"][0]["bullets"] = []
    c = _by_id(check_content(doc))["content.bullets_per_role"]
    assert c.status == "fail" and c.measurement == [doc["projects"][0]["id"]]


def test_dates_present():
    doc = _doc()
    doc["experience"][1]["end"] = ""
    c = _by_id(check_content(doc))["content.dates_present"]
    assert (c.status, c.severity) == ("fail", "warning") and c.measurement == ["exp-002"]


def test_personal_identifiers_are_errors():
    doc = _doc()
    doc["contact"]["date_of_birth"] = "1990-01-01"
    doc["photo"] = "me.png"
    c = _by_id(check_content(doc))["content.personal_identifiers"]
    assert (c.status, c.severity) == ("fail", "error") and c.blocking
    assert c.measurement == ["contact.date_of_birth", "photo"]


def test_messages_never_quote_resume_text():
    doc = _doc(summary=f"Highly motivated {SENTINEL} team player " + "y" * 600)
    doc["contact"].pop("phone")
    doc["contact"]["dob"] = SENTINEL
    doc["experience"][0]["bullets"][0]["text"] = f"Responsible for {SENTINEL} " + "z" * 230
    doc["projects"][0]["bullets"][0]["text"] = f"Worked on {SENTINEL}"
    doc["experience"][1]["start"] = ""
    checks = check_content(doc)
    assert any(c.status == "fail" for c in checks)
    for c in checks:
        assert SENTINEL not in c.message
        assert SENTINEL not in repr(c.measurement)


def test_summarize():
    doc = _doc()
    doc["contact"]["dob"] = "x"  # error
    doc["projects"][0]["bullets"][0]["text"] = "Worked on it"  # warning
    s = summarize(check_content(doc))
    assert s["blocking"] == ["content.personal_identifiers"]
    assert s["passed"] is False
    assert s["by_status"]["fail"] == 2
    assert s["failed_by_severity"] == {"critical": 0, "error": 1, "warning": 1, "info": 0}
    assert s["total"] == sum(s["by_status"].values()) == sum(s["by_severity"].values())
    assert summarize([])["passed"] is True


# --------------------------------------------------------------------------
# score_ats wrapper: frozen outputs of the pre-refactor implementation.
# --------------------------------------------------------------------------

def _bad_case() -> dict:
    bad = copy.deepcopy(SYNTHETIC_LEGACY_MASTER)
    bad["summary"] = "Highly motivated team player."
    bad["contact"] = {"email": "a@b.test", "dob": "1990", "photo": "x.png"}
    bad["experience"][0]["bullets"] = [{"text": "Responsible for things " + "x" * 230}, "Worked on stuff"]
    bad["experience"][1]["start"] = ""
    bad["experience"].append({"title": "T", "company": "C", "start": "2020", "end": "2021", "bullets": ["b"] * 7})
    bad["experience"].append({"title": "T2", "company": "C", "start": "2019", "end": "2020", "bullets": []})
    bad["projects"][0]["bullets"] = ["Helped with a thing"]
    return bad


def _no_exp_bullets_case() -> dict:
    d = copy.deepcopy(SYNTHETIC_LEGACY_MASTER)
    d["experience"] = [{"title": "X", "start": "a", "end": "b", "bullets": []}]
    d["summary"] = "short"
    return d


OLD_OUTPUTS = {
    "legacy": {"score": 100, "points": 13, "max_points": 13, "issues": []},
    "normalized": {"score": 100, "points": 13, "max_points": 13, "issues": []},
    "bad": {"score": 31, "points": 4, "max_points": 13, "issues": [
        "Missing email or phone in contact info -- ATS and recruiters both need this front and center.",
        "Summary is unusually short or long -- aim for 2-4 sentences (roughly 40-600 characters).",
        "1 bullet(s) are very long (>220 chars) -- break these up, ATS and recruiters both skim.",
        "2 bullet(s) start with a weak phrase like 'Responsible for' -- lead with an action verb and a result instead.",
        "1 experience entr(y/ies) are missing start/end dates -- ATS systems parse employment gaps from these.",
        "Summary contains generic filler (team player, highly motivated) -- replace with a specific role/domain/evidence-based pitch.",
        "Contact info includes unnecessary personal identifiers (photo, dob) -- omit unless a specific employer/country/portal requests them.",
        "Only 0/10 experience bullets contain a number -- add honest scale/outcome metrics where defensible (see resume_etiquette.yaml: bullet_formula.quantify_when_possible).",
        "2 role(s) have 0 or >6 bullets -- aim for 3-6 bullets on recent/relevant roles, fewer on older ones.",
    ]},
    "empty": {"score": 8, "points": 1, "max_points": 13, "issues": [
        "Missing email or phone in contact info -- ATS and recruiters both need this front and center.",
        "No content in the 'summary' section -- ATS parsers look for standard headings like this.",
        "No content in the 'experience' section -- ATS parsers look for standard headings like this.",
        "No content in the 'education' section -- ATS parsers look for standard headings like this.",
        "No content in the 'skills' section -- ATS parsers look for standard headings like this.",
        "No summary found.",
        "No experience bullets found.",
    ]},
    "no_exp_bullets": {"score": 62, "points": 8, "max_points": 13, "issues": [
        "Summary is unusually short or long -- aim for 2-4 sentences (roughly 40-600 characters).",
        "No experience bullets found.",
        "1 role(s) have 0 or >6 bullets -- aim for 3-6 bullets on recent/relevant roles, fewer on older ones.",
    ]},
}


@pytest.mark.parametrize("name,build", [
    ("legacy", lambda: copy.deepcopy(SYNTHETIC_LEGACY_MASTER)),
    ("normalized", lambda: normalize_master(copy.deepcopy(SYNTHETIC_LEGACY_MASTER), "resume")),
    ("bad", _bad_case),
    ("empty", dict),
    ("no_exp_bullets", _no_exp_bullets_case),
])
def test_score_ats_matches_pre_refactor_behavior(name, build):
    result = score_ats(build())
    assert set(result) == {"score", "points", "max_points", "issues"}
    assert result == OLD_OUTPUTS[name]
