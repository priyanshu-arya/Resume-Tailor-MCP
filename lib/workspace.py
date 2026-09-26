"""Workspace manager -- where a user's personal data lives (spec §4-6).

Personal data (masters, versions, evidence, exports, logs) lives outside the
repository, under one workspace:

    $RESUME_TAILOR_HOME (default ~/.resume-tailor)/
    ├── config.yaml                 # active_workspace_id, schema_version
    └── workspaces/<workspace_id>/
        ├── master/{resume,cv}.yaml (+ backups/, legacy/)
        ├── data/{versions,jd_history,exports,tailoring_sessions,releases,evidence}/
        └── monitoring/

For local stdio use the workspace belongs to the installation. The hostname
is deliberately not part of the identity -- it is not a security boundary.
Nothing here auto-creates or auto-selects a workspace: callers must run
`initialize_workspace()` explicitly on first use.

`safe_child` is the only function that turns a caller-supplied ID into a
path, so every ID-addressed file stays inside its workspace.
"""

from __future__ import annotations

import os
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


from lib.errors import ResumeTailorError
from lib.schemas import SCHEMA_VERSION, validate_kind

ENV_HOME = "RESUME_TAILOR_HOME"

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")

_DATA_SUBDIRS = ("versions", "jd_history", "exports", "tailoring_sessions", "releases", "evidence")


def utc_now_iso() -> str:
    """UTC ISO 8601 with a trailing Z, second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def app_root() -> Path:
    """Application root, read at call time so tests can point it elsewhere."""
    env = os.environ.get(ENV_HOME)
    return Path(env).expanduser().resolve() if env else (Path.home() / ".resume-tailor").resolve()


def config_path() -> Path:
    return app_root() / "config.yaml"


@dataclass(frozen=True)
class Workspace:
    id: str
    root: Path

    @property
    def master_dir(self) -> Path:
        return self.root / "master"

    @property
    def backups_dir(self) -> Path:
        return self.master_dir / "backups"

    @property
    def legacy_dir(self) -> Path:
        return self.master_dir / "legacy"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def versions_dir(self) -> Path:
        return self.data_dir / "versions"

    @property
    def jd_dir(self) -> Path:
        return self.data_dir / "jd_history"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "tailoring_sessions"

    @property
    def releases_dir(self) -> Path:
        return self.data_dir / "releases"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def monitoring_dir(self) -> Path:
        return self.root / "monitoring"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"

    def master_path(self, kind: str) -> Path:
        return self.master_dir / f"{validate_kind(kind)}.yaml"


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------

def validate_id(name: str, what: str = "id") -> str:
    """Accept only simple IDs: letters, digits, '.', '_', '-'; no '..'."""
    if not isinstance(name, str) or not _ID_RE.fullmatch(name) or ".." in name:
        raise ResumeTailorError(
            "INVALID_ID",
            f"Invalid {what}: use 1-81 letters, digits, '.', '_' or '-' (no path separators).",
            details={"what": what},
        )
    return name


def safe_child(base: Path, name: str, suffix: str = "") -> Path:
    """Resolve base/<name><suffix>, guaranteeing it stays inside base."""
    validate_id(name)
    base = Path(base).resolve()
    candidate = (base / f"{name}{suffix}").resolve()
    if candidate.parent != base:
        raise ResumeTailorError("PATH_TRAVERSAL", "Path escapes the workspace.")
    return candidate


def slugify(text: str, max_len: int = 60) -> str:
    """Human-friendly, filesystem-safe ID from free text (e.g. save_as)."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    text = text[:max_len].strip("-")
    if not text:
        raise ResumeTailorError("INVALID_ID", "save_as must contain at least one letter or digit.")
    return text


# --------------------------------------------------------------------------
# Config + initialization
# --------------------------------------------------------------------------

def _read_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    from lib import safe_yaml
    data = safe_yaml.load_file(path)
    return data if isinstance(data, dict) else {}


def _write_config(config: dict) -> None:
    from lib.locking import atomic_write_yaml  # local import: locking imports nothing from here at module load
    atomic_write_yaml(config_path(), config)


def _workspace_from_id(workspace_id: str) -> Workspace:
    validate_id(workspace_id, "workspace id")
    workspaces = app_root() / "workspaces"
    return Workspace(id=workspace_id, root=safe_child(workspaces, workspace_id))


def _create_dirs(ws: Workspace) -> None:
    for d in (ws.master_dir, ws.backups_dir, ws.legacy_dir, ws.monitoring_dir):
        d.mkdir(parents=True, exist_ok=True)
    for sub in _DATA_SUBDIRS:
        (ws.data_dir / sub).mkdir(parents=True, exist_ok=True)


def _describe(ws: Workspace, created: bool) -> dict:
    return {
        "workspace_id": ws.id,
        "root": str(ws.root),
        "created": created,
        "master_paths": {k: str(ws.master_path(k)) for k in ("resume", "cv")},
        "masters_present": {k: ws.master_path(k).exists() for k in ("resume", "cv")},
    }


def initialize_workspace() -> dict:
    """Create a workspace on first run. Idempotent: if the configured
    workspace already exists it is returned unchanged (created=False).
    Never writes personal data."""
    config = _read_config()
    existing = config.get("active_workspace_id")
    if existing:
        ws = _workspace_from_id(existing)
        if ws.root.exists():
            _create_dirs(ws)  # repair any missing subdirs, never touches files
            return _describe(ws, created=False)
        # configured but missing on disk: refuse to silently pick another one
        raise ResumeTailorError(
            "WORKSPACE_NOT_INITIALIZED",
            "The configured workspace directory is missing. Restore it, or remove "
            "config.yaml to start a new workspace.",
            details={"workspace_id": existing},
        )

    workspace_id = f"RT-{secrets.token_hex(4).upper()}"
    ws = _workspace_from_id(workspace_id)
    _create_dirs(ws)
    _write_config({
        "active_workspace_id": workspace_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now_iso(),
    })
    return _describe(ws, created=True)


def get_workspace() -> Workspace:
    config = _read_config()
    workspace_id = config.get("active_workspace_id")
    if not workspace_id:
        raise ResumeTailorError(
            "WORKSPACE_NOT_INITIALIZED",
            "Resume Tailor workspace is not initialized. Create a new workspace with "
            "initialize_workspace (and migrate_legacy_data if you have existing masters).",
        )
    ws = _workspace_from_id(workspace_id)
    if not ws.root.exists():
        raise ResumeTailorError(
            "WORKSPACE_NOT_INITIALIZED",
            "The configured workspace directory is missing.",
            details={"workspace_id": workspace_id},
        )
    return ws


def describe_workspace() -> dict:
    return _describe(get_workspace(), created=False)


# Convenience path helpers (spec §5 interface).

def get_master_path(kind: str, ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).master_path(kind)


def get_versions_path(ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).versions_dir


def get_exports_path(ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).exports_dir


def get_releases_path(ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).releases_dir


def get_monitoring_path(ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).monitoring_dir


def get_evidence_path(ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).evidence_dir


def get_sessions_path(ws: Workspace | None = None) -> Path:
    return (ws or get_workspace()).sessions_dir


def list_workspace_size(ws: Workspace | None = None) -> dict:
    """Bytes and file counts per top-level area. Deletes nothing."""
    ws = ws or get_workspace()
    areas = {"master": ws.master_dir, "monitoring": ws.monitoring_dir}
    areas.update({f"data/{s}": ws.data_dir / s for s in _DATA_SUBDIRS})
    report, total = {}, 0
    for label, path in areas.items():
        files = [p for p in path.rglob("*") if p.is_file()] if path.exists() else []
        size = sum(p.stat().st_size for p in files)
        total += size
        report[label] = {"files": len(files), "bytes": size}
    return {
        "workspace_id": ws.id,
        "root": str(ws.root),
        "total_bytes": total,
        "areas": report,
        "note": f"To purge everything, delete {ws.root} yourself. Nothing is deleted automatically.",
    }
