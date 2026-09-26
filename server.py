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
import json
import sys
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
from lib import export as _export
from lib.matching import match_resume_to_jd as _match_resume_to_jd
from lib.ats import score_ats as _score_ats
from lib.export import export_resume as _export_resume

mcp = FastMCP("resume-tailor")

ETIQUETTE_PATH = Path(__file__).resolve().parent / "resources" / "resume_etiquette.yaml"


# --------------------------------------------------------------------------
# Resources -- read-only, URI-addressable state
# --------------------------------------------------------------------------

def _safe_tool(fn):
    """Uniform error model (spec §69): business failures become
    {"ok": false, "error": {...}}; anything unexpected becomes a generic
    INTERNAL_ERROR with no paths or personal data in the response."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ResumeTailorError as e:
            return e.to_result()
        except Exception as e:  # noqa: BLE001 - last-resort boundary
            print(f"resume-tailor: internal error in {fn.__name__}: {type(e).__name__}", file=sys.stderr)
            return internal_error_result()
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
def initialize_workspace() -> dict:
    """Create the local Resume Tailor workspace on first run (under
    $RESUME_TAILOR_HOME, default ~/.resume-tailor). Idempotent: returns the
    existing workspace if one is configured. Writes no personal data -- it
    reports where the master Resume/CV belong. If you have masters from the
    old repo layout (resources/master_*.yaml), run migrate_legacy_data next."""
    return {"ok": True, **_workspace.initialize_workspace()}


@mcp.tool()
@_safe_tool
def get_workspace() -> dict:
    """Show the active workspace: ID, root, master paths and which masters
    exist. Errors with WORKSPACE_NOT_INITIALIZED if there is none yet --
    never picks or creates one silently."""
    return {"ok": True, **_workspace.describe_workspace()}


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
                            confirmed: bool = False, metrics: list[str] | None = None) -> dict:
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
                                  confirmed=confirmed, metrics=metrics)


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

    Every new/changed block needs source_refs to real master blocks or to
    evidence the user confirmed in THIS workflow (pass those IDs in
    evidence_ids). Titles, companies, dates, degrees and contact details
    cannot be patched. repair_of=<version_id> re-applies that version's
    patches plus drop_block/reorder-only repairs (max 3 per workflow).
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


@mcp.tool(structured_output=False)
@_safe_tool
def export_resume(version: str = "master", format: str = "pdf", template: str = "auto", jd_text: str | None = None):
    """Export a resume version to a real file.

    ALWAYS compiles and returns the PDF + its LaTeX source directly in this
    tool's response, no matter what `format` is -- there is nothing to go
    dig out of a folder afterwards. The PDF is always rendered with the
    Jake's-Resume-style `classic-minimalist` LaTeX template, since that's
    the only layout with a real LaTeX/PDF renderer; if a different
    `template` was requested it's noted in the response but the PDF still
    gets produced. (Both files are also saved under data/exports/ as a
    backup.)

    Pass format="docx", "md", or "txt" to ALSO save the resume in that
    format (section order/heading color follow `template` there) --
    the PDF + LaTeX are still always included on top of it. format="tex"
    just skips the docx/md/txt extra and returns the PDF + LaTeX alone,
    same as the default format="pdf".

    template: a specific template id (see list_templates), or "auto" (default).
    Auto-selection needs a JD to score against -- pass jd_text, or it falls
    back to the classic-minimalist default template.
    """
    resume = _load_readable(version)
    export_dir = _workspace.get_exports_path()
    version = _workspace.validate_id(version or "master", "version")

    if template == "auto":
        template_id = _templates.recommend_template(jd_text, resume)["recommended_template"] if jd_text else _templates.DEFAULT_TEMPLATE_ID
    else:
        template_id = template

    fmt = format.lower().lstrip(".")

    export_dir.mkdir(parents=True, exist_ok=True)
    pdf_out_path = str(export_dir / f"{version}.pdf")

    pdf_render_template = "classic-minimalist"
    tex_source = _export.to_tex(resume, pdf_render_template)
    pdf_path = Path(_export.to_pdf(resume, pdf_out_path, pdf_render_template)).resolve()
    tex_path = pdf_path.with_suffix(".tex")
    pdf_bytes = pdf_path.read_bytes()

    summary = {"template": pdf_render_template, "pdf_path": str(pdf_path), "tex_path": str(tex_path)}

    extra_note = ""
    extra_file_line = ""
    if fmt not in ("pdf", "tex"):
        extra_out_path = str(export_dir / f"{version}.{fmt}")
        extra_path = Path(_export_resume(resume, extra_out_path, fmt, template_id)).resolve()
        summary[f"{fmt}_path"] = str(extra_path)
        extra_file_line = f"Also saved {fmt}: {extra_path}\n"
    if template_id != pdf_render_template:
        extra_note = (
            f"(Requested template '{template_id}' has no LaTeX/PDF renderer yet, so the PDF/LaTeX "
            f"above used '{pdf_render_template}' instead"
            + (f"; the {fmt} file above still uses '{template_id}'.)\n" if fmt not in ("pdf", "tex") else ".)\n")
        )

    return [
        types.TextContent(
            type="text",
            text=(
                f"Saved PDF to: {pdf_path}\n"
                f"Saved LaTeX to: {tex_path}\n"
                f"{extra_file_line}"
                "(If the PDF doesn't appear as an attachment above in this chat, "
                "open it directly from that path -- some MCP clients don't render "
                "embedded file blobs inline.)\n"
                f"{extra_note}\n"
                f"```json\n{json.dumps(summary, indent=2)}\n```\n\n"
                f"LaTeX source:\n\n```latex\n{tex_source}\n```"
            ),
        ),
        types.EmbeddedResource(
            type="resource",
            resource=types.BlobResourceContents(
                uri=f"file://{pdf_path}",
                mimeType="application/pdf",
                blob=base64.b64encode(pdf_bytes).decode("ascii"),
            ),
        ),
    ]


@mcp.tool()
@_safe_tool
def list_versions() -> dict:
    """List all saved resume versions (excluding the master)."""
    return {"versions": storage.list_version_ids()}


# --------------------------------------------------------------------------
# Prompts -- guided multi-step workflows
# --------------------------------------------------------------------------

@mcp.prompt()
def tailor_resume_workflow(jd_text: str, company: str = "", role: str = "") -> str:
    """Guided workflow: analyze a JD against the master resume, then draft
    and save a tailored version."""
    suggested_id = "-".join(x for x in [company.lower().replace(" ", "-"), role.lower().replace(" ", "-"), str(date.today())] if x) or f"tailored-{date.today()}"
    return f"""I want you to tailor my resume to this job description. Please:

1. Read the resume://etiquette resource first -- it has the bullet formula, summary formula by career stage, ATS/keyword rules, and the golden rule (reorganize and tailor real evidence, never invent metrics, technologies, titles, dates, publications, or skills).
2. Call match_resume_to_jd with this JD text against my master resume to see the keyword gap analysis (matched / missing / weak, plus the score).
3. Call get_master_resume to see my current resume content.
4. Rewrite the summary and relevant experience/project bullets to naturally incorporate the MISSING and WEAK keywords where they're truthfully applicable -- do not fabricate experience I don't have. Follow the bullet formula (action verb + context/method + measurable result where honestly available) and avoid the weak openers and generic summary phrases listed in resume://etiquette.
5. Call tailor_resume with save_as="{suggested_id}" (or a better id you choose), the full updated resume dict, and this jd_text so I get an updated match score back.
6. Call score_ats on the new version to check formatting and etiquette issues (weak openers, generic summary phrases, unnecessary personal identifiers, quantification, bullet density).
7. Show me a summary: before/after match score, what changed, and any remaining ATS/etiquette issues.
8. MANDATORY, not optional: call export_resume with format="pdf" (the default) and this jd_text. It returns the compiled PDF and its LaTeX source directly in the tool result. Deliver BOTH in your reply -- the PDF as the actual attached/embedded file, and the LaTeX source as a ```latex code block -- every time I ask you to tailor a resume/CV to a JD. Saving a version with tailor_resume is not the deliverable; the PDF + LaTeX are. Never stop at a file path or a prose description of the changes.

Here is the job description:

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
