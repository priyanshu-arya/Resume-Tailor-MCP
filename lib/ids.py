"""Stable master block IDs and the block index (spec §12).

`normalize_master` converts a legacy/parsed resume dict (skill items and
certifications as plain strings, bullets as strings) into the ID-bearing
master shape. `assign_ids` is deterministic and only fills IDs that are
missing, so re-running it never renumbers blocks Claude may already cite.

`index_blocks` is what patch/provenance validation resolves source refs
against: every citable block ID -> its type, section, parent, text and the
evidence category the master block can support (derived from its section).
"""

from __future__ import annotations

import copy
import re

from lib.schemas import MASTER_SKILL_CATEGORY, MASTER_SUMMARY_CATEGORY, SCHEMA_VERSION, SUMMARY_ID, validate_kind

_SECTIONS = ("skills", "experience", "education", "projects", "certifications")
_INTERN_RE = re.compile(r"\bintern(ship)?s?\b", re.IGNORECASE)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "item"


def _as_bullet(b) -> dict:
    return dict(b) if isinstance(b, dict) else {"text": str(b)}


def normalize_master(raw: dict, kind: str) -> dict:
    """Legacy/parsed dict -> master shape with IDs and metadata. Pure."""
    validate_kind(kind)
    doc = copy.deepcopy(raw or {})
    doc.setdefault("name", "")
    doc["contact"] = doc.get("contact") or {}
    doc["summary"] = doc.get("summary") or ""
    for key in _SECTIONS:
        doc[key] = doc.get(key) or []
    doc["unparsed"] = doc.get("unparsed") or []

    groups = []
    for g in doc["skills"]:
        if isinstance(g, str):
            g = {"category": "General", "items": [g]}
        g = dict(g)
        g["items"] = [dict(i) if isinstance(i, dict) else {"name": str(i)} for i in (g.get("items") or [])]
        groups.append(g)
    doc["skills"] = groups

    for key in ("experience", "education", "projects"):
        entries = []
        for e in doc[key]:
            e = dict(e)
            e["bullets"] = [_as_bullet(b) for b in (e.get("bullets") or [])]
            entries.append(e)
        doc[key] = entries

    doc["certifications"] = [dict(c) if isinstance(c, dict) else {"text": str(c)} for c in doc["certifications"]]

    meta = dict(doc.get("metadata") or {})
    meta["kind"] = kind
    meta.setdefault("schema_version", SCHEMA_VERSION)
    doc["metadata"] = meta
    return assign_ids(doc)


def _next_free(prefix: str, used: set[str], width: int = 3, counters: dict | None = None) -> str:
    """Next ID after the highest ever issued for `prefix` -- never the lowest
    gap. A deleted block's ID is never reissued, so an older version that
    cites it can't silently point at a different block. The high-water mark
    persists in metadata.id_counters."""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    highest = max((int(m.group(1)) for u in used if (m := pattern.match(u))), default=0)
    if counters is not None:
        highest = max(highest, int(counters.get(prefix, 0)))
    n = highest + 1
    new = f"{prefix}{n:0{width}d}"
    used.add(new)
    if counters is not None:
        counters[prefix] = n
    return new


def assign_ids(doc: dict) -> dict:
    """Fill missing IDs in place (and return doc). Existing IDs are kept and
    deleted IDs are never reused (see _next_free)."""
    used: set[str] = set(_all_ids(doc))
    meta = doc.get("metadata")
    counters = meta.setdefault("id_counters", {}) if isinstance(meta, dict) else None
    _next = lambda prefix, width=3: _next_free(prefix, used, width, counters)  # noqa: E731
    for u in list(used):  # record already-present IDs in the high-water marks
        m = re.match(r"^(.*?-(?:b)?)(\d+)$", u)
        if counters is not None and m and not u.startswith("skill-"):
            counters[m.group(1)] = max(int(counters.get(m.group(1), 0)), int(m.group(2)))

    for key, prefix in (("experience", "exp-"), ("projects", "proj-"), ("education", "edu-")):
        for entry in doc.get(key) or []:
            if not entry.get("id"):
                entry["id"] = _next(prefix)
            for b in entry.get("bullets") or []:
                if not b.get("id"):
                    b["id"] = _next(f"{entry['id']}-b", 2)

    for group in doc.get("skills") or []:
        if not group.get("id"):
            group["id"] = _next("skg-")
        for item in group.get("items") or []:
            if not item.get("id"):
                base = f"skill-{_slug(item.get('name', ''))}"
                candidate, n = base, 2
                while candidate in used:
                    candidate = f"{base}-{n}"
                    n += 1
                item["id"] = candidate
                used.add(candidate)

    for cert in doc.get("certifications") or []:
        if not cert.get("id"):
            cert["id"] = _next("cert-")
    return doc


def _all_ids(doc: dict):
    for key in ("experience", "projects", "education"):
        for entry in doc.get(key) or []:
            if isinstance(entry, dict):
                if entry.get("id"):
                    yield entry["id"]
                for b in entry.get("bullets") or []:
                    if isinstance(b, dict) and b.get("id"):
                        yield b["id"]
    for group in doc.get("skills") or []:
        if isinstance(group, dict):
            if group.get("id"):
                yield group["id"]
            for item in group.get("items") or []:
                if isinstance(item, dict) and item.get("id"):
                    yield item["id"]
    for cert in doc.get("certifications") or []:
        if isinstance(cert, dict) and cert.get("id"):
            yield cert["id"]


def experience_category(entry: dict) -> str:
    return "internship" if _INTERN_RE.search(entry.get("title", "") or "") else "professional"


def project_category(entry: dict) -> str:
    return "academic" if entry.get("academic") else "personal_project"


def index_blocks(doc: dict) -> dict[str, dict]:
    """{block_id: {type, section, parent_id, text, category}} for a master
    (or a version -- the shape is identical). `category` is None for blocks
    that cannot be cited as evidence (summary, skill groups)."""
    index: dict[str, dict] = {}

    def add(block_id, type_, section, parent_id, text, category):
        if block_id:
            index[block_id] = {"type": type_, "section": section, "parent_id": parent_id,
                               "text": text or "", "category": category}

    # The master summary may support a rewritten summary (placement: summary
    # only). An empty summary has nothing to cite.
    add(SUMMARY_ID, "summary", "summary", None, doc.get("summary", ""),
        MASTER_SUMMARY_CATEGORY if doc.get("summary") else None)

    for e in doc.get("experience") or []:
        cat = experience_category(e)
        add(e.get("id"), "experience", "experience", None, f"{e.get('title', '')} {e.get('company', '')}".strip(), cat)
        for b in e.get("bullets") or []:
            add(b.get("id"), "experience_bullet", "experience", e.get("id"), b.get("text", ""), cat)

    for p in doc.get("projects") or []:
        cat = project_category(p)
        add(p.get("id"), "project", "projects", None, " ".join(x for x in (p.get("name"), p.get("stack")) if x), cat)
        for b in p.get("bullets") or []:
            add(b.get("id"), "project_bullet", "projects", p.get("id"), b.get("text", ""), cat)

    for ed in doc.get("education") or []:
        add(ed.get("id"), "education", "education", None, f"{ed.get('degree', '')} {ed.get('school', '')}".strip(), "academic")
        for b in ed.get("bullets") or []:
            add(b.get("id"), "education_bullet", "education", ed.get("id"), b.get("text", ""), "academic")

    for g in doc.get("skills") or []:
        add(g.get("id"), "skill_group", "skills", None, g.get("category", ""), None)
        for item in g.get("items") or []:
            add(item.get("id"), "skill_item", "skills", g.get("id"), item.get("name", ""), MASTER_SKILL_CATEGORY)

    for c in doc.get("certifications") or []:
        add(c.get("id"), "certification", "certifications", None, c.get("text", ""), "certification")

    return index


# Renderers and analyzers accept both the legacy shape (plain strings) and
# the ID-bearing shape (dicts); these two helpers are the only place that
# difference is handled.

def skill_names(group: dict) -> list[str]:
    return [i.get("name", "") if isinstance(i, dict) else str(i) for i in (group.get("items") or [])]


def cert_text(cert) -> str:
    return cert.get("text", "") if isinstance(cert, dict) else str(cert)
