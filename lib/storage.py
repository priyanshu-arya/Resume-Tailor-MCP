"""Workspace-backed storage for masters, versions and saved JDs.

Everything is YAML so it stays human-readable and diffable. All reads go
through `yaml_load_file` (safe_load + size and nesting limits); all writes
are atomic. Master writes are guarded three ways (spec §8, §63):

1. the per-workspace lock,
2. an optimistic hash check -- the caller passes the hash it read, and the
   write aborts with MASTER_CONFLICT if the file changed since,
3. a backup of the previous master before it is replaced.

Versions are write-once: saving an existing version ID fails with
VERSION_EXISTS, and a released version can never be rewritten.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from lib import safe_yaml
from lib.errors import ResumeTailorError
from lib.locking import atomic_write_yaml, sha256_of, workspace_lock, yaml_dump
from lib.schemas import MasterDocument, TailoredVersion, validate_kind
from lib.workspace import Workspace, get_workspace, safe_child, utc_now_iso

MAX_YAML_BYTES = safe_yaml.MAX_YAML_BYTES
MAX_YAML_DEPTH = safe_yaml.MAX_YAML_DEPTH

# Names that refer to a master when a "version" argument is accepted for a
# read-only operation (export/validate a master). Masters are never a
# tailoring *source* through this path -- tailoring loads them via
# load_master() only.
MASTER_ALIASES = {
    "": "resume",
    "master": "resume",
    "master-resume": "resume",
    "master_resume": "resume",
    "master-cv": "cv",
    "master_cv": "cv",
}


def resolve_master_alias(name: str | None) -> str | None:
    """Return the master kind if `name` is a master alias, else None."""
    return MASTER_ALIASES.get((name or "").strip())


# --------------------------------------------------------------------------
# Safe YAML
# --------------------------------------------------------------------------

def _depth(obj, level: int = 1) -> int:
    if level > MAX_YAML_DEPTH:
        return level
    if isinstance(obj, dict):
        return max([level] + [_depth(v, level + 1) for v in obj.values()])
    if isinstance(obj, list):
        return max([level] + [_depth(v, level + 1) for v in obj])
    return level


def check_structure(data) -> None:
    if _depth(data) > MAX_YAML_DEPTH:
        raise ResumeTailorError("YAML_TOO_DEEP", f"Document nesting exceeds {MAX_YAML_DEPTH} levels.")


def yaml_load_text(text: str):
    data = safe_yaml.load_text(text)
    check_structure(data)
    return data


def yaml_load_file(path: Path):
    data = safe_yaml.load_file(path)
    if data is not None:
        check_structure(data)
    return data


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

def master_hash(doc: dict | None) -> str | None:
    return None if doc is None else sha256_of(doc)


def load_master(kind: str = "resume", ws: Workspace | None = None) -> tuple[dict | None, str | None]:
    """Return (master_dict, master_hash); (None, None) if no master yet."""
    validate_kind(kind)
    ws = ws or get_workspace()
    doc = yaml_load_file(ws.master_path(kind))
    return doc, master_hash(doc)


def require_master(kind: str = "resume", ws: Workspace | None = None) -> tuple[dict, str]:
    doc, h = load_master(kind, ws)
    if doc is None:
        ws = ws or get_workspace()
        raise ResumeTailorError(
            "MASTER_NOT_FOUND",
            f"No master {kind} in this workspace ({ws.id}). Tailoring cannot proceed: the master "
            f"is the only source of truth. Do NOT reconstruct it from memory, from this "
            f"conversation, from a previous tailored version, or from another workspace. Ask the "
            f"user for one folder holding their existing {kind} and call "
            f"discover_masters(folder), or build one with the create-master-file skill. If their "
            f"data may be in a different workspace, call list_workspaces and ask -- never guess.",
            details={"kind": kind, "workspace_id": ws.id},
        )
    return doc, h


def validate_master_doc(doc: dict, kind: str) -> None:
    try:
        model = MasterDocument.model_validate(doc)
    except ValidationError as e:
        raise ResumeTailorError("MASTER_INVALID", "Master does not match the master schema.",
                                details={"errors": _error_summary(e)}) from None
    if model.metadata.kind != kind:
        raise ResumeTailorError("MASTER_INVALID", f"metadata.kind is {model.metadata.kind!r}, expected {kind!r}.")
    check_structure(doc)


def save_master(kind: str, doc: dict, expected_hash: str | None, reason: str,
                ws: Workspace | None = None) -> str:
    """Atomically write a master. `expected_hash` must be the hash of the
    master the caller read (None = "I expect no master to exist").
    Returns the new master hash."""
    validate_kind(kind)
    ws = ws or get_workspace()
    validate_master_doc(doc, kind)
    path = ws.master_path(kind)
    with workspace_lock(ws):
        current, current_hash = load_master(kind, ws)
        if current is not None and expected_hash is None:
            raise ResumeTailorError("MASTER_EXISTS", f"A master {kind} already exists; pass its hash to replace it.",
                                    details={"current_hash": current_hash})
        if current_hash != expected_hash:
            raise ResumeTailorError("MASTER_CONFLICT", f"The master {kind} changed since it was read.",
                                    details={"expected_hash": expected_hash, "current_hash": current_hash})
        if current is not None:
            stamp = utc_now_iso().replace(":", "").replace("-", "")
            backup = ws.backups_dir / f"{kind}-{stamp}-{current_hash[:8]}.yaml"
            atomic_write_yaml(backup, current)
        atomic_write_yaml(path, doc)
        new_hash = master_hash(doc)
        history_path = ws.master_dir / "history.yaml"
        history = yaml_load_file(history_path) or []
        entry = {"at": utc_now_iso(), "kind": kind, "reason": reason,
                "previous_hash": current_hash, "new_hash": new_hash}
        imported = (doc.get("metadata") or {}).get("imported")
        if isinstance(imported, dict):
            # the import TIME lives here, not in metadata.imported itself --
            # metadata is inside sha256_of(doc), so a timestamp there would
            # make the preview/confirm protocol's proposed_hash unmatchable
            # on every confirm (see docs/spec-map.md D6 / lib.discovery).
            entry["source_filename"] = imported.get("source_filename")
            entry["source_folder_name"] = imported.get("source_folder_name")
            entry["source_folder_hash"] = imported.get("source_folder_hash")
            entry["source_hash"] = imported.get("source_hash")
        history.append(entry)
        atomic_write_yaml(history_path, history)
    return new_hash


def read_master_history(kind: str = "resume", limit: int = 20, ws: Workspace | None = None) -> list[dict]:
    """Newest-first write history for one master kind: when it changed, why,
    the hash before and after, and -- for imports -- the source filename,
    folder name/hash and file hash. Contains no resume content, so it is
    safe to show the user verbatim (R-USER-11).

    Read-only and lock-free by design: save_master writes history.yaml from
    INSIDE workspace_lock, so this must never try to take that lock too."""
    validate_kind(kind)
    ws = ws or get_workspace()
    limit = max(1, min(int(limit or 20), 500))
    history_path = ws.master_dir / "history.yaml"
    history = yaml_load_file(history_path)
    if not isinstance(history, list):
        return []
    rows = [h for h in history if isinstance(h, dict) and h.get("kind") == kind]
    rows.reverse()
    return rows[:limit]


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

def version_path(version_id: str, ws: Workspace | None = None) -> Path:
    ws = ws or get_workspace()
    return safe_child(ws.versions_dir, version_id, ".yaml")


def version_exists(version_id: str, ws: Workspace | None = None) -> bool:
    return version_path(version_id, ws).exists()


def load_version(version_id: str, ws: Workspace | None = None) -> dict | None:
    """Load a saved tailored version. Master aliases are NOT resolved here."""
    return yaml_load_file(version_path(version_id, ws))


def require_version(version_id: str, ws: Workspace | None = None) -> dict:
    doc = load_version(version_id, ws)
    if doc is None:
        raise ResumeTailorError("VERSION_NOT_FOUND", f"No saved version {version_id!r}.",
                                details={"version_id": version_id})
    return doc


def save_version(version_id: str, doc: dict, ws: Workspace | None = None, *, validate: bool = True) -> Path:
    """Write-once save. Fails with VERSION_EXISTS if the ID is taken."""
    ws = ws or get_workspace()
    path = version_path(version_id, ws)
    if validate:
        try:
            TailoredVersion.model_validate(doc)
        except ValidationError as e:
            raise ResumeTailorError("PATCH_INVALID", "Version does not match the version schema.",
                                    details={"errors": _error_summary(e)}) from None
    check_structure(doc)
    with workspace_lock(ws):
        if path.exists():
            existing = yaml_load_file(path) or {}
            if (existing.get("metadata") or {}).get("released"):
                raise ResumeTailorError("VERSION_RELEASED", f"Version {version_id!r} is released and immutable; "
                                        "create a new version instead.")
            raise ResumeTailorError("VERSION_EXISTS", f"Version {version_id!r} already exists; choose a new ID.")
        atomic_write_yaml(path, doc, exclusive=True)
    return path


def update_version_metadata(version_id: str, updates: dict, ws: Workspace | None = None) -> dict:
    """Metadata-only update for a DRAFT version (e.g. marking it released).
    Content is never changed through this path; released versions are frozen."""
    ws = ws or get_workspace()
    path = version_path(version_id, ws)
    with workspace_lock(ws):
        doc = require_version(version_id, ws)
        meta = doc.setdefault("metadata", {})
        if meta.get("released"):
            raise ResumeTailorError("VERSION_RELEASED", f"Version {version_id!r} is released and immutable.")
        frozen = {"version_id", "workspace_id", "document_kind", "source_master_hash", "workflow_id"} & set(updates)
        if frozen:
            raise ResumeTailorError("PATCH_INVALID", "Identity fields of a version cannot be changed.",
                                    details={"fields": sorted(frozen)})
        meta.update(updates)
        if not meta.get("legacy"):
            try:
                TailoredVersion.model_validate(doc)
            except ValidationError as e:
                raise ResumeTailorError("PATCH_INVALID", "Metadata update breaks the version schema.",
                                        details={"errors": _error_summary(e)}) from None
        atomic_write_yaml(path, doc)
    return doc


def list_version_ids(ws: Workspace | None = None) -> list[str]:
    ws = ws or get_workspace()
    if not ws.versions_dir.exists():
        return []
    return sorted(p.stem for p in ws.versions_dir.glob("*.yaml"))


# --------------------------------------------------------------------------
# Saved JDs
# --------------------------------------------------------------------------

def jd_path(jd_id: str, ws: Workspace | None = None) -> Path:
    ws = ws or get_workspace()
    return safe_child(ws.jd_dir, jd_id, ".yaml")


def save_jd(jd_id: str, jd_text: str, extracted: dict | None = None, ws: Workspace | None = None) -> Path:
    ws = ws or get_workspace()
    path = jd_path(jd_id, ws)
    with workspace_lock(ws):
        atomic_write_yaml(path, {"jd_text": jd_text, "extracted": extracted or {}})
    return path


def load_jd(jd_id: str, ws: Workspace | None = None):
    return yaml_load_file(jd_path(jd_id, ws))


def list_jd_ids(ws: Workspace | None = None) -> list[str]:
    ws = ws or get_workspace()
    if not ws.jd_dir.exists():
        return []
    return sorted(p.stem for p in ws.jd_dir.glob("*.yaml"))


def dump_yaml(data) -> str:
    return yaml_dump(data)


def _error_summary(e: ValidationError, limit: int = 10) -> list[dict]:
    # Field locations and messages only -- never the offending input values,
    # which may contain personal data.
    return [{"loc": ".".join(str(p) for p in err["loc"]), "msg": err["msg"]} for err in e.errors()[:limit]]
