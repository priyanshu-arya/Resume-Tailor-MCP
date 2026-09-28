"""Structured audit log (spec §49-50, §55).

Every notable step of a workflow appends one JSON line to
`<workspace>/monitoring/audit.jsonl`:

    {"timestamp": "2026-09-27T12:00:00Z", "workspace_id": "RT-...", "event": "...", ...}

Privacy is enforced structurally, not by caller discipline:

* the event name must come from `EVENTS` (unknown names raise ValueError so
  taxonomy drift is caught by tests);
* only keys in `ALLOWED_FIELDS` are kept -- anything else (a name, an email,
  resume or JD text) is silently dropped;
* string values are truncated to 120 characters and must match a narrow
  ID/hash/code alphabet, otherwise they are replaced with "<redacted>";
* nested dicts and other structures are dropped; `rule_ids` is the only
  list allowed (at most 20 sanitized strings);
* `category` must be one of `FAILURE_CATEGORIES` and `severity` one of
  critical/error/warning/info, else the field is dropped.

Logs therefore hold IDs, hashes, counts, codes, categories, statuses and
timings -- never full names, contact details, resume text or JD text.

Logging never breaks a tool: with no initialized workspace, on an OSError,
or if the small audit lock cannot be taken, `log_event` returns None. It
uses its own lock file (`monitoring/.audit.lock`), not the workspace lock,
so it can be called while a writer holds the workspace lock.

The controlled debug mode described in spec §65 (temporarily logging
richer diagnostics) is deliberately NOT implemented: there is no switch
that relaxes the field allow-list or the value sanitizer.

Monitoring observes software reliability only; nothing here evaluates the
candidate or modifies rules, templates, code or master data.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from lib.errors import FAILURE_CATEGORIES, ResumeTailorError
from lib.locking import file_lock
from lib.workspace import get_workspace, utc_now_iso

EVENTS = frozenset({
    # workspace / master lifecycle (R-USER-01..17)
    "workspace_initialized", "workspace_selected", "workspace_switched", "legacy_migrated",
    "master_updated", "master_discovered", "master_imported", "master_created", "master_loaded",
    "master_conflict",
    # tailoring workflow
    "workflow_started", "requirements_analyzed", "evidence_saved", "tailor_succeeded", "tailor_rejected",
    "validation_completed", "release_succeeded", "release_blocked", "export_completed",
    # creating a structural entry is the most consequential new capability
    # add_project_entry has -- it gets its own line rather than being folded
    # into tailor_succeeded.
    "project_entry_added",
    # LaTeX/PDF compile (Phase 7): emitted from lib/release.py, not the server
    # wrapper, since only release.py sees the compile step itself.
    "pdf_compiled",
    # failures
    "tool_error", "internal_error",
})
# Deliberately NOT added, with reasons (do not add these later without
# re-reading why): "master_discovery_started" -- carries nothing the
# completion event (master_discovered) lacks, and a scan that raises is
# already recorded as tool_error. "master_selected" -- a chat-level choice
# the server cannot observe; the observable moment is the import itself
# (master_imported), which already records candidate_count/source_hash.
# "MASTER_LOADED" (broad, on every master read) -- every mutation already
# records which master it used; a read event would fire on every
# get_master_resume/resource read and drown the log. The narrow
# `master_loaded` above is scoped to the get_master_resume tool only.
# "EVIDENCE_REQUESTED" (per JD term) -- the only payload worth having is the
# *term*, and a JD term is document content; it would be the first
# whitelisted field carrying free-form document text. The aggregate
# `evidence_prompt_count` on `requirements_analyzed` already feeds metrics.

ALLOWED_FIELDS = frozenset({
    "tool", "workflow_id", "version_id", "evidence_id", "release_report_id", "kind", "template_id", "mode", "format",
    "category", "rule_id", "rule_ids", "code", "severity", "status", "error_class",
    "patch_count", "rejection_count", "check_count", "critical_count", "warning_count", "template_failures",
    "pdf_failures", "pdf_checked", "repair_attempt", "evidence_prompt_count", "missing_count", "unknown_count",
    "migrated_count", "conflict_count", "duration_ms", "master_hash", "source_master_hash", "tex_sha256", "pdf_sha256",
    "jd_sha256", "applied",
    # workspace identity / discovery -- NOTE "workspace_id" itself is
    # deliberately never whitelisted: build_record injects it structurally
    # (the workspace whose log this record is IN), and whitelisting it would
    # let the field loop overwrite that injected value after the fact, i.e.
    # let a caller forge which workspace's log a record belongs to. A switch
    # is instead logged as two records, one per side, using these two:
    "from_workspace_id", "to_workspace_id",
    "candidate_count", "entries_examined",  # proves a folder scan was bounded
    "source_hash",  # which file was imported (not the folder path)
    "workspace_count", "state",
    # pdf_compiled -- integers only, no PII risk
    "page_count", "overfull_count",
    # Phase 3.2/4.7: gap-analysis and evidence-prompt shape, never document text.
    "evidence_category", "prompt_reason", "requirement_type", "weak_count", "supported_count",
    "new_entry_count", "rules_version",
})
# Rejected for pdf_compiled, recorded so a later phase doesn't quietly add
# them: "term" (document content), "career_stage" (candidate-descriptive),
# "basename"/"path"/"export_basename" (export_basename is a live trap --
# deterministic_filename builds "First_Last_Role_Resume" from the
# candidate's real name), "jd_title", "company", "role".

SEVERITIES = frozenset({"critical", "error", "warning", "info"})

AUDIT_FILE = "audit.jsonl"
ERRORS_FILE = "errors.jsonl"
LOCK_FILE = ".audit.lock"

MAX_STR = 120
MAX_RULE_IDS = 20
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_READ_RECORDS = 50_000
REDACTED = "<redacted>"
_SAFE_RE = re.compile(r"^[A-Za-z0-9 ._:/+#@=-]*$")

_DROP = object()

# Fields with a fixed, non-free-text shape get a type/format check instead of
# the generic string path -- otherwise a caller passing a free-text string
# into a field the schema promises is a bool or a hex hash would sail through
# _SAFE_RE (it has no comma/paren) and land in the log unredacted.
_BOOL_FIELDS = frozenset({"applied", "pdf_checked"})
_INT_FIELDS = frozenset({
    "patch_count", "rejection_count", "check_count", "critical_count", "warning_count",
    "template_failures", "pdf_failures", "repair_attempt", "evidence_prompt_count",
    "missing_count", "unknown_count", "migrated_count", "conflict_count", "duration_ms",
    "candidate_count", "entries_examined", "workspace_count", "page_count", "overfull_count",
    "weak_count", "supported_count", "new_entry_count",
})
_HASH_FIELDS = frozenset({
    "master_hash", "source_master_hash", "tex_sha256", "pdf_sha256", "jd_sha256", "source_hash",
})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _clean_str(value: str) -> str:
    value = value[:MAX_STR]
    return value if _SAFE_RE.match(value) else REDACTED


def _clean_value(key: str, value):
    if value is None:
        return _DROP
    if key == "category":
        return value if isinstance(value, str) and value in FAILURE_CATEGORIES else _DROP
    if key == "severity":
        return value if isinstance(value, str) and value in SEVERITIES else _DROP
    if key == "rule_ids":
        if not isinstance(value, (list, tuple)):
            return _DROP
        return [_clean_str(v) for v in value[:MAX_RULE_IDS] if isinstance(v, str)]
    if key in _BOOL_FIELDS:
        return value if isinstance(value, bool) else _DROP
    if key in _INT_FIELDS:
        return value if isinstance(value, int) and not isinstance(value, bool) else _DROP
    if key in _HASH_FIELDS:
        return value if isinstance(value, str) and _HASH_RE.match(value) else _DROP
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _DROP
    if isinstance(value, str):
        return _clean_str(value)
    return _DROP  # dicts, lists (other than rule_ids), objects


def build_record(event: str, ws, fields: dict) -> dict:
    if event not in EVENTS:
        raise ValueError(f"Unknown audit event: {event!r}")
    record = {"timestamp": utc_now_iso(), "workspace_id": ws.id, "event": event}
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS:
            continue
        cleaned = _clean_value(key, value)
        if cleaned is not _DROP:
            record[key] = cleaned
    return record


def _resolve_ws(ws):
    if ws is not None:
        return ws
    try:
        return get_workspace()
    except (ResumeTailorError, OSError):
        return None


def _append(ws, names: tuple[str, ...], record: dict) -> bool:
    line = (json.dumps(record, ensure_ascii=True, sort_keys=False) + "\n").encode("utf-8")
    mon = Path(ws.monitoring_dir)
    try:
        with file_lock(mon / LOCK_FILE, timeout=5.0):
            for name in names:
                with open(mon / name, "ab") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
        return True
    except (OSError, ResumeTailorError):
        return False


def _log(event: str, ws, fields: dict, names: tuple[str, ...]) -> dict | None:
    if event not in EVENTS:  # validate before anything else: programming error
        raise ValueError(f"Unknown audit event: {event!r}")
    ws = _resolve_ws(ws)
    if ws is None:
        return None
    record = build_record(event, ws, fields)
    return record if _append(ws, names, record) else None


def log_event(event: str, *, ws=None, **fields) -> dict | None:
    """Append one sanitized event to audit.jsonl. Returns the record, or
    None if it could not be written (never raises, except ValueError for an
    unknown event name)."""
    return _log(event, ws, fields, (AUDIT_FILE,))


def log_error(event: str, *, ws=None, **fields) -> dict | None:
    """Like log_event, but the record goes to errors.jsonl as well as audit.jsonl."""
    return _log(event, ws, fields, (AUDIT_FILE, ERRORS_FILE))


def _parse_lines(lines) -> list[dict]:
    events = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            events.append(rec)
    return events


def _read_bounded_tail(path: Path) -> list[str]:
    """The file's last MAX_READ_BYTES, as whole lines: if that boundary
    falls mid-line, the partial first line is discarded (its predecessor's
    bytes are outside the window, so it cannot be reconstructed)."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > MAX_READ_BYTES:
            f.seek(size - MAX_READ_BYTES)
            raw = f.read()
            raw = raw.split(b"\n", 1)[1] if b"\n" in raw else b""
        else:
            raw = f.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def read_events(ws=None, *, errors: bool = False) -> list[dict]:
    """Parseable records from audit.jsonl (or errors.jsonl), in append
    order, bounded to the last MAX_READ_BYTES of the file and the last
    MAX_READ_RECORDS records within that window. Malformed lines are
    skipped. Raises WORKSPACE_NOT_INITIALIZED if no workspace is given and
    none is configured.

    Beyond the byte cap, rates computed from this data become "recent
    history" rather than the whole log's true rate -- the defence against
    that is log rotation, deferred past v1; `health()` flags when the log
    is past half the cap so a human can act before truth quietly narrows."""
    ws = ws or get_workspace()
    path = Path(ws.monitoring_dir) / (ERRORS_FILE if errors else AUDIT_FILE)
    if not path.exists():
        return []
    events = _parse_lines(_read_bounded_tail(path))
    return events[-MAX_READ_RECORDS:] if len(events) > MAX_READ_RECORDS else events


def tail_events(limit: int, ws=None, *, errors: bool = False) -> list[dict]:
    """The last `limit` records, in append order -- a fast path for
    diagnostics that never needs the full bounded window. `limit` is
    clamped to [1, MAX_READ_RECORDS] by the caller."""
    events = read_events(ws, errors=errors)
    return events[-limit:] if limit < len(events) else events
