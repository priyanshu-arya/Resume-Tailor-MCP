"""Document-structure and provenance-completeness checks (spec §32, §40).

Pure and deterministic. Validates a tailored version (or a master) against
its requested kind, the template contract's section model, and the
provenance invariant that every claim carries at least one source ref.

Any contract field that is missing or the string "unknown" makes the
related check `not_available`, never `pass`. Messages name block IDs,
sections and ref IDs, never resume text.
"""

from __future__ import annotations

from typing import Any

from lib.schemas import SUMMARY_ID, Check
from lib.validators.content import document_kind, required_sections

UNKNOWN = "unknown"

# The order lib/latex.render_latex emits sections in (classic-minimalist).
# The renderer, not the document, controls order; a contract order must be a
# subsequence of this or the PDF would not match the contract.
RENDERER_SECTION_ORDER = ("summary", "experience", "projects", "skills", "education", "certifications")

# Standard ATS section headings (resume_etiquette.yaml page_and_format.layout
# and section_order_by_target). Compared case-insensitively.
STANDARD_HEADINGS = frozenset(h.lower() for h in (
    "Summary", "Professional Summary", "Profile", "Professional Profile", "Research Profile",
    "Experience", "Work Experience", "Professional Experience", "Research Experience",
    "Projects", "Selected Projects", "Skills", "Technical Skills", "Core Competencies",
    "Education", "Certifications", "Licenses & Certifications", "Publications", "Awards",
    "Teaching", "Leadership", "Volunteer Experience",
))

# Top-level keys that are not renderable sections.
_NON_SECTION_KEYS = frozenset({"name", "contact", "metadata", "unparsed", "id"})


def _missing(value) -> bool:
    return value is None or value == UNKNOWN


def _field(contract: Any, *path: str):
    """contract[path...] or None if any step is missing / "unknown"."""
    node = contract
    for key in path:
        if _missing(node) or not isinstance(node, dict):
            return None
        node = node.get(key)
    return None if _missing(node) else node


def _chk(id_, status, severity, category, source, measurement=None, expected=None, message="") -> Check:
    return Check(id=id_, status=status, severity=severity, category=category,
                 measurement=measurement, expected=expected, source=source, message=message)


# --------------------------------------------------------------------------
# Provenance walking
# --------------------------------------------------------------------------

def _refs(obj) -> list[dict]:
    raw = obj.get("source_refs") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict) and r.get("type") in ("master", "evidence") and r.get("id")]


def _claims(doc: dict):
    """Yield (label, refs) for every claim-bearing block in the document."""
    for section in ("experience", "projects", "education"):
        for i, entry in enumerate(doc.get(section) or []):
            if not isinstance(entry, dict):
                continue
            eid = entry.get("id") or f"{section}[{i}]"
            for j, b in enumerate(entry.get("bullets") or []):
                label = (b.get("id") if isinstance(b, dict) else None) or f"{eid}.bullets[{j}]"
                yield label, _refs(b)
    for gi, group in enumerate(doc.get("skills") or []):
        if not isinstance(group, dict):
            continue
        gid = group.get("id") or f"skills[{gi}]"
        for k, item in enumerate(group.get("items") or []):
            label = (item.get("id") if isinstance(item, dict) else None) or f"{gid}.items[{k}]"
            yield label, _refs(item)
    for ci, cert in enumerate(doc.get("certifications") or []):
        label = (cert.get("id") if isinstance(cert, dict) else None) or f"certifications[{ci}]"
        yield label, _refs(cert)
    if doc.get("summary"):
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        yield SUMMARY_ID, _refs({"source_refs": meta.get("summary_source_refs")})


def _list_ids(ids: list[str], limit: int = 10) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _check_kind(doc: dict, kind: str) -> Check:
    actual = document_kind(doc)
    ok = actual == kind
    return _chk("structure.document_kind", "pass" if ok else "fail", "critical", "SOURCE", "provenance",
                actual, kind, "" if ok else f"Document kind is {actual!r}, expected {kind!r}.")


def _check_required(doc: dict, kind: str) -> Check:
    required = required_sections(kind)
    missing = [s for s in required if not doc.get(s)]
    if not missing:
        return _chk("structure.required_sections", "pass", "error", "TEMPLATE", "template_contract",
                    [], sorted(required))
    sev = "error" if any(required[s] == "error" for s in missing) else "warning"
    return _chk("structure.required_sections", "fail", sev, "TEMPLATE", "template_contract", missing,
                sorted(required), f"A {kind} needs these empty section(s): {', '.join(missing)}.")


def _doc_sections(doc: dict) -> list[str]:
    """Non-empty sections in the document (known ones plus any extra list-valued key)."""
    out = [s for s in RENDERER_SECTION_ORDER if doc.get(s)]
    for key, value in doc.items():
        if key not in _NON_SECTION_KEYS and key not in RENDERER_SECTION_ORDER and isinstance(value, list) and value:
            out.append(key)
    return out


def _check_order(doc: dict, contract: dict) -> Check:
    order = _field(contract, "sections", "order")
    if not isinstance(order, list):
        return _chk("structure.section_order", "not_available", "error", "TEMPLATE", "template_contract",
                    None, None, "Template contract has no known section order.")
    problems = []
    unknown = [s for s in order if s not in RENDERER_SECTION_ORDER]
    if unknown:
        problems.append(f"contract lists section(s) the renderer does not produce: {', '.join(map(str, unknown))}")
    if len(set(order)) != len(order):
        problems.append("contract order has duplicates")
    known = [s for s in order if s in RENDERER_SECTION_ORDER]
    if known != [s for s in RENDERER_SECTION_ORDER if s in known]:
        problems.append("contract order differs from the order the renderer emits")
    unrenderable = [s for s in _doc_sections(doc) if s not in order]
    if unrenderable:
        problems.append(f"non-empty section(s) the template would not render: {', '.join(unrenderable)}")
    measurement = {"contract_order": order, "document_sections": _doc_sections(doc)}
    if problems:
        return _chk("structure.section_order", "fail", "error", "TEMPLATE", "template_contract",
                    measurement, list(RENDERER_SECTION_ORDER), "; ".join(problems) + ".")
    return _chk("structure.section_order", "pass", "error", "TEMPLATE", "template_contract",
                measurement, list(RENDERER_SECTION_ORDER))


def _check_headings(contract: dict) -> Check:
    headings = _field(contract, "sections", "headings")
    order = _field(contract, "sections", "order")
    if not isinstance(headings, dict):
        return _chk("structure.standard_headings", "not_available", "error", "TEMPLATE", "template_contract",
                    None, None, "Template contract has no known section headings.")
    nonstandard = sorted(k for k, h in headings.items()
                         if not isinstance(h, str) or h.strip().lower() not in STANDARD_HEADINGS)
    missing = [s for s in order if s not in headings] if isinstance(order, list) else []
    if nonstandard or missing:
        parts = []
        if nonstandard:
            parts.append(f"non-standard heading for section(s): {', '.join(nonstandard)}")
        if missing:
            parts.append(f"no heading for section(s): {', '.join(missing)}")
        return _chk("structure.standard_headings", "fail", "error", "TEMPLATE", "template_contract",
                    headings, "standard ATS headings", "; ".join(parts) + ".")
    return _chk("structure.standard_headings", "pass", "error", "TEMPLATE", "template_contract",
                headings, "standard ATS headings")


def _check_provenance(doc: dict, master_index: dict | None, evidence: dict | None) -> list[Check]:
    claims = list(_claims(doc))
    unsourced = [label for label, refs in claims if not refs]
    out = [_chk("structure.provenance_complete", "fail" if unsourced else "pass", "critical", "FACTUAL",
                "provenance", {"unsourced": unsourced, "claims": len(claims)}, {"unsourced": []},
                f"{len(unsourced)} claim(s) have no source_refs: {_list_ids(unsourced)}." if unsourced else "")]

    for ref_type, index, cid in (("master", master_index, "structure.master_refs_resolve"),
                                 ("evidence", evidence, "structure.evidence_refs_resolve")):
        if index is None:
            out.append(_chk(cid, "not_available", "critical", "FACTUAL", "provenance", None, None,
                            f"No {ref_type} index supplied."))
            continue
        bad = sorted({f"{label}->{r['id']}" for label, refs in claims for r in refs
                      if r["type"] == ref_type and r["id"] not in index})
        out.append(_chk(cid, "fail" if bad else "pass", "critical", "FACTUAL", "provenance",
                        bad, [], f"{len(bad)} {ref_type} ref(s) do not resolve: {_list_ids(bad)}." if bad else ""))
    return out


def _check_unknown_jd(doc: dict) -> Check:
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    reqs = meta.get("unknown_jd_requirements") or []
    reqs = reqs if isinstance(reqs, list) else []
    return _chk("structure.unknown_jd_requirements", "pass", "info", "WORKFLOW", "provenance",
                {"count": len(reqs), "requirements": [str(r) for r in reqs]}, None,
                f"{len(reqs)} JD requirement(s) have no supporting evidence and were left out." if reqs else "")


def check_structure(doc: dict, contract: dict, kind: str, master_index: dict | None = None,
                    evidence: dict[str, dict] | None = None) -> list[Check]:
    doc = doc if isinstance(doc, dict) else {}
    contract = contract if isinstance(contract, dict) else {}
    return [
        _check_kind(doc, kind),
        _check_required(doc, kind),
        _check_order(doc, contract),
        _check_headings(contract),
        *_check_provenance(doc, master_index, evidence),
        _check_unknown_jd(doc),
    ]
