"""Render the structured resume into the classic "Jake's Resume" LaTeX
layout (matches the `classic-minimalist` template). This is the single
source of truth for both the .tex and .pdf export -- the PDF is compiled
straight from this generated .tex, so the two can never drift apart.
"""

from __future__ import annotations

from lib.links import linkedin_label, normalize_url

_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def esc(text) -> str:
    """Escape LaTeX special characters in plain text."""
    text = "" if text is None else str(text)
    return "".join(_SPECIAL_CHARS.get(ch, ch) for ch in text)


def _bullets_text(bullets) -> list[str]:
    return [b.get("text", "") if isinstance(b, dict) else str(b) for b in bullets or []]


_PREAMBLE = r"""\documentclass[letterpaper,11pt]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}

\pagestyle{fancy}
\fancyhf{}
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

% Standard, readable resume margins -- this template is allowed to run to
% ~1.5-2 pages for an experienced candidate rather than crushing margins
% and spacing to force everything onto one page.
\addtolength{\oddsidemargin}{-0.4in}
\addtolength{\evensidemargin}{-0.4in}
\addtolength{\textwidth}{0.8in}
\addtolength{\topmargin}{-0.5in}
\addtolength{\textheight}{1.0in}

\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}
\setlist{topsep=2pt, itemsep=2pt, parsep=0pt, partopsep=0pt}

\titleformat{\section}{\vspace{2pt}\scshape\raggedright\large}{}{0em}{}[\color{black}\titlerule \vspace{2pt}]

\newcommand{\resumeItem}[1]{\item\small{#1}}

\newcommand{\resumeSubheading}[4]{
  \item
    \begin{tabular*}{0.98\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{2pt}
}

\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.98\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{2pt}
}

\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}[leftmargin=0.18in]}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{6pt}}

\begin{document}
"""

_CLOSING = r"""
\end{document}
"""


def _header(resume: dict) -> str:
    name = esc(resume.get("name", ""))
    contact = resume.get("contact", {}) or {}

    parts = []
    if contact.get("phone"):
        parts.append(esc(contact["phone"]))
    if contact.get("email"):
        email = contact["email"]
        parts.append(rf"\href{{mailto:{email}}}{{\underline{{{esc(email)}}}}}")
    if contact.get("website"):
        url = contact["website"]
        parts.append(rf"\href{{{normalize_url(url)}}}{{\underline{{{esc(url)}}}}}")
    if contact.get("linkedin"):
        url = contact["linkedin"]
        parts.append(rf"\href{{{normalize_url(url)}}}{{\underline{{{esc(linkedin_label(url))}}}}}")
    if contact.get("github"):
        url = contact["github"]
        parts.append(rf"\href{{{normalize_url(url)}}}{{\underline{{{esc(url)}}}}}")
    if contact.get("substack"):
        url = contact["substack"]
        parts.append(rf"\href{{{normalize_url(url)}}}{{\underline{{{esc(url)}}}}}")
    if contact.get("location"):
        parts.append(esc(contact["location"]))

    contact_line = " $|$ ".join(parts)

    return (
        "\\begin{center}\n"
        f"    \\textbf{{\\Huge \\scshape {name}}} \\\\ \\vspace{{2pt}}\n"
        f"    \\small {contact_line}\n"
        "\\end{center}\n"
    )


def _section_summary(resume: dict) -> str:
    if not resume.get("summary"):
        return ""
    return (
        "\\section{Summary}\n"
        f"\\small{{{esc(resume['summary'])}}}\n\n"
    )


def _section_experience(resume: dict) -> str:
    if not resume.get("experience"):
        return ""
    lines = ["\\section{Experience}", "  \\resumeSubHeadingListStart"]
    for exp in resume["experience"]:
        dates = f"{exp.get('start', '')} -- {exp.get('end', '')}".strip(" -")
        lines.append(
            "    \\resumeSubheading\n"
            f"      {{{esc(exp.get('title', ''))}}}{{{esc(dates)}}}\n"
            f"      {{{esc(exp.get('company', ''))}}}{{{esc(exp.get('location', ''))}}}"
        )
        bullets = _bullets_text(exp.get("bullets"))
        if bullets:
            lines.append("    \\resumeItemListStart")
            for b in bullets:
                lines.append(f"        \\resumeItem{{{esc(b)}}}")
            lines.append("    \\resumeItemListEnd")
    lines.append("  \\resumeSubHeadingListEnd")
    return "\n".join(lines) + "\n\n"


def _section_projects(resume: dict) -> str:
    if not resume.get("projects"):
        return ""
    lines = ["\\section{Projects}", "    \\resumeSubHeadingListStart"]
    for proj in resume["projects"]:
        name = esc(proj.get("name", ""))
        github = proj.get("github")
        name_tex = rf"\href{{{normalize_url(github)}}}{{\textbf{{{name}}}}}" if github else f"\\textbf{{{name}}}"
        stack = proj.get("stack") or proj.get("tech_stack")
        title = name_tex
        if stack:
            title += f" $|$ \\emph{{{esc(stack)}}}"
        dates = esc(proj.get("dates", ""))
        lines.append(f"      \\resumeProjectHeading\n          {{{title}}}{{{dates}}}")
        bullets = _bullets_text(proj.get("bullets"))
        if bullets:
            lines.append("          \\resumeItemListStart")
            for b in bullets:
                lines.append(f"            \\resumeItem{{{esc(b)}}}")
            lines.append("          \\resumeItemListEnd")
    lines.append("    \\resumeSubHeadingListEnd")
    return "\n".join(lines) + "\n\n"


def _section_skills(resume: dict) -> str:
    if not resume.get("skills"):
        return ""
    rows = []
    for group in resume["skills"]:
        category = esc(group.get("category", "General"))
        items = esc(", ".join(group.get("items", [])))
        rows.append(f"     \\textbf{{{category}}}{{: {items}}} \\\\")
    body = " \\vspace{1pt}\n".join(rows)
    return (
        "\\section{Technical Skills}\n"
        " \\begin{itemize}[leftmargin=0.15in, label={}]\n"
        "    \\small{\\item{\n"
        f"{body}\n"
        "    }}\n"
        " \\end{itemize}\n\n"
    )


def _section_education(resume: dict) -> str:
    if not resume.get("education"):
        return ""
    lines = ["\\section{Education}", "  \\resumeSubHeadingListStart"]
    for edu in resume["education"]:
        lines.append(
            "    \\resumeSubheading\n"
            f"      {{{esc(edu.get('school', ''))}}}{{{esc(edu.get('year', ''))}}}\n"
            f"      {{{esc(edu.get('degree', ''))}}}{{}}"
        )
    lines.append("  \\resumeSubHeadingListEnd")
    return "\n".join(lines) + "\n\n"


def _section_certifications(resume: dict) -> str:
    if not resume.get("certifications"):
        return ""
    items = " $\\bullet$ ".join(esc(c) for c in resume["certifications"])
    return (
        "\\section{Certifications}\n"
        f"\\small{{{items}}}\n\n"
    )


def render_latex(resume: dict, template_id: str | None = None) -> str:
    """Build the full .tex source for a resume using the Jake's-Resume-style
    layout. `template_id` is accepted for interface parity with the other
    exporters but only `classic-minimalist` is implemented today."""
    body = [
        _header(resume),
        _section_summary(resume),
        _section_experience(resume),
        _section_projects(resume),
        _section_skills(resume),
        _section_education(resume),
        _section_certifications(resume),
    ]
    return _PREAMBLE + "\n".join(part for part in body if part) + _CLOSING
