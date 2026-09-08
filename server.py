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
import json
from datetime import date
from pathlib import Path

import yaml
from mcp import types
from mcp.server.fastmcp import FastMCP

from lib import storage, parsing
from lib import templates as _templates
from lib import export as _export
from lib.keywords import extract_jd_keywords as _extract_jd_keywords
from lib.matching import match_resume_to_jd as _match_resume_to_jd
from lib.ats import score_ats as _score_ats
from lib.export import export_resume as _export_resume

mcp = FastMCP("resume-tailor")

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "exports"
ETIQUETTE_PATH = Path(__file__).resolve().parent / "resources" / "resume_etiquette.yaml"


# --------------------------------------------------------------------------
# Resources -- read-only, URI-addressable state
# --------------------------------------------------------------------------

@mcp.resource("resume://master")
def resource_master_resume() -> str:
    """The current master (canonical) resume as YAML."""
    resume = storage.load_master()
    if resume is None:
        return "# No master resume yet.\n# Use the parse_resume tool to import one, or edit resources/master_resume.yaml directly."
    return yaml.safe_dump(resume, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://sections/{name}")
def resource_resume_section(name: str) -> str:
    """One section of the master resume (summary, skills, experience,
    education, projects, certifications, contact)."""
    resume = storage.load_master()
    if resume is None:
        return f"# No master resume yet -- nothing to show for section '{name}'."
    if name not in resume:
        return f"# Unknown section '{name}'. Known sections: {', '.join(resume.keys())}"
    return yaml.safe_dump({name: resume[name]}, sort_keys=False, allow_unicode=True)


@mcp.resource("resume://versions/{version_id}")
def resource_resume_version(version_id: str) -> str:
    """A previously saved tailored resume version, as YAML."""
    data = storage.load_version(version_id)
    if data is None:
        return f"# No saved version named '{version_id}'. Use list_versions to see what's available."
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


@mcp.resource("jd://history/{jd_id}")
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
def parse_resume(file_path: str) -> dict:
    """Parse a resume file (.md, .txt, .docx, or .pdf) into the structured
    format and save it as the master resume (resources/master_resume.yaml).

    This OVERWRITES the current master resume. If you already have one and
    just want to try a different source file, back up resources/master_resume.yaml
    first (or check resume://versions for anything you've already saved).
    """
    resume = parsing.parse_resume_file(file_path)
    storage.save_master(resume)
    return {
        "saved_to": str(storage.MASTER_PATH),
        "name": resume.get("name"),
        "sections_found": [
            k for k in ("summary", "skills", "experience", "education", "projects", "certifications")
            if resume.get(k)
        ],
        "unparsed_items": len(resume.get("unparsed", [])),
        "note": "Check the 'unparsed' field in the YAML if unparsed_items > 0 -- move that content into the right fields by hand." if resume.get("unparsed") else None,
    }


@mcp.tool()
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
def match_resume_to_jd(jd_text: str, version: str = "master") -> dict:
    """Compare a resume version against a job description and return a
    deterministic gap analysis: matched / missing / weak keywords and a
    match score. 'weak' means the keyword is only listed under Skills but
    never backed up by an actual experience or project bullet.
    """
    resume = storage.load_version(version)
    if resume is None:
        raise ValueError(f"No resume found for version '{version}'. Run parse_resume first, or check list_versions.")
    return _match_resume_to_jd(resume, jd_text)


@mcp.tool()
def get_master_resume() -> dict:
    """Return the full structured master resume as a dict, for editing."""
    resume = storage.load_master()
    if resume is None:
        raise ValueError("No master resume yet. Run parse_resume first.")
    return resume


@mcp.tool()
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

    storage.save_version(save_as, resume)

    result = {"version_id": save_as, "saved_to": str(storage.version_path(save_as))}
    if jd_text:
        match = _match_resume_to_jd(resume, jd_text)
        result["match_score"] = match["score"]
        result["still_missing"] = match["missing"]
    return result


@mcp.tool()
def diff_versions(version_a: str, version_b: str) -> dict:
    """Show a unified diff between two saved resume versions (use 'master'
    for the current master resume)."""
    a = storage.load_version(version_a)
    b = storage.load_version(version_b)
    if a is None:
        raise ValueError(f"No version '{version_a}'")
    if b is None:
        raise ValueError(f"No version '{version_b}'")

    a_text = yaml.safe_dump(a, sort_keys=False, allow_unicode=True).splitlines(keepends=True)
    b_text = yaml.safe_dump(b, sort_keys=False, allow_unicode=True).splitlines(keepends=True)
    diff = list(difflib.unified_diff(a_text, b_text, fromfile=version_a, tofile=version_b))
    return {"diff": "".join(diff) or "No differences.", "lines_changed": len(diff)}


@mcp.tool()
def score_ats(version: str = "master") -> dict:
    """Run formatting-focused ATS compatibility checks against a resume
    version (standard section headings, bullet length, action-verb starts,
    dates present, etc). This is separate from keyword matching -- use
    match_resume_to_jd for keyword coverage against a specific JD."""
    resume = storage.load_version(version)
    if resume is None:
        raise ValueError(f"No resume found for version '{version}'.")
    return _score_ats(resume)


@mcp.tool()
def list_templates() -> dict:
    """List all available resume layout templates (id, name, section order,
    and what kind of JD/candidate each one fits best). Use recommend_template
    to have one picked automatically for a specific JD."""
    return {"templates": _templates.list_templates()}


@mcp.tool()
def recommend_template(jd_text: str, version: str = "master") -> dict:
    """Score every resume template against a job description (and the
    resume's own content, e.g. whether it has certifications or heavily
    quantified bullets) and recommend the best-fitting one. Deterministic --
    no LLM call. Pass the returned recommended_template id to export_resume."""
    resume = storage.load_version(version)
    return _templates.recommend_template(jd_text, resume)


@mcp.tool(structured_output=False)
def export_resume(version: str = "master", format: str = "pdf", out_path: str | None = None, template: str = "auto", jd_text: str | None = None):
    """Export a resume version to a real file.

    format="pdf" (the default) is the one to use whenever someone wants a
    finished, tailored resume: it renders the Jake's-Resume-style LaTeX
    template, compiles it to PDF with the bundled tectonic engine, and
    returns BOTH the compiled PDF and its LaTeX source directly in this
    tool's response -- there is nothing to go dig out of a folder
    afterwards. (A copy of both files is still saved under data/exports/ as
    a backup.)

    Other formats: "tex" (just the LaTeX source, no compile), "docx", "md",
    "txt" -- these return a dict with the saved file path instead.

    template: a specific template id (see list_templates), or "auto" (default).
    Only "classic-minimalist" has a real pdf/tex renderer today -- the other
    templates only affect section order/heading color in docx/md/txt.
    Auto-selection needs a JD to score against -- pass jd_text, or it falls
    back to the classic-minimalist default template.
    """
    resume = storage.load_version(version)
    if resume is None:
        raise ValueError(f"No resume found for version '{version}'.")

    if template == "auto":
        template_id = _templates.recommend_template(jd_text, resume)["recommended_template"] if jd_text else _templates.DEFAULT_TEMPLATE_ID
    else:
        template_id = template

    fmt = format.lower().lstrip(".")

    if out_path is None:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = str(EXPORT_DIR / f"{version}.{fmt}")

    if fmt == "pdf":
        if template_id != "classic-minimalist":
            raise ValueError(
                f"Template '{template_id}' has no LaTeX/PDF renderer yet -- only "
                "'classic-minimalist' does. Pass template='classic-minimalist', "
                "or use format='docx'/'md'/'txt' for the other layouts."
            )
        tex_source = _export.to_tex(resume, template_id)
        pdf_path = _export.to_pdf(resume, out_path, template_id)
        pdf_bytes = Path(pdf_path).read_bytes()

        summary = {
            "template": template_id,
            "pdf_path": pdf_path,
            "tex_path": str(Path(pdf_path).with_suffix(".tex")),
        }
        return [
            types.TextContent(
                type="text",
                text=(
                    f"```json\n{json.dumps(summary, indent=2)}\n```\n\n"
                    f"LaTeX source:\n\n```latex\n{tex_source}\n```"
                ),
            ),
            types.EmbeddedResource(
                type="resource",
                resource=types.BlobResourceContents(
                    uri=f"file://{Path(pdf_path).resolve()}",
                    mimeType="application/pdf",
                    blob=base64.b64encode(pdf_bytes).decode("ascii"),
                ),
            ),
        ]

    saved_path = _export_resume(resume, out_path, fmt, template_id)
    return {"format": fmt, "path": saved_path, "template": template_id}


@mcp.tool()
def list_versions() -> dict:
    """List all saved resume versions (excluding the master)."""
    return {"versions": storage.list_version_ids()}


@mcp.tool()
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
8. Call export_resume with format="pdf" (the default) and this jd_text. It returns the compiled PDF and its LaTeX source directly in the tool result -- put the LaTeX source in your reply as a ```latex code block, and make sure the PDF comes through to me in the chat. Do not just tell me a file path and stop there.

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
