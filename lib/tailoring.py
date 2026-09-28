"""Master-only tailoring (spec §13-14, §37, §60).

The server loads the workspace master itself; Claude only supplies
structured patches (validated by lib/patches.py) and the IDs of evidence
the user confirmed in this workflow. There is deliberately no way to pass a
resume body in, and no way to use a previous tailored version as a source:
a version is a historical output, never an input.

Repairs follow the same rule. A repair does not edit the failing version --
it re-applies that version's original (already validated) patches plus
new drop/reorder-only patches to the *same* master, producing a new
version. Source refs therefore never drift, and a repair can only remove or
reorder content, never add claims.
"""

from __future__ import annotations

from lib import storage, workflows
from lib.errors import ResumeTailorError
from lib.locking import atomic_write_yaml, workspace_lock
from lib.matching import match_resume_to_jd
from lib.patches import parse_patches, validate_and_apply
from lib.schemas import REPAIR_SAFE_OPERATIONS, validate_kind
from lib.workspace import Workspace, get_workspace, safe_child, slugify, utc_now_iso
from lib.locking import sha256_text


def _patches_path(ws: Workspace, version_id: str):
    return safe_child(ws.sessions_dir, f"patches-{version_id}", ".yaml")


def load_version_patches(version_id: str, ws: Workspace) -> dict:
    data = storage.yaml_load_file(_patches_path(ws, version_id))
    if data is None:
        raise ResumeTailorError("VERSION_NOT_FOUND", f"No recorded patches for version {version_id!r}.")
    return data


def _load_evidence(ws: Workspace, workflow_id: str, evidence_ids: list[str]) -> dict[str, dict]:
    if not evidence_ids:
        return {}
    try:
        from lib import evidence as _evidence
    except ImportError:
        raise ResumeTailorError("EVIDENCE_NOT_FOUND", "Evidence support is not available yet.") from None
    return _evidence.load_evidence(workflow_id, evidence_ids, ws=ws)


def _provenance_hook(master: dict, evidence: dict[str, dict]):
    try:
        from lib.validators import provenance as _prov
    except ImportError:
        return None
    return _prov.make_hook(master, evidence)


def _resolve_template(template: str, master: dict, jd_text: str | None) -> tuple[str, str | None]:
    try:
        from lib import templates as _templates
        resolve = _templates.resolve_for_tailoring
    except (ImportError, AttributeError):
        return ("classic-minimalist" if template in (None, "", "auto") else template), None
    return resolve(template, master, jd_text)


def _rules_version() -> str | None:
    try:
        from lib import rules as _rules
    except ImportError:
        return None
    return _rules.rules_version()


def _choose_version_id(save_as: str, workflow_id: str, ws: Workspace) -> str:
    base = slugify(save_as)
    if not storage.version_exists(base, ws):
        return base
    suffixed = f"{base}-{sha256_text(workflow_id + save_as)[:4]}"
    if storage.version_exists(suffixed, ws):
        raise ResumeTailorError("VERSION_EXISTS", f"Version {base!r} (and {suffixed!r}) already exist; "
                                "choose a different save_as.")
    return suffixed


_SECTION_LABELS = {"experience": "Experience", "projects": "Projects", "education": "Education",
                   "skills": "Skills", "summary": "Summary"}


def evidence_usage(body: dict, summary_refs: list[dict], evidence: dict[str, dict], workflow: dict) -> dict:
    """What confirmed evidence was actually used, where, and what was
    intentionally left out (spec §57-58). Derived from the saved blocks'
    source_refs, so it reports what is really on the page."""
    used: list[dict] = []

    def note(refs, section, block_id):
        for ref in refs or []:
            if ref.get("type") == "evidence" and ref.get("id") in evidence:
                ev = evidence[ref["id"]]
                used.append({"term": ev.get("term_display") or ev["term"], "category": ev["category"],
                             "section": _SECTION_LABELS[section], "evidence_id": ev["id"], "block_id": block_id})

    note(summary_refs, "summary", "sum-001")
    for section in ("experience", "projects", "education"):
        for entry in body.get(section) or []:
            if section == "projects":
                # An entry's own source_refs are only set for a brand-new
                # entry (add_project_entry); an existing master project has
                # none, so this is a no-op for it.
                note(entry.get("source_refs"), section, entry.get("id"))
            for b in entry.get("bullets") or []:
                note(b.get("source_refs"), section, b.get("id"))
    for group in body.get("skills") or []:
        for item in group.get("items") or []:
            if isinstance(item, dict):
                note(item.get("source_refs"), "skills", item.get("id"))

    used_terms = {u["term"].lower() for u in used} | {evidence[u["evidence_id"]]["term"] for u in used}
    not_added = [{"term": ev.get("term_display") or ev["term"], "reason": "you said you have no experience with it"}
                 for ev in evidence.values() if ev["category"] == "none"]
    declined = {ev["term"] for ev in evidence.values() if ev["category"] == "none"}
    analysis = workflow.get("analysis") or {}
    requirements = analysis.get("requirements")
    if requirements:
        status_by_term = {r["term"]: r.get("status") for r in requirements}
        for term in analysis.get("priority_missing", []):
            if term in used_terms or term in declined:
                continue
            if status_by_term.get(term) == "weak":
                not_added.append({"term": term, "reason": "already listed under Skills; no bullet added"})
            else:
                not_added.append({"term": term, "reason": "no supporting evidence in the master or confirmed by you"})
    else:
        for term in analysis.get("priority_missing", []):
            if term not in used_terms and term not in declined:
                not_added.append({"term": term, "reason": "no supporting evidence in the master or confirmed by you"})
    return {"added_terms": used, "not_added": not_added}


def tailor(save_as: str, patches: list[dict], *, workflow_id: str, jd_text: str | None = None,
           evidence_ids: list[str] | None = None, template: str = "auto", source_kind: str = "resume",
           repair_of: str | None = None, ws: Workspace | None = None) -> dict:
    ws = ws or get_workspace()
    validate_kind(source_kind)
    workflow = workflows.load_workflow(workflows.check_workflow_id(workflow_id), ws)
    if workflow["source_kind"] != source_kind:
        raise ResumeTailorError("INVALID_KIND", f"Workflow {workflow_id} was started for the master "
                                f"{workflow['source_kind']}, not {source_kind}.")

    from lib import resolve
    master, master_hash = resolve.require_tailorable_master(source_kind, ws)

    recorded_hash = workflow.get("source_master_hash")
    if recorded_hash and recorded_hash != master_hash:
        raise ResumeTailorError(
            "MASTER_CONFLICT",
            "The master changed since this workflow's gap analysis was run. The evidence prompts "
            "and priority_missing you saw may be stale. Re-run analyze_tailoring_requirements "
            "against the current master before tailoring.",
            details={"analyzed_master_hash": recorded_hash, "current_master_hash": master_hash},
        )

    if jd_text is None and workflow.get("jd_id"):
        jd_text = (storage.load_jd(workflow["jd_id"], ws) or {}).get("jd_text")

    new_patches = [p.model_dump(mode="json", exclude_none=True) for p in parse_patches(patches)]
    evidence_ids = list(dict.fromkeys(evidence_ids or []))

    if repair_of:
        if workflow.get("repair_attempts", 0) >= workflows.MAX_REPAIR_ATTEMPTS:
            raise ResumeTailorError("REPAIR_LIMIT", f"Repair limit ({workflows.MAX_REPAIR_ATTEMPTS}) reached "
                                    "for this workflow; release is blocked.")
        bad = [i for i, p in enumerate(new_patches) if p["operation"] not in REPAIR_SAFE_OPERATIONS]
        if bad:
            raise ResumeTailorError("PATCH_INVALID", "Repairs may only drop or reorder blocks.",
                                    details={"rejections": [{"patch_index": i, "rule": "repair.forbidden_operation"}
                                                            for i in bad]})
        prior = storage.require_version(repair_of, ws)
        prior_meta = prior.get("metadata") or {}
        if prior_meta.get("workflow_id") != workflow_id:
            raise ResumeTailorError("PATCH_INVALID", "Can only repair a version from the same workflow.")
        if prior_meta.get("source_master_hash") != master_hash:
            raise ResumeTailorError("MASTER_CONFLICT", "The master changed since the version being repaired "
                                    "was created; start a new tailoring run.")
        recorded = load_version_patches(repair_of, ws)
        all_patches = recorded["patches"] + new_patches
        evidence_ids = list(dict.fromkeys(recorded.get("evidence_ids", []) + evidence_ids))
    else:
        all_patches = new_patches

    evidence = _load_evidence(ws, workflow_id, evidence_ids)
    body, report = validate_and_apply(master, all_patches, workflow_id=workflow_id, evidence=evidence,
                                      provenance_hook=_provenance_hook(master, evidence))

    usage = evidence_usage(body, report["summary"]["source_refs"], evidence, workflow)
    template_id, template_version = _resolve_template(template, master, jd_text)
    match = match_resume_to_jd(body, jd_text) if jd_text else None

    with workspace_lock(ws):
        _, current_hash = storage.load_master(source_kind, ws)
        if current_hash != master_hash:
            raise ResumeTailorError("MASTER_CONFLICT", "The master changed while tailoring; nothing was saved.",
                                    details={"expected_hash": master_hash, "current_hash": current_hash})
        version_id = _choose_version_id(save_as, workflow_id, ws)
        doc = dict(body)
        doc["metadata"] = {
            "version_id": version_id,
            "workspace_id": ws.id,
            "workflow_id": workflow_id,
            "source_master_hash": master_hash,
            "document_kind": source_kind,
            "template_id": template_id,
            "template_version": template_version,
            "rules_version": _rules_version(),
            "jd_id": workflow.get("jd_id"),
            "evidence_ids": evidence_ids,
            "created_at": utc_now_iso(),
            "released": False,
            "release_report_id": None,
            "repair_of": repair_of,
            "unknown_jd_requirements": list((workflow.get("analysis") or {}).get("unknown_requirements", [])),
            "summary_source_refs": report["summary"]["source_refs"],
            "summary_claim_strength": report["summary"].get("claim_strength"),
        }
        repair_attempt = workflow.get("repair_attempts", 0) + (1 if repair_of else 0)
        storage.save_version(version_id, doc, ws)
        atomic_write_yaml(_patches_path(ws, version_id), {"version_id": version_id, "workflow_id": workflow_id,
                                                          "evidence_ids": evidence_ids, "patches": all_patches})
        workflows.update_workflow(
            workflow_id, ws=ws, append={"version_ids": [version_id], "evidence_ids": evidence_ids},
            set_fields={"status": "tailored", "repair_attempts": repair_attempt},
        )

    result = {
        "ok": True,
        "version_id": version_id,
        "workflow_id": workflow_id,
        "source": f"workspace master {source_kind}",
        "source_master_hash": master_hash,
        "template_id": template_id,
        "template_version": template_version,
        "rules_version": _rules_version(),
        "released": False,
        "changed_block_ids": report.get("changed_block_ids", []),
        "new_block_ids": report.get("new_block_ids", []),
        "new_entry_ids": report.get("new_entry_ids", []),
        "dropped_block_ids": report.get("dropped_block_ids", []),
        "added_terms": usage["added_terms"],
        "not_added": usage["not_added"],
        "repair_attempt": repair_attempt,
        "next_step": "Call validate_version, then release_resume, then export_resume.",
    }
    if match:
        result["match_score"] = match["score"]
        result["still_missing"] = match["missing"]
    return result
