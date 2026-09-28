"""Metrics are derived from audit events and can be rebuilt."""

from __future__ import annotations

import json

import pytest

from lib import audit, metrics
from lib.errors import FAILURE_CATEGORIES


def _ev(event, **kw):
    return {"timestamp": "2026-01-01T00:00:00Z", "workspace_id": "RT-TEST", "event": event, **kw}


SYNTHETIC = [
    _ev("workflow_started", workflow_id="wf-a"),
    _ev("workflow_started", workflow_id="wf-b"),
    _ev("workflow_started", workflow_id="wf-c"),
    _ev("requirements_analyzed", workflow_id="wf-a", evidence_prompt_count=3),
    _ev("requirements_analyzed", workflow_id="wf-b", evidence_prompt_count=2),
    _ev("tailor_succeeded", workflow_id="wf-a", repair_attempt=0),
    _ev("tailor_succeeded", workflow_id="wf-b", repair_attempt=1),
    _ev("tailor_succeeded", workflow_id="wf-c"),
    _ev("tailor_rejected", workflow_id="wf-b", category="FACTUAL", code="PROVENANCE_VIOLATION"),
    _ev("tailor_rejected", workflow_id="wf-c", category="SOURCE", code="VERSION_NOT_FOUND"),
    _ev("validation_completed", workflow_id="wf-a", status="passed", template_failures=0,
        pdf_checked=True, pdf_failures=0),
    _ev("validation_completed", workflow_id="wf-b", status="failed", category="FORMAT",
        template_failures=2, pdf_checked=True, pdf_failures=1),
    _ev("validation_completed", workflow_id="wf-c", status="passed", pdf_checked=False),
    _ev("validation_completed", workflow_id="wf-c", status="passed", template_failures=0),
    _ev("release_blocked", workflow_id="wf-a", category="LATEX_PDF"),
    _ev("release_succeeded", workflow_id="wf-a"),     # later success: not blocked
    _ev("release_blocked", workflow_id="wf-b", category="TEMPLATE"),
    _ev("release_succeeded", workflow_id="wf-c"),
    _ev("release_succeeded", workflow_id="wf-c"),     # duplicate: counted once
    _ev("tool_error", tool="export_resume", category="WORKFLOW", code="NOT_RELEASED"),
    _ev("internal_error", error_class="KeyError", category="WORKFLOW"),
    _ev("export_completed", workflow_id="wf-c", format="pdf", status="ok"),
]


def test_compute_metrics_on_synthetic_events():
    m = metrics.compute_metrics(SYNTHETIC)
    assert m["workflow_count"] == 3
    assert m["successful_workflows"] == 2          # wf-a, wf-c
    assert m["blocked_workflows"] == 1             # wf-b only
    assert m["validation_pass_rate"] == pytest.approx(3 / 4)
    assert m["provenance_pass_rate"] == pytest.approx(3 / 4)   # 3 ok / (3 + 1 FACTUAL)
    assert m["template_compliance_rate"] == pytest.approx(2 / 3)
    assert m["pdf_success_rate"] == pytest.approx(1 / 2)
    assert m["failure_counts_by_category"] == {
        "SOURCE": 1, "FACTUAL": 1, "TEMPLATE": 1, "FORMAT": 1, "LATEX_PDF": 1, "WORKFLOW": 2,
    }
    assert m["repair_count"] == 1
    assert m["evidence_request_count"] == 5
    assert m["event_counts"]["workflow_started"] == 3
    assert m["event_counts"]["release_succeeded"] == 3
    assert m["note"].startswith("System reliability metrics only")
    assert m["generated_at"].endswith("Z")


def test_no_data_rates_are_none_not_zero():
    m = metrics.compute_metrics([])
    for key in ("validation_pass_rate", "provenance_pass_rate", "template_compliance_rate", "pdf_success_rate"):
        assert m[key] is None
    assert m["workflow_count"] == 0 and m["successful_workflows"] == 0 and m["blocked_workflows"] == 0
    assert m["repair_count"] == 0 and m["evidence_request_count"] == 0
    assert m["failure_counts_by_category"] == {c: 0 for c in FAILURE_CATEGORIES}

    # validations present but none carries template_failures / pdf_checked -> those rates stay None
    m = metrics.compute_metrics([_ev("validation_completed", status="passed")])
    assert m["validation_pass_rate"] == 1.0
    assert m["template_compliance_rate"] is None
    assert m["pdf_success_rate"] is None


def test_blocked_excludes_later_success_but_counts_success_then_block():
    evs = [
        _ev("release_blocked", workflow_id="x"),
        _ev("release_succeeded", workflow_id="x"),
        _ev("release_succeeded", workflow_id="y"),
        _ev("release_blocked", workflow_id="y"),   # blocked after success: counts as blocked
    ]
    m = metrics.compute_metrics(evs)
    assert m["blocked_workflows"] == 1
    assert m["successful_workflows"] == 2


def test_failure_counts_have_all_six_keys():
    m = metrics.compute_metrics([_ev("tool_error", category="FACTUAL")])
    assert set(m["failure_counts_by_category"]) == set(FAILURE_CATEGORIES)
    assert len(FAILURE_CATEGORIES) == 6
    assert m["failure_counts_by_category"]["FACTUAL"] == 1


def test_status_failed_counts_even_on_non_failure_event():
    m = metrics.compute_metrics([_ev("validation_completed", status="failed", category="FORMAT"),
                                 _ev("validation_completed", status="passed", category="FORMAT")])
    assert m["failure_counts_by_category"]["FORMAT"] == 1


def _log_synthetic(ws):
    for e in SYNTHETIC:
        fields = {k: v for k, v in e.items() if k not in ("timestamp", "workspace_id", "event")}
        assert audit.log_event(e["event"], ws=ws, **fields) is not None


def test_rebuild_metrics_writes_file_and_is_reproducible(workspace):
    _log_synthetic(workspace)
    first = metrics.rebuild_metrics(workspace)
    path = workspace.monitoring_dir / "metrics.json"
    assert json.loads(path.read_text()) == first
    assert first["workflow_count"] == 3 and first["blocked_workflows"] == 1

    path.unlink()
    second = metrics.rebuild_metrics()  # default workspace
    assert path.exists()
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    assert first == {k: v for k, v in metrics.compute_metrics(SYNTHETIC).items() if k != "generated_at"}


def test_workflow_summary_filters_by_workflow(workspace):
    _log_synthetic(workspace)
    s = metrics.workflow_summary("wf-b", workspace)
    assert s["workflow_id"] == "wf-b"
    assert s["event_count"] == 6
    assert s["event_counts"] == {
        "release_blocked": 1, "requirements_analyzed": 1, "tailor_rejected": 1,
        "tailor_succeeded": 1, "validation_completed": 1, "workflow_started": 1,
    }
    assert s["last_event"] == "release_blocked"
    assert s["last_status"] == "failed"
    assert s["failures_by_category"]["FACTUAL"] == 1
    assert s["failures_by_category"]["TEMPLATE"] == 1
    assert s["failures_by_category"]["FORMAT"] == 1
    assert s["failures_by_category"]["WORKFLOW"] == 0  # tool_error/internal_error had no workflow_id

    empty = metrics.workflow_summary("wf-none", workspace)
    assert empty["event_count"] == 0 and empty["last_status"] is None
    assert set(empty["failures_by_category"]) == set(FAILURE_CATEGORIES)
