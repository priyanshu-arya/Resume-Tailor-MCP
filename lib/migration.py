"""Explicit, safe, idempotent migration from the v1 repository layout.

v1 kept personal data inside the repository (`resources/master_*.yaml`,
`data/versions`, `data/jd_history`, `data/exports`). v2 keeps it in a
workspace outside the repo. Moving a person's canonical master is the most
dangerous write this project makes, so migration is built around three
guarantees:

1. **Nothing is lost.** Legacy files are only ever read -- never modified or
   deleted. Before a master is written, the raw legacy bytes are copied
   verbatim into `master/legacy/` and the copy's hash is verified against
   the source; on a mismatch nothing is written.
2. **Nothing is overwritten silently.** If the workspace already has a
   master that did not come from this exact legacy file, that is a
   conflict. The caller must choose `keep_workspace` or
   `replace_with_legacy`; a replace goes through `save_master`, which backs
   up the previous master.
3. **Running it again is a no-op.** Every decision is logged in
   `monitoring/migrations.yaml` keyed by (kind, source_hash), and the
   migrated master carries `metadata.migration.source_hash`, so a rerun
   reports `already_migrated` / `already_resolved` without writing a
   record, master, backup or snapshot.

Migration never creates a workspace: `get_workspace()` raises
WORKSPACE_NOT_INITIALIZED if the user has not run initialize_workspace.
"""

from __future__ import annotations

from pathlib import Path

from lib import storage
from lib.errors import ResumeTailorError
from lib.ids import normalize_master
from lib.locking import atomic_write_bytes, atomic_write_yaml, sha256_bytes, sha256_file, workspace_lock
from lib.workspace import Workspace, get_workspace, validate_id, utc_now_iso

KINDS = ("resume", "cv")
CONFLICT_CHOICES = ("keep_workspace", "replace_with_legacy")
MIGRATION_SOURCE = "legacy_repository"
MIGRATION_REASON = "migration from legacy repository"

# Log decisions that mean "this legacy file's content is now the master".
_APPLIED_DECISIONS = ("migrated", "replaced")


def default_legacy_root() -> Path:
    return Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Migration log
# --------------------------------------------------------------------------

def log_path(ws: Workspace) -> Path:
    return ws.monitoring_dir / "migrations.yaml"


def load_log(ws: Workspace) -> list[dict]:
    data = storage.yaml_load_file(log_path(ws))
    return data if isinstance(data, list) else []


def _find_record(log: list[dict], kind: str, source_hash: str) -> dict | None:
    for rec in log:
        if isinstance(rec, dict) and rec.get("kind") == kind and rec.get("source_hash") == source_hash:
            return rec
    return None


def _append_record(ws: Workspace, record: dict) -> None:
    with workspace_lock(ws):
        log = load_log(ws)
        log.append(record)
        atomic_write_yaml(log_path(ws), log)


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------

def _snapshot(ws: Workspace, kind: str, raw: bytes, source_hash: str) -> Path:
    """Copy the legacy bytes verbatim and prove the copy is identical."""
    snap = ws.legacy_dir / f"{kind}-{source_hash[:12]}.yaml"
    if not (snap.exists() and sha256_file(snap) == source_hash):
        atomic_write_bytes(snap, raw)
    actual = sha256_file(snap)
    if actual != source_hash:
        raise ResumeTailorError(
            "MIGRATION_CONFLICT",
            f"Legacy {kind} snapshot does not match its source; no master was written.",
            details={"kind": kind, "reason": "snapshot_hash_mismatch",
                     "legacy_source_hash": source_hash, "snapshot_hash": actual},
        )
    return snap


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------

def _migrated_doc(loaded, kind: str, source_hash: str, src: Path, migrated_at: str) -> dict:
    if not isinstance(loaded, dict):
        raise ResumeTailorError("MASTER_INVALID", f"Legacy {kind} is not a YAML mapping.", details={"kind": kind})
    doc = normalize_master(loaded, kind)
    doc["metadata"]["migration"] = {
        "source": MIGRATION_SOURCE,
        "source_hash": source_hash,
        "migrated_at": migrated_at,
        "legacy_path": str(src),
    }
    return doc


def _write_master(ws: Workspace, kind: str, src: Path, raw: bytes, loaded, source_hash: str,
                  expected_hash: str | None, decision: str) -> dict:
    migrated_at = utc_now_iso()
    snap = _snapshot(ws, kind, raw, source_hash)
    doc = _migrated_doc(loaded, kind, source_hash, src, migrated_at)
    new_hash = storage.save_master(kind, doc, expected_hash, MIGRATION_REASON, ws=ws)
    _append_record(ws, {
        "kind": kind, "source_hash": source_hash, "legacy_path": str(src),
        "migrated_at": migrated_at, "snapshot_path": str(snap),
        "master_hash": new_hash, "decision": decision,
    })
    return {"status": decision, "master_hash": new_hash, "legacy_source_hash": source_hash,
            "snapshot_path": str(snap)}


def _migrate_master(ws: Workspace, legacy_root: Path, kind: str, conflict_choice: str | None) -> dict:
    src = legacy_root / "resources" / f"master_{kind}.yaml"
    if not src.is_file():
        return {"status": "no_legacy"}

    raw = src.read_bytes()
    source_hash = sha256_bytes(raw)
    loaded = storage.yaml_load_text(raw.decode("utf-8"))  # safe YAML + size/depth limits

    with workspace_lock(ws):
        current, current_hash = storage.load_master(kind, ws)
        if current is None:
            return _write_master(ws, kind, src, raw, loaded, source_hash, None, "migrated")

        migration = (current.get("metadata") or {}).get("migration") or {}
        base = {"master_hash": current_hash, "legacy_source_hash": source_hash}
        if migration.get("source_hash") == source_hash:
            return {"status": "already_migrated", **base}
        record = _find_record(load_log(ws), kind, source_hash)
        if record is not None:
            if record.get("decision") in _APPLIED_DECISIONS:
                return {"status": "already_migrated", **base}
            return {"status": "already_resolved", "decision": record.get("decision"), **base}

        if conflict_choice is None:
            return {"status": "conflict", "details": {
                "workspace_master_hash": current_hash,
                "legacy_source_hash": source_hash,
                "workspace_master_from_migration": bool(migration),
            }}
        if conflict_choice == "keep_workspace":
            _append_record(ws, {
                "kind": kind, "source_hash": source_hash, "legacy_path": str(src),
                "migrated_at": utc_now_iso(), "snapshot_path": None,
                "master_hash": current_hash, "decision": "kept_workspace",
            })
            return {"status": "kept_workspace", **base}
        if conflict_choice == "replace_with_legacy":
            return _write_master(ws, kind, src, raw, loaded, source_hash, current_hash, "replaced")
        raise ResumeTailorError(
            "MIGRATION_CONFLICT",
            f"Unknown conflict_choice {conflict_choice!r}; use one of {', '.join(CONFLICT_CHOICES)}.",
            details={"allowed": list(CONFLICT_CHOICES)},
        )


# --------------------------------------------------------------------------
# Versions and JDs (opt-in)
# --------------------------------------------------------------------------

def _is_valid_id(name: str) -> bool:
    try:
        validate_id(name)
        return True
    except ResumeTailorError:
        return False


def _migrate_version(ws: Workspace, path: Path) -> dict:
    stem = path.stem
    if not _is_valid_id(stem):
        return {"version_id": stem, "status": "skipped_invalid_id"}
    raw = path.read_bytes()
    source_hash = sha256_bytes(raw)
    with workspace_lock(ws):
        target = storage.version_path(stem, ws)
        if target.exists():
            existing = storage.yaml_load_file(target) or {}
            existing_hash = (existing.get("metadata") or {}).get("legacy_source_hash") \
                if isinstance(existing, dict) else None
            status = "already_migrated" if existing_hash == source_hash else "conflict"
            return {"version_id": stem, "status": status}
        doc = storage.yaml_load_text(raw.decode("utf-8"))
        if not isinstance(doc, dict):
            return {"version_id": stem, "status": "skipped_not_mapping"}
        meta = doc.setdefault("metadata", {})
        if not isinstance(meta, dict):
            meta = doc["metadata"] = {}
        meta.update({
            "legacy": True, "version_id": stem, "document_kind": "resume",
            "legacy_source_hash": source_hash, "migrated_at": utc_now_iso(), "released": False,
        })
        storage.save_version(stem, doc, ws=ws, validate=False)
    return {"version_id": stem, "status": "migrated"}


def _migrate_jd(ws: Workspace, path: Path) -> dict:
    stem = path.stem
    if not _is_valid_id(stem):
        return {"jd_id": stem, "status": "skipped_invalid_id"}
    raw = path.read_bytes()
    storage.yaml_load_text(raw.decode("utf-8"))  # reject unsafe YAML before copying
    with workspace_lock(ws):
        target = storage.jd_path(stem, ws)
        if target.exists():
            return {"jd_id": stem, "status": "skipped_existing"}
        atomic_write_bytes(target, raw, exclusive=True)
    return {"jd_id": stem, "status": "copied"}


def _yaml_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.yaml") if p.is_file()) if directory.is_dir() else []


def _count_exports(legacy_root: Path) -> int:
    exports = legacy_root / "data" / "exports"
    if not exports.is_dir():
        return 0
    return sum(1 for p in exports.iterdir() if p.is_file() and not p.name.startswith("."))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def migrate_legacy(legacy_root: Path | None = None, *, include_versions: bool = False,
                   include_jds: bool = False, conflict_choice: str | None = None,
                   ws: Workspace | None = None) -> dict:
    """Migrate legacy masters (and optionally versions/JDs) into the workspace.

    `conflict_choice` applies to every master kind that is in conflict.
    Legacy files are never modified or deleted."""
    ws = ws or get_workspace()
    legacy_root = Path(legacy_root) if legacy_root is not None else default_legacy_root()

    masters = {kind: _migrate_master(ws, legacy_root, kind, conflict_choice) for kind in KINDS}
    versions = [_migrate_version(ws, p) for p in _yaml_files(legacy_root / "data" / "versions")] \
        if include_versions else None
    jds = [_migrate_jd(ws, p) for p in _yaml_files(legacy_root / "data" / "jd_history")] \
        if include_jds else None

    unresolved = any(m["status"] == "conflict" for m in masters.values()) or \
        any(v["status"] == "conflict" for v in versions or [])
    return {
        "ok": not unresolved,
        "workspace_id": ws.id,
        "masters": masters,
        "versions": versions,
        "jds": jds,
        "legacy_exports_left_in_place": _count_exports(legacy_root),
        "note": "Legacy files were not deleted.",
    }
