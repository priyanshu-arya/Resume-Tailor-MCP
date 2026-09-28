"""User-confirmed evidence for tailoring (spec §17-20).

When a JD requirement is not supported by the master, the system never
invents it. It asks the user, and only the user's explicit answer becomes a
saved evidence record. Tailoring then receives evidence *IDs*, never free
text, so every added claim traces back to something the user actually said.

Silence, memory, "sounds right" or "optimize it" are not confirmation:
`save_evidence` refuses anything but `confirmed=True`.

Records live at data/evidence/<workflow_id>/<evidence_id>.yaml and are only
loadable through the workflow they were confirmed in.

Nothing here logs or returns the JD text, and error messages never echo the
user's evidence text.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from lib import rules, storage
from lib.errors import ResumeTailorError
from lib.keywords import certification_requirements, extract_jd_keywords, normalize_term, term_type
from lib.locking import atomic_write_yaml, workspace_lock
from lib.matching import match_resume_to_jd, requirement_view
from lib.schemas import EvidenceCategory, TailoringEvidence, validate_kind
from lib.workflows import check_workflow_id, create_workflow, load_workflow, update_workflow
from lib.workspace import Workspace, get_workspace, safe_child, utc_now_iso

MAX_JD_CHARS = 50_000
MAX_TERM_CHARS = 80
MAX_EVIDENCE_CHARS = 1_000
MAX_METRICS = 10
MAX_METRIC_CHARS = 40
MAX_EVIDENCE_PER_WORKFLOW = 99  # two-digit counter

CATEGORIES: list[str] = [c.value for c in EvidenceCategory]

# Machine-readable reason a term was prompted for, in priority order.
PROMPT_REASONS = ("must_have_missing", "must_have_weak", "certification_required",
                  "unrecognized_requirement")
_REASON_PRIORITY = {r: i for i, r in enumerate(PROMPT_REASONS)}
MAX_PROMPTS = 12
MAX_UNKNOWN_PROMPTS = 5

_PLACEMENT_SECTIONS = ("experience", "projects", "skills", "summary", "education")


def _placement_by_category() -> dict[str, dict[str, str]]:
    """Live table of category -> section -> placement, built from
    `rules.placement` (never a duplicate of the YAML matrix)."""
    return {cat: {sec: rules.placement(cat, sec) for sec in _PLACEMENT_SECTIONS} for cat in CATEGORIES}

CATEGORY_PROMPT = (
    "How have you used it? Pick the one that fits best:\n"
    "- professional: in a paid job (full-time, part-time, contract)\n"
    "- internship: during an internship or co-op\n"
    "- personal_project: in a project you built on your own time\n"
    "- academic: in research, a thesis or a university project\n"
    "- coursework: in a class or course assignment\n"
    "- certification: you hold a certification covering it\n"
    "- learning_only: you are studying it but have not applied it yet\n"
    "- none: you have not used it (it will not be added)\n"
    "Describe what you actually did in your own words. Only what you state is saved; "
    "nothing is assumed or embellished."
)

_PLACEMENT_HINT = (
    "Placement depends on the category you confirm: professional/internship -> the matching "
    "Experience entry; personal_project/academic -> Projects (or research); coursework -> "
    "Education; certification -> Certifications; learning_only -> may be mentioned as learning "
    "only, never as experience; none -> not added."
)
_WEAK_PLACEMENT_HINT = (
    "Already listed under Skills but no bullet shows it in use. " + _PLACEMENT_HINT
)

_METRIC_NOTE = ("Each metric must appear word-for-word in evidence_text, so every number comes "
                "from the user's own statement.")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _normalize_term(term: str) -> str:
    """Canonical form of a term (k8s -> kubernetes). Uses the keyword
    module's alias map when present; otherwise lowercase + whitespace fold."""
    from lib import keywords
    fn = getattr(keywords, "normalize_term", None)
    if callable(fn):
        return fn(term)
    return " ".join(term.lower().split())


def _fold(text: str) -> str:
    return " ".join(text.lower().split())


def _invalid(message: str, **details) -> ResumeTailorError:
    return ResumeTailorError("EVIDENCE_INVALID", message, details=details)


def _not_found(message: str, **details) -> ResumeTailorError:
    return ResumeTailorError("EVIDENCE_NOT_FOUND", message, details=details)


def _id_re(workflow_id: str) -> re.Pattern:
    return re.compile(rf"ev-{re.escape(workflow_id)}-(\d{{2}})")


def _workflow_dir(ws: Workspace, workflow_id: str):
    return safe_child(ws.evidence_dir, workflow_id)


def _existing_ids(ws: Workspace, workflow_id: str) -> list[str]:
    folder = _workflow_dir(ws, workflow_id)
    if not folder.is_dir():
        return []
    pat = _id_re(workflow_id)
    return sorted(p.stem for p in folder.glob("ev-*.yaml") if pat.fullmatch(p.stem))


def _question(term: str, status: str) -> str:
    if status == "weak":
        return (f"'{term}' is listed in your Skills, but none of your experience or project "
                f"bullets show it in use. Have you used {term}? If so, where and how -- what did "
                f"you do with it? If not, say so and it will be left as is.")
    if status == "unknown":
        return (f"The job description mentions '{term}'. Do you have experience with this? If so, "
                f"where and how -- what did you do? If not, say so and it will not be added.")
    return (f"The job description asks for '{term}', and your master does not mention it. Have "
            f"you used {term}? If so, where and how -- what did you do with it? If not, say so "
            f"and it will not be added.")


def _decline_phrasing(term: str) -> str:
    """The exact sentence to say if the user declines this prompt --
    server-generated so it can't soften into something vaguer (CLAUDE.md's
    evidence-question rules)."""
    return f"I will not add {term} because no evidence was provided."


def _prompt(term: str, status: str, reason: str, importance: str) -> dict:
    return {
        "term": term,
        "status": status,
        "reason": reason,
        "requirement_type": term_type(term),
        "importance": importance,
        "question": _question(term, status),
        "categories": list(CATEGORIES),
        "allowed_placement_hint": _WEAK_PLACEMENT_HINT if status == "weak" else _PLACEMENT_HINT,
        "placement_by_category": _placement_by_category(),
        "decline_phrasing": _decline_phrasing(term),
        "not_added_phrasing": _decline_phrasing(term),
    }


def _build_prompts(requirements: list[dict], jd_text: str) -> tuple[list[dict], bool]:
    """Evidence prompts from `requirement_view` rows, machine-reasoned and
    budgeted: must_have missing -> must_have weak -> certification_required ->
    unrecognized (unknown-axis, capped at MAX_UNKNOWN_PROMPTS), then the whole
    list capped at MAX_PROMPTS. Nice-to-have terms never get a prompt. Each
    term is prompted at most once, using its highest-priority applicable
    reason."""
    cert_terms = {normalize_term(t) for t in certification_requirements(jd_text)}
    candidates: list[tuple[str, dict]] = []
    for row in requirements:
        status, importance, term = row["status"], row["importance"], row["term"]
        if importance == "must_have" and status == "missing":
            reason = "must_have_missing"
        elif importance == "must_have" and status == "weak":
            reason = "must_have_weak"
        elif normalize_term(term) in cert_terms and status in ("missing", "weak"):
            reason = "certification_required"
        elif status == "unknown":
            reason = "unrecognized_requirement"
        else:
            continue
        candidates.append((reason, row))

    candidates.sort(key=lambda pair: _REASON_PRIORITY[pair[0]])

    prompts: list[dict] = []
    unknown_count = 0
    truncated = False
    for reason, row in candidates:
        if reason == "unrecognized_requirement":
            if unknown_count >= MAX_UNKNOWN_PROMPTS:
                truncated = True
                continue
            unknown_count += 1
        if len(prompts) >= MAX_PROMPTS:
            truncated = True
            break
        prompts.append(_prompt(row["term"], row["status"], reason, row["importance"]))
    total = len(prompts)
    for i, p in enumerate(prompts, start=1):
        p["order"] = i
        p["total"] = total
    return prompts, truncated


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyze_requirements(jd_text: str, source_kind: str = "resume", workflow_id: str | None = None,
                         ws: Workspace | None = None) -> dict:
    """Gap-analyse the master against a JD and produce neutral evidence
    questions for every unsupported must-have requirement."""
    ws = ws or get_workspace()
    validate_kind(source_kind)
    from lib import resolve
    master, master_hash_value = resolve.require_tailorable_master(source_kind, ws=ws)

    if not isinstance(jd_text, str) or not jd_text.strip():
        raise _invalid("jd_text must be a non-empty string.")
    if len(jd_text) > MAX_JD_CHARS:
        raise _invalid(f"jd_text is too long (max {MAX_JD_CHARS} characters).",
                       max_chars=MAX_JD_CHARS)

    if workflow_id is None:
        workflow = create_workflow(source_kind, jd_text, extract_jd_keywords(jd_text), ws)
        workflow_id = workflow["workflow_id"]
    else:
        check_workflow_id(workflow_id)
        workflow = load_workflow(workflow_id, ws)
        if workflow.get("source_kind") != source_kind:
            raise ResumeTailorError(
                "INVALID_KIND",
                f"Workflow {workflow_id} was started for a {workflow.get('source_kind')!r} master, "
                f"not {source_kind!r}.",
                details={"workflow_id": workflow_id, "workflow_kind": workflow.get("source_kind"),
                         "requested_kind": source_kind},
            )

    result = match_resume_to_jd(master, jd_text)
    must_have = list(result.get("must_have", []))
    missing = list(result.get("missing", []))
    weak = list(result.get("weak", []))
    matched = list(result.get("matched", []))
    unknown = list(dict.fromkeys(result.get("unknown_requirements", []) or []))

    missing_set, weak_set = set(missing), set(weak)
    priority_missing_terms = [t for t in must_have if t in missing_set]
    priority_weak_terms = [t for t in must_have if t in weak_set]
    priority_missing = priority_missing_terms + priority_weak_terms

    requirements = requirement_view(result, master)
    status_counts: dict[str, int] = {}
    for row in requirements:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    prompts, prompts_truncated = _build_prompts(requirements, jd_text)

    source = f"workspace master {source_kind}"
    trimmed_requirements = [{"term": r["term"], "status": r["status"], "importance": r["importance"],
                             "reason": r["reason"]} for r in requirements]
    analysis = {
        "source": source,
        "confirmed": matched,
        "weak": weak,
        "missing": missing,
        "priority_missing": priority_missing,
        "unknown_requirements": unknown,
        "requirements": trimmed_requirements,
        "evidence_prompts": [{"term": p["term"], "status": p["status"]} for p in prompts],
        "match_score": result.get("score"),
        "ats_visible_score": result.get("ats_visible_score"),
        "analyzed_at": utc_now_iso(),
    }
    # Record which master this gap analysis was computed against, so a later
    # tailor_resume call can detect a master change in between and refuse to
    # use evidence prompts that were asked against a now-stale master.
    update_workflow(workflow_id, set_fields={"analysis": analysis, "source_master_hash": master_hash_value},
                    ws=ws)

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "source": source,
        "confirmed": matched,
        "weak": weak,
        "missing": missing,
        "priority_missing": priority_missing,
        "unknown_requirements": unknown,
        "requirements": requirements,
        "status_counts": status_counts,
        "evidence_prompts": prompts,
        "prompts_truncated": prompts_truncated,
        "ask_one_at_a_time": True,
        "prompt_count": len(prompts),
        "match_score": result.get("score"),
        "ats_visible_score": result.get("ats_visible_score"),
    }


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

def _clean_metrics(metrics, evidence_text: str) -> list[str]:
    if metrics is None:
        return []
    if not isinstance(metrics, list):
        raise _invalid("metrics must be a list of strings.")
    if len(metrics) > MAX_METRICS:
        raise _invalid(f"At most {MAX_METRICS} metrics per evidence record.", max_metrics=MAX_METRICS)
    folded_text = _fold(evidence_text)
    cleaned: list[str] = []
    for i, m in enumerate(metrics):
        if not isinstance(m, str) or not m.strip():
            raise _invalid("Each metric must be a non-empty string.", metric_index=i)
        m = " ".join(m.split())
        if len(m) > MAX_METRIC_CHARS:
            raise _invalid(f"Each metric must be at most {MAX_METRIC_CHARS} characters.",
                           metric_index=i, max_chars=MAX_METRIC_CHARS)
        if _fold(m) not in folded_text:
            raise _invalid("A metric does not appear in evidence_text. " + _METRIC_NOTE, metric_index=i)
        if m not in cleaned:
            cleaned.append(m)
    return cleaned


def save_evidence(workflow_id: str, term: str, category: str, evidence_text: str = "",
                  confirmed: bool = False, metrics: list[str] | None = None,
                  prompt_reason: str | None = None,
                  ws: Workspace | None = None) -> dict:
    """Save one fact the user explicitly stated about a JD requirement."""
    ws = ws or get_workspace()
    check_workflow_id(workflow_id)
    load_workflow(workflow_id, ws)

    if prompt_reason is not None and prompt_reason not in PROMPT_REASONS:
        raise _invalid("prompt_reason must be one of: " + ", ".join(PROMPT_REASONS) + ".",
                       allowed=list(PROMPT_REASONS))

    if confirmed is not True:
        raise _invalid(
            "Evidence can only be saved with confirmed=True, after the user has explicitly stated "
            "it. Silence, assumptions, 'sounds right' or a request to optimize are not "
            "confirmation -- ask the user and save only what they actually said.",
            term=term if isinstance(term, str) and len(term) <= MAX_TERM_CHARS else None,
        )

    try:
        category_value = EvidenceCategory(category).value
    except (ValueError, TypeError):
        raise _invalid("category must be one of: " + ", ".join(CATEGORIES) + ".",
                       allowed=list(CATEGORIES)) from None

    if not isinstance(term, str) or not term.strip():
        raise _invalid("term must be a non-empty string.")
    term_display = " ".join(term.split())
    if len(term_display) > MAX_TERM_CHARS:
        raise _invalid(f"term must be at most {MAX_TERM_CHARS} characters.", max_chars=MAX_TERM_CHARS)
    term_norm = _normalize_term(term_display)
    if not isinstance(term_norm, str) or not term_norm.strip():
        raise _invalid("term normalizes to an empty value.")

    if evidence_text is None:
        evidence_text = ""
    if not isinstance(evidence_text, str):
        raise _invalid("evidence_text must be a string.")
    evidence_text = evidence_text.strip()
    if category_value != EvidenceCategory.none.value and not evidence_text:
        raise _invalid("evidence_text is required for every category except 'none': record what "
                       "the user said they did, in their words.", term=term_display)
    if len(evidence_text) > MAX_EVIDENCE_CHARS:
        raise _invalid(f"evidence_text must be at most {MAX_EVIDENCE_CHARS} characters.",
                       max_chars=MAX_EVIDENCE_CHARS, term=term_display)

    clean_metrics = _clean_metrics(metrics, evidence_text)

    note_none = ("Category 'none' records that the user does not have this; the term will be "
                 "reported as \"not added\" and never inserted into the resume.")
    note = note_none if category_value == "none" else (
        "Saved. Pass this evidence ID to tailor_resume; the claim may not go beyond what "
        "evidence_text states. " + note_none)

    folder = _workflow_dir(ws, workflow_id)
    with workspace_lock(ws):
        existing_ids = _existing_ids(ws, workflow_id)
        folded_text = _fold(evidence_text)
        for eid in existing_ids:
            rec = storage.yaml_load_file(safe_child(folder, eid, ".yaml"))
            if (isinstance(rec, dict) and rec.get("term") == term_norm
                    and rec.get("category") == category_value
                    and _fold(str(rec.get("evidence_text", ""))) == folded_text):
                update_workflow(workflow_id, append={"evidence_ids": [eid]}, ws=ws)
                dup_result = {"ok": True, "evidence": rec,
                             "note": f"Identical evidence already saved as {eid}; reusing it. " + note}
                if category_value == "none":
                    dup_result["statement"] = _decline_phrasing(term_display)
                return dup_result

        pat = _id_re(workflow_id)
        highest = max((int(pat.fullmatch(e).group(1)) for e in existing_ids), default=0)
        if highest >= MAX_EVIDENCE_PER_WORKFLOW:
            raise _invalid(f"A workflow can hold at most {MAX_EVIDENCE_PER_WORKFLOW} evidence records.")
        evidence_id = f"ev-{workflow_id}-{highest + 1:02d}"

        model = TailoringEvidence(
            id=evidence_id,
            workflow_id=workflow_id,
            workspace_id=ws.id,
            term=term_norm,
            category=category_value,
            evidence_text=evidence_text,
            confirmed=True,
            metrics=clean_metrics,
            created_at=utc_now_iso(),
            term_display=term_display,
            prompt_reason=prompt_reason,
        )
        record = model.model_dump(mode="json")
        atomic_write_yaml(safe_child(folder, evidence_id, ".yaml"), record, exclusive=True)
        update_workflow(workflow_id, append={"evidence_ids": [evidence_id]}, ws=ws)

    result = {"ok": True, "evidence": record, "note": note}
    if category_value == "none":
        result["statement"] = _decline_phrasing(term_display)
    return result


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _load_one(ws: Workspace, workflow_id: str, evidence_id) -> dict:
    if not isinstance(evidence_id, str) or not _id_re(workflow_id).fullmatch(evidence_id):
        raise _not_found("Evidence ID does not belong to this workflow.",
                         evidence_id=evidence_id if isinstance(evidence_id, str) else None,
                         workflow_id=workflow_id)
    data = storage.yaml_load_file(safe_child(_workflow_dir(ws, workflow_id), evidence_id, ".yaml"))
    if data is None:
        raise _not_found(f"No evidence {evidence_id} in workflow {workflow_id}.",
                         evidence_id=evidence_id, workflow_id=workflow_id)
    if not isinstance(data, dict):
        raise _invalid(f"Evidence {evidence_id} is malformed.", evidence_id=evidence_id)
    if (data.get("id") != evidence_id or data.get("workflow_id") != workflow_id
            or data.get("workspace_id") != ws.id):
        raise _not_found(f"Evidence {evidence_id} does not belong to this workflow/workspace.",
                         evidence_id=evidence_id, workflow_id=workflow_id)
    if data.get("confirmed") is not True:
        raise _invalid(f"Evidence {evidence_id} is not user-confirmed and cannot be used.",
                       evidence_id=evidence_id)

    try:
        model = TailoringEvidence.model_validate(data)
    except ValidationError as e:
        raise _invalid(f"Evidence {evidence_id} failed validation.", evidence_id=evidence_id,
                       errors=[{"loc": list(err["loc"]), "msg": err["msg"]} for err in e.errors()[:5]]
                       ) from None
    record = model.model_dump(mode="json")
    if record["category"] != "none" and not record["evidence_text"].strip():
        raise _invalid(f"Evidence {evidence_id} has no evidence text.", evidence_id=evidence_id)
    folded_text = _fold(record["evidence_text"])
    for i, m in enumerate(record["metrics"]):
        if _fold(m) not in folded_text:
            raise _invalid(f"Evidence {evidence_id} has a metric not stated in its text.",
                           evidence_id=evidence_id, metric_index=i)
    return record


def load_evidence(workflow_id: str, evidence_ids: list[str], ws: Workspace | None = None) -> dict[str, dict]:
    """Load user-confirmed evidence records of one workflow -> {id: record}."""
    ws = ws or get_workspace()
    check_workflow_id(workflow_id)
    load_workflow(workflow_id, ws)
    if not isinstance(evidence_ids, list):
        raise _not_found("evidence_ids must be a list of evidence IDs.")
    return {eid: _load_one(ws, workflow_id, eid) for eid in dict.fromkeys(evidence_ids)}


def list_evidence(workflow_id: str, ws: Workspace | None = None) -> list[dict]:
    """All evidence records of a workflow, sorted by ID."""
    ws = ws or get_workspace()
    check_workflow_id(workflow_id)
    load_workflow(workflow_id, ws)
    ids = _existing_ids(ws, workflow_id)
    loaded = load_evidence(workflow_id, ids, ws=ws)
    return [loaded[i] for i in ids]
