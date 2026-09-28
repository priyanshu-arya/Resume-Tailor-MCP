"""Read-only forensics over the audit log (spec §51, Phase 7).

`system_diagnostics()` is a new tool, not an extension of
`get_workflow_status`: that tool calls `rebuild_metrics()`, which **writes**
`monitoring/metrics.json`. This module must never write anything -- it is a
log -> count -> identify-patterns -> recommend-to-a-human pipeline and
nothing more.

Monitoring boundary (v1): this module counts, groups and looks up a fixed
recommendation table. It never retries, repairs, relaxes a threshold,
rewrites `resume_etiquette.yaml`/`templates.yaml`, or scores candidate
quality. `recommendations` come only from `REMEDIATION_HINTS` -- no
free-form text generation -- so a diagnosis is always traceable to one row
in a table a human wrote, not something inferred at call time. See
`docs/architecture.md`'s "Monitoring boundary (v1)" section and
`CLAUDE.md`'s Prohibited list for the same rule stated at the other two
layers this repo enforces things at.
"""

from __future__ import annotations

from collections import Counter

from lib import audit
from lib.errors import FAILURE_CATEGORIES
from lib.metrics import compute_metrics
from lib.workflows import load_workflow
from lib.workspace import Workspace, get_workspace

MAX_LIMIT = audit.MAX_READ_RECORDS
DEFAULT_LIMIT = 200

_FAILURE_EVENTS = frozenset({"tailor_rejected", "release_blocked", "tool_error", "internal_error"})
_FAILURE_STATUSES = frozenset({"failed", "blocked"})

# code/check-id -> one human next step. Fixed table, no free-form generation.
REMEDIATION_HINTS: dict[str, str] = {
    "PATCH_INVALID": "Review the rejected patch operations and fix the input; repairs may only drop/reorder blocks.",
    "PROVENANCE_VIOLATION": "Every new/changed block needs a source_ref to a real master block or confirmed evidence.",
    "MASTER_CONFLICT": "The master changed mid-workflow; re-run analyze_tailoring_requirements against the current master.",
    "TEMPLATE_UNKNOWN": "Choose a registered template id from list_templates.",
    "TEMPLATE_NOT_RELEASABLE": "Choose a supported template (e.g. classic-minimalist); experimental templates cannot release.",
    "TEMPLATE_REGISTRY_INVALID": "The template registry entry is malformed; fix resources/templates/templates.yaml.",
    "LATEX_COMPILE_FAILED": "Inspect the compile log for the TeX error; a bad patch or template bug is the usual cause.",
    "PDF_VALIDATION_UNAVAILABLE": "Install/configure the PDF inspection backend; release is blocked without measured PDF checks.",
    "NOT_RELEASED": "Call release_resume and resolve any critical failures before exporting in release mode.",
    "REPAIR_LIMIT": "The 3-repair cap was hit; start a new tailoring run instead of repairing further.",
    "WORKFLOW_NOT_FOUND": "The workflow id is unknown or belongs to another workspace; start a new workflow.",
    "source.master_hash": "The master changed after this version was tailored; tailor a new version.",
    "template.releasable": "Choose a supported, released template; nothing is substituted automatically.",
    "latex.compile": "Inspect the compile log; the TeX generated from this version failed to build.",
    "pdf.backend": "PDF validation is unavailable in this environment; release is blocked until it is restored.",
    "release.career_stage": "Set career_stage on the master so the real page cap (not just the template limit) is enforced.",
}


def _clamp_limit(limit: int) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _top(counter: Counter, n: int = 10) -> list[dict]:
    return [{"key": k, "count": c} for k, c in counter.most_common(n) if k]


def _recommendations(by_code: Counter, by_check_id: Counter) -> list[dict]:
    seen, out = set(), []
    for key, _ in (by_code + by_check_id).most_common():
        hint = REMEDIATION_HINTS.get(key)
        if hint and key not in seen:
            seen.add(key)
            out.append({"key": key, "hint": hint})
    return out


def _health(ws: Workspace, all_events: list[dict]) -> dict:
    path = ws.monitoring_dir / audit.AUDIT_FILE
    log_bytes = path.stat().st_size if path.exists() else 0
    return {
        "log_bytes": log_bytes,
        "log_rotation_recommended": log_bytes > audit.MAX_READ_BYTES // 2,
        "event_count_in_window": len(all_events),
        "records_capped": len(all_events) >= audit.MAX_READ_RECORDS,
    }


def system_diagnostics(workflow_id: str | None = None, limit: int = DEFAULT_LIMIT,
                       errors_only: bool = False, ws: Workspace | None = None) -> dict:
    """Read-only. Reconstructs a timeline from the audit log, counts failures
    by category/code/check-id, and looks up fixed remediation hints. Never
    calls rebuild_metrics -- metrics here are `compute_metrics` over the same
    bounded event window, a pure function with no write side effect."""
    ws = ws or get_workspace()
    if workflow_id is not None:
        load_workflow(workflow_id, ws)  # raises WORKFLOW_NOT_FOUND if unknown/foreign

    limit = _clamp_limit(limit)
    all_events = audit.read_events(ws)
    scoped = [e for e in all_events if workflow_id is None or e.get("workflow_id") == workflow_id]

    failures = [e for e in scoped if e.get("event") in _FAILURE_EVENTS or e.get("status") in _FAILURE_STATUSES]
    by_category = Counter(e.get("category") for e in failures if e.get("category") in FAILURE_CATEGORIES)
    by_code = Counter(e.get("code") for e in failures if isinstance(e.get("code"), str))
    by_check_id = Counter(cid for e in failures for cid in (e.get("rule_ids") or []) if isinstance(cid, str))

    timeline_source = failures if errors_only else scoped
    truncated = len(timeline_source) > limit
    timeline = timeline_source[-limit:]

    return {
        "scope": "workflow" if workflow_id else "workspace",
        "workflow_id": workflow_id,
        "timeline": timeline,
        "event_counts": dict(sorted(Counter(e.get("event") for e in scoped).items())),
        "top_failures": {
            "by_category": _top(by_category),
            "by_code": _top(by_code),
            "by_check_id": _top(by_check_id),
        },
        "metrics": compute_metrics(scoped if workflow_id else all_events),
        "health": _health(ws, all_events),
        "recommendations": _recommendations(by_code, by_check_id),
        "truncated": truncated,
        "records_read": len(all_events),
        "limit": limit,
        "note": "System reliability diagnostics only -- read-only, no candidate/resume quality judgment, "
                "no automatic remediation. Recommendations come from a fixed table; nothing here acts on its own.",
    }
