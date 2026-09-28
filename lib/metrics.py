"""Reliability metrics derived from the audit log (spec §51).

Metrics are a pure function of `monitoring/audit.jsonl`: `metrics.json` is
only a cache and can always be deleted and rebuilt with `rebuild_metrics`.
Rates are None (not 0) when there is no data for them.

These numbers describe how reliably the software ran. They are not a
measure of candidate or resume quality, and nothing here modifies rules,
templates, code or master data.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from lib.audit import read_events
from lib.errors import FAILURE_CATEGORIES
from lib.locking import atomic_write_bytes
from lib.workspace import get_workspace, utc_now_iso

METRICS_FILE = "metrics.json"
NOTE = "System reliability metrics only -- not a measure of candidate or resume quality."

_FAILURE_EVENTS = frozenset({"tailor_rejected", "release_blocked", "tool_error", "internal_error"})
_FAILURE_STATUSES = frozenset({"failed", "blocked"})


def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _failure_counts(events: list[dict]) -> dict:
    counts = {c: 0 for c in FAILURE_CATEGORIES}
    for e in events:
        if e.get("event") in _FAILURE_EVENTS or e.get("status") in _FAILURE_STATUSES:
            cat = e.get("category")
            if cat in counts:
                counts[cat] += 1
    return counts


def compute_metrics(events: list[dict]) -> dict:
    by_event = Counter(e.get("event") for e in events if isinstance(e.get("event"), str))

    succeeded: set = set()
    blocked: set = set()
    for e in events:  # append order == chronological order
        wid = e.get("workflow_id")
        if not wid:
            continue
        if e.get("event") == "release_succeeded":
            succeeded.add(wid)
            blocked.discard(wid)
        elif e.get("event") == "release_blocked":
            blocked.add(wid)

    validations = [e for e in events if e.get("event") == "validation_completed"]
    val_passed = sum(1 for e in validations if e.get("status") == "passed")

    with_template = [e for e in validations if _is_int(e.get("template_failures"))]
    template_ok = sum(1 for e in with_template if e["template_failures"] == 0)

    pdf_checked = [e for e in validations if e.get("pdf_checked") is True]
    pdf_ok = sum(1 for e in pdf_checked if e.get("pdf_failures") == 0)

    tailor_ok = by_event.get("tailor_succeeded", 0)
    tailor_factual = sum(
        1 for e in events if e.get("event") == "tailor_rejected" and e.get("category") == "FACTUAL"
    )

    repairs = sum(
        1 for e in events
        if e.get("event") == "tailor_succeeded"
        and isinstance(e.get("repair_attempt"), (int, float)) and not isinstance(e.get("repair_attempt"), bool)
        and e["repair_attempt"] > 0
    )
    evidence_requests = sum(
        e["evidence_prompt_count"] for e in events
        if e.get("event") == "requirements_analyzed" and _is_int(e.get("evidence_prompt_count"))
    )

    return {
        "generated_at": utc_now_iso(),
        "note": NOTE,
        "event_count": len(events),
        "event_counts": dict(sorted(by_event.items())),
        "workflow_count": by_event.get("workflow_started", 0),
        "successful_workflows": len(succeeded),
        "blocked_workflows": len(blocked),
        "validation_pass_rate": _rate(val_passed, len(validations)),
        "provenance_pass_rate": _rate(tailor_ok, tailor_ok + tailor_factual),
        "template_compliance_rate": _rate(template_ok, len(with_template)),
        "pdf_success_rate": _rate(pdf_ok, len(pdf_checked)),
        "failure_counts_by_category": _failure_counts(events),
        "repair_count": repairs,
        "evidence_request_count": evidence_requests,
    }


def rebuild_metrics(ws=None) -> dict:
    """Recompute metrics from audit.jsonl and atomically write monitoring/metrics.json."""
    ws = ws or get_workspace()
    metrics = compute_metrics(read_events(ws))
    data = (json.dumps(metrics, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(Path(ws.monitoring_dir) / METRICS_FILE, data)
    return metrics


def workflow_summary(workflow_id, ws=None) -> dict:
    """Events of one workflow: counts by event, last status, failures by category."""
    ws = ws or get_workspace()
    events = [e for e in read_events(ws) if e.get("workflow_id") == workflow_id]
    last_status = None
    for e in events:
        if e.get("status") is not None:
            last_status = e["status"]
    return {
        "workflow_id": workflow_id,
        "event_count": len(events),
        "event_counts": dict(sorted(Counter(e.get("event") for e in events).items())),
        "last_event": events[-1].get("event") if events else None,
        "last_status": last_status,
        "first_timestamp": events[0].get("timestamp") if events else None,
        "last_timestamp": events[-1].get("timestamp") if events else None,
        "failures_by_category": _failure_counts(events),
        "note": NOTE,
    }
