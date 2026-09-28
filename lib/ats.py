"""Formatting-focused ATS compatibility score (legacy shape).

This is deliberately separate from keyword matching (see matching.py): a
resume can be 100% keyword-matched and still get mangled by an ATS parser
because of missing standard sections, dense bullets or missing dates.

The rules themselves live once, in lib/validators/content.py (which cites
resources/resume_etiquette.yaml). `score_ats` is a thin wrapper that turns
those measurements into the historical {score, points, max_points, issues}
shape. For backward compatibility its bullet rules look at experience
bullets only; the structured checks in `check_content` also cover projects.
"""

from lib.validators import content
from lib.validators.content import (  # re-exported for backward compatibility
    ATS_SECTIONS as REQUIRED_SECTIONS,
    GENERIC_SUMMARY_PHRASES,
    UNNECESSARY_CONTACT_FIELDS,
)

__all__ = ["score_ats", "REQUIRED_SECTIONS", "GENERIC_SUMMARY_PHRASES", "UNNECESSARY_CONTACT_FIELDS"]


def score_ats(resume: dict) -> dict:
    m = content.measure(resume, bullet_sections=("experience",))
    issues: list[str] = []
    points = 0
    max_points = 0

    def rule(ok: bool, issue: str | None = None) -> None:
        nonlocal points, max_points
        max_points += 1
        if ok:
            points += 1
        elif issue:
            issues.append(issue)

    # 1. Contact info present
    rule(m["has_email"] and m["has_phone"],
         "Missing email or phone in contact info -- ATS and recruiters both need this front and center.")

    # 2. Standard section presence
    for section in REQUIRED_SECTIONS:
        rule(m["sections_present"][section],
             f"No content in the '{section}' section -- ATS parsers look for standard headings like this.")

    # 3. Summary length (not empty, not a wall of text)
    has_summary = m["summary_chars"] > 0
    rule(has_summary and m["summary_in_range"],
         (f"Summary is unusually short or long -- aim for 2-4 sentences (roughly "
          f"{content.SUMMARY_MIN_CHARS}-{content.SUMMARY_MAX_CHARS} characters).")
         if has_summary else "No summary found.")

    # 4. Bullet length
    total = len(m["bullets"])
    n_long = len(m["long_bullets"])
    rule(total > 0 and n_long == 0,
         "No experience bullets found." if total == 0 else
         f"{n_long} bullet(s) are very long (>{content.LONG_BULLET_CHARS} chars) -- break these up, "
         "ATS and recruiters both skim.")

    # 5. Action-verb openers
    n_weak = len(m["weak_opener_bullets"])
    rule(n_weak == 0 and total > 0,
         f"{n_weak} bullet(s) start with a weak phrase like 'Responsible for' -- lead with an action verb "
         "and a result instead." if n_weak else None)

    # 6. Dates present on experience entries
    n_missing = len(m["entries_missing_dates"])
    rule(n_missing == 0 and m["has_experience"],
         f"{n_missing} experience entr(y/ies) are missing start/end dates -- ATS systems parse employment "
         "gaps from these." if n_missing else None)

    # 7. Specific (non-generic) summary
    hits = m["generic_phrases"]
    rule(has_summary and not hits,
         f"Summary contains generic filler ({', '.join(hits)}) -- replace with a specific "
         "role/domain/evidence-based pitch." if hits else None)

    # 8. No unnecessary personal identifiers in contact
    present = m["identifiers_in_contact"]
    rule(not present,
         f"Contact info includes unnecessary personal identifiers ({', '.join(present)}) -- omit unless a "
         "specific employer/country/portal requests them.")

    # 9. Quantified bullets
    n_quant = len(m["quantified_bullets"])
    rule(total > 0 and n_quant / total >= content.QUANTIFIED_RATIO_MIN,
         f"Only {n_quant}/{total} experience bullets contain a number -- add honest scale/outcome metrics "
         "where defensible (see resume_etiquette.yaml: bullet_formula.quantify_when_possible)."
         if total > 0 else None)

    # 10. Bullet density per role
    n_out = len(m["entries_out_of_range"])
    rule(m["has_experience"] and n_out == 0,
         f"{n_out} role(s) have 0 or >{content.BULLETS_PER_ENTRY_MAX} bullets -- aim for 3-6 bullets on "
         "recent/relevant roles, fewer on older ones." if n_out else None)

    score = round(100 * points / max_points) if max_points else 0
    return {"score": score, "points": points, "max_points": max_points, "issues": issues}
