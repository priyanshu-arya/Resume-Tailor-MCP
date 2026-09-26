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


def _listed_in_skills(kw: str, items: list[str], skills_text: str) -> bool:
    # A skill item that *is* the term (any alias/case, e.g. "golang", "K8s")
    # counts; otherwise fall back to a boundary match inside the item text.
    return any(normalize_term(i) == kw for i in items) or term_in_text(kw, skills_text)


def match_resume_to_jd(resume: dict, jd_text: str) -> dict:
    extracted = extract_jd_keywords(jd_text)
    skill_items = _skill_items(resume)
    skills_text = " \n ".join(skill_items)
    proof_text = _proof_text(resume)

    matched, missing, weak = [], [], []

    all_keywords = list(dict.fromkeys(extracted["must_have"] + extracted["nice_to_have"]))
    for kw in all_keywords:
        if term_in_text(kw, proof_text):
            matched.append(kw)
        elif _listed_in_skills(kw, skill_items, skills_text):
            # only in the Skills list, never backed up by a bullet -- ATS
            # keyword scanners will count it, but a human reviewer may not
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
