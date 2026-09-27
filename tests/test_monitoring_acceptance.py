"""Phase 5 acceptance: audit / monitoring core, end to end.

[ ] each relevant mutation has workflow_id   [ ] events are recorded
[ ] logs avoid unnecessary PII               [ ] failed operations are classified
[ ] metrics can be rebuilt from audit history
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import server
from lib import storage

REPO = Path(__file__).resolve().parent.parent
HAS_TECTONIC = (REPO / "bin" / "tectonic").exists() or bool(shutil.which("tectonic"))
JD = "Backend Engineer\nRequirements:\n- Python\n- FastAPI\n- AWS\nWe value a calm sentinel-jd-sentence culture.\n"
EVIDENCE_TEXT = "Built REST APIs using FastAPI for my personal sentinel-evidence project."


def _events(ws, errors=False):
    path = ws.monitoring_dir / ("errors.jsonl" if errors else "audit.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _flow(release: bool):
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    ev = server.save_tailoring_evidence(wf, "FastAPI", "personal_project", EVIDENCE_TEXT, confirmed=True)
    ev_id = ev["evidence"]["id"]
    out = server.tailor_resume("acme", [{"operation": "add_skill_item", "category": "Frameworks", "name": "FastAPI",
                                         "source_refs": [{"type": "evidence", "id": ev_id}]}], wf,
                               evidence_ids=[ev_id])
    assert out["ok"], out
    if release:
        assert server.release_resume(out["version_id"], wf)["released"]
        server.export_resume(out["version_id"], wf)
    return wf, out["version_id"]


@pytest.mark.skipif(not HAS_TECTONIC, reason="tectonic not available")
def test_workflow_events_recorded_with_workflow_id(master):
    ws, _, _ = master
    wf, vid = _flow(release=True)
    events = _events(ws)
    names = [e["event"] for e in events]
    for expected in ("workflow_started", "requirements_analyzed", "evidence_saved", "tailor_succeeded",
                     "validation_completed", "release_succeeded", "export_completed"):
        assert expected in names, expected
    for e in events:
        if e["event"] in ("workflow_started", "requirements_analyzed", "evidence_saved", "tailor_succeeded",
                          "validation_completed", "release_succeeded"):
            assert e["workflow_id"] == wf, e
        assert e["timestamp"].endswith("Z") and e["workspace_id"] == ws.id


@pytest.mark.skipif(not HAS_TECTONIC, reason="tectonic not available")
def test_logs_contain_no_pii_or_content(master):
    ws, doc, _ = master
    _flow(release=True)
    server.tailor_resume("bad", [{"operation": "replace_block", "target": {"id": "exp-001-b01"}, "new_content": {
        "text": "Invented sentinel-patch-text claim with 99% uplift.",
        "source_refs": [{"type": "master", "id": "exp-001-b01"}]}}], "wf-20990101-000000")
    blob = "".join(p.read_text() for p in ws.monitoring_dir.glob("*.json*"))
    for secret in (doc["name"], doc["contact"]["email"], doc["contact"]["phone"], "sentinel-jd-sentence",
                   "sentinel-evidence", "sentinel-patch-text", doc["summary"][:30],
                   doc["experience"][0]["bullets"][0]["text"][:30]):
        assert secret not in blob, secret


def test_failed_operations_are_classified(master):
    ws, _, _ = master
    wf = server.analyze_tailoring_requirements(JD)["workflow_id"]
    server.tailor_resume("bad", [{"operation": "replace_block", "target": {"id": "exp-002-b01"}, "new_content": {
        "text": "Implemented unit tests with pytest, raising coverage 95%.",
        "source_refs": [{"type": "master", "id": "exp-002-b01"}]}}], wf)
    server.save_tailoring_evidence(wf, "AWS", "professional", "x", confirmed=False)
    errors = _events(ws, errors=True)
    rejected = [e for e in errors if e["event"] == "tailor_rejected"]
    assert rejected and rejected[0]["category"] == "FACTUAL" and rejected[0]["workflow_id"] == wf
    assert "provenance.unsupported_metric" in rejected[0]["rule_ids"]
    tool_errors = [e for e in errors if e["event"] == "tool_error"]
    assert tool_errors[0]["code"] == "EVIDENCE_INVALID" and tool_errors[0]["category"] == "FACTUAL"


@pytest.mark.parametrize("call", [
    lambda: server.tailor_resume("x", [], ""),
    lambda: server.save_tailoring_evidence("", "AWS", "professional", "x", confirmed=True),
    lambda: server.validate_version("acme", ""),
    lambda: server.release_resume("acme", ""),
    lambda: server.export_resume("acme", ""),
])
def test_workflow_id_required_for_mutations(master, call):
    assert call()["error"]["code"] == "WORKFLOW_REQUIRED"


@pytest.mark.skipif(not HAS_TECTONIC, reason="tectonic not available")
def test_metrics_rebuild_from_audit_history(master):
    ws, _, _ = master
    wf, _ = _flow(release=True)
    status = server.get_workflow_status()
    metrics = status["metrics"]
    assert metrics["workflow_count"] == 1 and metrics["successful_workflows"] == 1
    assert metrics["evidence_request_count"] >= 1
    (ws.monitoring_dir / "metrics.json").unlink()
    again = server.get_workflow_status()["metrics"]
    assert {k: v for k, v in again.items() if k != "generated_at"} == \
           {k: v for k, v in metrics.items() if k != "generated_at"}
    one = server.get_workflow_status(wf)
    assert one["status"] == "released" and one["versions"][0]["released"]
