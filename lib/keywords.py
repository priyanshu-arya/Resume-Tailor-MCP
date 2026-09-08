"""Deterministic JD keyword extraction. No LLM call -- plain heuristics,
so results are reproducible and fast.
"""

import re

# A reasonably broad seed list of common tech / professional skill terms.
# Extend this freely -- it's the main lever for improving match quality.
KNOWN_SKILLS = [
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang",
    "rust", "ruby", "php", "scala", "kotlin", "swift", "r", "sql", "nosql",
    "react", "angular", "vue", "next.js", "node.js", "django", "flask",
    "fastapi", "spring", "spring boot", ".net", "graphql", "rest", "grpc",
    "aws", "azure", "gcp", "google cloud", "kubernetes", "docker", "terraform",
    "ansible", "jenkins", "ci/cd", "github actions", "gitlab ci",
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
    "kafka", "rabbitmq", "spark", "hadoop", "airflow", "snowflake",
    "databricks", "tableau", "power bi", "looker", "excel",
    "machine learning", "deep learning", "nlp", "computer vision",
  "pytorch", "tensorflow", "scikit-learn", "pandas", "numpy",
    "agile", "scrum", "kanban", "jira", "confluence",
    "figma", "sketch", "adobe xd", "product management", "project management",
    "leadership", "stakeholder management", "cross-functional",
    "microservices", "api design", "system design", "distributed systems",
    "unit testing", "test automation", "selenium", "cypress", "pytest",
    "html", "css", "sass", "tailwind", "webpack", "vite",
    "linux", "bash", "shell scripting", "git", "github", "gitlab",
    "salesforce", "hubspot", "sap", "erp", "crm",
]

SENIORITY_TERMS = [
    "intern", "junior", "associate", "mid-level", "senior", "staff",
    "principal", "lead", "manager", "director", "vp", "head of", "chief",
]

REQUIRED_SECTION_HEADERS = re.compile(
    r"(required|requirements|must have|minimum qualifications|what you.?ll need)",
    re.IGNORECASE,
)
PREFERRED_SECTION_HEADERS = re.compile(
    r"(preferred|nice to have|bonus|good to have)",
    re.IGNORECASE,
)


def _find_skills(text: str) -> set[str]:
    lowered = text.lower()
    found = set()
    for skill in KNOWN_SKILLS:
        # word-boundary-ish match, tolerant of "c++"/".net" punctuation
        pattern = re.escape(skill)
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", lowered):
            found.add(skill)
    return found


def _split_by_section(text: str) -> tuple[str, str, str]:
    """Return (required_text, preferred_text, rest_text) by best-effort
    splitting on common JD section headers. Handles both a header on its
    own line ("Requirements:" followed by a bulleted list) and a header
    with content inline on the same line ("Required: Python, Django, ...")."""
    lines = text.splitlines()
    required, preferred, rest = [], [], []
    bucket = rest
    for line in lines:
        req_match = REQUIRED_SECTION_HEADERS.search(line)
        pref_match = PREFERRED_SECTION_HEADERS.search(line)
        # Only treat this as a section header if the header phrase is at (or
        # near) the start of the line -- avoids false positives on JD prose
        # like "...and other requirements as needed." mid-paragraph.
        if req_match and req_match.start() <= 3 and len(line.strip()) < 100:
            bucket = required
            remainder = line[req_match.end():].lstrip(" :-\t")
            if remainder:
                bucket.append(remainder)
            continue
        if pref_match and pref_match.start() <= 3 and len(line.strip()) < 100:
            bucket = preferred
            remainder = line[pref_match.end():].lstrip(" :-\t")
            if remainder:
                bucket.append(remainder)
            continue
        bucket.append(line)
    return "\n".join(required), "\n".join(preferred), "\n".join(rest)


def extract_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 90:
            return stripped
    return ""


def extract_seniority(text: str) -> list[str]:
    lowered = text.lower()
    return [t for t in SENIORITY_TERMS if re.search(rf"\b{re.escape(t)}\b", lowered)]


def extract_years_experience(text: str) -> str | None:
    match = re.search(r"(\d+)\+?\s*(?:-\s*\d+\s*)?years?", text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_jd_keywords(jd_text: str) -> dict:
    required_text, preferred_text, rest_text = _split_by_section(jd_text)

    required_skills = _find_skills(required_text)
    preferred_skills = _find_skills(preferred_text)
    rest_skills = _find_skills(rest_text)

    # Anything found only in the general body (no explicit required/preferred
    # split detected) is treated as must_have by default -- most JDs don't
    # actually separate the two sections.
    if not required_skills and not preferred_skills:
        required_skills = rest_skills
    else:
        required_skills |= (rest_skills - preferred_skills)

    return {
        "title": extract_title(jd_text),
        "seniority": extract_seniority(jd_text),
        "years_experience": extract_years_experience(jd_text),
        "must_have": sorted(required_skills),
        "nice_to_have": sorted(preferred_skills - required_skills),
    }
