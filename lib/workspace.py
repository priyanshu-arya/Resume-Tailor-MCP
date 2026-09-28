"""Workspace manager -- where a user's personal data lives (spec §4-6).

Personal data (masters, versions, evidence, exports, logs) lives outside the
repository, under one or more workspaces:

    $RESUME_TAILOR_HOME (default ~/.resume-tailor)/
    ├── config.yaml                 # active_workspace_id, schema_version, workspaces{}
    ├── .config.lock
    └── workspaces/<workspace_id>/
        ├── master/{resume,cv}.yaml (+ backups/, legacy/)
        ├── data/{versions,jd_history,exports,tailoring_sessions,releases,evidence}/
        └── monitoring/

For local stdio use the workspace belongs to the installation. The hostname
is deliberately not part of the identity -- it is not a security boundary.
Nothing here auto-creates or auto-selects a workspace: callers must run
`initialize_workspace()` explicitly on first use, and if several workspaces
exist on this machine with none bound, `get_workspace()` refuses to guess
(`WORKSPACE_AMBIGUOUS`) rather than silently minting a new, empty one that
orphans whatever the user already had (R-USER-01..17; see docs/spec-map.md
and CLAUDE.md's identity section).

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

# The shape `initialize_workspace()` mints ids in; used only to flag an
# oddly-named directory in list_workspaces (informational, not a security
# gate -- validate_id/safe_child are what confine path access).
WORKSPACE_ID_RE = re.compile(r"^RT-[0-9A-F]{8}$")

MAX_WORKSPACES_LISTED = 200

# Resolution states (R-USER-01..06). Named here because workspace identity is
# this module's concern; the state machine that computes one of these lives
# in lib/resolve.py, which may import storage -- this module must not
# (storage imports workspace, so the reverse would cycle).
NO_WORKSPACE = "NO_WORKSPACE"
WORKSPACE_FOUND = "WORKSPACE_FOUND"
WORKSPACE_INVALID = "WORKSPACE_INVALID"
WORKSPACE_NEEDS_SETUP = "WORKSPACE_NEEDS_SETUP"
RESOLUTION_STATES = (NO_WORKSPACE, WORKSPACE_FOUND, WORKSPACE_INVALID, WORKSPACE_NEEDS_SETUP)


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


def config_lock_path() -> Path:
    """Lock for read-modify-write of config.yaml. This is the app root, not a
    workspace, so it has its own lock file rather than reusing workspace_lock."""
    return app_root() / ".config.lock"


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


def _active_id(config: dict) -> str | None:
    value = config.get("active_workspace_id")
    return value if isinstance(value, str) and value else None


# --------------------------------------------------------------------------
# Multi-workspace discovery (R-USER-05, R-USER-15)
# --------------------------------------------------------------------------

def existing_workspace_ids() -> list[str]:
    """Workspace ids that physically exist under app_root()/workspaces,
    filtered for path safety. Never reads config.yaml, so a planted directory
    can never widen what the server will bind to via config manipulation.
    Symlinked entries and names failing validate_id are skipped. Sorted,
    unbounded here -- callers apply MAX_WORKSPACES_LISTED and report
    truncation, since this list may also be used just to test non-emptiness."""
    workspaces_dir = app_root() / "workspaces"
    if not workspaces_dir.is_dir():
        return []
    ids: list[str] = []
    for entry in sorted(workspaces_dir.iterdir(), key=lambda p: p.name):
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            validate_id(entry.name, "workspace id")
        except ResumeTailorError:
            continue
        ids.append(entry.name)
    return ids


def _ambiguous_error(available: list[str]) -> ResumeTailorError:
    return ResumeTailorError(
        "WORKSPACE_AMBIGUOUS",
        f"{len(available)} Resume Tailor workspace(s) exist on this machine but none is "
        "selected (config.yaml is missing, unreadable, or has no active_workspace_id). "
        "Refusing to guess which one is yours: call list_workspaces and ask the user which "
        "one is theirs, then select_workspace(workspace_id). Do NOT call initialize_workspace "
        "-- that would create a new, empty workspace and orphan the existing one(s).",
        details={"available_workspace_ids": available[:MAX_WORKSPACES_LISTED]},
    )


def list_workspaces() -> dict:
    """Every workspace on this machine, and which one is active. Reads no
    resume content -- only whether a master file exists. Never switches,
    never creates."""
    config = _read_config()
    active_id = _active_id(config)
    known = config.get("workspaces")
    known = known if isinstance(known, dict) else {}

    all_ids = existing_workspace_ids()
    truncated = len(all_ids) > MAX_WORKSPACES_LISTED
    ids = all_ids[:MAX_WORKSPACES_LISTED]

    rows = []
    for wid in ids:
        ws = Workspace(id=wid, root=app_root() / "workspaces" / wid)
        meta = known.get(wid)
        meta = meta if isinstance(meta, dict) else {}
        rows.append({
            "workspace_id": wid,
            "label": meta.get("label"),
            "created_at": meta.get("created_at"),
            "active": wid == active_id,
            "masters_present": {k: ws.master_path(k).exists() for k in ("resume", "cv")},
            "id_format_ok": bool(WORKSPACE_ID_RE.fullmatch(wid)),
        })
    return {
        "workspaces": rows,
        "active_workspace_id": active_id,
        "count": len(rows),
        "truncated": truncated,
        "note": "Contains no resume content -- only whether a master file exists.",
    }


def select_workspace(workspace_id: str) -> dict:
    """Bind active_workspace_id to an EXISTING workspace, by an id the user
    explicitly named (R-USER-14). Creates no workspace and copies no data.
    Raises INVALID_ID/PATH_TRAVERSAL for a malformed id (via safe_child) and
    WORKSPACE_NOT_FOUND if the directory does not exist -- it never falls
    back to another workspace."""
    ws = _workspace_from_id(workspace_id)
    if not ws.root.exists():
        raise ResumeTailorError(
            "WORKSPACE_NOT_FOUND",
            f"No workspace directory for {workspace_id!r} on this machine. Call "
            "list_workspaces to see what exists, or discover_masters(folder) to import one.",
            details={"workspace_id": workspace_id},
        )
    from lib.locking import file_lock
    with file_lock(config_lock_path()):
        config = _read_config()
        previous = _active_id(config)
        config["active_workspace_id"] = workspace_id
        config.setdefault("schema_version", SCHEMA_VERSION)
        config.setdefault("created_at", utc_now_iso())
        _write_config(config)

    switched = previous is not None and previous != workspace_id
    if switched:
        # The generic post-call audit path (server._success_events) logs the
        # "to" side automatically, because by the time it runs config.yaml
        # already points here. It has no way to reach the "from" workspace's
        # own log, though, so that side is logged here, directly, into the
        # workspace we are leaving -- best-effort: a logging failure must
        # never undo or fail the switch itself.
        try:
            from lib import audit
            audit.log_event("workspace_switched", ws=_workspace_from_id(previous), to_workspace_id=workspace_id)
        except Exception:  # noqa: BLE001
            pass

    result = _describe(ws, created=False)
    result["previous_workspace_id"] = previous
    result["switched"] = switched
    return result


def _mint_workspace(label: str | None) -> dict:
    """Create a brand-new workspace and bind to it. Only ever called from
    initialize_workspace(), and only along a path that has already refused to
    guess -- see initialize_workspace's docstring."""
    if label is not None:
        label = slugify(label, max_len=40)
    from lib.locking import file_lock
    with file_lock(config_lock_path()):
        config = _read_config()
        workspace_id = f"RT-{secrets.token_hex(4).upper()}"
        ws = _workspace_from_id(workspace_id)
        _create_dirs(ws)
        now = utc_now_iso()
        known = config.get("workspaces")
        known = dict(known) if isinstance(known, dict) else {}
        known[workspace_id] = {"created_at": now, "label": label}
        config["active_workspace_id"] = workspace_id
        config["schema_version"] = SCHEMA_VERSION
        config.setdefault("created_at", now)
        config["workspaces"] = known
        _write_config(config)
    return _describe(ws, created=True)


def initialize_workspace(create_new: bool = False, label: str | None = None) -> dict:
    """Create a workspace. Idempotent for the default call: if the configured
    workspace already exists it is returned unchanged (created=False). Never
    writes personal data.

    create_new=False (the default -- unchanged behaviour except one new
    guard):
      * a bound workspace that exists      -> returned unchanged (created=False)
      * a bound workspace that is gone     -> WORKSPACE_NOT_INITIALIZED
      * no binding, but workspace(s) exist on disk -> WORKSPACE_AMBIGUOUS
        (this used to silently mint a brand-new empty workspace and orphan
        the existing one's master/versions/evidence -- see docs/baseline.md
        defect D-1. It no longer does.)
      * no binding, nothing on disk         -> create the first workspace

    create_new=True: create an ADDITIONAL workspace and bind to it, only when
    the user explicitly asked for a second workspace (R-USER-16). Refused
    while the currently bound workspace is missing from disk, so create_new
    can never be used to walk away from a broken binding. No code in this
    repository ever passes create_new=True except in direct response to that
    request -- it is never set implicitly.

    label: an optional human-readable hint shown by list_workspaces (e.g.
    "work-laptop"), slugified and stored only in config.yaml -- never
    audited, never resume content."""
    config = _read_config()
    existing = _active_id(config)

    if create_new:
        if existing:
            ws = _workspace_from_id(existing)
            if not ws.root.exists():
                raise ResumeTailorError(
                    "WORKSPACE_NOT_INITIALIZED",
                    "The currently bound workspace directory is missing. Restore it, or call "
                    "select_workspace on an existing one, before creating another -- create_new "
                    "is refused so it can never be used to walk away from a broken binding.",
                    details={"workspace_id": existing},
                )
        return _mint_workspace(label)

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

    available = existing_workspace_ids()
    if available:
        raise _ambiguous_error(available)

    return _mint_workspace(label)


def get_workspace() -> Workspace:
    config = _read_config()
    workspace_id = _active_id(config)
    if not workspace_id:
        available = existing_workspace_ids()
        if available:
            raise _ambiguous_error(available)
        raise ResumeTailorError(
            "WORKSPACE_NOT_INITIALIZED",
            "Resume Tailor workspace is not initialized. Create a new workspace with "
            "initialize_workspace (and migrate_legacy_data if you have existing masters).",
        )
    ws = _workspace_from_id(workspace_id)
    if not ws.root.exists():
        available = existing_workspace_ids()
        details = {"workspace_id": workspace_id}
        message = "The configured workspace directory is missing."
        if available:
            details["available_workspace_ids"] = available[:MAX_WORKSPACES_LISTED]
            message += (" Other workspace(s) exist on this machine -- call list_workspaces and, "
                        "if the user tells you which one is theirs, select_workspace(workspace_id).")
        raise ResumeTailorError("WORKSPACE_NOT_INITIALIZED", message, details=details)
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
