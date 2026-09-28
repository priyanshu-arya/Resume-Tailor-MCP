"""Deterministic gap analysis between a structured resume and JD keywords.

Uses `term_in_text` (token-boundary, alias-aware, case rules for ambiguous
terms) -- never raw substring checks, so `go` doesn't match "Google" and
`git` doesn't match "digital" (spec §33).
"""

from .ids import cert_text, skill_names
from .keywords import extract_jd_keywords, normalize_term, term_in_text


def _skill_items(resume: dict) -> list[str]:
    parts = []
    for skill_group in resume.get("skills", []) or []:
        parts.extend(str(n) for n in skill_names(skill_group) if n)
    return parts


def _proof_text(resume: dict) -> str:
    """Text where a keyword actually 'counts' -- summary, experience bullets,
    and project bullets -- as opposed to just being listed under Skills.
    Case is preserved: ambiguous terms ("Go", "R", "REST") need it."""
    parts = [resume.get("summary", "") or ""]
    for exp in resume.get("experience", []) or []:
        parts.append(exp.get("title", "") or "")
        for b in exp.get("bullets", []) or []:
            parts.append(b.get("text", "") if isinstance(b, dict) else str(b))
    for proj in resume.get("projects", []) or []:
        parts.append(proj.get("name", "") or "")
        for b in proj.get("bullets", []) or []:
            parts.append(b.get("text", "") if isinstance(b, dict) else str(b))
    for cert in resume.get("certifications", []) or []:
        parts.append(cert_text(cert))
    return " \n ".join(p for p in parts if p)


def _mentioned_text(resume: dict) -> tuple[list[str], str]:
    """Terms that are merely *listed* (skill items + project stacks), as
    opposed to proven by a bullet. A term appearing only in a project's
    `stack` field used to be reported `missing` here, same as if it were
    absent entirely -- it is now treated the same as a Skills-only mention:
    `weak`, not `missing`."""
    parts = list(_skill_items(resume))
    for proj in resume.get("projects", []) or []:
        stack = proj.get("stack")
        if stack:
            parts.append(str(stack))
    return parts, " \n ".join(parts)


def _listed_in_skills(kw: str, items: list[str], mentioned_text: str) -> bool:
    # A skill/stack item that *is* the term (any alias/case, e.g. "golang",
    # "K8s") counts; otherwise fall back to a boundary match inside the text.
    return any(normalize_term(i) == kw for i in items) or term_in_text(kw, mentioned_text)


REQUIREMENT_STATUSES = ("supported", "weak", "missing")


def classify_term(term: str, resume: dict) -> str:
    """'supported' | 'weak' | 'missing' for ANY string (not just a term the
    JD extractor recognized), using the exact same proof-text /
    mentioned-text split as match_resume_to_jd. Used to re-check a JD term
    that the unknown-requirements heuristic flagged, against the master --
    a token the master already proves should never be reported `unknown`."""
    proof_text = _proof_text(resume)
    if term_in_text(term, proof_text):
        return "supported"
    items, mentioned_text = _mentioned_text(resume)
    if _listed_in_skills(term, items, mentioned_text):
        return "weak"
    return "missing"


_STATUS_REASON = {
    "supported": "appears in the summary or an experience/project bullet",
    "weak": "only listed under Skills or a project's stack, not proven by a bullet",
    "missing": "not found anywhere in the master",
}


def requirement_view(result: dict, resume: dict) -> list[dict]:
    """One row per JD requirement (must-have, nice-to-have, and the
    unknown-requirements axis), priority-ordered and deduplicated:
    {term, term_display, status, importance, requirement_type, reason}.
    `status` is one of REQUIREMENT_STATUSES + "unknown" (unknown-axis terms
    the master does not prove stay `unknown`; ones it does prove come back
    `supported`/`weak` like any other term -- never reported unknown once
    the master itself demonstrates them). `importance` is one of
    ("must_have", "nice_to_have", "unrecognized")."""
    from lib.keywords import term_type

    must_have_set = set(result.get("must_have", []))
    rows: list[dict] = []
    seen: set[str] = set()

    def add(term: str, status: str, importance: str, reason: str) -> None:
        key = normalize_term(term)
        if key in seen:
            return
        seen.add(key)
        rows.append({"term": term, "term_display": term, "status": status, "importance": importance,
                    "requirement_type": term_type(term), "reason": reason})

    for kw in result.get("matched", []):
        importance = "must_have" if kw in must_have_set else "nice_to_have"
        add(kw, "supported", importance, _STATUS_REASON["supported"])
    for kw in result.get("weak", []):
        importance = "must_have" if kw in must_have_set else "nice_to_have"
        add(kw, "weak", importance, _STATUS_REASON["weak"])
    for kw in result.get("missing", []):
        importance = "must_have" if kw in must_have_set else "nice_to_have"
        add(kw, "missing", importance, _STATUS_REASON["missing"])
    for term in result.get("unknown_requirements", []) or []:
        status = classify_term(term, resume)
        if status == "supported":
            reason = "not in the known-skills vocabulary, but " + _STATUS_REASON["supported"]
        elif status == "weak":
            reason = "not in the known-skills vocabulary, but " + _STATUS_REASON["weak"]
        else:
            reason = "not in the known-skills vocabulary and not found in the master"
            status = "unknown"
        add(term, status, "unrecognized", reason)

    priority = {"must_have": 0, "nice_to_have": 1, "unrecognized": 2}
    rows.sort(key=lambda r: priority.get(r["importance"], 3))
    return rows


def match_resume_to_jd(resume: dict, jd_text: str) -> dict:
    extracted = extract_jd_keywords(jd_text)
    mentioned_items, mentioned_text = _mentioned_text(resume)
    proof_text = _proof_text(resume)

    matched, missing, weak = [], [], []

    all_keywords = list(dict.fromkeys(extracted["must_have"] + extracted["nice_to_have"]))
    for kw in all_keywords:
        if term_in_text(kw, proof_text):
            matched.append(kw)
        elif _listed_in_skills(kw, mentioned_items, mentioned_text):
            # only listed (Skills or a project's stack), never backed up by
            # a bullet -- ATS keyword scanners will count it, a human
            # reviewer may not
            weak.append(kw)
        else:
            missing.append(kw)

    total = len(all_keywords) or 1
    score = round(100 * len(matched) / total)
    ats_visible_score = round(100 * (len(matched) + len(weak)) / total)

    return {
        "jd_title": extracted["title"],
        "jd_seniority": extracted["seniority"],
        "jd_years_experience": extracted["years_experience"],
        "must_have": extracted["must_have"],
        "nice_to_have": extracted["nice_to_have"],
        "matched": matched,
        "missing": missing,
        "weak": weak,
        "score": score,
        "ats_visible_score": ats_visible_score,
        "unknown_requirements": extracted["unknown_requirements"],
    }
