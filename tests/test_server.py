"""Server-level checks: tools go through the workspace and share one error model."""

from __future__ import annotations

import server


def test_tools_require_workspace(rt_home):
    result = server.get_master_resume("resume")
    assert result["ok"] is False
    assert result["error"]["code"] == "WORKSPACE_NOT_INITIALIZED"


def test_initialize_then_get_workspace(rt_home):
    created = server.initialize_workspace()
    assert created["ok"] and created["created"]
    again = server.get_workspace()
    assert again["workspace_id"] == created["workspace_id"]
    assert str(rt_home) in again["root"]


def test_invalid_kind_is_structured_error(workspace):
    result = server.get_master_resume("portfolio")
    assert result == {**result, "ok": False}
    assert result["error"]["code"] == "INVALID_KIND"


def test_get_master_returns_hash(master):
    ws, doc, h = master
    result = server.get_master_resume("resume")
    assert result["ok"] and result["master_hash"] == h
    assert result["master"]["experience"][0]["id"] == "exp-001"


def test_cv_does_not_fall_back_to_resume(master):
    result = server.get_master_resume("cv")
    assert result["error"]["code"] == "MASTER_NOT_FOUND"


def test_unexpected_exception_is_sanitized(workspace, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("/Users/secret/path alex.example@example.test")
    monkeypatch.setattr(server.storage, "require_master", boom)
    result = server.get_master_resume("resume")
    assert result["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in str(result) and "example.test" not in str(result)


def test_read_only_tools_work_on_normalized_master(master):
    jd = "Requirements: Python, Kubernetes, AWS"
    match = server.match_resume_to_jd(jd, "master")
    assert "python" in match["matched"]
    assert "kubernetes" in match["missing"]
    assert "score" in server.score_ats("master")


def test_version_path_traversal_rejected(master):
    result = server.score_ats("../../etc/passwd")
    assert result["error"]["code"] == "INVALID_ID"
