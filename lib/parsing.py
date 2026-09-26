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

Imported files are untrusted: parse_resume_file checks that the path is a
regular file with an allowed extension and a bounded size, requires UTF-8
for text formats, caps the extracted text, and never executes or evaluates
anything. Failures are ResumeTailorError codes whose messages name only the
file's basename, never its contents.
"""

import re
import stat
from pathlib import Path

from lib.errors import ResumeTailorError

# Imports are untrusted input (spec §9, §66): cap the file size before
# reading, and cap the extracted text before parsing it.
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 500_000
ALLOWED_SUFFIXES = (".md", ".markdown", ".txt", ".docx", ".pdf")

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


def _too_large_chars(name: str) -> ResumeTailorError:
    return ResumeTailorError(
        "IMPORT_TOO_LARGE",
        f"Text extracted from {name} exceeds {MAX_EXTRACTED_CHARS:,} characters; "
        "this is far larger than any resume. Trim the file and try again.",
        details={"file": name, "max_chars": MAX_EXTRACTED_CHARS},
    )


def _check_chars(text: str, name: str) -> str:
    if len(text) > MAX_EXTRACTED_CHARS:
        raise _too_large_chars(name)
    return text


def _missing_dependency(package: str, suffix: str) -> ResumeTailorError:
    return ResumeTailorError(
        "IMPORT_UNSUPPORTED",
        f"Reading {suffix} files requires the optional package {package!r}. "
        f"Install it with: pip install {package}  (or import a .md/.txt file instead).",
        details={"missing_dependency": package},
    )


def _unreadable(name: str, what: str) -> ResumeTailorError:
    # Deliberately generic: library exceptions can quote file bytes.
    return ResumeTailorError(
        "IMPORT_UNSUPPORTED",
        f"Could not read {name} as a {what} file (corrupt, encrypted or not really a {what}).",
        details={"file": name},
    )


def _docx_text(file_path: str) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        raise _missing_dependency("python-docx", ".docx") from None

    name = Path(file_path).name
    try:
        document = docx.Document(file_path)
    except Exception:
        raise _unreadable(name, ".docx") from None
    parts: list[str] = []
    total = 0
    for p in document.paragraphs:
        parts.append(p.text)
        total += len(p.text) + 1
        if total > MAX_EXTRACTED_CHARS + 1:
            raise _too_large_chars(name)
    return _check_chars("\n".join(parts), name)


def _docx_text_to_resume(text: str) -> dict:
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


def parse_docx(file_path: str) -> dict:
    return _docx_text_to_resume(_docx_text(file_path))


def _pdf_text(file_path: str) -> str:
    try:
        import pdfplumber
    except ImportError:
        raise _missing_dependency("pdfplumber", ".pdf") from None

    name = Path(file_path).name
    parts: list[str] = []
    total = 0
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                parts.append(page_text)
                total += len(page_text) + 1
                if total > MAX_EXTRACTED_CHARS + 1:  # stop early; don't extract the rest
                    raise _too_large_chars(name)
    except ResumeTailorError:
        raise
    except Exception:
        raise _unreadable(name, ".pdf") from None
    return _check_chars("\n".join(parts), name)


def _pdf_text_to_resume(text: str) -> dict:
    resume = _empty_resume()
    resume["unparsed"].append(
        "RAW PDF TEXT (PDF layout makes automatic section-detection unreliable -- "
        "please move this into the proper fields by hand):\n" + text
    )
    # Best-effort: first line as name
    first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
    resume["name"] = first_line
    return resume


def parse_pdf(file_path: str) -> dict:
    return _pdf_text_to_resume(_pdf_text(file_path))


def _plain_text(path: Path) -> str:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ResumeTailorError(
            "IMPORT_UNSUPPORTED",
            f"{path.name} is not valid UTF-8 text. Re-save it as UTF-8 and try again.",
            details={"file": path.name},
        ) from None
    return _check_chars(text, path.name)


def _checked_path(file_path: str) -> Path:
    """Resolve and vet an import path before any byte of it is read."""
    raw = Path(str(file_path)).expanduser()
    name = raw.name
    try:
        path = raw.resolve(strict=True)
        st = path.stat()
    except (OSError, RuntimeError):
        raise ResumeTailorError("IMPORT_UNSUPPORTED", f"File not found: {name}",
                                details={"file": name}) from None
    if not stat.S_ISREG(st.st_mode):
        raise ResumeTailorError("IMPORT_UNSUPPORTED", f"{name} is not a regular file.",
                                details={"file": name})
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ResumeTailorError(
            "IMPORT_UNSUPPORTED",
            f"Unsupported resume format {suffix or '(none)'!r}; use one of {', '.join(ALLOWED_SUFFIXES)}.",
            details={"file": name, "suffix": suffix},
        )
    if st.st_size > MAX_IMPORT_BYTES:
        raise ResumeTailorError(
            "IMPORT_TOO_LARGE",
            f"{name} is {st.st_size:,} bytes; the import limit is {MAX_IMPORT_BYTES:,} bytes.",
            details={"file": name, "size": st.st_size, "max_bytes": MAX_IMPORT_BYTES},
        )
    return path


def parse_resume_file(file_path: str) -> dict:
    """Parse an untrusted resume file. Raises ResumeTailorError
    (IMPORT_UNSUPPORTED / IMPORT_TOO_LARGE) instead of builtin exceptions;
    messages name the file's basename but never quote its contents."""
    path = _checked_path(file_path)
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt", ".markdown"):
        return parse_markdown(_plain_text(path))
    if suffix == ".docx":
        return parse_docx(str(path))
    return parse_pdf(str(path))
