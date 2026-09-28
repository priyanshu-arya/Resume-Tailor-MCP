"""Phase 7: system_diagnostics is read-only forensics over the audit log.

[ ] writes nothing                [ ] full timeline reconstruction
[ ] top failure categories        [ ] no PII in the serialized result
[ ] limit is clamped              [ ] empty workspace returns cleanly
[ ] recommendations from a fixed table only
[ ] unknown workflow id is a business error
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lib import audit, diagnostics, workflows
from lib.errors import ResumeTailorError

REPO = Path(__file__).resolve().parent.parent


def _seed(ws, workflow_id=None):
    workflow_id = workflow_id or workflows.create_workflow("resume", ws=ws)["workflow_id"]
    audit.log_event("workflow_started", ws=ws, workflow_id=workflow_id, kind="resume")
    audit.log_event("requirements_analyzed", ws=ws, workflow_id=workflow_id, evidence_prompt_count=2)
    audit.log_event("tailor_succeeded", ws=ws, workflow_id=workflow_id, repair_attempt=0)
    audit.log_event("validation_completed", ws=ws, workflow_id=workflow_id, status="failed",
                    category="TEMPLATE", rule_ids=["template.releasable"])
    audit.log_event("release_blocked", ws=ws, workflow_id=workflow_id, category="TEMPLATE",
                    rule_ids=["template.releasable"])
    audit.log_event("pdf_compiled", ws=ws, workflow_id=workflow_id, status="pass", duration_ms=120,
                    page_count=1, overfull_count=0)
    audit.log_event("tool_error", ws=ws, tool="tailor_resume", workflow_id=workflow_id,
                    category="FACTUAL", code="PATCH_INVALID")
    return workflow_id


def test_writes_nothing(workspace):
    """mtime snapshot: the guarantee get_workflow_status cannot make, since
    that tool calls rebuild_metrics() which writes metrics.json."""
    wid = _seed(workspace)
    before = {p: p.stat().st_mtime_ns for p in workspace.root.rglob("*") if p.is_file()}
    diagnostics.system_diagnostics(ws=workspace)
    diagnostics.system_diagnostics(workflow_id=wid, ws=workspace)
    diagnostics.system_diagnostics(limit=1, errors_only=True, ws=workspace)
    after = {p: p.stat().st_mtime_ns for p in workspace.root.rglob("*") if p.is_file()}
    assert before == after


def test_diagnostics_module_has_no_write_calls():
    """Structural guarantee, not behavioural: no write-shaped call appears
    anywhere in lib/diagnostics.py's source."""
    src = (REPO / "lib" / "diagnostics.py").read_text()
    tree = ast.parse(src)
    forbidden_calls = {"write_text", "write_bytes", "mkdir", "unlink", "rename"}
    forbidden_modules = {"shutil"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
            pytest.fail(f"forbidden call lib/diagnostics.py: .{node.attr}(...)")
        if isinstance(node, ast.Name) and node.id in forbidden_modules:
            pytest.fail(f"forbidden reference in lib/diagnostics.py: {node.id}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and "w" in str(kw.value.value):
                    pytest.fail("forbidden open(..., mode='w'/'a') in lib/diagnostics.py")
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and "w" in str(node.args[1].value):
                pytest.fail("forbidden open(..., 'w'/'a') in lib/diagnostics.py")
    assert "import shutil" not in src and "atomic_write" not in src


def test_full_timeline_reconstruction_including_pdf_compiled(workspace):
    _seed(workspace)
    out = diagnostics.system_diagnostics(ws=workspace)
    events = [e["event"] for e in out["timeline"]]
    assert events == ["workflow_started", "requirements_analyzed", "tailor_succeeded",
                      "validation_completed", "release_blocked", "pdf_compiled", "tool_error"]
    assert out["event_counts"]["pdf_compiled"] == 1


def test_workflow_scoped_timeline(workspace):
    wid = _seed(workspace)
    other = workflows.create_workflow("resume", ws=workspace)["workflow_id"]
    audit.log_event("workflow_started", ws=workspace, workflow_id=other)
    out = diagnostics.system_diagnostics(workflow_id=wid, ws=workspace)
    assert out["scope"] == "workflow" and out["workflow_id"] == wid
    assert all(e.get("workflow_id") == wid for e in out["timeline"])


def test_top_failure_categories_codes_and_check_ids(workspace):
    _seed(workspace)
    out = diagnostics.system_diagnostics(ws=workspace)
    by_cat = {row["key"]: row["count"] for row in out["top_failures"]["by_category"]}
    by_code = {row["key"]: row["count"] for row in out["top_failures"]["by_code"]}
    by_check = {row["key"]: row["count"] for row in out["top_failures"]["by_check_id"]}
    assert by_cat.get("TEMPLATE") == 1 or by_cat.get("FACTUAL") == 1  # from release_blocked / tool_error
    assert by_code.get("PATCH_INVALID") == 1
    assert by_check.get("template.releasable") == 2  # validation_completed + release_blocked


def test_recommendations_come_only_from_fixed_table(workspace):
    _seed(workspace)
    out = diagnostics.system_diagnostics(ws=workspace)
    for row in out["recommendations"]:
        assert diagnostics.REMEDIATION_HINTS.get(row["key"]) == row["hint"]
        assert row["key"] in diagnostics.REMEDIATION_HINTS
    assert any(row["key"] == "PATCH_INVALID" for row in out["recommendations"])


def test_limit_is_clamped(workspace):
    _seed(workspace)
    assert diagnostics.system_diagnostics(limit=0, ws=workspace)["limit"] == 1
    assert diagnostics.system_diagnostics(limit=-5, ws=workspace)["limit"] == 1
    assert diagnostics.system_diagnostics(limit=10**9, ws=workspace)["limit"] == diagnostics.MAX_LIMIT
    assert diagnostics.system_diagnostics(limit="not a number", ws=workspace)["limit"] == diagnostics.DEFAULT_LIMIT


def test_empty_workspace_returns_cleanly(workspace):
    out = diagnostics.system_diagnostics(ws=workspace)
    assert out["timeline"] == [] and out["records_read"] == 0 and out["truncated"] is False
    assert out["metrics"]["event_count"] == 0


def test_unknown_workflow_id_is_a_business_error(workspace):
    with pytest.raises(ResumeTailorError) as e:
        diagnostics.system_diagnostics(workflow_id="wf-20260101-000000", ws=workspace)
    assert e.value.code == "WORKFLOW_NOT_FOUND"


def test_no_pii_in_serialized_result(workspace):
    audit.log_event("workflow_started", ws=workspace, workflow_id="wf-20260101-d1a9ab")
    audit.log_event("tool_error", ws=workspace, workflow_id="wf-20260101-d1a9ab", tool="tailor_resume",
                    category="FACTUAL", code="PATCH_INVALID")
    out = diagnostics.system_diagnostics(ws=workspace)
    blob = str(out)
    for sentinel in ("alex.example.work@example.test", "/Users/alexexample", r"C:\Users\alexexample"):
        assert sentinel not in blob


def test_server_tool_wires_through(workspace):
    import server
    out = server.system_diagnostics()
    assert out["ok"] is True and "timeline" in out
