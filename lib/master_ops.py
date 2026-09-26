"""Explicit, previewed, confirmed changes to a master (spec §59, §63, §64).

The master is the only source of truth for tailoring, so it must never be
changed as a side effect. `set_master` is the one entry point for creating,
replacing or updating a master, and it works in two steps whenever there is
something to lose:

1. Without `confirm`, it builds the candidate document, writes nothing, and
   returns a unified YAML diff plus two hashes: the current master's hash
   and the candidate's hash (`proposed_hash`).
2. With `confirm=True`, it rebuilds the candidate and only writes if the
   rebuilt candidate hashes to exactly the `proposed_hash` the user
   approved, and the master still hashes to `expected_hash`. That makes
   "what was written" provably "what was shown". `storage.save_master`
   then re-checks the hash under the workspace lock, backs up the old
   master and writes atomically.

Creating the first master writes immediately: there is nothing to protect.

Block IDs are what tailored versions cite, so they must stay stable and a
deleted ID must never be reissued to a different block. Both modes
therefore carry the current master's `metadata.id_counters` high-water
marks forward (per-prefix max) *before* IDs are assigned, so any new block
gets an ID above every ID the master has ever issued.
"""

from __future__ import annotations

import copy
import difflib

from lib import ids, parsing, storage
from lib.errors import ResumeTailorError
from lib.locking import sha256_of
from lib.schemas import validate_kind

CAREER_STAGES = ("fresher", "1-3", "3-5", "5-10", "manager", "director", "academic")
MODES = ("replace", "update")
MAX_DIFF_LINES = 400
CONTENT_SECTIONS = ("summary", "skills", "experience", "education", "projects", "certifications")

NEXT_STEP = ("Show the diff to the user; if they approve call again with confirm=True, "
             "expected_hash=current_hash, proposed_hash=proposed_hash.")


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

def master_readiness(doc: dict | None) -> dict:
    """A master is ready for tailoring when it has no unparsed content, or
    the user has explicitly accepted leaving that content out."""
    doc = doc or {}
    unparsed = len(doc.get("unparsed") or [])
    accepted = bool((doc.get("metadata") or {}).get("unparsed_accepted"))
    return {"ready": unparsed == 0 or accepted, "unparsed_items": unparsed, "unparsed_accepted": accepted}


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------

def _invalid(message: str, **details) -> ResumeTailorError:
    return ResumeTailorError("MASTER_INVALID", message, details=details)


def _validate_args(kind, file_path, resume, mode, career_stage) -> None:
    validate_kind(kind)
    if mode not in MODES:
        raise _invalid(f"mode must be one of {MODES}, got {mode!r}.", mode=str(mode))
    if mode == "replace" and (file_path is None) == (resume is None):
        raise _invalid("mode='replace' takes exactly one of file_path or resume.")
    if mode == "update":
        if file_path is not None:
            raise _invalid("mode='update' does not accept file_path; pass the full edited resume instead.")
        if resume is None:
            raise _invalid("mode='update' requires resume (the full edited master).")
    if resume is not None and not isinstance(resume, dict):
        raise _invalid("resume must be a mapping (the structured master document).")
    if career_stage is not None and career_stage not in CAREER_STAGES:
        raise _invalid(f"career_stage must be one of {CAREER_STAGES}.", career_stage=str(career_stage))


# --------------------------------------------------------------------------
# Building the candidate
# --------------------------------------------------------------------------

def _merge_counters(*counter_maps) -> dict:
    merged: dict[str, int] = {}
    for counters in counter_maps:
        for prefix, n in (counters or {}).items():
            try:
                merged[prefix] = max(merged.get(prefix, 0), int(n))
            except (TypeError, ValueError):
                continue
    return merged


def _raw_input(file_path, resume) -> tuple[dict, str]:
    if file_path is not None:
        return parsing.parse_resume_file(file_path), "import from file"
    return copy.deepcopy(resume), "replace from structured resume"


def _seed_metadata(raw: dict, current: dict | None, mode: str) -> dict:
    """Metadata the candidate starts from, before IDs are assigned."""
    cand_meta = dict(raw.get("metadata") or {})
    cur_meta = dict((current or {}).get("metadata") or {})
    if current is None:
        return cand_meta
    cand_meta["id_counters"] = _merge_counters(cur_meta.get("id_counters"), cand_meta.get("id_counters"))
    if cur_meta.get("migration") is not None:
        cand_meta["migration"] = copy.deepcopy(cur_meta["migration"])
    if mode == "update":
        for key in ("career_stage", "unparsed_accepted"):
            if key in cur_meta:
                cand_meta[key] = cur_meta[key]
    return cand_meta


def build_candidate(kind: str, raw: dict, current: dict | None, mode: str,
                    career_stage: str | None, accept_unparsed: bool) -> dict:
    """Pure: raw resume dict + current master -> normalized candidate master."""
    raw = copy.deepcopy(raw or {})
    raw["metadata"] = _seed_metadata(raw, current, mode)
    candidate = ids.normalize_master(raw, kind)
    meta = candidate["metadata"]
    if career_stage is not None:
        meta["career_stage"] = career_stage
    if accept_unparsed:
        meta["unparsed_accepted"] = True
    ids.assign_ids(candidate)  # idempotent; fills anything still missing above the high-water mark
    return candidate


# --------------------------------------------------------------------------
# Diff + results
# --------------------------------------------------------------------------

def yaml_diff(current: dict, candidate: dict, kind: str, limit: int = MAX_DIFF_LINES) -> str:
    lines = list(difflib.unified_diff(
        storage.dump_yaml(current).splitlines(),
        storage.dump_yaml(candidate).splitlines(),
        fromfile=f"master_{kind} (current)", tofile=f"master_{kind} (proposed)", lineterm="",
    ))
    if len(lines) > limit:
        omitted = len(lines) - limit
        lines = lines[:limit] + [f"... diff truncated: {omitted} more lines not shown ..."]
    return "\n".join(lines) if lines else "(no changes)"


def _sections_found(doc: dict) -> list[str]:
    return [s for s in CONTENT_SECTIONS if doc.get(s)]


def _applied_result(kind: str, doc: dict, new_hash: str) -> dict:
    r = master_readiness(doc)
    if r["ready"]:
        note = "Master saved and ready for tailoring."
    else:
        note = (f"Master saved, but it is NOT ready for tailoring: {r['unparsed_items']} unparsed item(s) "
                "must be placed into the proper sections (update the master), or the user must explicitly "
                "accept leaving them out (call again with accept_unparsed=True).")
    return {"ok": True, "applied": True, "kind": kind, "master_hash": new_hash,
            "master_ready": r["ready"], "unparsed_items": r["unparsed_items"],
            "sections_found": _sections_found(doc), "note": note}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def set_master(kind: str, *, file_path: str | None = None, resume: dict | None = None,
               mode: str = "replace", expected_hash: str | None = None, confirm: bool = False,
               proposed_hash: str | None = None, career_stage: str | None = None,
               accept_unparsed: bool = False, ws=None) -> dict:
    """Create, replace or update a master. See the module docstring for the
    preview -> confirm protocol."""
    _validate_args(kind, file_path, resume, mode, career_stage)
    current, current_hash = storage.load_master(kind, ws)
    if mode == "update":
        if current is None:
            storage.require_master(kind, ws)  # raises MASTER_NOT_FOUND
        raw, reason = copy.deepcopy(resume), "update"
    else:
        raw, reason = _raw_input(file_path, resume)

    candidate = build_candidate(kind, raw, current, mode, career_stage, accept_unparsed)
    storage.validate_master_doc(candidate, kind)  # never preview something that can't be saved
    candidate_hash = sha256_of(candidate)

    if current is None:
        new_hash = storage.save_master(kind, candidate, None, reason, ws)
        return _applied_result(kind, candidate, new_hash)

    if not confirm:
        return {"ok": True, "applied": False, "kind": kind, "current_hash": current_hash,
                "proposed_hash": candidate_hash, "diff": yaml_diff(current, candidate, kind),
                "next_step": NEXT_STEP}

    if expected_hash is None:
        raise ResumeTailorError("CONFIRMATION_REQUIRED",
                                "confirm=True needs expected_hash (the current_hash from the preview).",
                                details={"current_hash": current_hash})
    if expected_hash != current_hash:
        raise ResumeTailorError("MASTER_CONFLICT", f"The master {kind} changed since it was previewed.",
                                details={"expected_hash": expected_hash, "current_hash": current_hash})
    if proposed_hash != candidate_hash:
        raise ResumeTailorError(
            "CONFIRMATION_REQUIRED",
            "proposed_hash does not match the change being applied; preview again (confirm=False) "
            "and show the new diff to the user.",
            details={"proposed_hash": proposed_hash, "candidate_hash": candidate_hash},
        )
    new_hash = storage.save_master(kind, candidate, expected_hash, reason, ws)
    return _applied_result(kind, candidate, new_hash)
