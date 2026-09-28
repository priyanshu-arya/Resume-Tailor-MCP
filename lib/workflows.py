"""Tailoring workflow sessions (spec §48).

A workflow groups everything one tailoring run produces -- the JD, the
evidence the user confirmed, the versions created, validation/release
state and the repair counter -- under one ID. Evidence is only usable
inside the workflow it was confirmed in, so a workflow is also the
boundary that stops one job's answers leaking into another.

Sessions live at data/tailoring_sessions/<workflow_id>.yaml. The JD text
itself is stored once under data/jd_history/<workflow_id>.yaml; the session
keeps only its hash.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from pydantic import ValidationError

from lib import storage
from lib.errors import ResumeTailorError
from lib.locking import atomic_write_yaml, sha256_text, workspace_lock
from lib.schemas import WorkflowMetadata, validate_kind
from lib.workspace import Workspace, get_workspace, safe_child, utc_now_iso

_WORKFLOW_RE = re.compile(r"wf-\d{8}-[0-9a-f]{6}")
MAX_REPAIR_ATTEMPTS = 3


def new_workflow_id() -> str:
    return f"wf-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3)}"


def check_workflow_id(workflow_id: str | None) -> str:
    if not workflow_id:
        raise ResumeTailorError("WORKFLOW_REQUIRED", "workflow_id is required -- start one with "
                                "analyze_tailoring_requirements.")
    if not isinstance(workflow_id, str) or not _WORKFLOW_RE.fullmatch(workflow_id):
        raise ResumeTailorError("INVALID_ID", "workflow_id must look like wf-YYYYMMDD-xxxxxx.")
    return workflow_id


def _path(ws: Workspace, workflow_id: str):
    return safe_child(ws.sessions_dir, check_workflow_id(workflow_id), ".yaml")


def create_workflow(source_kind: str, jd_text: str | None = None, extracted: dict | None = None,
                    ws: Workspace | None = None) -> dict:
    ws = ws or get_workspace()
    validate_kind(source_kind)
    workflow_id = new_workflow_id()
    record = WorkflowMetadata(
        workflow_id=workflow_id,
        workspace_id=ws.id,
        source_kind=source_kind,
        created_at=utc_now_iso(),
    )
    if jd_text is not None:
        storage.save_jd(workflow_id, jd_text, extracted, ws=ws)
        record.jd_id = workflow_id
        record.jd_sha256 = sha256_text(jd_text)
    with workspace_lock(ws):
        atomic_write_yaml(_path(ws, workflow_id), record.model_dump(mode="json"), exclusive=True)
    return record.model_dump(mode="json")


def load_workflow(workflow_id: str, ws: Workspace | None = None) -> dict:
    ws = ws or get_workspace()
    data = storage.yaml_load_file(_path(ws, workflow_id))
    if data is None:
        raise ResumeTailorError("WORKFLOW_NOT_FOUND", f"No workflow {workflow_id!r} in this workspace.",
                                details={"workflow_id": workflow_id})
    if data.get("workspace_id") != ws.id:
        raise ResumeTailorError("WORKFLOW_NOT_FOUND", "Workflow belongs to a different workspace.")
    return data


def update_workflow(workflow_id: str, *, set_fields: dict | None = None, append: dict | None = None,
                    ws: Workspace | None = None) -> dict:
    """Read-modify-write under the workspace lock. `append` adds to list
    fields without duplicates."""
    ws = ws or get_workspace()
    with workspace_lock(ws):
        data = load_workflow(workflow_id, ws)
        data.update(set_fields or {})
        for key, values in (append or {}).items():
            current = list(data.get(key) or [])
            for v in values:
                if v not in current:
                    current.append(v)
            data[key] = current
        try:
            WorkflowMetadata.model_validate(data)
        except ValidationError as e:
            raise ResumeTailorError("WORKFLOW_NOT_FOUND", "Workflow record is invalid.",
                                    details={"errors": [err["msg"] for err in e.errors()[:5]]}) from None
        atomic_write_yaml(_path(ws, workflow_id), data)
    return data


def list_workflow_ids(ws: Workspace | None = None) -> list[str]:
    ws = ws or get_workspace()
    return sorted(p.stem for p in ws.sessions_dir.glob("wf-*.yaml")) if ws.sessions_dir.exists() else []
