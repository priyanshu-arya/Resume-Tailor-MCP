"""Audit log: structure, no-PII sanitization, robustness, concurrency."""

from __future__ import annotations

import json
import os
import re
import stat
import threading

import pytest

from lib import audit


def _lines(ws, name="audit.jsonl"):
    path = ws.monitoring_dir / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_record_is_json_line_with_utc_timestamp(workspace):
    rec = audit.log_event("workflow_started", ws=workspace, workflow_id="wf-1", tool="start_workflow",
                          duration_ms=12, applied=True, master_hash="ab" * 32, status=None)
    assert rec is not None
    lines = _lines(workspace)
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == rec
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", parsed["timestamp"])
    assert parsed["workspace_id"] == workspace.id
    assert parsed["event"] == "workflow_started"
    assert parsed["workflow_id"] == "wf-1"
    assert parsed["duration_ms"] == 12 and parsed["applied"] is True
    assert parsed["master_hash"] == "ab" * 32
    assert "status" not in parsed  # None omitted


def test_default_workspace_is_used(workspace):
    assert audit.log_event("export_completed", format="pdf") is not None
    assert audit.read_events()[0]["format"] == "pdf"


def test_unknown_fields_dropped_and_pii_absent(workspace):
    rec = audit.log_event(
        "tailor_succeeded", ws=workspace, version_id="v1",
        name="Alex Example", email="alex.example@example.test", phone="+1 555 0100",
        jd_text="We are hiring a Senior Backend Engineer with Kubernetes",
        resume_text="Built 18 Python/Django REST endpoints",
    )
    assert set(rec) == {"timestamp", "workspace_id", "event", "version_id"}
    raw = (workspace.monitoring_dir / "audit.jsonl").read_text()
    for s in ("Alex Example", "alex.example@example.test", "555 0100", "Senior Backend", "Django"):
        assert s not in raw


def test_free_text_in_allowed_field_is_redacted(workspace):
    rec = audit.log_event("tool_error", ws=workspace, code="Alex Example, alex@example.test")
    assert rec["code"] == "<redacted>"
    raw = (workspace.monitoring_dir / "audit.jsonl").read_text()
    assert "Alex" not in raw and "example.test" not in raw


def test_long_strings_truncated_and_nested_dropped(workspace):
    rec = audit.log_event("tool_error", ws=workspace, code="A" * 500, error_class={"nested": "x"},
                          rule_ids=[f"R{i}" for i in range(30)] + [{"x": 1}], duration_ms=float("nan"))
    assert rec["code"] == "A" * 120
    assert "error_class" not in rec
    assert rec["rule_ids"] == [f"R{i}" for i in range(20)]
    assert "duration_ms" not in rec


def test_rule_ids_items_sanitized(workspace):
    rec = audit.log_event("release_blocked", ws=workspace, rule_ids=["FMT.MARGIN", "free text, with comma"])
    assert rec["rule_ids"] == ["FMT.MARGIN", "<redacted>"]


def test_bad_category_and_severity_dropped(workspace):
    rec = audit.log_event("tool_error", ws=workspace, category="NOT_A_CATEGORY", severity="panic", code="X")
    assert "category" not in rec and "severity" not in rec
    rec = audit.log_event("tool_error", ws=workspace, category="FACTUAL", severity="critical")
    assert rec["category"] == "FACTUAL" and rec["severity"] == "critical"


def test_unknown_event_raises(workspace):
    with pytest.raises(ValueError):
        audit.log_event("candidate_scored", ws=workspace)
    with pytest.raises(ValueError):
        audit.log_error("nope", ws=workspace)
    assert _lines(workspace) == []


def test_no_workspace_returns_none(rt_home):
    assert audit.log_event("workflow_started", workflow_id="wf-1") is None
    assert audit.log_error("internal_error", error_class="KeyError") is None


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_unwritable_monitoring_dir_returns_none(workspace):
    mon = workspace.monitoring_dir
    original = stat.S_IMODE(mon.stat().st_mode)
    mon.chmod(0o500)
    try:
        assert audit.log_event("workflow_started", ws=workspace, workflow_id="wf-1") is None
        assert audit.log_error("internal_error", ws=workspace) is None
    finally:
        mon.chmod(original)
    assert audit.log_event("workflow_started", ws=workspace, workflow_id="wf-1") is not None


def test_log_error_writes_both_files(workspace):
    rec = audit.log_error("internal_error", ws=workspace, tool="tailor_resume", error_class="KeyError",
                          category="WORKFLOW", severity="critical")
    assert rec is not None
    assert [json.loads(line) for line in _lines(workspace)] == [rec]
    assert [json.loads(line) for line in _lines(workspace, "errors.jsonl")] == [rec]
    assert audit.read_events(workspace, errors=True) == [rec]
    audit.log_event("workflow_started", ws=workspace)
    assert len(audit.read_events(workspace)) == 2
    assert len(audit.read_events(workspace, errors=True)) == 1


def test_concurrent_appends_are_intact(workspace):
    def worker(t):
        for i in range(50):
            assert audit.log_event("evidence_saved", ws=workspace, workflow_id=f"wf-{t}",
                                   evidence_id=f"ev-{t}-{i}", patch_count=i) is not None

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    lines = _lines(workspace)
    assert len(lines) == 400
    records = [json.loads(line) for line in lines]
    assert len({r["evidence_id"] for r in records}) == 400


def test_read_events_skips_malformed_lines(workspace):
    audit.log_event("workflow_started", ws=workspace, workflow_id="wf-1")
    with open(workspace.monitoring_dir / "audit.jsonl", "a") as f:
        f.write("{not json\n\n")
    audit.log_event("workflow_started", ws=workspace, workflow_id="wf-2")
    assert [e["workflow_id"] for e in audit.read_events(workspace)] == ["wf-1", "wf-2"]
