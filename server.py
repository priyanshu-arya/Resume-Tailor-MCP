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

from lib import storage, parsing
from lib import workspace as _workspace
from lib import migration as _migration
from lib.errors import ResumeTailorError, internal_error_result
from lib.ids import normalize_master
from lib.schemas import validate_kind
from lib import templates as _templates
from lib import export as _export
from lib.keywords import extract_jd_keywords as _extract_jd_keywords
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
            f"# Use the parse_resume tool (kind='{kind}'), the create-master-file skill, "
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
def parse_resume(file_path: str, kind: str = "resume", replace_hash: str | None = None) -> dict:
    """Parse a resume/CV file (.md, .txt, .docx, or .pdf) into the structured
    format and save it as the master document.

    kind: "resume" (default) or "cv" -- two separate canonical documents in
    the workspace, never mixed.

    If a master of that kind already exists this fails with MASTER_EXISTS.
    Confirm with the user, then call again with replace_hash set to the
    current master's hash (from get_master_resume) to replace it; the old
    one is backed up.
    """
    validate_kind(kind)
    resume = normalize_master(parsing.parse_resume_file(file_path), kind)
    storage.save_master(kind, resume, replace_hash, "parse_resume import")
    return {
        "ok": True,
        "kind": kind,
        "saved_to": str(_workspace.get_master_path(kind)),
        "name": resume.get("name"),
        "sections_found": [
            k for k in ("summary", "skills", "experience", "education", "projects", "certifications")
            if resume.get(k)
        ],
        "unparsed_items": len(resume.get("unparsed", [])),
        "note": "Check the 'unparsed' field in the YAML if unparsed_items > 0 -- move that content into the right fields by hand." if resume.get("unparsed") else None,
    }


@mcp.tool()
@_safe_tool
def extract_jd_keywords(jd_text: str, save_as: str | None = None) -> dict:
    """Extract must-have / nice-to-have keywords, title, seniority, and
    years-of-experience signal from a pasted job description.

    Pass save_as (e.g. "acme-swe-2026") to also persist the JD under
    jd://history/{save_as} for later reuse.
    """
    result = _extract_jd_keywords(jd_text)
    if save_as:
        storage.save_jd(save_as, jd_text, result)
    return result


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
    return {"ok": True, "kind": kind, "master_hash": master_hash, "master": resume}


@mcp.tool()
@_safe_tool
def tailor_resume(save_as: str, resume: dict, jd_text: str | None = None, source_version: str = "master") -> dict:
    """Save a tailored resume as a new version.

    `resume` is the FULL structured resume dict (same shape as
    resume://master: name, contact, summary, skills, experience, education,
    projects, certifications) after you've rewritten the relevant bullets /
    summary / skills to match the job description. This tool just validates
    and persists it -- the actual tailoring judgment (which bullets to
    reword, which keywords to weave in naturally) should happen in the
    conversation, informed by match_resume_to_jd's gap analysis and the
    rules in the resume://etiquette resource (bullet formula, honest
    quantification, no fabricated metrics/titles/skills, summary formula
    by career stage). score_ats checks several of these automatically.

    save_as should be a short filesystem-safe id, e.g. "acme-swe-2026-09".
    """
    required_keys = {"name", "contact", "summary", "skills", "experience", "education", "projects", "certifications"}
    missing_keys = required_keys - set(resume.keys())
    if missing_keys:
        raise ValueError(f"resume is missing required keys: {sorted(missing_keys)}")

    # Phase-1 bridge: replaced by the patch-based interface in phase 2.
    ws = _workspace.get_workspace()
    version_id = _workspace.slugify(save_as)
    resume = dict(resume)
    resume["metadata"] = {"version_id": version_id, "workspace_id": ws.id, "document_kind": "resume",
                          "created_at": _workspace.utc_now_iso()}
    storage.save_version(version_id, resume)

    result = {"ok": True, "version_id": version_id, "saved_to": str(storage.version_path(version_id))}
    if jd_text:
        match = _match_resume_to_jd(resume, jd_text)
        result["match_score"] = match["score"]
        result["still_missing"] = match["missing"]
    return result


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


@mcp.tool()
@_safe_tool
def list_saved_jds() -> dict:
    """List all job descriptions saved via extract_jd_keywords(save_as=...)."""
    return {"jds": storage.list_jd_ids()}


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
