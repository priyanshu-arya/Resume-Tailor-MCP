"""Claim-strength, placement, technology, metric and verb rules (spec §16, §24).

Plugged into `lib.patches.validate_and_apply` as its `provenance_hook`.
`patches.py` has already checked that every source ref exists, is citable,
is confirmed and belongs to this workflow; this module decides whether the
cited sources actually *support* the new wording in the target section.

Principle: Claude may optimize wording, Python decides what is true. Every
rule is deterministic and fails closed. The tables (placement matrix, verb
families, familiarity markers) live in resources/resume_etiquette.yaml and
are read through `lib.rules`.

Messages never quote patch text (it may be personal data and is returned to
the MCP client / written to audit logs): they name rule IDs, canonical
technology terms, verb families, ref IDs and categories only.
"""

from __future__ import annotations

import re
from typing import Callable

from lib import rules
from lib.keywords import detect_terms, normalize_term, term_in_text
from lib.schemas import CLAIM_RANK, MASTER_SKILL_CATEGORY, claim_rank

# Sections a skills/education target is allowed to take low-grade evidence into.
_LOW_GRADE_ONLY = frozenset({"learning_only", "coursework"})
_LOW_GRADE_SECTIONS = frozenset({"skills", "education"})

# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

# A number not glued to a preceding word ("EC2", "S3", "OAuth2" are names,
# not metrics) nor to a letter-hyphen ("GPT-4"). Thousands separators,
# decimals, an optional currency sign and a %, x/×, or k/m/b suffix.
_METRIC_RE = re.compile(
    r"(?<![\w.,])(?<![A-Za-z]-)"
    r"(?P<cur>[$₹€])?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<suf>\s?%|[x×kmb](?![a-z]))?",
    re.IGNORECASE,
)


def _norm_number(num: str) -> str:
    num = num.replace(",", "")
    if "." in num:
        num = num.rstrip("0").rstrip(".") or "0"
    return num.lstrip("0") or "0"


def extract_metrics(text: str) -> list[tuple[str, str, str]]:
    """Numeric tokens as normalized (currency, number, suffix) triples."""
    out = []
    for m in _METRIC_RE.finditer(text or ""):
        suffix = (m.group("suf") or "").strip().lower().replace("×", "x")
        out.append((m.group("cur") or "", _norm_number(m.group("num")), suffix))
    return out


def _metric_supported(metric: tuple[str, str, str], pool: set[tuple[str, str, str]]) -> bool:
    cur, num, suf = metric
    for p_cur, p_num, p_suf in pool:
        # A currency sign may be dropped in the new text, never added/changed.
        if num == p_num and suf == p_suf and (cur == p_cur or not cur):
            return True
    return False


# --------------------------------------------------------------------------
# Verbs
# --------------------------------------------------------------------------

def _word_re(forms: set[str]) -> re.Pattern:
    alternatives = "|".join(sorted((re.escape(f) for f in forms), key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z])(?:{alternatives})(?![A-Za-z])", re.IGNORECASE)


def _families_in(text: str, families: list[tuple[str, re.Pattern]]) -> list[str]:
    return [label for label, pattern in families if pattern.search(text or "")]


def _compiled_families() -> list[tuple[str, re.Pattern]]:
    return [(_family_label(f), _word_re(f)) for f in rules.high_scope_families()]


def _family_label(family: set[str]) -> str:
    # Prefer the past-tense form (what a bullet usually uses) as the label.
    past = sorted(f for f in family if f.endswith("ed") or f in ("led", "drove", "oversaw"))
    return past[0] if past else sorted(family)[0]


# --------------------------------------------------------------------------
# The hook
# --------------------------------------------------------------------------

def _rej(rule: str, message: str) -> dict:
    return {"rule": f"provenance.{rule}", "message": message}


def _is_familiarity_group(category: str | None) -> bool:
    name = (category or "").casefold()
    return any(marker in name for marker in rules.familiarity_markers())


def _placement(category: str | None, section: str) -> str:
    if category == "none":
        return "no"
    return rules.placement(category, section) if isinstance(category, str) else "no"


def _rank(category) -> int:
    try:
        return claim_rank(category)
    except (KeyError, TypeError):
        return 0  # unknown category supports nothing


def _ref_label(ref: dict) -> str:
    return f"{ref.get('type')} ref {str(ref.get('id'))[:80]} ({ref.get('category')})"


def check(ctx: dict) -> list[dict]:
    """Apply every claim rule to one content patch; returns rejections."""
    section = ctx.get("target_section") or ""
    refs: list[dict] = ctx.get("ref_infos") or []
    text: str = ctx.get("text") or ""
    is_skill = ctx.get("target_type") == "skill_item"
    problems: list[dict] = []

    if not refs:  # patches.py rejects this first; never pass it silently
        return [_rej("missing_refs", "New content must cite at least one source ref.")]

    # 1. placement: every cited source must be allowed in the target section.
    placements = {}
    for ref in refs:
        placements[ref["id"]] = p = _placement(ref.get("category"), section)
        if p == "no":
            problems.append(_rej("placement", f"{_ref_label(ref)} cannot support content in {section}."))

    # 1b. an internship stays an internship: internship sources never appear
    # under a full-time role, and a bullet under an internship role never
    # claims full-time professional strength.
    parent = ctx.get("parent_category")
    if section == "experience" and parent == "professional":
        interns = [r for r in refs if r.get("category") == "internship"]
        if interns:
            problems.append(_rej("internship_as_professional",
                                 f"{', '.join(_ref_label(r) for r in interns)} is internship evidence and cannot "
                                 "appear under a full-time role."))
    if section == "experience" and parent == "internship" and ctx.get("claim_strength") == "professional":
        problems.append(_rej("internship_as_professional",
                             "A bullet under an internship role cannot claim professional strength."))

    # 7. no silent expansion from learning/coursework into real-work sections.
    if all(ref.get("category") in _LOW_GRADE_ONLY for ref in refs) and section not in _LOW_GRADE_SECTIONS:
        problems.append(_rej("scope_expansion",
                             f"Only learning_only/coursework evidence is cited; it cannot support content in {section}."))

    families = _compiled_families()
    text_families = _families_in(text, families)
    text_metrics = extract_metrics(text)

    # 2. limited placement: low-scope wording only.
    limited = [ref for ref in refs if placements[ref["id"]] == "limited"]
    if limited:
        ids = ", ".join(str(r["id"])[:80] for r in limited)
        if is_skill:
            if not _is_familiarity_group(ctx.get("skill_group_category")):
                markers = ", ".join(rules.familiarity_markers())
                problems.append(_rej("familiarity_group",
                                     f"Evidence {ids} only supports a skill in a familiarity group "
                                     f"(a category containing one of: {markers})."))
        else:
            if text_families:
                problems.append(_rej("limited_wording",
                                     f"Evidence {ids} has limited placement in {section}; high-scope verbs "
                                     f"({', '.join(text_families)}) are not allowed."))
            if text_metrics:
                problems.append(_rej("limited_wording",
                                     f"Evidence {ids} has limited placement in {section}; metrics are not allowed "
                                     f"({len(text_metrics)} found)."))

    # 3. claim strength.
    claim = ctx.get("claim_strength")
    claim = getattr(claim, "value", claim)
    if claim is not None:
        max_rank = max(_rank(r.get("category")) for r in refs)
        if claim not in CLAIM_RANK:
            problems.append(_rej("claim_strength", "claim_strength is not a valid category."))
        elif CLAIM_RANK[claim] > max_rank:
            problems.append(_rej("claim_strength",
                                 f"claim_strength {claim} exceeds the strongest cited evidence."))
        elif _placement(claim, section) == "no":
            problems.append(_rej("claim_strength", f"claim_strength {claim} is not allowed in {section}."))

    # 4. technologies.
    ref_terms: set[str] = set()
    for ref in refs:
        ref_terms |= detect_terms(ref.get("text") or "")
        if ref.get("type") == "evidence" and ref.get("term"):
            ref_terms.add(normalize_term(str(ref["term"])))
    if is_skill:
        name_norm = normalize_term(text)
        supported = name_norm in ref_terms or any(
            term_in_text(text, ref.get("text") or "")
            or (ref.get("category") == MASTER_SKILL_CATEGORY
                and normalize_term(ref.get("text") or "") == name_norm)
            for ref in refs)
        if not supported:
            named = sorted(detect_terms(text))
            what = f"Skill ({', '.join(named)})" if named else "Skill item"
            problems.append(_rej("unsupported_technology", f"{what} is not supported by any cited source."))
    else:
        missing = sorted(detect_terms(text) - ref_terms)
        if missing:
            problems.append(_rej("unsupported_technology",
                                 f"Technologies not supported by the cited sources: {', '.join(missing)}."))

    # 5. metrics: only from cited master text or evidence metric metadata.
    if text_metrics:
        pool: set[tuple[str, str, str]] = set()
        for ref in refs:
            if ref.get("type") == "master":
                pool.update(extract_metrics(ref.get("text") or ""))
            else:
                for metric in ref.get("metrics") or []:
                    pool.update(extract_metrics(str(metric)))
        unsupported = [m for m in text_metrics if not _metric_supported(m, pool)]
        if unsupported:
            problems.append(_rej("unsupported_metric",
                                 f"{len(unsupported)} number(s)/metric(s) do not appear in a cited master "
                                 "block or in the metrics of cited evidence."))

    # 6. high-scope verbs need the same verb family in a cited source.
    if text_families:
        source_families: set[str] = set()
        for ref in refs:
            source_families.update(_families_in(ref.get("text") or "", families))
        missing_verbs = [f for f in text_families if f not in source_families]
        if missing_verbs:
            problems.append(_rej("high_scope_verb",
                                 f"High-scope verb(s) not present in any cited source: {', '.join(missing_verbs)}."))

    return problems


def make_hook(master: dict, evidence: dict[str, dict] | None) -> Callable[[dict], list[dict]]:
    """Build the `provenance_hook` for `validate_and_apply`.

    `ctx["ref_infos"]` already carries each ref's category, text, term and
    metrics (resolved by patches.py against this same master/evidence), so
    the closure needs nothing else; `master` and `evidence` are accepted for
    the tailoring.py contract.
    """
    def hook(ctx: dict) -> list[dict]:
        return check(ctx)
    return hook
