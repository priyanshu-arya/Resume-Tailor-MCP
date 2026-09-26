"""Etiquette / ATS content rules as executable checks (spec §32, §40).

This module is the only home of the content rules. `lib.ats.score_ats` is a
thin wrapper over `measure()` below, so a rule changed here changes both the
structured checks and the legacy score.

Pure and deterministic: no LLM, no network, no filesystem writes. The one
read is resources/resume_etiquette.yaml (via lib.safe_yaml) for the
machine-readable weak-opener list.

Messages and measurements name block IDs (or positional labels such as
"experience[1].bullets[0]" for legacy docs without IDs) and counts. They
never quote resume text, which is personal data and ends up in reports and
audit logs.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Iterable

from lib.errors import ResumeTailorError
from lib.rules import ETIQUETTE_PATH
from lib.safe_yaml import load_file
from lib.schemas import SUMMARY_ID, Check

SOURCE = "etiquette"
CATEGORY = "FORMAT"

# --------------------------------------------------------------------------
# Rule constants. Where resume_etiquette.yaml has a machine-readable list it
# is loaded (see weak_openers()); the values below are prose in the YAML, so
# they live here once, each pointing at its YAML section.
# --------------------------------------------------------------------------

# The four headings the legacy ATS score looks for (page_and_format.layout:
# "standard section headings (Summary, Skills, Experience, Education, ...)").
ATS_SECTIONS = ("summary", "experience", "education", "skills")

# Required sections per document kind, with the severity of a missing one.
# resume: document_type (targeted resume); cv: academic_and_research /
# section_order_by_target.academic_faculty -- a CV's shape follows the
# scholarly record, but Education is always present.
REQUIRED_SECTIONS_BY_KIND: dict[str, dict[str, str]] = {
    "resume": {"experience": "error", "education": "error", "skills": "error", "summary": "warning"},
    "cv": {"education": "error"},
}

# summary_formula: "2-4 lines" -- roughly 40-600 characters.
SUMMARY_MIN_CHARS = 40
SUMMARY_MAX_CHARS = 600

# high_impact_mistakes: "Dense paragraphs instead of scannable bullets" --
# one to two rendered lines.
LONG_BULLET_CHARS = 220

# bullet_formula.bullets_per_role: "3-6 for the most recent/relevant roles;
# fewer for older ones" -> 0 or more than 6 is out of range.
BULLETS_PER_ENTRY_MIN = 1
BULLETS_PER_ENTRY_MAX = 6

# bullet_formula.quantify_when_possible: at least half the bullets carry a number.
QUANTIFIED_RATIO_MIN = 0.5
_DIGIT_RE = re.compile(r"\d")

# summary_formula.avoid (prose in the YAML).
GENERIC_SUMMARY_PHRASES = (
    "seeking a challenging position",
    "seeking an opportunity",
    "results-oriented",
    "hard-working",
    "hardworking",
    "team player",
    "excellent communication skills",
    "detail-oriented individual",
    "highly motivated",
    "utilize my skills",
    "think outside the box",
)

# header_contact.omit_by_default (prose in the YAML) as field names.
UNNECESSARY_CONTACT_FIELDS = (
    "photo", "date_of_birth", "dob", "age", "marital_status", "religion",
    "national_id", "passport_number", "aadhaar", "pan",
)

# --------------------------------------------------------------------------
# YAML-backed list
# --------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict[str, Any] = {"mtime": None, "openers": None}


def weak_openers() -> tuple[str, ...]:
    """bullet_formula.weak_openers_to_avoid from resume_etiquette.yaml
    (cached, re-read when the file changes). Fails closed."""
    try:
        mtime = ETIQUETTE_PATH.stat().st_mtime_ns
    except OSError:
        raise ResumeTailorError("RULES_UNAVAILABLE", "The resume rules file is missing.") from None
    with _lock:
        if _cache["openers"] is None or _cache["mtime"] != mtime:
            data = load_file(ETIQUETTE_PATH)
            raw = ((data or {}).get("bullet_formula") or {}).get("weak_openers_to_avoid") if isinstance(data, dict) else None
            if not isinstance(raw, list) or not raw:
                raise ResumeTailorError("RULES_UNAVAILABLE",
                                        "The resume rules file has no bullet_formula.weak_openers_to_avoid list.")
            _cache["openers"] = tuple(str(o).strip().lower() for o in raw if str(o).strip())
            _cache["mtime"] = mtime
        return _cache["openers"]


# --------------------------------------------------------------------------
# Measurement (shared by check_content and lib.ats.score_ats)
# --------------------------------------------------------------------------

def _text(b) -> str:
    return (b.get("text", "") if isinstance(b, dict) else str(b)) or ""


def _entry_label(section: str, i: int, entry) -> str:
    return (entry.get("id") if isinstance(entry, dict) else None) or f"{section}[{i}]"


def _bullet_label(section: str, i: int, entry, j: int, b) -> str:
    bid = b.get("id") if isinstance(b, dict) else None
    return bid or f"{_entry_label(section, i, entry)}.bullets[{j}]"


def _entries(doc: dict, section: str) -> list:
    value = doc.get(section) or []
    return [e for e in value if isinstance(e, dict)] if isinstance(value, list) else []


def document_kind(doc: dict) -> str | None:
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    return meta.get("document_kind") or meta.get("kind")


def required_sections(kind: str | None) -> dict[str, str]:
    """{section: severity-if-missing} for a document kind (default resume)."""
    return REQUIRED_SECTIONS_BY_KIND.get(kind or "resume", REQUIRED_SECTIONS_BY_KIND["resume"])


def measure(doc: dict, bullet_sections: Iterable[str] = ("experience", "projects")) -> dict:
    """Raw, text-free measurements of a resume body. `bullet_sections` picks
    which sections' bullets and entries the bullet rules look at."""
    doc = doc if isinstance(doc, dict) else {}
    contact = doc.get("contact") if isinstance(doc.get("contact"), dict) else {}
    summary = doc.get("summary") or ""
    summary = summary if isinstance(summary, str) else str(summary)
    lowered = summary.lower()
    openers = weak_openers()

    bullets: list[str] = []
    long_, weak, quantified = [], [], []
    entries_out_of_range: list[str] = []
    for section in bullet_sections:
        for i, entry in enumerate(_entries(doc, section)):
            entry_bullets = entry.get("bullets") or []
            if not (BULLETS_PER_ENTRY_MIN <= len(entry_bullets) <= BULLETS_PER_ENTRY_MAX):
                entries_out_of_range.append(_entry_label(section, i, entry))
            for j, b in enumerate(entry_bullets):
                label = _bullet_label(section, i, entry, j, b)
                text = _text(b)
                bullets.append(label)
                if len(text) > LONG_BULLET_CHARS:
                    long_.append(label)
                if text.strip().lower().startswith(openers):
                    weak.append(label)
                if _DIGIT_RE.search(text):
                    quantified.append(label)

    experience = _entries(doc, "experience")
    return {
        "has_email": bool(contact.get("email")),
        "has_phone": bool(contact.get("phone")),
        "sections_present": {s: bool(doc.get(s)) for s in
                             ("summary", "experience", "education", "skills", "projects", "certifications")},
        "summary_chars": len(summary),
        "summary_in_range": SUMMARY_MIN_CHARS <= len(summary) <= SUMMARY_MAX_CHARS,
        "generic_phrases": [p for p in GENERIC_SUMMARY_PHRASES if p in lowered],
        "bullets": bullets,
        "long_bullets": long_,
        "weak_opener_bullets": weak,
        "quantified_bullets": quantified,
        "entries_out_of_range": entries_out_of_range,
        "has_experience": bool(doc.get("experience")),
        "entries_missing_dates": [_entry_label("experience", i, e) for i, e in enumerate(experience)
                                  if not e.get("start") or not e.get("end")],
        "identifiers_in_contact": [f for f in UNNECESSARY_CONTACT_FIELDS if contact.get(f)],
        "identifiers_top_level": [f for f in UNNECESSARY_CONTACT_FIELDS if doc.get(f)],
    }


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _check(id_, status, severity, measurement=None, expected=None, message="") -> Check:
    return Check(id=id_, status=status, severity=severity, category=CATEGORY,
                 measurement=measurement, expected=expected, source=SOURCE, message=message)


def _ids(labels: list[str], limit: int = 10) -> str:
    shown = ", ".join(labels[:limit])
    return shown + (f" (+{len(labels) - limit} more)" if len(labels) > limit else "")


def check_content(doc: dict) -> list[Check]:
    """Etiquette/ATS content checks over experience AND project bullets."""
    m = measure(doc, ("experience", "projects"))
    out: list[Check] = []

    # contact
    if m["has_email"] and m["has_phone"]:
        out.append(_check("content.contact_email_phone", "pass", "error",
                          {"email": True, "phone": True}, {"email": True, "phone": True}))
    else:
        missing = [k for k in ("email", "phone") if not m[f"has_{k}"]]
        out.append(_check("content.contact_email_phone", "fail", "error" if not m["has_email"] else "warning",
                          {"email": m["has_email"], "phone": m["has_phone"]}, {"email": True, "phone": True},
                          f"Contact is missing: {', '.join(missing)}."))

    # required sections
    required = required_sections(document_kind(doc))
    missing = [s for s in required if not m["sections_present"].get(s)]
    if not missing:
        out.append(_check("content.required_sections", "pass", "error", [], sorted(required)))
    else:
        sev = "error" if any(required[s] == "error" for s in missing) else "warning"
        out.append(_check("content.required_sections", "fail", sev, missing, sorted(required),
                          f"Empty required section(s): {', '.join(missing)}."))

    # summary length
    expected_len = {"min_chars": SUMMARY_MIN_CHARS, "max_chars": SUMMARY_MAX_CHARS}
    if not m["summary_chars"]:
        out.append(_check("content.summary_length", "not_available", "warning", 0, expected_len,
                          "No summary to measure."))
    elif m["summary_in_range"]:
        out.append(_check("content.summary_length", "pass", "warning", m["summary_chars"], expected_len))
    else:
        out.append(_check("content.summary_length", "fail", "warning", m["summary_chars"], expected_len,
                          f"Summary ({SUMMARY_ID}) is {m['summary_chars']} characters; aim for "
                          f"{SUMMARY_MIN_CHARS}-{SUMMARY_MAX_CHARS}."))

    # generic summary phrases
    if not m["summary_chars"]:
        out.append(_check("content.generic_summary", "not_available", "warning", None, [],
                          "No summary to check."))
    elif not m["generic_phrases"]:
        out.append(_check("content.generic_summary", "pass", "warning", [], []))
    else:
        out.append(_check("content.generic_summary", "fail", "warning", m["generic_phrases"], [],
                          f"Summary ({SUMMARY_ID}) uses {len(m['generic_phrases'])} generic phrase(s) from the "
                          "etiquette avoid-list; replace with a specific, evidence-based pitch."))

    # bullet rules (experience + projects)
    total = len(m["bullets"])
    if total == 0:
        for cid in ("content.bullet_length", "content.banned_opener"):
            out.append(_check(cid, "not_available", "warning", 0, None, "No experience or project bullets."))
        out.append(_check("content.quantified_ratio", "not_available", "info", None, QUANTIFIED_RATIO_MIN,
                          "No experience or project bullets."))
    else:
        long_ = m["long_bullets"]
        out.append(_check("content.bullet_length", "fail" if long_ else "pass", "warning",
                          {"over_limit": long_, "total": total}, {"max_chars": LONG_BULLET_CHARS},
                          f"{len(long_)} bullet(s) over {LONG_BULLET_CHARS} characters: {_ids(long_)}." if long_ else ""))
        weak = m["weak_opener_bullets"]
        out.append(_check("content.banned_opener", "fail" if weak else "pass", "warning",
                          {"bullets": weak, "total": total}, {"banned_openers": list(weak_openers())},
                          f"{len(weak)} bullet(s) open with a banned weak phrase: {_ids(weak)}." if weak else ""))
        ratio = round(len(m["quantified_bullets"]) / total, 3)
        ok = ratio >= QUANTIFIED_RATIO_MIN
        out.append(_check("content.quantified_ratio", "pass" if ok else "fail", "info",
                          {"ratio": ratio, "quantified": len(m["quantified_bullets"]), "total": total},
                          {"min_ratio": QUANTIFIED_RATIO_MIN},
                          "" if ok else f"{len(m['quantified_bullets'])}/{total} bullets contain a number; add honest "
                                        "scale/outcome metrics where defensible."))

    # bullets per role / project
    rng = {"min": BULLETS_PER_ENTRY_MIN, "max": BULLETS_PER_ENTRY_MAX}
    if not (m["sections_present"]["experience"] or m["sections_present"]["projects"]):
        out.append(_check("content.bullets_per_role", "not_available", "warning", None, rng,
                          "No experience or project entries."))
    else:
        bad = m["entries_out_of_range"]
        out.append(_check("content.bullets_per_role", "fail" if bad else "pass", "warning", bad, rng,
                          f"{len(bad)} entr(y/ies) with 0 or more than {BULLETS_PER_ENTRY_MAX} bullets: {_ids(bad)}."
                          if bad else ""))

    # dates on experience entries
    if not m["has_experience"]:
        out.append(_check("content.dates_present", "not_available", "warning", None, None,
                          "No experience entries."))
    else:
        bad = m["entries_missing_dates"]
        out.append(_check("content.dates_present", "fail" if bad else "pass", "warning", bad, [],
                          f"Experience entr(y/ies) missing start/end dates: {_ids(bad)}." if bad else ""))

    # personal identifiers
    found = [f"contact.{f}" for f in m["identifiers_in_contact"]] + m["identifiers_top_level"]
    out.append(_check("content.personal_identifiers", "fail" if found else "pass", "error", found, [],
                      f"Personal identifier field(s) present: {', '.join(found)}; omit unless the employer, "
                      "country or portal explicitly requires them." if found else ""))
    return out


# --------------------------------------------------------------------------
# Report summary
# --------------------------------------------------------------------------

def _get(c, key):
    return getattr(c, key) if isinstance(c, Check) else (c or {}).get(key)


def summarize(checks: Iterable) -> dict:
    """Counts by status and severity plus the blocking check IDs. A check
    blocks when it failed with severity critical or error (spec §40)."""
    by_status = {s: 0 for s in ("pass", "fail", "warning", "not_available")}
    by_severity = {s: 0 for s in ("critical", "error", "warning", "info")}
    failed_by_severity = dict(by_severity)
    blocking: list[str] = []
    total = 0
    for c in checks:
        total += 1
        status, severity = _get(c, "status"), _get(c, "severity")
        by_status[status] = by_status.get(status, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        if status == "fail":
            failed_by_severity[severity] = failed_by_severity.get(severity, 0) + 1
            if severity in ("critical", "error"):
                blocking.append(_get(c, "id"))
    return {"total": total, "by_status": by_status, "by_severity": by_severity,
            "failed_by_severity": failed_by_severity, "blocking": blocking, "passed": not blocking}
