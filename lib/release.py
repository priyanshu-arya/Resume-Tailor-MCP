"""Validation, release gate and export (spec §38-45, §61).

    version (draft) --validate_version--> ValidationReport
                    --release_resume----> release report + frozen version + released PDF/TeX
                    --export_resume-----> the released files, byte-identical

The PDF is always compiled from the exact TeX string that is returned and
hashed, and release requires *measured* PDF checks: if the PDF inspection
backend is missing, nothing can be released. Validation also replays the
version's recorded patches against the master it claims to come from, so a
version YAML edited on disk (e.g. an invented bullet pasted in) is caught.

Draft exports exist for previewing and for validating a master, but they
are always labelled DRAFT / UNVERIFIED and never count as a release.
"""

from __future__ import annotations

import json
import secrets
import shutil
import time
from pathlib import Path

from lib import audit, storage, workflows
from lib import export as _export
from lib import filenames as _filenames
from lib import templates as _templates
from lib.errors import ResumeTailorError
from lib.ids import index_blocks
from lib.locking import atomic_write_bytes, sha256_bytes, sha256_of, sha256_text, workspace_lock
from lib.schemas import Check, ReleaseReport, ValidationReport, contract_gaps
from lib.validators import content as _content
from lib.validators import format_tex as _format_tex
from lib.validators import pdf as _pdf
from lib.validators import structure as _structure
from lib.workspace import Workspace, get_workspace, safe_child, utc_now_iso

# Page caps by career stage (CLAUDE.md strict rules). None = no corporate cap.
STAGE_MAX_PAGES = {"fresher": 1, "1-3": 1, "3-5": 2, "5-10": 2, "manager": 2, "director": 3, "academic": None}

# Every check this module emits directly (as opposed to the four dedicated
# validators in lib/validators/), in report order. These are the checks
# that only the release gate itself can run: they need the live master, the
# resolved template contract, or the actual compile step, not just the
# rendered document.
ALL_CHECK_IDS = (
    "source.master_hash",
    "provenance.replay",
    "template.releasable",
    "release.career_stage",
    "template.renderer",
    "latex.compile",
    "pdf.backend",
)

DRAFT_LABEL = "DRAFT / UNVERIFIED -- not released, not validated for submission"


def _log_pdf_compiled(ws, workflow_id, **fields) -> None:
    """Best-effort emission from inside the compile step (the server wrapper
    only sees the aggregate outcome, not this stage). Re-raises ValueError
    (unknown event = programming error); swallows everything else -- a
    logging failure must never change a release outcome."""
    try:
        audit.log_event("pdf_compiled", ws=ws, workflow_id=workflow_id, **fields)
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - logging must never break a release
        pass


def _check(id_, status, severity, category, message, source="release_gate", measurement=None, expected=None) -> Check:
    return Check(id=id_, status=status, severity=severity, category=category, message=message,
                 source=source, measurement=measurement, expected=expected)


def _staging_dir(ws: Workspace, version_id: str) -> Path:
    base = ws.exports_dir / ".staging"
    base.mkdir(parents=True, exist_ok=True)
    path = safe_child(base, version_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def page_cap(contract: dict, master: dict) -> tuple[int | None, Check]:
    limits = contract.get("limits") if isinstance(contract.get("limits"), dict) else {}
    contract_max = limits.get("max_pages")
    stage = (master.get("metadata") or {}).get("career_stage")
    if stage is None:
        return contract_max, _check("release.career_stage", "warning", "warning", "FORMAT",
                                    "Master has no career_stage; only the template's page limit is enforced. "
                                    "Set it with set_master_resume(career_stage=...).",
                                    expected=list(STAGE_MAX_PAGES))
    stage_max = STAGE_MAX_PAGES.get(stage)
    caps = [c for c in (contract_max, stage_max) if c]
    cap = min(caps) if caps else None
    # Non-blocking visibility fix: when the template's own limit is what's
    # actually binding (stage allows more pages than the template caps at),
    # a "pass"/"info" here would let e.g. a director's cut-off third page
    # look like a passing check. Surface it as a warning instead -- no
    # release outcome changes, only what the user is told.
    template_is_binding = contract_max is not None and (stage_max is None or contract_max < stage_max)
    if template_is_binding:
        return cap, _check("release.career_stage", "warning", "warning", "FORMAT",
                           f"Page cap {cap} comes from the template, not your career stage: career_stage "
                           f"{stage!r} allows {stage_max} page(s) but the template declares max_pages "
                           f"{contract_max}. Content must fit {cap} page(s). Never shrink margins or type to fit.",
                           measurement=stage, expected=cap)
    return cap, _check("release.career_stage", "pass", "info", "FORMAT",
                       f"Page cap {cap} (template {contract_max}, career stage {stage!r} -> {stage_max}).",
                       measurement=stage, expected=cap)


def _replay_check(version: dict, master: dict, evidence: dict, ws: Workspace) -> Check:
    """Re-derive the version from master + recorded patches; any difference
    means the saved version was altered after validation-at-tailor-time."""
    from lib import tailoring
    from lib.patches import validate_and_apply
    meta = version["metadata"]
    try:
        recorded = tailoring.load_version_patches(meta["version_id"], ws)
        body, report = validate_and_apply(master, recorded["patches"], workflow_id=meta["workflow_id"],
                                          evidence=evidence, provenance_hook=tailoring._provenance_hook(master, evidence))
    except ResumeTailorError as e:
        return _check("provenance.replay", "fail", "critical", "FACTUAL",
                      f"Recorded patches no longer validate ({e.code}).", source="provenance")
    saved = {k: v for k, v in version.items() if k != "metadata"}
    if sha256_of(body) != sha256_of(saved) or report["summary"]["source_refs"] != meta.get("summary_source_refs"):
        return _check("provenance.replay", "fail", "critical", "FACTUAL",
                      "Saved version differs from what its validated patches produce (edited after tailoring?).",
                      source="provenance")
    return _check("provenance.replay", "pass", "critical", "FACTUAL",
                  "Version reproduces exactly from the master and its validated patches.", source="provenance")


def _rules_version_drift_check(meta: dict) -> Check:
    """Non-blocking: a rules_version bump can make an in-flight draft fail
    provenance.replay at validate/release time if a new rule rejects one of
    its recorded patches. That surfaces as a generic 'recorded patches no
    longer validate' critical failure that repairs cannot fix -- this warning
    makes the cause legible instead."""
    from lib import rules
    try:
        current = rules.rules_version()
    except ResumeTailorError:
        return _check("release.rules_version_drift", "not_available", "warning", "WORKFLOW",
                      "Current rules_version is unavailable.")
    recorded = meta.get("rules_version")
    if recorded and recorded != current:
        return _check("release.rules_version_drift", "warning", "warning", "WORKFLOW",
                      f"Tailored under rules_version {recorded!r}; the workspace now runs {current!r}. "
                      "If provenance.replay fails on this version, re-tailor rather than repair.",
                      measurement=recorded, expected=current)
    return _check("release.rules_version_drift", "pass", "warning", "WORKFLOW",
                  "Version was tailored under the current rules_version.", measurement=recorded, expected=current)


def _load_context(version_id: str, workflow_id: str, ws: Workspace):
    workflows.check_workflow_id(workflow_id)
    version = storage.require_version(version_id, ws)
    meta = version.get("metadata") or {}
    if meta.get("legacy"):
        raise ResumeTailorError("PATCH_INVALID", "Legacy versions cannot be validated or released; "
                                "tailor a new version from the workspace master.")
    if meta.get("workflow_id") != workflow_id:
        raise ResumeTailorError("WORKFLOW_NOT_FOUND", f"Version {version_id!r} does not belong to {workflow_id}.")
    workflow = workflows.load_workflow(workflow_id, ws)
    master, master_hash = storage.require_master(meta["document_kind"], ws)
    from lib import evidence as _evidence
    evidence = _evidence.load_evidence(workflow_id, meta.get("evidence_ids") or [], ws=ws)
    return version, meta, workflow, master, master_hash, evidence


def validate(version_id: str, workflow_id: str, ws: Workspace | None = None) -> dict:
    """Run every validator; compile the exact TeX in staging. Returns a dict
    with the ValidationReport plus internal staging info."""
    ws = ws or get_workspace()
    version, meta, workflow, master, master_hash, evidence = _load_context(version_id, workflow_id, ws)
    kind = meta["document_kind"]
    checks: list[Check] = []
    measured, inferred, not_available = {}, {}, []

    # --- source ---
    if meta.get("source_master_hash") != master_hash:
        checks.append(_check("source.master_hash", "fail", "critical", "SOURCE",
                             "The master changed after this version was tailored; tailor a new version.",
                             measurement=master_hash, expected=meta.get("source_master_hash")))
    else:
        checks.append(_check("source.master_hash", "pass", "critical", "SOURCE", "Version derives from the current master."))
        checks.append(_replay_check(version, master, evidence, ws))
    checks.append(_rules_version_drift_check(meta))

    # --- template ---
    template_id = meta.get("template_id")
    info = _templates.resolve_template(template_id)  # TEMPLATE_UNKNOWN propagates: never guess
    contract = _templates.get_contract(template_id)  # TEMPLATE_REGISTRY_INVALID propagates for a malformed entry
    if info["releasable"]:
        checks.append(_check("template.releasable", "pass", "critical", "TEMPLATE",
                             f"{template_id} v{info['version']} is supported with a production renderer."))
    else:
        checks.append(_check("template.releasable", "fail", "critical", "TEMPLATE",
                             f"{template_id} is {info['status']}"
                             f"{'' if info['has_renderer'] else ' and has no production renderer'}; it cannot be "
                             "released. Choose a supported template (e.g. classic-minimalist) -- nothing was substituted."))

    gaps = contract_gaps(contract) if info["releasable"] else None
    if not info["releasable"]:
        checks.append(_check("template.contract_complete", "not_available", "error", "TEMPLATE",
                             "Not checked: the template is not releasable."))
    elif gaps:
        checks.append(_check("template.contract_complete", "fail", "critical", "TEMPLATE",
                             f"{template_id} is marked supported but its contract is incomplete ({len(gaps)} "
                             "fields unknown); format checks would silently degrade to not_available. Restore "
                             "resources/templates/templates.yaml.", measurement=gaps))
    else:
        checks.append(_check("template.contract_complete", "pass", "critical", "TEMPLATE",
                             f"{template_id}'s contract fully specifies every required field."))

    # --- content / structure ---
    checks += _content.check_content(version)
    checks += _structure.check_structure(version, contract, kind, index_blocks(master), evidence)

    cap, cap_check = page_cap(contract, master)
    checks.append(cap_check)

    tex = pdf_path = compile_info = None
    backend = _pdf.detect_pdf_backend()
    if not info["has_renderer"]:
        not_available += ["tex", "pdf"]
        checks.append(_check("template.renderer", "fail", "critical", "TEMPLATE",
                             f"No production renderer for {template_id}; no PDF can be produced."))
    else:
        tex = _templates.render_template(template_id, version)
        tex_checks, inferred = _format_tex.check_tex(tex, contract)
        checks += tex_checks
        compile_started = time.monotonic()
        try:
            compile_info = _export.compile_tex(tex, _staging_dir(ws, version_id), version_id)
        except ResumeTailorError as e:
            checks.append(_check("latex.compile", "fail", "critical", "LATEX_PDF", f"LaTeX compilation failed ({e.code})."))
            _log_pdf_compiled(ws, workflow_id, status="fail",
                              duration_ms=int((time.monotonic() - compile_started) * 1000),
                              code=e.code, category="LATEX_PDF")
        else:
            checks.append(_check("latex.compile", "pass", "critical", "LATEX_PDF", "Compiled the exact returned TeX."))
            pdf_path = compile_info["pdf_path"]
            pdf_checks, measured, na = _pdf.check_pdf(pdf_path, contract, version, cap or 99, compile_info, backend)
            checks += pdf_checks
            not_available += na
            _log_pdf_compiled(ws, workflow_id, status="pass",
                              duration_ms=int((time.monotonic() - compile_started) * 1000),
                              page_count=measured.get("page_count"),
                              overfull_count=compile_info.get("overfull_hbox_count"))
    if not backend.get("available"):
        checks.append(_check("pdf.backend", "fail", "critical", "LATEX_PDF", backend.get("message") or
                             "PDF validation unavailable. Production release blocked."))

    report = ValidationReport(
        version_id=version_id, workflow_id=workflow_id, template_id=template_id, checks=checks,
        measured_properties=measured, inferred_properties=inferred, not_available=sorted(set(not_available)),
        tex_sha256=sha256_text(tex) if tex is not None else None,
        pdf_sha256=compile_info["pdf_sha256"] if compile_info and pdf_path else None,
    )
    workflows.update_workflow(workflow_id, ws=ws, set_fields={"status": "validated" if report.passed else "blocked"})
    return {"report": report, "tex": tex, "compile_info": compile_info, "master": master, "meta": meta,
            "workflow": workflow}


def lifecycle_state(meta: dict) -> str:
    """Derived, non-persisted DRAFT/RELEASED state (spec §38, Phase 6
    lifecycle decision). There is deliberately no stored per-version
    `validated` flag: `validate_version` writes nothing version-scoped, and
    `release_resume` always re-validates fully under `workspace_lock` before
    freezing a version, so a stored "validated: true" would only ever be
    trusted *less* than what the gate already recomputes on demand. The
    three-state DRAFT -> VALIDATED -> RELEASED lifecycle collapses here to
    two: validation is a stateless, repeatable action, not a state."""
    return "released" if meta.get("released") else "draft"


def report_summary(report: ValidationReport) -> dict:
    return {
        "passed": report.passed,
        "critical_failures": [{"id": c.id, "category": c.category, "message": c.message}
                              for c in report.critical_failures],
        "warnings": [{"id": c.id, "message": c.message} for c in report.warnings],
        "measured_properties": report.measured_properties,
        "inferred_properties": report.inferred_properties,
        "not_available": report.not_available,
        "tex_sha256": report.tex_sha256,
        "pdf_sha256": report.pdf_sha256,
        "check_count": len(report.checks),
    }


def validate_version(version_id: str, workflow_id: str, ws: Workspace | None = None) -> dict:
    from lib import reporting
    result = validate(version_id, workflow_id, ws)
    return {"ok": True, "version_id": version_id, "workflow_id": workflow_id,
            "lifecycle_state": lifecycle_state(result["meta"]),
            **report_summary(result["report"]), "checklist": reporting.checklist(result["report"].checks)}


def _export_basename(master: dict, workflow: dict, kind: str, version_id: str, ws: Workspace) -> str:
    jd = storage.load_jd(workflow["jd_id"], ws) if workflow.get("jd_id") else None
    title = ((jd or {}).get("extracted") or {}).get("title") or ""
    role = _filenames.role_from_jd_title(title) if title else None
    name = _filenames.deterministic_filename(master.get("name", ""), role, kind, "pdf")
    stem = name[:-len(".pdf")]
    if (ws.exports_dir / f"{stem}.pdf").exists():
        stem = _filenames.deterministic_filename(master.get("name", ""), role, kind, "pdf",
                                                 collision_suffix=version_id)[:-len(".pdf")]
    return stem


def release_resume(version_id: str, workflow_id: str, ws: Workspace | None = None) -> dict:
    from lib import reporting
    ws = ws or get_workspace()
    workflows.check_workflow_id(workflow_id)
    with workspace_lock(ws):
        current = storage.require_version(version_id, ws)
        if (current.get("metadata") or {}).get("released"):
            meta = current["metadata"]
            return {"ok": True, "released": True, "already_released": True, "version_id": version_id,
                    "release_report_id": meta.get("release_report_id"), "export_basename": meta.get("export_basename"),
                    "lifecycle_state": lifecycle_state(meta),
                    "completion": reporting.completion_summary(version_id, workflow_id, ws=ws)}

        result = validate(version_id, workflow_id, ws)
        report: ValidationReport = result["report"]
        meta, master, workflow = result["meta"], result["master"], result["workflow"]
        release_id = f"rel-{secrets.token_hex(4)}"
        released = report.passed
        export_basename = _export_basename(master, workflow, meta["document_kind"], version_id, ws) if released else None

        rel = ReleaseReport(
            release_report_id=release_id, version_id=version_id, workspace_id=ws.id, workflow_id=workflow_id,
            released=released, created_at=utc_now_iso(), template_id=meta.get("template_id"),
            template_version=meta.get("template_version"), rules_version=meta.get("rules_version"),
            source_master_hash=meta.get("source_master_hash"), tex_sha256=report.tex_sha256,
            pdf_sha256=report.pdf_sha256, checks=report.checks,
            critical_failures=[c.id for c in report.critical_failures], warnings=[c.id for c in report.warnings],
            measured_properties=report.measured_properties, inferred_properties=report.inferred_properties,
            not_available=report.not_available,
        )
        report_path = safe_child(ws.releases_dir, f"{version_id}-{release_id}", ".json")
        atomic_write_bytes(report_path, json.dumps(rel.model_dump(mode="json"), indent=2).encode("utf-8"),
                           exclusive=True)

        if not released:
            return {"ok": True, "released": False, "version_id": version_id, "release_report_id": release_id,
                    "status": "blocked", "lifecycle_state": lifecycle_state(meta), **report_summary(report),
                    "next_step": "Fix the critical failures (repairs may only drop/reorder blocks, via "
                                 "tailor_resume(repair_of=...)), or choose a supported template. Max 3 repairs.",
                    "completion": reporting.completion_summary(version_id, workflow_id, ws=ws,
                                                                release_report=rel.model_dump(mode="json"))}

        info = result["compile_info"]
        for ext in ("pdf", "tex"):
            shutil.copyfile(info[f"{ext}_path"], ws.exports_dir / f"{export_basename}.{ext}")
        if sha256_bytes((ws.exports_dir / f"{export_basename}.pdf").read_bytes()) != report.pdf_sha256:
            raise ResumeTailorError("LATEX_COMPILE_FAILED", "Released PDF does not match the validated PDF.")
        storage.update_version_metadata(version_id, {"released": True, "release_report_id": release_id,
                                                     "released_at": rel.created_at,
                                                     "export_basename": export_basename}, ws)
        workflows.update_workflow(workflow_id, ws=ws, set_fields={"status": "released"})
        released_rel = rel.model_dump(mode="json")
        released_rel["export_basename"] = export_basename
    return {"ok": True, "released": True, "version_id": version_id, "release_report_id": release_id,
            "export_basename": export_basename, "lifecycle_state": "released", **report_summary(report),
            "completion": reporting.completion_summary(version_id, workflow_id, ws=ws, release_report=released_rel)}


def load_release_report(version_id: str, release_id: str, ws: Workspace) -> dict:
    path = safe_child(ws.releases_dir, f"{version_id}-{release_id}", ".json")
    if not path.exists():
        raise ResumeTailorError("NOT_RELEASED", "Release report not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def export(version: str, workflow_id: str | None, fmt: str = "pdf", mode: str = "release",
           ws: Workspace | None = None) -> dict:
    """Returns {label, tex, pdf_bytes, pdf_path, tex_path, extra_path?, released, ...}."""
    ws = ws or get_workspace()
    fmt = (fmt or "pdf").lower().lstrip(".")
    if fmt not in ("pdf", "tex", "docx", "md", "txt"):
        raise ResumeTailorError("IMPORT_UNSUPPORTED", "format must be pdf, tex, docx, md or txt.")
    if mode not in ("release", "draft"):
        raise ResumeTailorError("PATCH_INVALID", "mode must be 'release' or 'draft'.")

    master_kind = storage.resolve_master_alias(version)
    if master_kind:
        if mode == "release":
            raise ResumeTailorError("NOT_RELEASED", "Masters are exported as drafts only (mode='draft'); "
                                    "tailor and release a version for a production PDF.")
        doc, _ = storage.require_master(master_kind, ws)
        version_id, template_id = f"master-{master_kind}", "classic-minimalist"
    else:
        workflows.check_workflow_id(workflow_id)
        doc = storage.require_version(version, ws)
        meta = doc.get("metadata") or {}
        if meta.get("workflow_id") != workflow_id:
            raise ResumeTailorError("WORKFLOW_NOT_FOUND", f"Version {version!r} does not belong to {workflow_id}.")
        version_id, template_id = version, meta.get("template_id")

    if mode == "release":
        meta = doc["metadata"]
        if not meta.get("released"):
            raise ResumeTailorError("NOT_RELEASED", f"Version {version!r} has not passed the release gate; call "
                                    "release_resume first (or export with mode='draft' for an unverified preview).")
        rel = load_release_report(version_id, meta["release_report_id"], ws)
        base = meta["export_basename"]
        pdf_path, tex_path = ws.exports_dir / f"{base}.pdf", ws.exports_dir / f"{base}.tex"
        try:
            pdf_bytes, tex = pdf_path.read_bytes(), tex_path.read_text(encoding="utf-8")
        except OSError as e:
            raise ResumeTailorError("NOT_RELEASED", f"Version {version!r} was released but its exported file(s) "
                                    "are missing from disk; re-release a new version.") from e
        if sha256_bytes(pdf_bytes) != rel["pdf_sha256"] or sha256_text(tex) != rel["tex_sha256"]:
            raise ResumeTailorError("NOT_RELEASED", "Released files were modified after release; re-release a new version.")
        label, stem = f"RELEASED ({meta['release_report_id']})", base
    else:
        tex = _templates.render_template(template_id, doc)  # no fallback: TEMPLATE_NO_RENDERER propagates
        drafts = ws.exports_dir / "drafts"
        drafts.mkdir(parents=True, exist_ok=True)
        stem = f"{version_id}-DRAFT-UNVERIFIED"
        info = _export.compile_tex(tex, drafts, stem)
        pdf_path, tex_path = Path(info["pdf_path"]), Path(info["tex_path"])
        pdf_bytes, label = pdf_path.read_bytes(), DRAFT_LABEL

    result = {"label": label, "released": mode == "release", "tex": tex, "pdf_bytes": pdf_bytes,
              "pdf_path": str(pdf_path), "tex_path": str(tex_path), "template_id": template_id,
              "tex_sha256": sha256_text(tex), "pdf_sha256": sha256_bytes(pdf_bytes)}
    if fmt in ("docx", "md", "txt"):
        extra = pdf_path.parent / f"{stem}.{fmt}"
        _export.export_resume(doc, str(extra), fmt, template_id)
        result["extra_path"] = str(extra)
    return result
