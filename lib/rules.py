"""Machine-readable truth rules, loaded from resources/resume_etiquette.yaml.

The etiquette file is the single source of truth; this module only reads it
(cached, re-read when the file's mtime changes) and answers small questions
the provenance validator asks. Every lookup fails closed: an unknown category,
section or malformed table entry means "no".
"""

from __future__ import annotations

import threading
from pathlib import Path

from lib.errors import ResumeTailorError
from lib.safe_yaml import load_file

ETIQUETTE_PATH = Path(__file__).resolve().parent.parent / "resources" / "resume_etiquette.yaml"

PLACEMENT_VALUES = ("yes", "limited", "no")

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": None}


def _load() -> dict:
    try:
        mtime = ETIQUETTE_PATH.stat().st_mtime_ns
    except OSError:
        raise ResumeTailorError("RULES_UNAVAILABLE", "The resume rules file is missing.") from None
    with _lock:
        if _cache["data"] is None or _cache["mtime"] != mtime:
            data = load_file(ETIQUETTE_PATH)
            if not isinstance(data, dict):
                raise ResumeTailorError("RULES_UNAVAILABLE", "The resume rules file is malformed.")
            _cache["data"], _cache["mtime"] = data, mtime
        return _cache["data"]


def _placement_value(v) -> str:
    # Bare yes/no would be YAML 1.1 booleans; accept them, fail closed otherwise.
    if v is True:
        return "yes"
    if isinstance(v, str) and v.strip().lower() in PLACEMENT_VALUES:
        return v.strip().lower()
    return "no"


def rules_version() -> str:
    version = _load().get("rules_version")
    if not isinstance(version, str) or not version:
        raise ResumeTailorError("RULES_UNAVAILABLE", "The resume rules file has no rules_version.")
    return version


def placement(category: str, section: str) -> str:
    """'yes' | 'limited' | 'no' for citing `category` evidence in `section`."""
    table = _load().get("evidence_placement")
    if not isinstance(table, dict) or not isinstance(category, str) or not isinstance(section, str):
        return "no"
    row = table.get(category)
    if not isinstance(row, dict) or section not in row:
        return "no"
    return _placement_value(row[section])


def high_scope_families() -> list[set[str]]:
    raw = (_load().get("verb_scope") or {}).get("high_scope") or []
    families: list[set[str]] = []
    for entry in raw:
        forms = entry if isinstance(entry, list) else [entry]
        family = {str(f).strip().lower() for f in forms if str(f).strip()}
        if family:
            families.append(family)
    return families


def safe_verbs() -> set[str]:
    raw = (_load().get("verb_scope") or {}).get("safe") or []
    return {str(v).strip().lower() for v in raw if str(v).strip()}


def familiarity_markers() -> list[str]:
    raw = _load().get("familiarity_group_markers") or []
    return [str(m).strip().lower() for m in raw if str(m).strip()]


def evidence_categories() -> list[str]:
    return [str(c) for c in (_load().get("evidence_categories") or [])]
