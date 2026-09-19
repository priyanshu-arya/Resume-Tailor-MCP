"""Parse a resume (markdown, .docx, or .pdf) into the structured schema.

Expected markdown shape (this is what parse_markdown looks for; it's
forgiving about spacing but wants these section headings):

    # Full Name
    email | phone | linkedin | location

    ## Summary
    A couple of sentences.

    ## Skills
    - Languages: Python, SQL, JavaScript
    - Cloud: AWS, GCP

    ## Experience
    ### Title, Company | Location | Start - End
    - bullet one
    - bullet two

    ## Education
    ### Degree, School | Year

    ## Projects
    ### Project Name
    - bullet

    ## Certifications
    - Cert name (year)

Anything it can't confidently place goes into an "unparsed" field on
the returned structure so you don't silently lose content -- check
that field after a first import and move things by hand in the YAML
if needed.
"""

import re
from pathlib import Path

SECTION_ALIASES = {
    "summary": "summary",
    "profile": "summary",
    "objective": "summary",
    "skills": "skills",
    "technical skills": "skills",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "education": "education",
    "projects": "projects",
    "certifications": "certifications",
    "certificates": "certifications",
}


def _empty_resume():
    return {
        "name": "",
        "contact": {"email": "", "phone": "", "website": "", "linkedin": "", "github": "", "substack": "", "location": ""},
        "summary": "",
        "skills": [],
        "experience": [],
        "education": [],
        "projects": [],
        "certifications": [],
        "unparsed": [],
    }


def parse_markdown(text: str) -> dict:
    resume = _empty_resume()
    lines = text.splitlines()

    # Name = first non-empty line if it's a level-1 heading or plain text
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines):
        first = lines[idx].strip()
        resume["name"] = re.sub(r"^#+\s*", "", first)
        idx += 1

    # Contact line: next non-empty line before any ## heading, if it looks like contact info
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines) and not lines[idx].strip().startswith("#"):
        contact_line = lines[idx].strip()
        if "@" in contact_line or "|" in contact_line:
            parts = [p.strip() for p in contact_line.split("|")]
            for p in parts:
                low = p.lower()
                if "@" in p:
                    resume["contact"]["email"] = p
                elif "linkedin" in low:
                    resume["contact"]["linkedin"] = p
                elif "github" in low:
                    resume["contact"]["github"] = p
                elif "substack" in low:
                    resume["contact"]["substack"] = p
                elif re.search(r"\d{3}", p):
                    resume["contact"]["phone"] = p
                elif re.match(r"^(https?://)?(www\.)?[\w.-]+\.[a-z]{2,}(/\S*)?$", low):
                    resume["contact"]["website"] = p
                else:
                    resume["contact"]["location"] = p
            idx += 1

    # Walk the rest, splitting on ## headings
    current_section = None
    buffer: list[str] = []

    def flush():
        nonlocal buffer
        if current_section is None or not buffer:
            buffer = []
            return
        _handle_section(resume, current_section, buffer)
        buffer = []

    for line in lines[idx:]:
        heading_match = re.match(r"^##\s+(.*)", line)
        if heading_match:
            flush()
            current_section = SECTION_ALIASES.get(
                heading_match.group(1).strip().lower(), None
            )
            if current_section is None:
                resume["unparsed"].append(f"## {heading_match.group(1)}")
            continue
        buffer.append(line)
    flush()

    return resume


def _handle_section(resume: dict, section: str, buffer: list[str]) -> None:
    text = "\n".join(buffer).strip()
    if not text:
        return

    if section == "summary":
        resume["summary"] = text
        return

    if section == "skills":
        for line in buffer:
            line = line.strip().lstrip("-*").strip()
            if not line:
                continue
            if ":" in line:
                category, items = line.split(":", 1)
                resume["skills"].append(
                    {
                        "category": category.strip(),
                        "items": [i.strip() for i in items.split(",") if i.strip()],
                    }
                )
            else:
                resume["skills"].append({"category": "General", "items": [line]})
        return

    if section == "certifications":
        for line in buffer:
            line = line.strip().lstrip("-*").strip()
            if line:
                resume["certifications"].append(line)
        return

    if section in ("experience", "projects", "education"):
        entries = []
        current = None
        for line in buffer:
            heading_match = re.match(r"^###\s+(.*)", line)
            bullet_match = re.match(r"^\s*[-*]\s+(.*)", line)
            if heading_match:
                if current:
                    entries.append(current)
                current = _parse_entry_heading(section, heading_match.group(1))
            elif bullet_match and current is not None:
                current.setdefault("bullets", []).append({"text": bullet_match.group(1).strip()})
            elif line.strip() and current is not None:
                # plain continuation line, treat as an extra bullet
                current.setdefault("bullets", []).append({"text": line.strip()})
        if current:
            entries.append(current)
        resume[section] = entries
        return


def _parse_entry_heading(section: str, heading: str) -> dict:
    parts = [p.strip() for p in heading.split("|")]
    head = parts[0]
    rest = parts[1:]

    if section == "experience":
        entry = {"title": head, "company": "", "location": "", "start": "", "end": "", "bullets": []}
        if "," in head:
            title, company = head.split(",", 1)
            entry["title"], entry["company"] = title.strip(), company.strip()
        if rest:
            entry["location"] = rest[0] if len(rest) > 0 else ""
        if len(rest) > 1:
            span = rest[1]
            if "-" in span:
                start, end = span.split("-", 1)
                entry["start"], entry["end"] = start.strip(), end.strip()
        return entry

    if section == "education":
        entry = {"degree": head, "school": "", "year": "", "bullets": []}
        if "," in head:
            degree, school = head.split(",", 1)
            entry["degree"], entry["school"] = degree.strip(), school.strip()
        if rest:
            entry["year"] = rest[0]
        return entry

    # projects
    return {"name": head, "bullets": []}


def parse_docx(file_path: str) -> dict:
    try:
        import docx  # python-docx
    except ImportError as e:
        raise RuntimeError(
            "Reading .docx requires python-docx. Install it with: "
            "pip install python-docx"
        ) from e

    document = docx.Document(file_path)
    text = "\n".join(p.text for p in document.paragraphs)
    # docx doesn't reliably carry "##" markdown headings, so fall back to a
    # best-effort heuristic: treat any short ALL CAPS or Title Case line with
    # no punctuation as a section heading and re-run the markdown parser.
    lines = text.splitlines()
    rebuilt = []
    known_headings = set(SECTION_ALIASES.keys())
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0:
            rebuilt.append(f"# {stripped}")
            continue
        if stripped.lower() in known_headings:
            rebuilt.append(f"## {stripped}")
        else:
            rebuilt.append(line)
    resume = parse_markdown("\n".join(rebuilt))
    if not resume["experience"] and not resume["skills"]:
        # heuristic failed to find structure -- keep the raw text so nothing is lost
        resume["unparsed"].append("RAW DOCX TEXT (heuristic section-detection failed):\n" + text)
    return resume


def parse_pdf(file_path: str) -> dict:
    try:
        import pdfplumber
    except ImportError as e:
        raise RuntimeError(
            "Reading .pdf requires pdfplumber. Install it with: pip install pdfplumber"
        ) from e

    with pdfplumber.open(file_path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    resume = _empty_resume()
    resume["unparsed"].append(
        "RAW PDF TEXT (PDF layout makes automatic section-detection unreliable -- "
        "please move this into the proper fields by hand):\n" + text
    )
    # Best-effort: first line as name
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    resume["name"] = first_line
    return resume


def parse_resume_file(file_path: str) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No such file: {file_path}")
    suffix = path.suffix.lower()
    text = None
    if suffix in (".md", ".txt", ".markdown"):
        text = path.read_text(encoding="utf-8")
        return parse_markdown(text)
    if suffix == ".docx":
        return parse_docx(str(path))
    if suffix == ".pdf":
        return parse_pdf(str(path))
    raise ValueError(f"Unsupported resume format: {suffix} (use .md, .txt, .docx, or .pdf)")
