"""Workspace + master resolution: the one place that answers "whose data is
this, and is it usable?" -- the first call of every session that touches a
resume (R-USER-01..13; docs/spec-map.md workspace/evidence entries).

Identity comes only from the local binding in config.yaml, resolved fresh on
every call by delegating to `lib.workspace`'s raise-based public API and
translating its error codes into a reported *state* instead of an exception.
Nothing here searches the filesystem for a resume, reads another workspace,
accepts an identity from a caller argument, or infers a person from a
previous tailored version or from conversation history. Where the answer is
"unknown" or "not ready", this module reports that state so the caller (the
MCP tool layer, and Claude above it) can ask the user -- never substitute
memory, chat history or a prior chat's resume for the master.

`lib.workspace` must not import this module (it would cycle back through
`lib.storage`, which this module needs); the dependency only runs this way.
"""

from __future__ import annotations

from pydantic import ValidationError

from lib import storage
from lib import workspace as _workspace
from lib.errors import ResumeTailorError
from lib.schemas import MasterDocument, validate_kind
from lib.workspace import (
    MAX_WORKSPACES_LISTED,
    NO_WORKSPACE,
    RESOLUTION_STATES,
    WORKSPACE_FOUND,
    WORKSPACE_INVALID,
    WORKSPACE_NEEDS_SETUP,
    Workspace,
)

REFUSE_TEXT = (
    "Do not reconstruct the user's resume from memory, from this conversation, from a previous "
    "tailored version, or from another workspace. The only sources of truth are the current "
    "workspace's master and evidence the user explicitly confirms in this workflow."
)


# --------------------------------------------------------------------------
# Workspace binding
# --------------------------------------------------------------------------

def _binding_state() -> dict:
    """{"state", "reason", "workspace_id", "root", "available_workspace_ids"},
    plus "_ws" (the resolved Workspace) when state is None (bound and
    present). Never raises: every code path lib.workspace.get_workspace can
    raise is translated here."""
    try:
        ws = _workspace.get_workspace()
    except ResumeTailorError as e:
        details = e.details or {}
        available = details.get("available_workspace_ids") or []
        if e.code == "WORKSPACE_AMBIGUOUS":
            return {"state": WORKSPACE_INVALID, "reason": "no_active_binding", "workspace_id": None,
                    "root": None, "available_workspace_ids": available[:MAX_WORKSPACES_LISTED]}
        if e.code == "WORKSPACE_NOT_INITIALIZED":
            if "workspace_id" in details:  # bound, but the directory is gone
                return {"state": WORKSPACE_INVALID, "reason": "missing_workspace_dir",
                        "workspace_id": details.get("workspace_id"), "root": None,
                        "available_workspace_ids": available[:MAX_WORKSPACES_LISTED]}
            return {"state": NO_WORKSPACE, "reason": None, "workspace_id": None, "root": None,
                    "available_workspace_ids": []}
        if e.code in ("INVALID_ID", "PATH_TRAVERSAL"):
            return {"state": WORKSPACE_INVALID, "reason": "invalid_workspace_id", "workspace_id": None,
                    "root": None, "available_workspace_ids": []}
        if e.code in ("YAML_UNSAFE", "YAML_TOO_LARGE", "YAML_TOO_DEEP"):
            return {"state": WORKSPACE_INVALID, "reason": "unreadable_config", "workspace_id": None,
                    "root": None, "available_workspace_ids": []}
        raise  # a code this function doesn't know: a programming error, surface it
    return {"state": None, "reason": None, "workspace_id": ws.id, "root": str(ws.root),
            "available_workspace_ids": [], "_ws": ws}


# --------------------------------------------------------------------------
# Master status
# --------------------------------------------------------------------------

def master_status(ws: Workspace, kind: str) -> dict:
    """{"present", "valid", "ready", "master_hash", "career_stage",
    "unparsed_items", "imported", "invalid_reason"}. Schema-validates
    without repairing; an invalid master is reported, never rewritten."""
    validate_kind(kind)
    empty = {"present": False, "valid": False, "ready": False, "master_hash": None,
             "career_stage": None, "unparsed_items": 0, "imported": None, "invalid_reason": None}
    try:
        doc, h = storage.load_master(kind, ws)
    except ResumeTailorError:
        # the file exists but is not even loadable YAML -- report it as an
        # invalid master, never let a malformed file raise out of a status
        # check that is meant to be safe to call at any time.
        path = ws.master_path(kind)
        if not path.exists():
            return empty
        return {**empty, "present": True, "invalid_reason": "unreadable_file"}
    if doc is None:
        return empty

    try:
        model = MasterDocument.model_validate(doc)
    except ValidationError:
        return {**empty, "present": True, "master_hash": h, "invalid_reason": "schema_validation_failed"}
    if model.metadata.kind != kind:
        return {**empty, "present": True, "master_hash": h, "invalid_reason": "kind_mismatch"}

    from lib.master_ops import master_readiness
    readiness = master_readiness(doc)
    imported = model.metadata.imported.model_dump(mode="json") if model.metadata.imported else None
    return {
        "present": True, "valid": True, "ready": readiness["ready"], "master_hash": h,
        "career_stage": model.metadata.career_stage, "unparsed_items": readiness["unparsed_items"],
        "imported": imported, "invalid_reason": None,
    }


def require_tailorable_master(kind: str, ws: Workspace | None = None) -> tuple[dict, str]:
    """storage.require_master plus the readiness gate, so that
    analyze_tailoring_requirements and tailor_resume fail identically. Before
    this, only tailor_resume checked readiness, so an unparsed master was
    caught only AFTER the user had answered every evidence prompt."""
    ws = ws or _workspace.get_workspace()
    doc, h = storage.require_master(kind, ws)
    from lib.master_ops import master_readiness
    readiness = master_readiness(doc)
    if not readiness["ready"]:
        raise ResumeTailorError(
            "MASTER_INVALID",
            "The master still has unparsed content. Place it into the proper sections (update the "
            "master), or have the user explicitly accept leaving it out, before tailoring.",
            details=readiness,
        )
    return doc, h


# --------------------------------------------------------------------------
# Combined state
# --------------------------------------------------------------------------

def _next_step(state: str, available: list[str]) -> str:
    if state == NO_WORKSPACE:
        return ("No workspace on this machine. Ask the user for the path of ONE folder that "
                "contains their existing resume or CV, then call discover_masters(folder). If they "
                "have no file, run the create-master-file skill. " + REFUSE_TEXT)
    if state == WORKSPACE_INVALID:
        if available:
            return ("Workspace(s) exist on this machine but none is bound (or the binding is "
                    "broken). Call list_workspaces, show the user the options, and only after they "
                    "say which one is theirs call select_workspace(workspace_id). Never guess, and "
                    "never call initialize_workspace to 'fix' this -- that would create a new, "
                    "empty workspace and orphan the existing one(s).")
        return ("The workspace binding is broken (invalid or unreadable config). Ask the user how "
                "they would like to proceed; do not silently create a new workspace.")
    if state == WORKSPACE_NEEDS_SETUP:
        return ("This workspace has no usable master yet. Ask the user for the path of ONE folder "
                "that contains their existing resume or CV, then call discover_masters(folder), or "
                "run the create-master-file skill if they have no file. " + REFUSE_TEXT)
    return "Workspace and master are ready. Proceed with analyze_tailoring_requirements."


def resolve_workspace_state() -> dict:
    """Never raises for a missing, ambiguous or broken binding -- it reports
    the state so the caller can ASK the user instead of repairing.

    {"state": one of RESOLUTION_STATES, "reason": str | None,
     "workspace_id": str | None, "root": str | None,
     "available_workspace_ids": [str],
     "masters": {"resume": <master_status>|None, "cv": <master_status>|None},
     "next_step": str, "refuse": str}
    """
    binding = _binding_state()

    if binding["state"] is not None:
        return {
            "state": binding["state"],
            "reason": binding["reason"],
            "workspace_id": binding["workspace_id"],
            "root": binding["root"],
            "available_workspace_ids": binding["available_workspace_ids"],
            "masters": {"resume": None, "cv": None},
            "next_step": _next_step(binding["state"], binding["available_workspace_ids"]),
            "refuse": REFUSE_TEXT,
        }

    ws = binding["_ws"]
    masters = {kind: master_status(ws, kind) for kind in ("resume", "cv")}
    any_present = masters["resume"]["present"] or masters["cv"]["present"]
    any_valid = masters["resume"]["valid"] or masters["cv"]["valid"]

    if not any_present:
        state, reason = WORKSPACE_NEEDS_SETUP, "no_master"
    elif not any_valid:
        state, reason = WORKSPACE_NEEDS_SETUP, "master_invalid"
    else:
        state, reason = WORKSPACE_FOUND, None

    return {
        "state": state,
        "reason": reason,
        "workspace_id": ws.id,
        "root": str(ws.root),
        "available_workspace_ids": [],
        "masters": masters,
        "next_step": _next_step(state, []),
        "refuse": REFUSE_TEXT,
    }
