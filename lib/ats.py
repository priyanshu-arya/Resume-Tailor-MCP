"""Formatting-focused ATS compatibility checks.

This is deliberately separate from keyword matching (see matching.py):
a resume can be 100% keyword-matched and still get mangled by an ATS
parser because of tables, columns, images, or missing standard section
names. This checks the structural stuff.

Rules encoded below (weak openers, generic summary phrases, unnecessary
personal identifiers, quantification, bullet density) come from
resources/resume_etiquette.yaml -- see that file for the full rationale
and sourcing.
"""

import re

REQUIRED_SECTIONS = ["summary", "experience", "education", "skills"]

WEAK_BULLET_OPENERS = ("responsible for", "worked on", "helped with", "assisted with", "duties included", "tasked with")

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

# Fields that etiquette guidance says to omit by default -- see
# header_contact.omit_by_default in resume_etiquette.yaml.
UNNECESSARY_CONTACT_FIELDS = (
    "photo", "date_of_birth", "dob", "age", "marital_status", "religion",
    "national_id", "passport_number", "aadhaar", "pan",
)


def score_ats(resume: dict) -> dict:
    issues = []
    points = 0
    max_points = 0

    # 1. Contact info present
    max_points += 1
    contact = resume.get("contact", {})
    if contact.get("email") and contact.get("phone"):
        points += 1
    else:
        issues.append("Missing email or phone in contact info -- ATS and recruiters both need this front and center.")

    # 2. Standard section presence
    for section in REQUIRED_SECTIONS:
        max_points += 1
        value = resume.get(section)
        if value:
            points += 1
        else:
            issues.append(f"No content in the '{section}' section -- ATS parsers look for standard headings like this.")

    # 3. Summary length (not empty, not a wall of text)
    max_points += 1
    summary = resume.get("summary", "")
    if summary and 40 <= len(summary) <= 600:
        points += 1
    elif summary:
        issues.append("Summary is unusually short or long -- aim for 2-4 sentences (roughly 40-600 characters).")
    else:
        issues.append("No summary found.")

    # 4. Bullet point length (ATS-friendly bullets are usually one line to two lines)
    max_points += 1
    long_bullets = 0
    total_bullets = 0
    for exp in resume.get("experience", []):
        for b in exp.get("bullets", []):
            text = b.get("text", "") if isinstance(b, dict) else str(b)
            total_bullets += 1
            if len(text) > 220:
                long_bullets += 1
    if total_bullets == 0:
        issues.append("No experience bullets found.")
    elif long_bullets == 0:
        points += 1
    else:
        issues.append(f"{long_bullets} bullet(s) are very long (>220 chars) -- break these up, ATS and recruiters both skim.")

    # 5. Bullets should start with an action verb, not "Responsible for" (weak ATS/recruiter signal)
    max_points += 1
    weak_starts = 0
    for exp in resume.get("experience", []):
        for b in exp.get("bullets", []):
            text = (b.get("text", "") if isinstance(b, dict) else str(b)).strip().lower()
            if text.startswith(WEAK_BULLET_OPENERS):
                weak_starts += 1
    if weak_starts == 0 and total_bullets > 0:
        points += 1
    elif weak_starts > 0:
        issues.append(f"{weak_starts} bullet(s) start with a weak phrase like 'Responsible for' -- lead with an action verb and a result instead.")

    # 6. Dates present on experience entries
    max_points += 1
    missing_dates = sum(
        1 for exp in resume.get("experience", []) if not exp.get("start") or not exp.get("end")
    )
    if missing_dates == 0 and resume.get("experience"):
        points += 1
    elif missing_dates:
        issues.append(f"{missing_dates} experience entr(y/ies) are missing start/end dates -- ATS systems parse employment gaps from these.")

    # 7. Summary should read as a specific pitch, not a generic template phrase
    max_points += 1
    lowered_summary = summary.lower()
    hit_phrases = [p for p in GENERIC_SUMMARY_PHRASES if p in lowered_summary]
    if summary and not hit_phrases:
        points += 1
    elif hit_phrases:
        issues.append(f"Summary contains generic filler ({', '.join(hit_phrases)}) -- replace with a specific role/domain/evidence-based pitch.")

    # 8. No unnecessary personal identifiers (photo, DOB, marital status, national ID, ...)
    max_points += 1
    present_unnecessary = [f for f in UNNECESSARY_CONTACT_FIELDS if contact.get(f)]
    if not present_unnecessary:
        points += 1
    else:
        issues.append(f"Contact info includes unnecessary personal identifiers ({', '.join(present_unnecessary)}) -- omit unless a specific employer/country/portal requests them.")

    # 9. Bullets should be quantified where possible (numbers/%/scale signal real evidence)
    max_points += 1
    quantified_bullets = 0
    for exp in resume.get("experience", []):
        for b in exp.get("bullets", []):
            text = b.get("text", "") if isinstance(b, dict) else str(b)
            if re.search(r"\d", text):
                quantified_bullets += 1
    if total_bullets > 0:
        quant_ratio = quantified_bullets / total_bullets
        if quant_ratio >= 0.5:
            points += 1
        else:
            issues.append(f"Only {quantified_bullets}/{total_bullets} experience bullets contain a number -- add honest scale/outcome metrics where defensible (see resume_etiquette.yaml: bullet_formula.quantify_when_possible).")

    # 10. Bullet density per role (too few reads thin, too many buries the strongest evidence)
    max_points += 1
    out_of_range_roles = [
        exp.get("title", "a role") for exp in resume.get("experience", [])
        if not (1 <= len(exp.get("bullets", [])) <= 6)
    ]
    if resume.get("experience") and not out_of_range_roles:
        points += 1
    elif out_of_range_roles:
        issues.append(f"{len(out_of_range_roles)} role(s) have 0 or >6 bullets -- aim for 3-6 bullets on recent/relevant roles, fewer on older ones.")

    score = round(100 * points / max_points) if max_points else 0

    return {
        "score": score,
        "points": points,
        "max_points": max_points,
        "issues": issues,
    }
