"""Folder-based master discovery and import (R-USER-05..11).

Exactly ONE folder, named by the user, examined one level deep. Nothing here
recurses into a subdirectory, follows a symlink out of that folder, reads a
whole file's content, or picks a candidate for the user -- it ranks what it
found and hands the choice back. Results and error messages name basenames
only, never a full path.

The folder is an import SOURCE, never a live master. `import_master` copies
a validated master into the workspace via the same preview/confirm protocol
every other master write goes through (`lib.master_ops.set_master`); the
source folder is never read again after that.
"""

from __future__ import annotations

import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from lib import parsing
from lib.errors import ResumeTailorError
from lib.locking import sha256_bytes, sha256_text
from lib.schemas import validate_kind
from lib.workspace import app_root

_REPO_ROOT = Path(__file__).resolve().parent.parent

MAX_DIR_ENTRIES = 500        # entries examined before the scan truncates
MAX_CANDIDATES = 20          # candidates returned
SNIFF_BYTES = 4096           # text formats only; never .pdf/.docx

YAML_SUFFIXES = (".yaml", ".yml")
# Discovery accepts a wider set of suffixes than the general-purpose
# set_master_resume(file_path=...) import path (parsing.ALLOWED_SUFFIXES):
# a .yaml/.yml file found through the folder-selection ritual is likely a
# master exported from this tool, and is handled by _load_yaml_candidate
# below rather than by lib.parsing (whose suffix gate deliberately does not
# change, so an arbitrary file_path argument still can't smuggle raw YAML in
# without going through discover_masters first).
DISCOVERY_SUFFIXES = parsing.ALLOWED_SUFFIXES + YAML_SUFFIXES
_TEXT_SNIFF_SUFFIXES = (".md", ".markdown", ".txt", ".yaml", ".yml")

_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}

_CURRICULUM_VITAE_RE = re.compile(r"curriculum[ _.-]?vitae", re.IGNORECASE)
# Filename word boundaries: '_' counts as \w, so \bcv\b would miss "Alex_CV.md".
# Use a letter-lookaround instead so '_', '-', '.' and camelCase all count as
# separators, the way a real filename actually delimits words.
_CV_RE = re.compile(r"(?<![A-Za-z])cv(?![A-Za-z])", re.IGNORECASE)
_RESUME_RE = re.compile(r"(?<![A-Za-z])r[eé]sum[eé](?![A-Za-z])", re.IGNORECASE)
_CV_HEADING_RE = re.compile(r"\b(publications?|teaching(?: experience)?|grants?|"
                            r"awards? and honou?rs?)\b", re.IGNORECASE)
_RESUME_HEADING_RE = re.compile(r"\b(experience|skills|summary)\b", re.IGNORECASE)
_METADATA_BLOCK_RE = re.compile(r"^\s*metadata\s*:", re.MULTILINE)
_KIND_CV_RE = re.compile(r"^\s+kind\s*:\s*['\"]?cv['\"]?\s*$", re.MULTILINE)
_KIND_RESUME_RE = re.compile(r"^\s+kind\s*:\s*['\"]?resume['\"]?\s*$", re.MULTILINE)


# --------------------------------------------------------------------------
# Path safety
# --------------------------------------------------------------------------

def folder_token(resolved: Path) -> str:
    """sha256 of the resolved absolute folder path, first 16 hex chars. A
    stable, non-reversible handle: it proves two imports came from the same
    folder without putting the path in the master or the audit log."""
    return sha256_text(str(resolved))[:16]


def _checked_dir(folder: str) -> Path:
    """Vet a user-supplied folder before a single entry is listed. Sibling of
    parsing._checked_path. Every failure is FOLDER_UNSUPPORTED naming only
    the folder's own basename, never the full path."""
    raw = Path(str(folder)).expanduser()
    name = raw.name or str(raw)
    try:
        path = raw.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):  # ValueError: e.g. an embedded NUL byte
        raise ResumeTailorError("FOLDER_UNSUPPORTED", f"Folder not found: {name}",
                                details={"folder": name}) from None
    try:
        st = path.stat()
    except OSError:
        raise ResumeTailorError("FOLDER_UNSUPPORTED", f"Folder not accessible: {name}",
                                details={"folder": name}) from None
    if not stat.S_ISDIR(st.st_mode):
        raise ResumeTailorError("FOLDER_UNSUPPORTED", f"{name} is not a folder.",
                                details={"folder": name})
    if not os.access(path, os.R_OK | os.X_OK):
        raise ResumeTailorError("FOLDER_UNSUPPORTED", f"Folder not readable: {name}",
                                details={"folder": name})

    root = app_root()
    if path == root or root in path.parents:
        raise ResumeTailorError(
            "FOLDER_UNSUPPORTED",
            "That folder is the Resume Tailor workspace root, not an import source. Point "
            "discover_masters at the folder that holds the user's original resume/CV file.",
            details={"folder": name})
    if path == _REPO_ROOT or _REPO_ROOT in path.parents:
        raise ResumeTailorError(
            "FOLDER_UNSUPPORTED",
            "That folder is inside this server's own repository, not a place personal data "
            "should live. Point discover_masters at the user's own resume folder instead.",
            details={"folder": name})
    return path


def safe_basename(name: str) -> str:
    """Accept a real filename -- spaces, parentheses and unicode included --
    while refusing anything that could address another directory: empty,
    '.', '..', any path separator, control characters, or over 255 bytes.
    Deliberately NOT lib.workspace.validate_id, which rejects a perfectly
    normal filename like 'My Resume (2).pdf'. Path containment is enforced
    separately, by _checked_child's resolve-then-compare-parent."""
    if not isinstance(name, str):
        raise ResumeTailorError("INVALID_ID", "filename must be a string.")
    if name in ("", ".", ".."):
        raise ResumeTailorError("INVALID_ID", "filename must not be empty, '.' or '..'.")
    if "/" in name or "\\" in name or (os.sep and os.sep in name) or (os.altsep and os.altsep in name):
        raise ResumeTailorError("INVALID_ID", "filename must not contain a path separator.")
    if any(ch in _CONTROL_CHARS for ch in name):
        raise ResumeTailorError("INVALID_ID", "filename must not contain control characters.")
    if len(name.encode("utf-8")) > 255:
        raise ResumeTailorError("INVALID_ID", "filename is too long.")
    return name


def _checked_child(directory: Path, filename: str) -> Path:
    """safe_basename, then resolve-and-assert-parent (the same trick that
    makes workspace.safe_child symlink-proof), then lstat + S_ISREG so a
    symlink target is never silently opened, then parsing._checked_path for
    suffix/size on every non-YAML format."""
    name = safe_basename(filename)
    candidate = (directory / name).resolve()
    if candidate.parent != directory:
        raise ResumeTailorError("PATH_TRAVERSAL", "Path escapes the selected folder.")
    try:
        lst = candidate.lstat()
    except OSError:
        raise ResumeTailorError("IMPORT_UNSUPPORTED", f"File not found: {name}",
                                details={"file": name}) from None
    if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
        raise ResumeTailorError("IMPORT_UNSUPPORTED", f"{name} is not a regular file.",
                                details={"file": name})

    suffix = candidate.suffix.lower()
    if suffix not in DISCOVERY_SUFFIXES:
        raise ResumeTailorError(
            "IMPORT_UNSUPPORTED",
            f"Unsupported resume format {suffix or '(none)'!r}; use one of {', '.join(DISCOVERY_SUFFIXES)}.",
            details={"file": name, "suffix": suffix})
    if suffix in YAML_SUFFIXES:
        if lst.st_size > parsing.MAX_IMPORT_BYTES:
            raise ResumeTailorError(
                "IMPORT_TOO_LARGE",
                f"{name} is {lst.st_size:,} bytes; the import limit is {parsing.MAX_IMPORT_BYTES:,} bytes.",
                details={"file": name, "size": lst.st_size, "max_bytes": parsing.MAX_IMPORT_BYTES})
        return candidate
    return parsing._checked_path(str(candidate))


def _load_yaml_candidate(path: Path) -> dict:
    """Load a .yaml/.yml candidate as a raw resume document, through the same
    hardened loader every other YAML file in this app goes through (no
    anchors/aliases/python tags; depth capped at 20, size at 2 MB -- tighter
    than the 5 MB general import cap). `metadata` is stripped: kind, career
    stage and provenance are supplied by this import, never trusted from a
    file the user merely pointed at.

    The version-marker check runs BEFORE that strip, not after: metadata is
    exactly where those markers (version_id, released, ...) live, so
    stripping first would make a tailored version YAML indistinguishable
    from an authored master and silently defeat _reject_version_document."""
    from lib import safe_yaml
    from lib.master_ops import _reject_version_document
    data = safe_yaml.load_file(path)
    if not isinstance(data, dict):
        raise ResumeTailorError("IMPORT_UNSUPPORTED", f"{path.name} is not a YAML mapping.",
                                details={"file": path.name})
    _reject_version_document(data)
    data = dict(data)
    data.pop("metadata", None)
    return data


# --------------------------------------------------------------------------
# Classification (pure)
# --------------------------------------------------------------------------

def classify_candidate(filename: str, sniff: str | None) -> dict:
    """Pure (no filesystem), so the guessing rules are unit-testable.
    {"kind_guess": "resume"|"cv"|"unknown", "reason": str,
    "confidence": "high"|"medium"|"low", "score": int}. First match wins,
    and `reason` always names the signal used -- this never silently guesses
    without saying why."""
    stem = Path(str(filename)).stem

    if sniff and _METADATA_BLOCK_RE.search(sniff):
        if _KIND_CV_RE.search(sniff):
            return {"kind_guess": "cv", "reason": "file declares metadata.kind: cv",
                    "confidence": "high", "score": 100}
        if _KIND_RESUME_RE.search(sniff):
            return {"kind_guess": "resume", "reason": "file declares metadata.kind: resume",
                    "confidence": "high", "score": 100}

    if _CURRICULUM_VITAE_RE.search(stem) or _CV_RE.search(stem):
        return {"kind_guess": "cv", "reason": "filename names 'cv' / 'curriculum vitae'",
                "confidence": "high", "score": 90}
    if _RESUME_RE.search(stem):
        return {"kind_guess": "resume", "reason": "filename names 'resume'",
                "confidence": "high", "score": 90}

    if sniff:
        cv_hits = len(_CV_HEADING_RE.findall(sniff))
        resume_hits = len(_RESUME_HEADING_RE.findall(sniff))
        if cv_hits and cv_hits >= resume_hits:
            return {"kind_guess": "cv",
                    "reason": "content has CV-style headings (publications/teaching/grants/awards)",
                    "confidence": "medium", "score": 60}
        if resume_hits:
            return {"kind_guess": "resume",
                    "reason": "content has resume-style headings (experience/skills/summary)",
                    "confidence": "medium", "score": 55}
        return {"kind_guess": "unknown", "reason": "no filename or content signal was found",
                "confidence": "low", "score": 10}

    return {"kind_guess": "unknown",
            "reason": "no content signal was available (binary format, or the file could not be "
                      "read): the filename is the only signal, and it named neither 'resume' nor 'cv'",
            "confidence": "low", "score": 5}


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def _mtime_iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def _sniff_text(path: Path) -> str | None:
    """First SNIFF_BYTES of a text-format file, best-effort. None for binary
    formats (.pdf/.docx) or on any read failure -- never raises."""
    if path.suffix.lower() not in _TEXT_SNIFF_SUFFIXES:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read(SNIFF_BYTES)
    except OSError:
        return None
    return raw.decode("utf-8", errors="ignore")


def discover_masters(folder: str, kind: str | None = None) -> dict:
    """Scan ONE folder, one level. Returns ranked candidates and NEVER a
    selection.

    {"ok", "folder_name", "folder_token", "entries_examined", "truncated",
     "candidate_count", "requires_user_selection",
     "candidates": [{"filename", "suffix", "size_bytes", "modified",
                     "kind_guess", "kind_guess_reason",
                     "kind_guess_confidence", "score",
                     "importable", "skip_reason"}],
     "next_step", "refuse"}

    Subdirectories and symlinked entries are skipped; at most
    MAX_DIR_ENTRIES entries are examined and `truncated` says so;
    oversized/unreadable files are RETURNED with importable=False and a
    skip_reason rather than silently dropped, so "nothing found" is never a
    lie. `kind`, if given, only nudges ranking -- it never hides a
    candidate. Zero candidates raises NO_MASTER_CANDIDATES. There is no
    `selected` field at any candidate count."""
    if kind is not None:
        validate_kind(kind)
    directory = _checked_dir(folder)

    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        raise ResumeTailorError("FOLDER_UNSUPPORTED", f"Folder not readable: {directory.name}",
                                details={"folder": directory.name}) from None

    examined = 0
    rows = []
    for entry in entries:
        if examined >= MAX_DIR_ENTRIES:
            break
        examined += 1
        try:
            lst = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(lst.st_mode) or not stat.S_ISREG(lst.st_mode):
            continue  # symlinks and subdirectories are never entered
        suffix = entry.suffix.lower()
        if suffix not in DISCOVERY_SUFFIXES:
            continue

        importable, skip_reason = True, None
        if lst.st_size > parsing.MAX_IMPORT_BYTES:
            importable, skip_reason = False, "file too large"

        sniff = _sniff_text(entry)
        guess = classify_candidate(entry.name, sniff)
        score = guess["score"] + (5 if kind is not None and guess["kind_guess"] == kind else 0)

        rows.append({
            "filename": entry.name,
            "suffix": suffix,
            "size_bytes": lst.st_size,
            "modified": _mtime_iso(lst.st_mtime),
            "kind_guess": guess["kind_guess"],
            "kind_guess_reason": guess["reason"],
            "kind_guess_confidence": guess["confidence"],
            "score": score,
            "importable": importable,
            "skip_reason": skip_reason,
            "_mtime": lst.st_mtime,
        })

    truncated = len(entries) > examined

    rows.sort(key=lambda c: (-c["score"], -c["_mtime"], c["filename"]))
    rows = rows[:MAX_CANDIDATES]
    for r in rows:
        del r["_mtime"]

    if not rows:
        raise ResumeTailorError(
            "NO_MASTER_CANDIDATES",
            f"No usable resume/CV file was found in that folder (looked for "
            f"{', '.join(DISCOVERY_SUFFIXES)}). Ask the user for a different folder, or use the "
            "create-master-file skill to build one from scratch. Do not reconstruct their resume "
            "from memory or from this conversation.",
            details={"folder": directory.name, "entries_examined": examined})

    multi = len(rows) > 1
    return {
        "ok": True,
        "folder_name": directory.name,
        "folder_token": folder_token(directory),
        "entries_examined": examined,
        "truncated": truncated,
        "candidate_count": len(rows),
        "requires_user_selection": multi,
        "candidates": rows,
        "next_step": (
            "Show the user this list (filename, kind guess and the reason for it) and ask which "
            "ONE file and which kind (resume or cv) to import, then call "
            "import_master_from_folder(folder, filename, kind)." if multi else
            "Confirm the filename and kind with the user, then call "
            "import_master_from_folder(folder, filename, kind)."
        ),
        "refuse": "Never pick a candidate automatically, and never import more than the one file "
                 "the user names.",
    }


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def import_master(folder: str, filename: str, kind: str, *, career_stage: str | None = None,
                  accept_unparsed: bool = False, expected_hash: str | None = None,
                  confirm: bool = False, proposed_hash: str | None = None, ws=None) -> dict:
    """Copy-in import of ONE explicitly named file as the master of ONE
    explicitly named kind. All three are required -- there is no code path
    here that picks a file or a kind on its own.

    Vets folder + filename, then delegates to master_ops.set_master so
    validation, the preview/confirm protocol, the backup and the history
    entry are byte-for-byte the same as every other master write. The source
    file is never modified and never read again after this call."""
    validate_kind(kind)
    directory = _checked_dir(folder)
    path = _checked_child(directory, filename)

    source_bytes = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in YAML_SUFFIXES:
        set_kwargs = {"resume": _load_yaml_candidate(path)}
    else:
        set_kwargs = {"file_path": str(path)}

    from lib.schemas import ImportInfo
    import_info = ImportInfo(
        source_filename=path.name,
        source_folder_name=directory.name,
        source_folder_hash=folder_token(directory),
        source_hash=sha256_bytes(source_bytes),
    ).model_dump(mode="json")

    from lib import master_ops
    return master_ops.set_master(
        kind, mode="replace", expected_hash=expected_hash, confirm=confirm,
        proposed_hash=proposed_hash, career_stage=career_stage, accept_unparsed=accept_unparsed,
        import_info=import_info, ws=ws, **set_kwargs,
    )
