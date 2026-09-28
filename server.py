"""Resume Tailor MCP server.

Exposes your resume as MCP resources, and a set of deterministic tools
for extracting JD keywords, scoring keyword/ATS match, saving tailored
versions, diffing them, and exporting to docx/pdf/txt.

Run directly for local testing:
    python server.py

Add to Claude Desktop via claude_desktop_config.json (see README.md).
"""

from __future__ import annotations

import base64
import difflib
import functools
import inspect
import json
import sys
import time
from datetime import date
from pathlib import Path

import yaml
from mcp import types
from mcp.server.fastmcp import FastMCP

from lib import storage
from lib import workspace as _workspace
from lib import migration as _migration
from lib.errors import ResumeTailorError, internal_error_result
from lib.ids import normalize_master
from lib.schemas import validate_kind
from lib import templates as _templates
from lib.matching import match_resume_to_jd as _match_resume_to_jd
from lib.ats import score_ats as _score_ats

mcp = FastMCP("resume-tailor")

ETIQUETTE_PATH = Path(__file__).resolve().parent / "resources" / "resume_etiquette.yaml"


# --------------------------------------------------------------------------
# Resources -- read-only, URI-addressable state
# --------------------------------------------------------------------------

def _audit(_channel: str, _event: str, **fields) -> None:
    """Best-effort structured audit event (IDs/counts/codes only). Logging
    must never change a tool's outcome."""
    try:
        from lib import audit
        (audit.log_error if _channel == "error" else audit.log_event)(_event, **fields)
    except ValueError:
        raise  # unknown event name: a programming error, surface it in tests
    except Exception:  # noqa: BLE001
        pass


def _count(items, prefix):
    return sum(1 for c in items or [] if str(c.get("id", "")).startswith(prefix))


def _validation_fields(result: dict) -> dict:
    fails = result.get("critical_failures") or []
    return {
        "status": "passed" if result.get("passed") else "failed",
        "critical_count": len(fails),
        "warning_count": len(result.get("warnings") or []),
        "template_failures": _count(fails, "template."),
        "pdf_failures": _count(fails, "pdf.") + _count(fails, "latex."),
        "pdf_checked": "pdf" not in (result.get("not_available") or []) and not _count(fails, "pdf.backend"),
        "rule_ids": [c["id"] for c in fails][:20],
    }


def _success_events(name: str, args: dict, result) -> list[tuple[str, dict]]:
    if not isinstance(result, dict):
        if name == "export_resume":
            return [("export_completed", {"version_id": args.get("version"), "mode": args.get("mode"),
                                          "format": args.get("format")})]
        return []
    wf = args.get("workflow_id") or result.get("workflow_id")
    if name == "initialize_workspace" and result.get("created"):
        return [("workspace_initialized", {})]
    if name == "select_workspace":
        if result.get("switched"):
            return [("workspace_switched", {"from_workspace_id": result.get("previous_workspace_id"),
                                            "to_workspace_id": result.get("workspace_id")})]
        return [("workspace_selected", {})]
    if name == "discover_masters":
        return [("master_discovered", {"candidate_count": result.get("candidate_count"),
                                       "entries_examined": result.get("entries_examined")})]
    if name == "import_master_from_folder":
        events = [("master_updated", {"kind": args.get("kind"), "applied": bool(result.get("applied")),
                                      "master_hash": result.get("master_hash")})]
        if result.get("applied"):
            events.append(("master_imported", {
                "kind": args.get("kind"), "applied": True, "master_hash": result.get("master_hash"),
                "source_hash": (result.get("import_provenance") or {}).get("source_hash")}))
        return events
    if name == "migrate_legacy_data":
        statuses = [m.get("status") for m in (result.get("masters") or {}).values()]
        return [("legacy_migrated", {"migrated_count": sum(s in ("migrated", "replaced") for s in statuses),
                                     "conflict_count": statuses.count("conflict")})]
    if name == "set_master_resume":
        events = [("master_updated", {"kind": args.get("kind"), "applied": bool(result.get("applied")),
                                      "master_hash": result.get("master_hash")})]
        if result.get("applied"):
            events.append(("master_created", {"kind": args.get("kind"), "applied": True,
                                              "master_hash": result.get("master_hash")}))
        return events
    if name == "get_master_resume":
        return [("master_loaded", {"kind": args.get("kind")})]
    if name == "analyze_tailoring_requirements":
        events = [] if args.get("workflow_id") else [("workflow_started", {"workflow_id": wf,
                                                                           "kind": args.get("source_kind")})]
        status_counts = result.get("status_counts") or {}
        return events + [("requirements_analyzed", {
            "workflow_id": wf, "evidence_prompt_count": len(result.get("evidence_prompts") or []),
            "missing_count": len(result.get("missing") or []),
            "unknown_count": len(result.get("unknown_requirements") or []),
            "weak_count": status_counts.get("weak"), "supported_count": status_counts.get("supported")})]
    if name == "save_tailoring_evidence":
        ev = result.get("evidence") or {}
        return [("evidence_saved", {"workflow_id": wf, "evidence_id": ev.get("id"),
                                    "evidence_category": ev.get("category"),
                                    "prompt_reason": ev.get("prompt_reason")})]
    if name == "tailor_resume":
        new_entry_count = len(result.get("new_entry_ids") or [])
        events = [("tailor_succeeded", {"workflow_id": wf, "version_id": result.get("version_id"),
                                        "patch_count": len(args.get("patches") or []),
                                        "repair_attempt": result.get("repair_attempt", 0),
                                        "template_id": result.get("template_id"),
                                        "source_master_hash": result.get("source_master_hash"),
                                        "new_entry_count": new_entry_count,
                                        "rules_version": result.get("rules_version")})]
        if new_entry_count:
            events.append(("project_entry_added", {"workflow_id": wf, "version_id": result.get("version_id"),
                                                    "new_entry_count": new_entry_count}))
        return events
    if name == "validate_version":
        return [("validation_completed", {"workflow_id": wf, "version_id": args.get("version_id"),
                                          **_validation_fields(result)})]
    if name == "release_resume" and not result.get("already_released"):
        base = {"workflow_id": wf, "version_id": args.get("version_id")}
        events = [("validation_completed", {**base, **_validation_fields(result)})]
        if result.get("released"):
            events.append(("release_succeeded", {**base, "release_report_id": result.get("release_report_id"),
                                                 "tex_sha256": result.get("tex_sha256"),
                                                 "pdf_sha256": result.get("pdf_sha256")}))
        else:
            fails = result.get("critical_failures") or []
            events.append(("release_blocked", {**base, "status": "blocked", "critical_count": len(fails),
                                               "category": fails[0]["category"] if fails else None,
                                               "rule_ids": [c["id"] for c in fails][:20]}))
        return events
    return []


def _safe_tool(fn):
    """Uniform error model (spec §69) plus audit events (spec §49):
    business failures become {"ok": false, "error": {...}}; anything
    unexpected becomes a generic INTERNAL_ERROR with no paths or personal
    data in the response. Every outcome is logged by ID/count/code only."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            call_args = dict(bound.arguments)
        except TypeError:
            call_args = dict(kwargs)
        name = fn.__name__
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except ResumeTailorError as e:
            fields = {"tool": name, "workflow_id": call_args.get("workflow_id"), "code": e.code,
                      "category": e.category, "severity": e.severity, "status": "failed",
                      "duration_ms": int((time.monotonic() - started) * 1000)}
            rejections = (e.details or {}).get("rejections") or []
            if name == "tailor_resume" and e.code in ("PATCH_INVALID", "PROVENANCE_VIOLATION"):
                _audit("error", "tailor_rejected", **fields, rejection_count=len(rejections),
                       rule_ids=[r.get("rule") for r in rejections if r.get("rule")][:20])
            elif (name in ("set_master_resume", "import_master_from_folder")
                  and e.code in ("MASTER_CONFLICT", "CONFIRMATION_REQUIRED", "MASTER_EXISTS")):
                _audit("error", "master_conflict", **fields)
            else:
                _audit("error", "tool_error", **fields)
            return e.to_result()
        except Exception as e:  # noqa: BLE001 - last-resort boundary
            print(f"resume-tailor: internal error in {name}: {type(e).__name__}", file=sys.stderr)
            _audit("error", "internal_error", tool=name, workflow_id=call_args.get("workflow_id"),
                   error_class=type(e).__name__, category="WORKFLOW", severity="critical", status="failed")
            return internal_error_result()
        for event, fields in _success_events(name, call_args, result):
            _audit("event", event, tool=name, duration_ms=int((time.monotonic() - started) * 1000), **fields)
        return result
    return wrapper


def _safe_resource(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ResumeTailorError as e:
            return f"# {e.code}: {e.message}"
    return wrapper


def _load_readable(version: str) -> dict:
    """Resolve a version name for READ-ONLY analysis (match/score/diff):
    master aliases read the workspace master of that kind, anything else a
    saved version. Tailoring never uses this -- it loads the master itself."""
    kind = storage.resolve_master_alias(version)
    if kind:
        return storage.require_master(kind)[0]
    return storage.require_version(version)


def _dump_master(kind: str) -> str:
    resume, _ = storage.load_master(kind)
    if resume is None:
        return (
            f"# No master {kind} yet.\n"
            f"# Use set_master_resume (kind='{kind}'), the create-master-file skill, "
            f"or migrate_legacy_data."
        )
    return yaml.safe_dump(resume, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://master")
@_safe_resource
def resource_master_resume() -> str:
    """The current master (canonical) resume as YAML."""
    return _dump_master("resume")


@mcp.resource("resume://master/{kind}")
@_safe_resource
def resource_master_by_kind(kind: str) -> str:
    """The current master document as YAML. kind: 'resume' or 'cv' -- these
    are two separate canonical documents, never mixed."""
    return _dump_master(validate_kind(kind))


@mcp.resource("resume://sections/{name}")
@_safe_resource
def resource_resume_section(name: str) -> str:
    """One section of the master resume (summary, skills, experience,
    education, projects, certifications, contact)."""
    return _section("resume", name)


@mcp.resource("resume://sections/{kind}/{name}")
@_safe_resource
def resource_section_by_kind(kind: str, name: str) -> str:
    """One section of the master document of `kind` ('resume' or 'cv')."""
    return _section(validate_kind(kind), name)


def _section(kind: str, name: str) -> str:
    resume, _ = storage.load_master(kind)
    if resume is None:
        return f"# No master {kind} yet -- nothing to show for section '{name}'."
    if name not in resume:
        return f"# Unknown section '{name}'. Known sections: {', '.join(resume.keys())}"
    return yaml.safe_dump({name: resume[name]}, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://versions/{version_id}")
@_safe_resource
def resource_resume_version(version_id: str) -> str:
    """A previously saved tailored resume version, as YAML."""
    data = storage.load_version(version_id)
    if data is None:
        return f"# No saved version named '{version_id}'. Use list_versions to see what's available."
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


@mcp.resource("jd://history/{jd_id}")
@_safe_resource
def resource_jd_history(jd_id: str) -> str:
    """A previously saved job description and its extracted keywords."""
    data = storage.load_jd(jd_id)
    if data is None:
        return f"# No saved JD named '{jd_id}'."
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://templates")
def resource_templates() -> str:
    """All available resume layout templates (id, name, sections, best-fit notes)."""
    return yaml.safe_dump({"templates": _templates.list_templates()}, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://templates/{template_id}")
def resource_template(template_id: str) -> str:
    """Full metadata for one resume layout template."""
    tmpl = _templates.get_template(template_id)
    if tmpl is None:
        ids = ", ".join(t["id"] for t in _templates.list_templates())
        return f"# No template named '{template_id}'. Known templates: {ids}"
    return yaml.safe_dump(tmpl, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://etiquette")
def resource_etiquette() -> str:
    """Condensed resume/CV writing rules (formatting standards, bullet
    formula, ATS/keyword strategy, section order by career stage, the
    no-fabrication golden rule, and a final quality gate) distilled from
    university career-center and ATS guidance. Read this before tailoring
    or writing any resume content -- score_ats also enforces several of
    these rules automatically."""
    return ETIQUETTE_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Tools -- deterministic actions
# --------------------------------------------------------------------------

@mcp.tool()
@_safe_tool
def initialize_workspace(create_new: bool = False, label: str | None = None) -> dict:
    """Create the local Resume Tailor workspace on first run (under
    $RESUME_TAILOR_HOME, default ~/.resume-tailor). Idempotent by default:
    returns the existing workspace if one is configured. Writes no personal
    data -- it reports where the master Resume/CV belong. If you have
    masters from the old repo layout (resources/master_*.yaml), run
    migrate_legacy_data next.

    Call get_workspace_status FIRST, not this tool -- if workspace(s) already
    exist on this machine but none is bound, this raises WORKSPACE_AMBIGUOUS
    rather than silently creating a new one and orphaning the existing
    data. Pass create_new=True ONLY when the user explicitly asks for an
    additional, separate workspace; never as a way to "fix" a broken or
    ambiguous binding -- use select_workspace for that instead."""
    return {"ok": True, **_workspace.initialize_workspace(create_new=create_new, label=label)}


@mcp.tool()
@_safe_tool
def get_workspace() -> dict:
    """Show the active workspace: ID, root, master paths and which masters
    exist. Errors with WORKSPACE_NOT_INITIALIZED if there is none yet, or
    WORKSPACE_AMBIGUOUS if several exist and none is bound -- never picks or
    creates one silently. Prefer get_workspace_status, which reports these
    states instead of raising, so you can ask the user what to do next."""
    return {"ok": True, **_workspace.describe_workspace()}


@mcp.tool()
@_safe_tool
def get_workspace_status() -> dict:
    """CALL THIS FIRST in any session that touches the user's resume. Reports
    whether this machine has a Resume Tailor workspace bound, and whether it
    holds a usable master -- without creating, selecting or guessing
    anything. Never raises for a missing or ambiguous binding.

    state:
      NO_WORKSPACE          - nothing on this machine yet. Ask the user for
                              the path of ONE folder containing their
                              existing resume/CV, then call
                              discover_masters(folder). If they have no
                              file, run the create-master-file skill. Do not
                              reconstruct their resume from memory, this
                              conversation, or any other workspace.
      WORKSPACE_FOUND       - bound and usable; see masters[kind].ready.
      WORKSPACE_NEEDS_SETUP - bound, but no valid/ready master yet. Import
                              one or create one.
      WORKSPACE_INVALID     - the binding is broken or ambiguous. See
                              available_workspace_ids, ASK the user which
                              is theirs, then select_workspace. Never pick
                              for them, and never call initialize_workspace
                              to "fix" this."""
    from lib import resolve
    return {"ok": True, **resolve.resolve_workspace_state()}


@mcp.tool()
@_safe_tool
def list_workspaces() -> dict:
    """Every Resume Tailor workspace on this machine, and which one (if any)
    is active. Reads no resume content -- only whether a master file exists
    for each workspace. Changes nothing."""
    return {"ok": True, **_workspace.list_workspaces()}


@mcp.tool()
@_safe_tool
def select_workspace(workspace_id: str) -> dict:
    """Bind the active workspace to an EXISTING one, by an ID the USER
    explicitly named (from list_workspaces). Never call this to "find" the
    user's data, never because a master is missing, and never based on
    which workspace merely looks more complete -- ask, don't guess. Creates
    nothing, copies nothing, deletes nothing. Both the workspace you switch
    away from and the one you switch to record the change in their own
    audit log. A workflow started before the switch will not be reachable
    afterward -- start over in the new workspace if needed."""
    return {"ok": True, **_workspace.select_workspace(workspace_id)}


@mcp.tool()
@_safe_tool
def discover_masters(folder: str, kind: str | None = None) -> dict:
    """Look for importable resume/CV files in EXACTLY ONE folder the user
    named. One level deep: no recursion, no other folder, no search of the
    machine, and no fallback location if the folder is empty.

    Returns ranked candidates with a resume-vs-CV guess and the reason for
    it -- it NEVER selects one, even when there is exactly one candidate.
    If more than one candidate comes back, show the user filename + kind
    guess + reason for each and ask which ONE file and which kind to
    import. Then call import_master_from_folder with that exact filename.
    `kind` is only an optional hint that nudges ranking; it never hides a
    candidate that doesn't match it."""
    from lib import discovery
    return {"ok": True, **discovery.discover_masters(folder, kind)}


@mcp.tool()
@_safe_tool
def import_master_from_folder(folder: str, filename: str, kind: str, career_stage: str | None = None,
                              accept_unparsed: bool = False, expected_hash: str | None = None,
                              confirm: bool = False, proposed_hash: str | None = None) -> dict:
    """Import ONE file the user chose from discover_masters's results as the
    master `kind` ("resume" or "cv" -- two separate documents, never
    merged). `folder`, `filename` and `kind` are all required: nothing here
    is inferred or guessed.

    The file is COPIED into the workspace; the folder is never a live
    master and is never read again after this call. Provenance (source
    filename, folder name/hash, file hash) is recorded on the master and
    can be read back with get_master_history.

    Replacing an existing master follows the same preview/confirm protocol
    as set_master_resume: the first call writes nothing and returns a diff
    plus current_hash/proposed_hash for the user to approve; call again
    with confirm=True, expected_hash, proposed_hash to apply it."""
    from lib import discovery
    return discovery.import_master(
        folder, filename, kind, career_stage=career_stage, accept_unparsed=accept_unparsed,
        expected_hash=expected_hash, confirm=confirm, proposed_hash=proposed_hash)


@mcp.tool()
@_safe_tool
def get_master_history(kind: str = "resume", limit: int = 20) -> dict:
    """Write history of the master `kind`: when it changed, why, the hash
    before and after, and -- for imports -- the source filename, folder
    name/hash and file hash. Read-only; contains no resume content, so it
    is safe to show the user verbatim."""
    from lib import storage
    return {"ok": True, "kind": kind, "history": storage.read_master_history(kind, limit)}


@mcp.tool()
@_safe_tool
def migrate_legacy_data(include_versions: bool = False, include_jds: bool = False,
                        conflict_choice: str | None = None) -> dict:
    """Copy masters from the old repo layout (resources/master_resume.yaml,
    resources/master_cv.yaml) into the workspace, hash-verified. Legacy
    files are never deleted. Idempotent.

    If the workspace already has a different master of that kind, nothing is
    written and a conflict is reported -- ask the user, then call again with
    conflict_choice="keep_workspace" or "replace_with_legacy" (the replaced
    master is backed up). include_versions / include_jds copy old tailored
    versions (marked legacy: true, never usable as a tailoring source) and
    saved JDs; only do this if the user asks."""
    return _migration.migrate_legacy(include_versions=include_versions, include_jds=include_jds,
                                     conflict_choice=conflict_choice)


@mcp.tool()
@_safe_tool
def list_workspace_size() -> dict:
    """Report storage used by the workspace, per area. Deletes nothing."""
    return {"ok": True, **_workspace.list_workspace_size()}


@mcp.tool()
@_safe_tool
def set_master_resume(kind: str, file_path: str | None = None, resume: dict | None = None,
                      mode: str = "replace", expected_hash: str | None = None, confirm: bool = False,
                      proposed_hash: str | None = None, career_stage: str | None = None,
                      accept_unparsed: bool = False) -> dict:
    """Create, import, replace or update a MASTER Resume/CV (kind "resume" or
    "cv" -- two separate documents). Only use this when the user explicitly
    asks to create/import/change their master. Tailoring evidence is never
    promoted to the master automatically.

    - First master of a kind: written immediately (from file_path, a
      .md/.txt/.docx/.pdf import, or from a structured `resume`).
    - Existing master: the first call writes NOTHING and returns a diff plus
      current_hash/proposed_hash. Show the diff to the user; only if they
      approve, call again with confirm=True, expected_hash=current_hash and
      proposed_hash. The previous master is backed up.
    - mode="update" takes the full edited master (from get_master_resume)
      as `resume`; existing block IDs are kept.
    - career_stage: fresher | 1-3 | 3-5 | 5-10 | manager | director | academic
      (drives the page-length cap).
    - A master with `unparsed` leftovers is not ready for tailoring until the
      content is placed or the user explicitly accepts it (accept_unparsed).
    """
    from lib import master_ops
    return master_ops.set_master(kind, file_path=file_path, resume=resume, mode=mode,
                                 expected_hash=expected_hash, confirm=confirm, proposed_hash=proposed_hash,
                                 career_stage=career_stage, accept_unparsed=accept_unparsed)


@mcp.tool()
@_safe_tool
def analyze_tailoring_requirements(jd_text: str, source_kind: str = "resume", workflow_id: str | None = None) -> dict:
    """STEP 1 of tailoring. Starts a workflow (or reuses workflow_id) and
    compares the JD with the workspace master: confirmed / weak / missing
    requirements, priority_missing, unknown_requirements and
    evidence_prompts. Ask the user each evidence prompt in your own words --
    never answer for them, and never assume an answer from memory or earlier
    conversations. Keep the returned workflow_id for every later step."""
    from lib import evidence
    return evidence.analyze_requirements(jd_text, source_kind=source_kind, workflow_id=workflow_id)


@mcp.tool()
@_safe_tool
def save_tailoring_evidence(workflow_id: str, term: str, category: str, evidence_text: str = "",
                            confirmed: bool = False, metrics: list[str] | None = None,
                            prompt_reason: str | None = None) -> dict:
    """STEP 2. Record what the user EXPLICITLY told you, in this workflow,
    about one missing JD term. Only call with confirmed=True when the user
    stated the fact themselves -- not from silence, memory, earlier chats,
    "sounds right", "just optimize it", or your own reasoning.

    category: professional | internship | personal_project | academic |
    coursework | certification | learning_only | none ("none" = they don't
    have it; the term will be reported as not added).
    evidence_text: the user's own description (e.g. "Built REST APIs with
    FastAPI for my personal RAG project"). metrics: any numbers the user
    gave, each quoted exactly as it appears in evidence_text. Pass the
    returned evidence IDs to tailor_resume."""
    from lib import evidence
    return evidence.save_evidence(workflow_id, term, category, evidence_text=evidence_text,
                                  confirmed=confirmed, metrics=metrics, prompt_reason=prompt_reason)


@mcp.tool()
@_safe_tool
def match_resume_to_jd(jd_text: str, version: str = "master") -> dict:
    """Compare a resume version against a job description and return a
    deterministic gap analysis: matched / missing / weak keywords and a
    match score. 'weak' means the keyword is only listed under Skills but
    never backed up by an actual experience or project bullet.
    """
    return _match_resume_to_jd(_load_readable(version), jd_text)


@mcp.tool()
@_safe_tool
def get_master_resume(kind: str = "resume") -> dict:
    """Return the workspace master document plus its hash.

    kind: "resume" or "cv" -- these are two separate canonical documents."""
    resume, master_hash = storage.require_master(validate_kind(kind))
    from lib.ids import index_blocks
    citable = {bid: {"type": info["type"], "section": info["section"], "category": info["category"]}
               for bid, info in index_blocks(resume).items() if info["category"]}
    return {
        "ok": True,
        "kind": kind,
        "master_hash": master_hash,
        "master": resume,
        "citable_blocks": citable,
        "note": "Cite these block IDs as source_refs ({type: master, id}) in tailor_resume patches. "
                "Only IDs listed here are citable.",
    }


@mcp.tool()
@_safe_tool
def tailor_resume(save_as: str, patches: list[dict], workflow_id: str, jd_text: str | None = None,
                  evidence_ids: list[str] | None = None, template: str = "auto",
                  source_kind: str = "resume", repair_of: str | None = None) -> dict:
    """Create a new tailored version from the WORKSPACE MASTER (loaded by the
    server -- you cannot pass a resume, and previous versions are never a
    source). You propose structured patches; the server validates every one
    and rejects the whole call if any fails (nothing partial is saved).

    Patch operations (target IDs come from get_master_resume.citable_blocks):
      {"operation": "replace_block", "target": {"id": "exp-001-b02"},
       "new_content": {"text": "...", "source_refs": [{"type": "master", "id": "exp-001-b02"}],
                       "claim_strength": "professional"}}          # summary (sum-001) or a bullet
      {"operation": "drop_block", "target": {"id": "proj-003"}}
      {"operation": "reorder", "section": "projects", "order": ["proj-002", "proj-001"]}
      {"operation": "reorder", "parent_id": "exp-001", "order": [...bullet ids...]}
      {"operation": "add_block", "parent_id": "proj-001", "new_content": {...}}   # new bullet
      {"operation": "add_skill_item", "category": "Frameworks", "name": "FastAPI",
       "source_refs": [{"type": "evidence", "id": "ev-..."}], "claim_strength": "personal_project"}
      {"operation": "add_project_entry", "name": "...", "stack": "...", "academic": false,
       "source_refs": [{"type": "evidence", "id": "ev-..."}], "claim_strength": "personal_project",
       "bullets": [{"text": "...", "source_refs": [...]}]}   # a brand-new Projects entry (1-4 bullets)

    Every new/changed block needs source_refs to real master blocks or to
    evidence the user confirmed in THIS workflow (pass those IDs in
    evidence_ids). Titles, companies, dates, degrees and contact details
    cannot be patched. add_project_entry can only ever create a new Projects
    entry -- there is no operation that adds a job, employer or role; needs
    at least one evidence ref of category professional/internship/
    personal_project/academic (never coursework/certification/learning_only,
    and never a master ref alone). repair_of=<version_id> re-applies that
    version's patches plus drop_block/reorder-only repairs (max 3 per
    workflow).
    """
    from lib import tailoring
    return tailoring.tailor(save_as, patches, workflow_id=workflow_id, jd_text=jd_text,
                            evidence_ids=evidence_ids, template=template, source_kind=source_kind,
                            repair_of=repair_of)


@mcp.tool()
@_safe_tool
def diff_versions(version_a: str, version_b: str) -> dict:
    """Show a unified diff between two saved resume versions (use 'master'
    for the current master resume)."""
    a = _load_readable(version_a)
    b = _load_readable(version_b)

    a_text = yaml.safe_dump(a, sort_keys=False, allow_unicode=True).splitlines(keepends=True)
    b_text = yaml.safe_dump(b, sort_keys=False, allow_unicode=True).splitlines(keepends=True)
    diff = list(difflib.unified_diff(a_text, b_text, fromfile=version_a, tofile=version_b))
    return {"diff": "".join(diff) or "No differences.", "lines_changed": len(diff)}


@mcp.tool()
@_safe_tool
def score_ats(version: str = "master") -> dict:
    """Run formatting-focused ATS compatibility checks against a resume
    version (standard section headings, bullet length, action-verb starts,
    dates present, etc). This is separate from keyword matching -- use
    match_resume_to_jd for keyword coverage against a specific JD."""
    return _score_ats(_load_readable(version))


@mcp.tool()
@_safe_tool
def list_templates() -> dict:
    """List all available resume layout templates (id, name, section order,
    and what kind of JD/candidate each one fits best). Use recommend_template
    to have one picked automatically for a specific JD."""
    return {"templates": _templates.list_templates()}


@mcp.tool()
@_safe_tool
def recommend_template(jd_text: str, version: str = "master") -> dict:
    """Score every resume template against a job description (and the
    resume's own content, e.g. whether it has certifications or heavily
    quantified bullets) and recommend the best-fitting one. Deterministic --
    no LLM call. Pass the returned recommended_template id to export_resume."""
    return _templates.recommend_template(jd_text, _load_readable(version))


@mcp.tool()
@_safe_tool
def validate_version(version_id: str, workflow_id: str) -> dict:
    """STEP 4. Run every deterministic check on a tailored version: source
    (master hash + patch replay), provenance, etiquette/ATS content,
    structure, template contract, LaTeX (inferred) and the compiled PDF
    (measured: page count/size, fonts, font size, margins, text). Returns
    critical_failures, warnings, measured_properties, inferred_properties
    and not_available. Nothing is released by this call."""
    from lib import release
    return release.validate_version(version_id, workflow_id)


@mcp.tool()
@_safe_tool
def release_resume(version_id: str, workflow_id: str) -> dict:
    """STEP 5. The release gate. Re-validates, and only if there are no
    critical/error failures (and PDF verification is available) writes a
    release report, freezes the version (released versions are immutable)
    and publishes the exact validated PDF + LaTeX. A blocked release returns
    the failures; repair with tailor_resume(repair_of=...) using only
    drop_block/reorder patches (max 3), or pick a supported template. Never
    tell the user the resume is done unless released is true."""
    from lib import release
    return release.release_resume(version_id, workflow_id)


@mcp.tool(structured_output=False)
@_safe_tool
def export_resume(version: str, workflow_id: str | None = None, format: str = "pdf", mode: str = "release"):
    """STEP 6. Return the PDF (embedded) and its exact LaTeX source.

    mode="release" (default): only for a version that passed release_resume;
    returns the byte-identical released files. mode="draft": an on-demand,
    clearly labelled DRAFT / UNVERIFIED preview (also how a master is
    previewed: version="master-resume" or "master-cv"). format docx/md/txt
    also writes that file. The PDF is always rendered with the version's own
    template -- a template without a production renderer is an error, never
    silently swapped for another one."""
    from lib import release
    out = release.export(version, workflow_id, format, mode)
    summary = {k: out[k] for k in ("label", "released", "template_id", "pdf_path", "tex_path", "tex_sha256",
                                   "pdf_sha256") if k in out}
    if "extra_path" in out:
        summary[f"{format}_path"] = out["extra_path"]
    return [
        types.TextContent(
            type="text",
            text=(
                f"{out['label']}\n"
                f"PDF: {out['pdf_path']}\nLaTeX: {out['tex_path']}\n"
                + (f"Also saved {format}: {out['extra_path']}\n" if "extra_path" in out else "")
                + "(If the PDF doesn't appear as an attachment, open it from that path -- some MCP clients "
                "don't render embedded file blobs inline.)\n\n"
                f"```json\n{json.dumps(summary, indent=2)}\n```\n\n"
                f"LaTeX source (this exact text was compiled into the PDF):\n\n```latex\n{out['tex']}\n```"
            ),
        ),
        types.EmbeddedResource(
            type="resource",
            resource=types.BlobResourceContents(
                uri=f"file://{out['pdf_path']}",
                mimeType="application/pdf",
                blob=base64.b64encode(out["pdf_bytes"]).decode("ascii"),
            ),
        ),
    ]


@mcp.tool()
@_safe_tool
def get_workflow_status(workflow_id: str | None = None) -> dict:
    """Status of one tailoring workflow (evidence, versions with released
    state, repair attempts, last status, audit summary), or -- with no
    workflow_id -- the list of workflow IDs plus system reliability metrics
    rebuilt from the audit log. Metrics describe the software, never the
    candidate."""
    from lib import metrics, workflows
    if not workflow_id:
        return {"ok": True, "workflows": workflows.list_workflow_ids(), "metrics": metrics.rebuild_metrics()}
    wf = workflows.load_workflow(workflows.check_workflow_id(workflow_id))
    from lib import release as _release
    versions = []
    for vid in wf.get("version_ids") or []:
        meta = (storage.load_version(vid) or {}).get("metadata") or {}
        versions.append({"version_id": vid, "released": bool(meta.get("released")),
                         "lifecycle_state": _release.lifecycle_state(meta),
                         "release_report_id": meta.get("release_report_id"), "template_id": meta.get("template_id"),
                         "repair_of": meta.get("repair_of")})
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "source_kind": wf["source_kind"],
        "status": wf.get("status"),
        "created_at": wf.get("created_at"),
        "evidence_ids": wf.get("evidence_ids", []),
        "versions": versions,
        "repair_attempts": wf.get("repair_attempts", 0),
        "analysis": wf.get("analysis", {}),
        "audit": metrics.workflow_summary(workflow_id),
    }


@mcp.tool()
@_safe_tool
def system_diagnostics(workflow_id: str | None = None, limit: int = 200, errors_only: bool = False) -> dict:
    """Read-only forensics over the audit log: a reconstructed timeline,
    failure counts by category/code/check-id, reliability metrics and fixed
    remediation hints. Unlike get_workflow_status, this never writes
    metrics.json -- use it for a strictly read-only view. Recommendations
    come only from a fixed table; nothing here retries, repairs or changes
    rules/thresholds/templates on its own -- report the failure and the
    recommended human action."""
    from lib import diagnostics
    return {"ok": True, **diagnostics.system_diagnostics(workflow_id, limit, errors_only)}


@mcp.tool()
@_safe_tool
def list_versions() -> dict:
    """List all saved resume versions (excluding the master), each with its
    derived lifecycle_state (draft/released) -- not a persisted "validated"
    flag; a version is only ever draft or released."""
    from lib import release as _release
    versions = []
    for vid in storage.list_version_ids():
        meta = (storage.load_version(vid) or {}).get("metadata") or {}
        versions.append({"version_id": vid, "lifecycle_state": _release.lifecycle_state(meta)})
    return {"versions": versions}


# --------------------------------------------------------------------------
# Prompts -- guided multi-step workflows
# --------------------------------------------------------------------------

@mcp.prompt()
def tailor_resume_workflow(jd_text: str, company: str = "", role: str = "", kind: str = "resume") -> str:
    """Guided, gated tailoring workflow: evidence -> patches -> validate ->
    release -> export."""
    suggested_id = "-".join(x for x in [company, role, str(date.today())] if x) or f"tailored-{date.today()}"
    return f"""Tailor my {kind} to this job description. The server enforces the rules; follow this order exactly:

1. Read resume://etiquette (content rules, evidence placement matrix, verb scope) before writing anything.
2. analyze_tailoring_requirements(jd_text, source_kind="{kind}") -> keep the workflow_id. Source of truth is ONLY the workspace master -- never Claude memory, earlier conversations, or previous tailored versions.
3. Ask me each evidence prompt ONE AT A TIME, in its `order` (ask_one_at_a_time is true for a reason -- batching invites one vague "yes" that isn't real confirmation for any single term), in plain words: whether and how I have used the term (professional, internship, personal project, academic, coursework, certification, learning only, or not at all). Do not answer for me or assume. Only after I explicitly answer, call save_tailoring_evidence(confirmed=True) with my own words (and any numbers I gave, quoted exactly). If I say I don't have it, save category "none" and say the returned `statement` back to me verbatim -- do not soften it or skip it.
4. get_master_resume(kind="{kind}") -> use citable_blocks IDs.
5. Propose structured patches (never a full resume) and call tailor_resume(save_as="{suggested_id}", patches=..., workflow_id=..., evidence_ids=[...]). Every new/changed block cites real source_refs. Reword for relevance and JD terminology only where the evidence supports it; never raise claim strength, add metrics, technologies or scope that the sources do not state. If the server rejects a patch, fix the patch -- do not work around the rule.
6. validate_version (returns a 7-group `checklist`: provenance, evidence, content, template, ats, latex, pdf), then release_resume. If release is blocked, repair only with drop_block/reorder patches via tailor_resume(repair_of=...) (max 3), or report that release is blocked. Never shrink fonts/margins or pick an unregistered layout.
7. export_resume(version, workflow_id) and deliver BOTH the attached PDF and the LaTeX source in a ```latex block.
8. Both validate_version and release_resume return a `completion` block -- render `completion.display_order` as your finish message; do not recompute or add items, and do not reconstruct it from memory across turns. It covers: Source (workspace master {kind}); Template; Added after my confirmation (term - category - section - why); Not added (say each `statement` verbatim); what was optimized; the validation checklist; Released: yes/no. Say plainly where each confirmed term was placed (e.g. "AWS was added to Projects because you confirmed your personal project ran on AWS EC2; it was not added to Experience"). If an older server has no `completion` block, assemble the same 10 parts yourself from the individual tool results.

Job description:
---
{jd_text}
---
"""


@mcp.prompt()
def quick_ats_check(jd_text: str, version: str = "master") -> str:
    """Just check keyword match + ATS formatting for a resume version
    against a JD, without rewriting anything."""
    return f"""Please check my resume (version="{version}") against this job description without rewriting anything yet:

1. Call match_resume_to_jd with this JD text.
2. Call score_ats on version="{version}" (checks formatting and the etiquette rules in resume://etiquette: weak bullet openers, generic summary phrases, unnecessary personal identifiers, quantification, bullet density).
3. Summarize: match score, top missing keywords, and top ATS/etiquette issues, ranked by what would help most.

Job description:
---
{jd_text}
---
"""


if __name__ == "__main__":
    mcp.run()
