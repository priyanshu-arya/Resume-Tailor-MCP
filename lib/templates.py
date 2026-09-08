"""Resume layout templates: metadata, listing, and JD-driven recommendation.

Templates are stored declaratively in data/templates/templates.yaml so new
layouts can be added without touching this logic. Scoring is deterministic
(no LLM call), mirroring the rest of this server's keyword/ATS tools.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from lib.keywords import extract_jd_keywords

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources" / "templates"
TEMPLATES_PATH = RESOURCES_DIR / "templates.yaml"

_SENIOR_TERMS = {"senior", "staff", "principal", "lead", "manager", "director", "vp", "head of", "chief"}
_ENTRY_TERMS = {"junior", "associate"}

DEFAULT_TEMPLATE_ID = "classic-minimalist"


def _load_all() -> list[dict]:
    with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["templates"]


def list_templates() -> list[dict]:
    """Summary view of every available template (id, name, sections, notes)."""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "source_file": t["source_file"],
            "sections": t["layout"]["sections"],
            "best_for": t["best_for"]["notes"].strip(),
        }
        for t in _load_all()
    ]


def get_template(template_id: str) -> dict | None:
    for t in _load_all():
        if t["id"] == template_id:
            return t
    return None


def template_pdf_path(template_id: str) -> Path | None:
    """Path to the original PDF sample a template was modeled on."""
    tmpl = get_template(template_id)
    if tmpl is None:
        return None
    return RESOURCES_DIR / tmpl["source_file"]


def _jd_level(seniority: list[str], years_experience: str | None) -> str:
    if "intern" in seniority:
        return "intern"
    if any(s in _SENIOR_TERMS for s in seniority):
        return "senior"
    if any(s in _ENTRY_TERMS for s in seniority):
        return "entry"
    if years_experience:
        match = re.search(r"\d+", years_experience)
        if match:
            years = int(match.group(0))
            if years >= 5:
                return "senior"
            if years <= 1:
                return "entry"
    return "mid"


def _quantified_bullet_ratio(resume: dict) -> float:
    bullets = []
    for exp in resume.get("experience") or []:
        bullets += [b.get("text", "") if isinstance(b, dict) else str(b) for b in exp.get("bullets") or []]
    if not bullets:
        return 0.0
    hits = sum(1 for b in bullets if re.search(r"\d", b))
    return hits / len(bullets)


_RESUME_SIGNAL_DETECTORS = {
    "has_certifications": lambda r: bool(r.get("certifications")),
    "many_projects": lambda r: len(r.get("projects") or []) >= 2,
    "research_experience": lambda r: any(
        "research" in f"{e.get('title', '')} {e.get('company', '')}".lower()
        for e in (r.get("experience") or [])
    ),
    "quantified_bullets": lambda r: _quantified_bullet_ratio(r) >= 0.4,
    "many_achievements": lambda r: bool(r.get("achievements")),
    "coursework_relevant": lambda r: bool(r.get("coursework")),
}


def _matched_keywords(template_keywords: list[str], jd_extracted: dict, jd_text: str) -> set[str]:
    lowered_jd = jd_text.lower()
    all_jd_terms = set(jd_extracted.get("must_have", [])) | set(jd_extracted.get("nice_to_have", []))
    matched = set()
    for kw in template_keywords:
        kwl = kw.lower()
        if kwl in all_jd_terms or kwl in lowered_jd:
            matched.add(kw)
    return matched


def recommend_template(jd_text: str, resume: dict | None = None) -> dict:
    """Score every template against a JD (and optionally the candidate's
    resume) and return the best match plus the reasoning and full scoreboard."""
    jd_extracted = extract_jd_keywords(jd_text)
    level = _jd_level(jd_extracted["seniority"], jd_extracted["years_experience"])

    scores: dict[str, int] = {}
    reasons_by_id: dict[str, list[str]] = {}

    for t in _load_all():
        best_for = t["best_for"]
        matched_kw = _matched_keywords(best_for.get("keywords", []), jd_extracted, jd_text)
        level_match = level in best_for.get("experience_level", [])
        matched_signals = [
            s for s in best_for.get("resume_signals", [])
            if resume and _RESUME_SIGNAL_DETECTORS.get(s, lambda r: False)(resume)
        ]

        score = 2 * len(matched_kw) + (3 if level_match else 0) + 2 * len(matched_signals)
        scores[t["id"]] = score

        reasons = []
        if matched_kw:
            reasons.append(f"JD keyword overlap: {', '.join(sorted(matched_kw))}")
        if level_match:
            reasons.append(f"JD level '{level}' matches this template's target level ({', '.join(best_for['experience_level'])})")
        for sig in matched_signals:
            reasons.append(f"Resume signal matched: {sig}")
        if not reasons:
            reasons.append("No strong signals matched; scored on general fit only.")
        reasons_by_id[t["id"]] = reasons

    best_id = max(scores, key=lambda k: scores[k])
    if scores[best_id] == 0:
        best_id = "generic-minimal"

    best = get_template(best_id)
    return {
        "recommended_template": best_id,
        "recommended_name": best["name"],
        "reasons": reasons_by_id[best_id],
        "jd_level_detected": level,
        "all_scores": scores,
    }
