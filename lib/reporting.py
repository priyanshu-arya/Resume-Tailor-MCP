"""Server-assembled completion data (spec §56-65, Phase 8.1).

The 10-part completion message CLAUDE.md mandates (source, hashes, what was
added/not-added and why, what was optimized, the validation checklist,
release/export identity) was previously split across two tool calls and two
turns -- `tailor_resume` had no `template_version`, `release_resume` had no
`template_id`/`source`/`added_terms`/`not_added`. Reconstructing it from
memory across turns is exactly when it drifts.

This module assembles that message as data instead: `completion_summary`
returns every field CLAUDE.md's step 10 names, derived from the saved
version, its recorded patches, its workflow and its release report -- never
from free text. `why` sentences are generated from `lib.rules.placement`,
the same table the provenance validator enforces against, not recalled.

`checklist()` groups every check id from every validator into seven fixed
groups (`GROUP_ORDER`), always all seven, so a check can be added to a
validator without the display silently dropping it -- `group_of` raises on
an unmapped id rather than returning a default.
"""

from __future__ import annotations

import json
from collections import Counter

from lib import rules, storage, tailoring, workflows
from lib.errors import ResumeTailorError
from lib.workspace import Workspace, get_workspace

GROUP_ORDER = ("provenance", "evidence", "content", "template", "ats", "latex", "pdf")

# Explicit ids win over the prefix table below.
CHECK_GROUP: dict[str, str] = {
    "source.master_hash": "provenance",
    "provenance.replay": "provenance",
    "release.rules_version_drift": "provenance",
    "release.career_stage": "template",
    "template.releasable": "template",
    "template.renderer": "template",
    "latex.compile": "latex",
}
CHECK_GROUP_PREFIX = (
    ("content.", "content"),
    ("template.", "latex"),
    ("pdf.", "pdf"),
    ("structure.", "provenance"),
)

_SECTION_KEYS = {"Summary": "summary", "Experience": "experience", "Projects": "projects",
                 "Education": "education", "Skills": "skills"}

_STATUS_RANK = {"not_run": 0, "pass": 1, "warning": 2, "not_available": 3, "fail": 4}

_OP_BUCKET = {"replace_block": "reworded", "add_block": "added", "add_skill_item": "added",
             "add_project_entry": "added", "drop_block": "dropped", "reorder": "reordered"}


def group_of(check_id: str) -> str:
    if check_id in CHECK_GROUP:
        return CHECK_GROUP[check_id]
    for prefix, group in CHECK_GROUP_PREFIX:
        if check_id.startswith(prefix):
            return group
    raise ValueError(f"Unmapped check id: {check_id!r} -- add it to CHECK_GROUP or CHECK_GROUP_PREFIX.")


def checklist(checks: list) -> dict:
    """All seven groups, always, so the display cannot silently omit one.
    {group: {status, checks, failed, warnings, not_available}}"""
    groups = {g: {"status": "not_run", "checks": [], "failed": [], "warnings": [], "not_available": []}
             for g in GROUP_ORDER}
    for c in checks:
        c = c if isinstance(c, dict) else c.model_dump(mode="json")
        bucket = groups[group_of(c["id"])]
        bucket["checks"].append(c["id"])
        if c["status"] == "not_available":
            bucket["not_available"].append(c["id"])
        elif c["status"] == "fail" and c.get("severity") in ("critical", "error"):
            bucket["failed"].append(c["id"])
        elif c["status"] == "warning" or c["status"] == "fail":
            bucket["warnings"].append(c["id"])
    for bucket in groups.values():
        if not bucket["checks"]:
            status = "not_run"
        elif bucket["failed"]:
            status = "fail"
        elif bucket["not_available"]:
            status = "not_available"
        elif bucket["warnings"]:
            status = "warning"
        else:
            status = "pass"
        bucket["status"] = status
    return groups


def _why(term: str, section_label: str, category: str, evidence_id: str) -> str:
    section_key = _SECTION_KEYS.get(section_label, section_label.lower())
    placement = rules.placement(category, section_key)
    base = f"{term} was added to {section_label} because you confirmed it as {category} ({evidence_id})"
    if placement == "limited":
        return f"{base}; no high-scope verbs or metrics were used"
    return base


def _optimized(patches: list[dict]) -> dict:
    counts = Counter()
    for p in patches:
        bucket = _OP_BUCKET.get(p.get("operation"))
        if bucket:
            counts[bucket] += 1
    return {"reworded": counts["reworded"], "added": counts["added"], "dropped": counts["dropped"],
            "reordered": counts["reordered"], "patch_count": len(patches)}


def _find_latest_release_report(version_id: str, ws: Workspace) -> dict | None:
    candidates = sorted(ws.releases_dir.glob(f"{version_id}-*.json"))
    latest = None
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if latest is None or data.get("created_at", "") > latest.get("created_at", ""):
            latest = data
    return latest


def completion_summary(version_id: str, workflow_id: str, *, ws: Workspace | None = None,
                       release_report: dict | None = None) -> dict:
    """The 10-part contract, assembled from the saved version, its recorded
    patches, its workflow and its release report."""
    ws = ws or get_workspace()
    version = storage.require_version(version_id, ws)
    meta = version.get("metadata") or {}
    if meta.get("workflow_id") != workflow_id:
        raise ResumeTailorError("WORKFLOW_NOT_FOUND", f"Version {version_id!r} does not belong to {workflow_id}.")
    workflow = workflows.load_workflow(workflow_id, ws)

    if release_report is None:
        release_report = _find_latest_release_report(version_id, ws)

    evidence = tailoring._load_evidence(ws, workflow_id, meta.get("evidence_ids") or [])
    usage = tailoring.evidence_usage(version, meta.get("summary_source_refs") or [], evidence, workflow)

    added_after_confirmation = [
        {"term": u["term"], "category": u["category"], "section": u["section"], "evidence_id": u["evidence_id"],
         "block_id": u["block_id"], "why": _why(u["term"], u["section"], u["category"], u["evidence_id"])}
        for u in usage["added_terms"]
    ]
    not_added = [
        {"term": n["term"], "reason": n["reason"], "statement": f"I will not add {n['term']} because {n['reason']}."}
        for n in usage["not_added"]
    ]

    try:
        recorded = tailoring.load_version_patches(version_id, ws)
        optimized = _optimized(recorded.get("patches") or [])
    except ResumeTailorError:
        optimized = _optimized([])

    from lib import templates as _templates
    template_id = meta.get("template_id")
    try:
        info = _templates.resolve_template(template_id) if template_id else {}
    except ResumeTailorError:
        info = {}

    released = bool(release_report and release_report.get("released"))
    checks = (release_report or {}).get("checks") or []
    validation = {
        "checklist": checklist(checks),
        "passed": bool(release_report and not (release_report.get("critical_failures"))),
        "critical_failures": (release_report or {}).get("critical_failures") or [],
        "warnings": (release_report or {}).get("warnings") or [],
        "not_available": (release_report or {}).get("not_available") or [],
        "check_count": len(checks),
        "measured_properties": (release_report or {}).get("measured_properties") or {},
    }

    if released:
        lifecycle_state, next_step = "released", "Call export_resume(mode='release') to get the PDF and TeX."
    elif release_report is not None:
        lifecycle_state = "blocked"
        next_step = ("Fix the critical failures (repairs may only drop/reorder blocks, via "
                    "tailor_resume(repair_of=...)), or choose a supported template. Max 3 repairs.")
    else:
        lifecycle_state, next_step = "draft", "Call validate_version, then release_resume."

    display_order = ("source", "source_master_hash", "document_kind", "template", "rules_version",
                     "added_after_confirmation", "not_added", "optimized", "validation", "released",
                     "release_report_id", "export_basename", "tex_sha256", "pdf_sha256")

    return {
        "source": f"workspace master {meta.get('document_kind')}",
        "source_master_hash": meta.get("source_master_hash"),
        "document_kind": meta.get("document_kind"),
        "template": {"id": template_id, "version": meta.get("template_version"),
                    "status": info.get("status"), "releasable": info.get("releasable")},
        "rules_version": meta.get("rules_version"),
        "added_after_confirmation": added_after_confirmation,
        "not_added": not_added,
        "optimized": optimized,
        "validation": validation,
        "released": released,
        "release_report_id": (release_report or {}).get("release_report_id"),
        "export_basename": meta.get("export_basename") or (release_report or {}).get("export_basename"),
        "tex_sha256": (release_report or {}).get("tex_sha256"),
        "pdf_sha256": (release_report or {}).get("pdf_sha256"),
        "lifecycle_state": lifecycle_state,
        "next_step": next_step,
        "display_order": display_order,
    }
