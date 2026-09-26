"""Resume layout templates: a hard whitelist, contracts, rendering, and
JD-driven recommendation (spec §26-30).

Templates are registered declaratively in resources/templates/templates.yaml;
only ids registered there exist. Template ids are looked up by exact string
match against that registry -- a requested id is never used to build a
filesystem path. Exactly one template (`classic-minimalist`) has a
production renderer; the rest are `experimental` metadata.

No silent fallback: `render_template(B)` either renders B or raises
(TEMPLATE_UNKNOWN / TEMPLATE_NO_RENDERER) -- it never renders another
template. An explicit request for an experimental template resolves as-is
(`releasable=False`) so the release gate can refuse it visibly.

Scoring is deterministic (no LLM call), mirroring the rest of this server's
keyword/ATS tools.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from lib import latex as _latex
from lib import safe_yaml
from lib.errors import ResumeTailorError
from lib.keywords import extract_jd_keywords
from lib.schemas import TemplateContract, TemplateStatus

RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources" / "templates"
TEMPLATES_PATH = RESOURCES_DIR / "templates.yaml"

_SENIOR_TERMS = {"senior", "staff", "principal", "lead", "manager", "director", "vp", "head of", "chief"}
_ENTRY_TERMS = {"junior", "associate"}

DEFAULT_TEMPLATE_ID = "classic-minimalist"


# The ONLY production renderers. A template id missing from this map has no
# renderer, whatever its metadata says.
RENDERERS: dict[str, Callable[[dict], str]] = {
    "classic-minimalist": lambda resume: _latex.render_latex(resume, "classic-minimalist"),
}


def _load_all() -> list[dict]:
    data = safe_yaml.load_file(TEMPLATES_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("templates"), list):
        raise ResumeTailorError("TEMPLATE_UNKNOWN", "Template registry is missing or malformed.")
    return data["templates"]


def registered_ids() -> list[str]:
    return [t["id"] for t in _load_all()]


def _unknown(template_id) -> ResumeTailorError:
    return ResumeTailorError(
        "TEMPLATE_UNKNOWN",
        f"Unknown template {template_id!r}. Only registered templates can be used.",
        details={"requested": str(template_id)[:200], "registered": registered_ids()},
    )


def _lookup(template_id) -> dict:
    """Exact-match lookup in the registry; never touches the filesystem with
    the requested id."""
    if isinstance(template_id, str):
        for t in _load_all():
            if t["id"] == template_id:
                return t
    raise _unknown(template_id)


def list_templates() -> list[dict]:
    """Summary view of every available template (id, name, sections, notes)."""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "source_file": t["source_file"],
            "status": t.get("status", TemplateStatus.experimental.value),
            "version": t.get("version"),
            "has_renderer": t["id"] in RENDERERS,
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


def get_contract(template_id: str) -> dict:
    """The template's §29 contract, validated through TemplateContract."""
    tmpl = _lookup(template_id)
    return TemplateContract.model_validate(tmpl).model_dump(mode="json")


def resolve_template(template_id: str) -> dict:
    """Resolve a registered id to its status. Raises TEMPLATE_UNKNOWN for
    anything not in the registry (including path-like strings)."""
    tmpl = _lookup(template_id)
    status = tmpl.get("status", TemplateStatus.experimental.value)
    has_renderer = tmpl["id"] in RENDERERS
    return {
        "id": tmpl["id"],
        "status": status,
        "version": str(tmpl.get("version")),
        "has_renderer": has_renderer,
        "releasable": status == TemplateStatus.supported.value and has_renderer,
    }


def is_releasable(template_id: str) -> bool:
    try:
        return resolve_template(template_id)["releasable"]
    except ResumeTailorError:
        return False


def render_template(template_id: str, resume: dict) -> str:
    """Render `resume` with exactly `template_id`. Never falls back to a
    different template: unknown -> TEMPLATE_UNKNOWN, no renderer ->
    TEMPLATE_NO_RENDERER."""
    resolved = resolve_template(template_id)
    renderer = RENDERERS.get(resolved["id"])
    if renderer is None:
        raise ResumeTailorError(
            "TEMPLATE_NO_RENDERER",
            f"Template {resolved['id']!r} ({resolved['status']}) has no renderer; it cannot be rendered. "
            "Choose a supported template instead.",
            details={"template_id": resolved["id"], "status": resolved["status"],
                     "renderable": sorted(RENDERERS)},
        )
    return renderer(resume)


def resolve_for_tailoring(template: str | None, master: dict | None, jd_text: str | None) -> tuple[str, str]:
    """Pick the template id + version recorded on a tailored version.

    "auto"/""/None -> recommend among releasable (supported + rendered)
    templates only. An explicit id must be registered (TEMPLATE_UNKNOWN
    otherwise) and is returned as-is even when it is experimental or has no
    renderer -- the release gate blocks it visibly; it is never swapped.
    """
    if template is None or (isinstance(template, str) and template.strip() in ("", "auto")):
        if jd_text:
            template_id = recommend_template(jd_text, master, supported_only=True)["recommended_template"]
        else:
            template_id = DEFAULT_TEMPLATE_ID
    else:
        template_id = template
    resolved = resolve_template(template_id)
    return resolved["id"], resolved["version"]


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


def recommend_template(jd_text: str, resume: dict | None = None, supported_only: bool = False) -> dict:
    """Score every template against a JD (and optionally the candidate's
    resume) and return the best match plus the reasoning and full scoreboard.

    With `supported_only`, only releasable templates (status `supported`
    and a registered renderer) are candidates, and a zero-score tie goes to
    DEFAULT_TEMPLATE_ID."""
    candidates = _load_all()
    if supported_only:
        candidates = [t for t in candidates if is_releasable(t["id"])]
        if not candidates:
            raise ResumeTailorError("TEMPLATE_NOT_RELEASABLE", "No supported template is available.")
    jd_extracted = extract_jd_keywords(jd_text)
    level = _jd_level(jd_extracted["seniority"], jd_extracted["years_experience"])

    scores: dict[str, int] = {}
    reasons_by_id: dict[str, list[str]] = {}

    for t in candidates:
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
        if supported_only:
            best_id = DEFAULT_TEMPLATE_ID if DEFAULT_TEMPLATE_ID in scores else best_id
        else:
            best_id = "generic-minimal"

    best = get_template(best_id)
    return {
        "recommended_template": best_id,
        "recommended_name": best["name"],
        "reasons": reasons_by_id[best_id],
        "jd_level_detected": level,
        "all_scores": scores,
        "supported_only": supported_only,
        "status": best.get("status"),
        "releasable": is_releasable(best_id),
    }
