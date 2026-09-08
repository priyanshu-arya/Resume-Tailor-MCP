"""Render the structured resume out to a real file: .md, .txt, .docx, .tex, .pdf."""

import shutil
import subprocess
from pathlib import Path

from lib import templates as _templates
from lib import latex as _latex

TECTONIC_BIN = Path(__file__).resolve().parent.parent / "bin" / "tectonic"

# Original, template-agnostic ordering -- used when no template_id is given
# so existing callers keep their exact prior behavior.
_DEFAULT_SECTION_ORDER = ["summary", "skills", "experience", "education", "projects", "certifications"]


def _section_order(template_id: str | None) -> list[str]:
    if not template_id:
        return _DEFAULT_SECTION_ORDER
    tmpl = _templates.get_template(template_id)
    if tmpl is None:
        raise ValueError(f"Unknown template '{template_id}'. Use list_templates to see valid ids.")
    # Layouts may reference sections (e.g. "achievements") that aren't part
    # of the resume schema -- drop anything we don't know how to render.
    return [s for s in tmpl["layout"]["sections"] if s in _DEFAULT_SECTION_ORDER]


def _bullets_text(bullets) -> list[str]:
    return [b.get("text", "") if isinstance(b, dict) else str(b) for b in bullets or []]


def _md_summary(resume: dict) -> list[str]:
    return ["## Summary", resume["summary"], ""] if resume.get("summary") else []


def _md_skills(resume: dict) -> list[str]:
    if not resume.get("skills"):
        return []
    lines = ["## Skills"]
    for group in resume["skills"]:
        items = ", ".join(group.get("items", []))
        lines.append(f"- {group.get('category', 'General')}: {items}")
    lines.append("")
    return lines


def _md_experience(resume: dict) -> list[str]:
    if not resume.get("experience"):
        return []
    lines = ["## Experience"]
    for exp in resume["experience"]:
        header = f"### {exp.get('title', '')}, {exp.get('company', '')} | {exp.get('location', '')} | {exp.get('start', '')} - {exp.get('end', '')}"
        lines.append(header)
        for b in _bullets_text(exp.get("bullets")):
            lines.append(f"- {b}")
        lines.append("")
    return lines


def _md_education(resume: dict) -> list[str]:
    if not resume.get("education"):
        return []
    lines = ["## Education"]
    for edu in resume["education"]:
        lines.append(f"### {edu.get('degree', '')}, {edu.get('school', '')} | {edu.get('year', '')}")
    lines.append("")
    return lines


def _md_projects(resume: dict) -> list[str]:
    if not resume.get("projects"):
        return []
    lines = ["## Projects"]
    for proj in resume["projects"]:
        lines.append(f"### {proj.get('name', '')}")
        for b in _bullets_text(proj.get("bullets")):
            lines.append(f"- {b}")
    lines.append("")
    return lines


def _md_certifications(resume: dict) -> list[str]:
    if not resume.get("certifications"):
        return []
    lines = ["## Certifications"]
    for cert in resume["certifications"]:
        lines.append(f"- {cert}")
    lines.append("")
    return lines


_MD_SECTION_RENDERERS = {
    "summary": _md_summary,
    "skills": _md_skills,
    "experience": _md_experience,
    "education": _md_education,
    "projects": _md_projects,
    "certifications": _md_certifications,
}


def to_markdown(resume: dict, template_id: str | None = None) -> str:
    lines = [f"# {resume.get('name', '')}"]
    contact = resume.get("contact", {})
    contact_line = " | ".join(v for v in [contact.get("email"), contact.get("phone"), contact.get("linkedin"), contact.get("github"), contact.get("substack"), contact.get("location")] if v)
    if contact_line:
        lines.append(contact_line)
    lines.append("")

    for section in _section_order(template_id):
        lines += _MD_SECTION_RENDERERS[section](resume)

    return "\n".join(lines).strip() + "\n"


def to_txt(resume: dict, template_id: str | None = None) -> str:
    # Plain text is the safest possible ATS format -- strip markdown symbols.
    md = to_markdown(resume, template_id)
    return "\n".join(
        line.lstrip("#").lstrip("-").strip() if line.strip() else ""
        for line in md.splitlines()
    )


# Templates whose source layout uses colored section headers (see
# data/templates/templates.yaml) get that color in the .docx export too.
_HEADING_COLOR_BY_TEMPLATE = {"full-stack-modern": (0x1F, 0x4E, 0x79)}


def _docx_heading(document, text: str, template_id: str | None):
    heading = document.add_heading(text, level=2)
    color = _HEADING_COLOR_BY_TEMPLATE.get(template_id)
    if color:
        from docx.shared import RGBColor
        for run in heading.runs:
            run.font.color.rgb = RGBColor(*color)


def _docx_summary(document, resume: dict, template_id: str | None) -> None:
    if resume.get("summary"):
        _docx_heading(document, "Summary", template_id)
        document.add_paragraph(resume["summary"])


def _docx_skills(document, resume: dict, template_id: str | None) -> None:
    if resume.get("skills"):
        _docx_heading(document, "Skills", template_id)
        for group in resume["skills"]:
            items = ", ".join(group.get("items", []))
            document.add_paragraph(f"{group.get('category', 'General')}: {items}", style="List Bullet")


def _docx_experience(document, resume: dict, template_id: str | None) -> None:
    if resume.get("experience"):
        _docx_heading(document, "Experience", template_id)
        for exp in resume["experience"]:
            p = document.add_paragraph()
            p.add_run(f"{exp.get('title', '')}, {exp.get('company', '')}").bold = True
            meta = " | ".join(v for v in [exp.get("location"), f"{exp.get('start', '')} - {exp.get('end', '')}"] if v)
            if meta:
                document.add_paragraph(meta)
            for b in _bullets_text(exp.get("bullets")):
                document.add_paragraph(b, style="List Bullet")


def _docx_education(document, resume: dict, template_id: str | None) -> None:
    if resume.get("education"):
        _docx_heading(document, "Education", template_id)
        for edu in resume["education"]:
            document.add_paragraph(f"{edu.get('degree', '')}, {edu.get('school', '')} ({edu.get('year', '')})")


def _docx_projects(document, resume: dict, template_id: str | None) -> None:
    if resume.get("projects"):
        _docx_heading(document, "Projects", template_id)
        for proj in resume["projects"]:
            p = document.add_paragraph()
            p.add_run(proj.get("name", "")).bold = True
            for b in _bullets_text(proj.get("bullets")):
                document.add_paragraph(b, style="List Bullet")


def _docx_certifications(document, resume: dict, template_id: str | None) -> None:
    if resume.get("certifications"):
        _docx_heading(document, "Certifications", template_id)
        for cert in resume["certifications"]:
            document.add_paragraph(cert, style="List Bullet")


_DOCX_SECTION_RENDERERS = {
    "summary": _docx_summary,
    "skills": _docx_skills,
    "experience": _docx_experience,
    "education": _docx_education,
    "projects": _docx_projects,
    "certifications": _docx_certifications,
}


def to_docx(resume: dict, out_path: str, template_id: str | None = None) -> str:
    try:
        import docx
        from docx.shared import Pt
    except ImportError as e:
        raise RuntimeError("Exporting to .docx requires python-docx. Install it with: pip install python-docx") from e

    order = _section_order(template_id)  # raises if template_id is unknown

    document = docx.Document()
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    document.add_heading(resume.get("name", ""), level=1)
    contact = resume.get("contact", {})
    contact_line = " | ".join(v for v in [contact.get("email"), contact.get("phone"), contact.get("linkedin"), contact.get("github"), contact.get("substack"), contact.get("location")] if v)
    if contact_line:
        document.add_paragraph(contact_line)

    for section in order:
        _DOCX_SECTION_RENDERERS[section](document, resume, template_id)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    document.save(out_path)
    return out_path


def to_tex(resume: dict, template_id: str | None = None) -> str:
    """Render the resume into the Jake's-Resume-style LaTeX source (see
    lib/latex.py). This is the same source the PDF is compiled from, so the
    two outputs can never drift apart."""
    return _latex.render_latex(resume, template_id)


def _tectonic_path() -> str:
    if TECTONIC_BIN.exists():
        return str(TECTONIC_BIN)
    found = shutil.which("tectonic")
    if found:
        return found
    raise RuntimeError(
        "No tectonic binary found (expected at bin/tectonic, or 'tectonic' on "
        "PATH). Download the standalone release for your platform from "
        "https://github.com/tectonic-typesetting/tectonic/releases and place "
        "it at bin/tectonic."
    )


def to_pdf(resume: dict, out_path: str, template_id: str | None = None) -> str:
    """Render the resume to LaTeX and compile it to PDF with tectonic (a
    self-contained LaTeX engine bundled at bin/tectonic -- no LibreOffice or
    system LaTeX distribution required)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tex_path = out_path.with_suffix(".tex")
    tex_path.write_text(to_tex(resume, template_id), encoding="utf-8")

    tectonic = _tectonic_path()
    result = subprocess.run(
        [tectonic, "--outdir", str(out_path.parent), str(tex_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tectonic compilation failed:\n{result.stdout}\n{result.stderr}")

    produced = tex_path.with_suffix(".pdf")
    if produced != out_path:
        produced.replace(out_path)
    return str(out_path)


def export_resume(resume: dict, out_path: str, fmt: str, template_id: str | None = None) -> str:
    fmt = fmt.lower().lstrip(".")
    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if fmt == "md":
        Path(out_path).write_text(to_markdown(resume, template_id), encoding="utf-8")
        return out_path
    if fmt == "txt":
        Path(out_path).write_text(to_txt(resume, template_id), encoding="utf-8")
        return out_path
    if fmt == "docx":
        return to_docx(resume, out_path, template_id)
    if fmt == "tex":
        Path(out_path).write_text(to_tex(resume, template_id), encoding="utf-8")
        return out_path
    if fmt == "pdf":
        return to_pdf(resume, out_path, template_id)
    raise ValueError(f"Unsupported export format: {fmt} (use md, txt, docx, tex, or pdf)")
