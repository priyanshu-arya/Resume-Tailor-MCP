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


def test_read_events_is_bounded_by_bytes(workspace, monkeypatch):
    """A log past MAX_READ_BYTES reads only its tail, in order, never a
    partial-line record."""
    monkeypatch.setattr(audit, "MAX_READ_BYTES", 2000)
    for i in range(200):
        audit.log_event("workflow_started", ws=workspace, workflow_id=f"wf-{i:04d}")
    events = audit.read_events(workspace)
    assert events  # non-empty
    assert events[-1]["workflow_id"] == "wf-0199"  # tail, not head
    ids = [e["workflow_id"] for e in events]
    assert ids == sorted(ids)  # append order preserved
    path = workspace.monitoring_dir / "audit.jsonl"
    assert path.stat().st_size > 2000  # the file itself was not truncated


def test_read_events_is_bounded_by_record_count(workspace, monkeypatch):
    monkeypatch.setattr(audit, "MAX_READ_RECORDS", 5)
    for i in range(20):
        audit.log_event("workflow_started", ws=workspace, workflow_id=f"wf-{i:04d}")
    events = audit.read_events(workspace)
    assert len(events) == 5
    assert [e["workflow_id"] for e in events] == [f"wf-{i:04d}" for i in range(15, 20)]


def test_tail_events_fast_path(workspace):
    for i in range(10):
        audit.log_event("workflow_started", ws=workspace, workflow_id=f"wf-{i:04d}")
    tail = audit.tail_events(3, workspace)
    assert [e["workflow_id"] for e in tail] == ["wf-0007", "wf-0008", "wf-0009"]
    assert len(audit.tail_events(100, workspace)) == 10


# --------------------------------------------------------------------------
# Phase 1: workspace identity / discovery events
# --------------------------------------------------------------------------

def test_new_workspace_events_accepted(workspace):
    for name, kwargs in (
        ("workspace_selected", {}),
        ("workspace_switched", {"from_workspace_id": "RT-AAAAAAAA", "to_workspace_id": "RT-BBBBBBBB"}),
        ("master_discovered", {"candidate_count": 3, "entries_examined": 5}),
        ("master_imported", {"kind": "resume", "source_hash": "ab" * 32}),
        ("master_created", {"kind": "resume", "applied": True}),
        ("master_loaded", {"kind": "resume"}),
        ("master_conflict", {"code": "MASTER_CONFLICT"}),
    ):
        rec = audit.log_event(name, ws=workspace, **kwargs)
        assert rec is not None and rec["event"] == name


def test_workspace_id_is_not_whitelisted():
    assert "workspace_id" not in audit.ALLOWED_FIELDS


def test_caller_supplied_workspace_id_cannot_override_the_injected_one(workspace):
    rec = audit.log_event("workspace_selected", ws=workspace, workspace_id="RT-FORGED00")
    assert rec["workspace_id"] == workspace.id  # the injected value wins; the forged kwarg is dropped


def test_switch_logged_in_both_workspaces(two_workspaces):
    from lib import workspace as wsmod
    id_a, id_b = two_workspaces
    ws_a = wsmod._workspace_from_id(id_a)
    ws_b = wsmod._workspace_from_id(id_b)
    audit.log_event("workspace_switched", ws=ws_b, to_workspace_id=id_a)
    audit.log_event("workspace_switched", ws=ws_a, from_workspace_id=id_b)
    rec_b = _lines(ws_b)
    rec_a = _lines(ws_a)
    assert json.loads(rec_b[-1])["workspace_id"] == id_b
    assert json.loads(rec_b[-1])["to_workspace_id"] == id_a
    assert json.loads(rec_a[-1])["workspace_id"] == id_a
    assert json.loads(rec_a[-1])["from_workspace_id"] == id_b


def test_from_to_workspace_id_free_text_is_redacted(workspace):
    rec = audit.log_event("workspace_switched", ws=workspace, from_workspace_id="Alex Example, evil")
    assert rec["from_workspace_id"] == "<redacted>"


def test_discovery_event_has_no_filename_or_path(workspace):
    rec = audit.log_event(
        "master_discovered", ws=workspace, candidate_count=2, entries_examined=4,
        folder="/Users/alex/Documents/my-secret-resume-folder", filename="Alex_Resume_2026.pdf",
    )
    raw = (workspace.monitoring_dir / "audit.jsonl").read_text()
    assert "my-secret-resume-folder" not in raw
    assert "Alex_Resume_2026.pdf" not in raw
    assert "folder" not in rec and "filename" not in rec


def test_events_taxonomy_is_closed():
    assert audit.EVENTS == frozenset({
        "workspace_initialized", "workspace_selected", "workspace_switched", "legacy_migrated",
        "master_updated", "master_discovered", "master_imported", "master_created", "master_loaded",
        "master_conflict", "workflow_started", "requirements_analyzed", "evidence_saved",
        "tailor_succeeded", "tailor_rejected", "project_entry_added", "validation_completed",
        "release_succeeded", "release_blocked", "export_completed", "pdf_compiled", "tool_error",
        "internal_error",
    })


def test_allowed_fields_is_a_closed_set():
    """Change-detector by design: a new field must be a deliberate, reviewable
    test edit -- see lib/audit.py for the PII-review reasoning on each."""
    assert audit.ALLOWED_FIELDS == frozenset({
        "tool", "workflow_id", "version_id", "evidence_id", "release_report_id", "kind", "template_id",
        "mode", "format", "category", "rule_id", "rule_ids", "code", "severity", "status", "error_class",
        "patch_count", "rejection_count", "check_count", "critical_count", "warning_count",
        "template_failures", "pdf_failures", "pdf_checked", "repair_attempt", "evidence_prompt_count",
        "missing_count", "unknown_count", "migrated_count", "conflict_count", "duration_ms", "master_hash",
        "source_master_hash", "tex_sha256", "pdf_sha256", "jd_sha256", "applied",
        "from_workspace_id", "to_workspace_id", "candidate_count", "entries_examined", "source_hash",
        "workspace_count", "state", "page_count", "overfull_count",
        "evidence_category", "prompt_reason", "requirement_type", "weak_count", "supported_count",
        "new_entry_count", "rules_version",
    })


def test_no_allowed_field_can_carry_pii(workspace):
    """Sweep a PII corpus through every whitelisted field name.

    Two layers of protection, both asserted here: (1) fields with a fixed
    shape (bool/int/hex-hash) reject a free-text value outright -- it never
    reaches the log; (2) punctuation-bearing free text (a resume bullet, a JD
    sentence, a Windows path) fails `_SAFE_RE` and is redacted. What is
    *not* claimed: plain alnum text with no comma/paren/backslash (e.g. a
    bare name) can pass `_SAFE_RE` on a generic string field -- the actual
    protection there is structural, that no document-content-carrying field
    (`term`, `path`, `export_basename`, ...) is ever whitelisted, asserted
    at the bottom of this test.
    """
    punctuated_corpus = [
        "alex.example.work@example.test, personal email",
        "+1 (415) 555-0182",
        "Built a REST API serving 2M requests/day, cutting p99 latency by 40%.",
        "Senior Backend Engineer (Fintech), 5+ years Python/AWS experience.",
        r"C:\Users\alexexample\Documents\resume.docx",
        {"nested": "dict-should-be-dropped"},
        ["nested", "list-should-be-dropped"],
    ]
    typed_fields = audit._BOOL_FIELDS | audit._INT_FIELDS | audit._HASH_FIELDS | {"category", "severity"}
    for field in sorted(audit.ALLOWED_FIELDS):
        for value in punctuated_corpus:
            rec = audit.log_event("workflow_started", ws=workspace, **{field: value})
            raw = (workspace.monitoring_dir / "audit.jsonl").read_text(encoding="utf-8")
            if field in typed_fields or (field != "rule_ids" and isinstance(value, (dict, list))):
                assert rec is None or field not in rec, f"field={field!r} value={value!r} was not dropped"
            for sentinel in ("alex.example.work@example.test", "415) 555-0182", "cutting p99 latency",
                             "Fintech", r"C:\Users\alexexample"):
                assert sentinel not in raw, f"field={field!r} value={value!r} leaked {sentinel!r}"
    # No path-shaped or document-content field is whitelisted -- that, not
    # the sanitizer, is what protects a plain-alnum POSIX path or free text.
    assert "path" not in audit.ALLOWED_FIELDS and "export_basename" not in audit.ALLOWED_FIELDS
    assert "term" not in audit.ALLOWED_FIELDS and "career_stage" not in audit.ALLOWED_FIELDS


def test_pdf_compiled_event_accepted(workspace):
    rec = audit.log_event("pdf_compiled", ws=workspace, status="pass", duration_ms=340, page_count=1,
                          overfull_count=0)
    assert rec is not None
    assert rec["page_count"] == 1 and rec["overfull_count"] == 0

    rec2 = audit.log_event("pdf_compiled", ws=workspace, status="fail", duration_ms=50,
                           code="LATEX_COMPILE_FAILED", category="LATEX_PDF")
    assert rec2 is not None and rec2["code"] == "LATEX_COMPILE_FAILED"
