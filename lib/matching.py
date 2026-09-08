"""Deterministic gap analysis between a structured resume and JD keywords."""

from .keywords import extract_jd_keywords


def _skills_blob(resume: dict) -> str:
    parts = []
    for skill_group in resume.get("skills", []):
        parts.extend(skill_group.get("items", []))
    return " \n ".join(parts).lower()


def _proof_blob(resume: dict) -> str:
    """Text where a keyword actually 'counts' -- summary, experience bullets,
    and project bullets -- as opposed to just being listed under Skills."""
    parts = [resume.get("summary", "")]
    for exp in resume.get("experience", []):
        parts.append(exp.get("title", ""))
        for b in exp.get("bullets", []):
            parts.append(b.get("text", "") if isinstance(b, dict) else str(b))
    for proj in resume.get("projects", []):
        parts.append(proj.get("name", ""))
        for b in proj.get("bullets", []):
            parts.append(b.get("text", "") if isinstance(b, dict) else str(b))
    for cert in resume.get("certifications", []):
        parts.append(cert if isinstance(cert, str) else str(cert))
    return " \n ".join(p for p in parts if p).lower()


def match_resume_to_jd(resume: dict, jd_text: str) -> dict:
    extracted = extract_jd_keywords(jd_text)
    skills_blob = _skills_blob(resume)
    proof_blob = _proof_blob(resume)

    matched, missing, weak = [], [], []

    all_keywords = list(dict.fromkeys(extracted["must_have"] + extracted["nice_to_have"]))
    for kw in all_keywords:
        has_proof = kw in proof_blob
        listed_in_skills = kw in skills_blob
        if has_proof:
            matched.append(kw)
        elif listed_in_skills:
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
    }
