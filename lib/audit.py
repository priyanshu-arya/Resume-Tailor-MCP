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
    "workspace_initialized", "legacy_migrated", "master_updated", "workflow_started", "requirements_analyzed",
    "evidence_saved", "tailor_succeeded", "tailor_rejected", "validation_completed", "release_succeeded",
    "release_blocked", "export_completed", "tool_error", "internal_error",
})

ALLOWED_FIELDS = frozenset({
    "tool", "workflow_id", "version_id", "evidence_id", "release_report_id", "kind", "template_id", "mode", "format",
    "category", "rule_id", "rule_ids", "code", "severity", "status", "error_class",
    "patch_count", "rejection_count", "check_count", "critical_count", "warning_count", "template_failures",
    "pdf_failures", "pdf_checked", "repair_attempt", "evidence_prompt_count", "missing_count", "unknown_count",
    "migrated_count", "conflict_count", "duration_ms", "master_hash", "source_master_hash", "tex_sha256", "pdf_sha256",
    "jd_sha256", "applied",
})

SEVERITIES = frozenset({"critical", "error", "warning", "info"})

AUDIT_FILE = "audit.jsonl"
ERRORS_FILE = "errors.jsonl"
LOCK_FILE = ".audit.lock"

MAX_STR = 120
MAX_RULE_IDS = 20
REDACTED = "<redacted>"
_SAFE_RE = re.compile(r"^[A-Za-z0-9 ._:/+#@=-]*$")

_DROP = object()


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


def read_events(ws=None, *, errors: bool = False) -> list[dict]:
    """All parseable records from audit.jsonl (or errors.jsonl), in append
    order. Malformed lines are skipped. Raises WORKSPACE_NOT_INITIALIZED if
    no workspace is given and none is configured."""
    ws = ws or get_workspace()
    path = Path(ws.monitoring_dir) / (ERRORS_FILE if errors else AUDIT_FILE)
    if not path.exists():
        return []
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
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
