"""Deterministic export filenames (spec §46).

`First_Last_<Role>_Resume.pdf` / `First_Last_<Role>_CV.pdf`. The result is
a pure function of its inputs: no random or time-based parts, and no
"final"/"v2"-style decorations. Collisions are resolved by the caller
passing a stable version identifier as `collision_suffix`.
"""

from __future__ import annotations

import re
import unicodedata

from lib.errors import ResumeTailorError

MAX_STEM_CHARS = 100
MAX_FILENAME_BYTES = 255  # common filesystem limit for one path component
ALLOWED_EXTS = ("pdf", "tex", "docx", "md", "txt")
_KIND_LABELS = {"resume": "Resume", "cv": "CV"}
_MAX_SUFFIX_CHARS = 81  # matches lib.workspace._ID_RE (version ids)
_MAX_INPUT_CHARS = 1000  # inputs are pre-truncated before any work

# Characters kept as-is besides Unicode letters, marks and digits.
_SAFE_PUNCT = set("-+.")
_UNDERSCORE_RUN = re.compile(r"_+")


def _sanitize(text: str | None) -> str:
    """NFKC-normalize and reduce to a filename-safe component.

    Letters/marks/digits in any script are kept ("José" stays "José").
    Path separators, Windows-reserved characters (: * ? " < > |), control
    characters, whitespace and any other punctuation/symbols become "_";
    runs of "_" collapse and leading/trailing "_"/"." are stripped.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)[:_MAX_INPUT_CHARS])
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "M", "N") or ch in _SAFE_PUNCT:
            out.append(ch)
        else:
            out.append("_")
    cleaned = _UNDERSCORE_RUN.sub("_", "".join(out))
    # Re-normalize: kept combining marks may have been separated from their base.
    cleaned = unicodedata.normalize("NFKC", cleaned)
    return cleaned.strip("_.")


def _trim(component: str, max_chars: int) -> str:
    """Shorten to at most max_chars, preferring a word ("_") boundary."""
    if len(component) <= max_chars:
        return component
    if max_chars <= 0:
        return ""
    cut = component[:max_chars]
    if component[max_chars] != "_" and "_" in cut:
        cut = cut[: cut.rindex("_")]
    return cut.strip("_.")


def _fits(stem: str, ext: str) -> bool:
    return len(stem) <= MAX_STEM_CHARS and len(f"{stem}.{ext}".encode("utf-8")) <= MAX_FILENAME_BYTES


def deterministic_filename(name: str, role: str | None, kind: str, ext: str,
                           collision_suffix: str | None = None) -> str:
    """Build `<Name>_<Role>_<Resume|CV>[_<suffix>].<ext>`.

    - Empty/unusable name: the name part is dropped, so the stem starts
      with the role, or is just "Resume"/"CV".
    - The stem is capped at 100 characters (and the whole filename at 255
      UTF-8 bytes): the role is trimmed first, then the name. The kind
      label and collision suffix are never trimmed.
    """
    label = _KIND_LABELS.get(str(kind).lower() if kind is not None else "")
    if label is None:
        raise ResumeTailorError("INVALID_KIND", f"kind must be one of {sorted(_KIND_LABELS)}.")
    ext_norm = str(ext or "").lower().lstrip(".")
    if ext_norm not in ALLOWED_EXTS:
        raise ValueError(f"Unsupported extension {ext!r}; allowed: {', '.join(ALLOWED_EXTS)}.")

    tail = [label]
    if collision_suffix is not None:
        suffix = _sanitize(collision_suffix)
        if not suffix or len(suffix) > _MAX_SUFFIX_CHARS:
            raise ResumeTailorError("INVALID_ID", "collision_suffix must be a short, stable identifier.")
        tail.append(suffix)
    tail_str = "_".join(tail)

    name_part = _trim(_sanitize(name), MAX_STEM_CHARS)
    role_part = _trim(_sanitize(role), MAX_STEM_CHARS)

    def build(n: str, r: str) -> str:
        return "_".join(p for p in (n, r, tail_str) if p)

    # Trim the role first (down to nothing), then the name.
    while not _fits(build(name_part, role_part), ext_norm) and role_part:
        role_part = _trim(role_part, len(role_part) - 1)
    while not _fits(build(name_part, role_part), ext_norm) and name_part:
        name_part = _trim(name_part, len(name_part) - 1)

    return f"{build(name_part, role_part)}.{ext_norm}"


_JD_PHRASE = re.compile(r"\bjob\s+description\b\s*[:\-–—|]?", re.IGNORECASE)
_PAREN = re.compile(r"\s*\(([^()]*)\)")
_LOCATION_HINT = re.compile(r",|\b(remote|hybrid|on-?site|in[- ]office|onsite)\b", re.IGNORECASE)
_AT_COMPANY = re.compile(r"\s+(?:at|@)\s+.*$", re.IGNORECASE)
_EDGE_PUNCT = " \t-–—:|,/"


def role_from_jd_title(title: str) -> str:
    """Light cleanup of a JD title into a role for `<Role>`.

    Drops the phrase "Job Description", parenthesized locations (a group
    containing a comma or remote/hybrid/on-site), and a company after
    " at " / " @ ". Other parentheticals (e.g. "(Backend)") are kept.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", str(title)[:_MAX_INPUT_CHARS])
    text = _JD_PHRASE.sub(" ", text)
    text = _PAREN.sub(lambda m: "" if _LOCATION_HINT.search(m.group(1)) else m.group(0), text)
    text = _AT_COMPANY.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(_EDGE_PUNCT)
