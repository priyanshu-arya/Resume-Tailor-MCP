"""Deterministic JD keyword extraction and term matching (spec §33, §34).

No LLM call -- plain heuristics, so results are reproducible and fast.

Matching model:

* Every concept has ONE canonical name in `KNOWN_SKILLS`. `ALIASES` maps a
  small, curated set of alternative spellings onto a canonical name. There
  is no semantic inference: a term only matches via its canonical spelling
  or a listed alias.
* A term matches on a token boundary, `(?<![A-Za-z0-9])term(?![A-Za-z0-9])`,
  so `git` never matches "digital" and `sql` never matches "nosql".
  Multi-word terms tolerate a space or hyphen between words.
* Ordinary terms are case-insensitive. Terms that collide with common English
  words or initials (`AMBIGUOUS_TERMS`) only match in an unambiguous,
  case-sensitive form (e.g. "Go", "R", "REST", "SAP", "Excel").
"""

from __future__ import annotations

import re
from functools import lru_cache

# One canonical name per concept. Extend freely -- it's the main lever for
# improving match quality. Alternative spellings go in ALIASES, not here.
KNOWN_SKILLS = [
    "python", "java", "javascript", "typescript", "c++", "c#", "go",
    "rust", "ruby", "php", "scala", "kotlin", "swift", "r", "sql", "nosql",
    "react", "angular", "vue", "next.js", "node.js", "django", "flask",
    "fastapi", "spring", "spring boot", ".net", "graphql", "rest", "grpc",
    "aws", "azure", "gcp", "kubernetes", "docker", "terraform",
    "ansible", "jenkins", "ci/cd", "github actions", "gitlab ci",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
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

# alias (lowercase) -> canonical KNOWN_SKILLS term. Curated and conservative:
# only spellings that unambiguously mean the canonical concept. Deliberately
# NOT aliased: "py" (ambiguous), "tf" (terraform vs tensorflow), "cv"
# (curriculum vitae), "postgre", "mongo".
ALIASES: dict[str, str] = {
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "cpp": "c++",
    "c sharp": "c#",
    "k8s": "kubernetes",
    "postgres": "postgresql",
    "node": "node.js",
    "nodejs": "node.js",
    "react.js": "react",
    "reactjs": "react",
    "vue.js": "vue",
    "vuejs": "vue",
    "nextjs": "next.js",
    "dotnet": ".net",
    "asp.net": ".net",
    "sklearn": "scikit-learn",
    "ml": "machine learning",
    "natural language processing": "nlp",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "amazon web services": "aws",
    "microsoft azure": "azure",
    "ci cd": "ci/cd",
    "cicd": "ci/cd",
    "restful": "rest",
    "rest api": "rest",
    "rest apis": "rest",
    "powerbi": "power bi",
    "tailwindcss": "tailwind",
    "apache spark": "spark",
    "microsoft excel": "excel",
    "ms excel": "excel",
}

# Terms (canonical or alias) that collide with ordinary words/initials. They
# match ONLY in one of the listed case-sensitive `forms`, on a token
# boundary, and not when the next text matches `reject_after` or the previous
# character matches `reject_before`. Any aliases NOT listed here (e.g.
# "golang", "restful", "microsoft excel") still match case-insensitively.
AMBIGUOUS_TERMS: dict[str, dict] = {
    # "R programming", "in R,", "R/RStudio" -- not "R. Smith", "R&D", "R-squared", "J.R."
    "r": {"forms": ("R",), "reject_after": r"[.&'\-+#]", "reject_before": r"[&'.\-]"},
    # "Go" -- not "go-to", "Go to market", "Google", "ago", lowercase "go"
    "go": {"forms": ("Go",), "reject_after": r"-|\s+to\b", "reject_before": ""},
    # "REST" -- not "the rest of the team"
    "rest": {"forms": ("REST",), "reject_after": "", "reject_before": ""},
    # "SAP" -- not "sap" (plant sap, "sap your energy")
    "sap": {"forms": ("SAP",), "reject_after": "", "reject_before": ""},
    # "Excel" -- not the verb "excel at"; "excellent" is already excluded by the boundary
    "excel": {"forms": ("Excel", "EXCEL"), "reject_after": "", "reject_before": ""},
    # "Swift" -- not "swift delivery"; not "SWIFT" (the banking network)
    "swift": {"forms": ("Swift",), "reject_after": "", "reject_before": ""},
    # "Sketch" -- not "sketch out a design"
    "sketch": {"forms": ("Sketch",), "reject_after": "", "reject_before": ""},
    # "Spark" -- not "spark curiosity"
    "spark": {"forms": ("Spark",), "reject_after": "", "reject_before": ""},
    # "Spring" framework -- not "Spring 2023" / "Spring '23" semester dates
    "spring": {"forms": ("Spring",), "reject_after": r"\s*'?\d", "reject_before": ""},
    # "TS" -- not the "TS/SCI" security clearance
    "ts": {"forms": ("TS",), "reject_after": r"/\s*SCI", "reject_before": ""},
}

SENIORITY_TERMS = [
    "intern", "junior", "associate", "mid-level", "senior", "staff",
    "principal", "lead", "manager", "director", "vp", "head of", "chief",
]

# Requirement-type classification for KNOWN_SKILLS -- transcription of the
# grouping already visible in that list's own comments/layout, not a new
# classifier. Used only to make an evidence prompt's `requirement_type`
# machine-readable; never a matching or provenance decision.
REQUIREMENT_TYPES = ("language", "framework", "platform", "datastore", "data_tool",
                     "practice", "domain", "tool", "certification", "technology")
DEFAULT_TERM_TYPE = "technology"

TERM_TYPES: dict[str, str] = {
    # languages
    "python": "language", "java": "language", "javascript": "language", "typescript": "language",
    "c++": "language", "c#": "language", "go": "language", "rust": "language", "ruby": "language",
    "php": "language", "scala": "language", "kotlin": "language", "swift": "language", "r": "language",
    "html": "language", "css": "language", "sass": "language", "bash": "language",
    "shell scripting": "language",
    # query / data languages
    "sql": "datastore", "nosql": "datastore",
    # frameworks
    "react": "framework", "angular": "framework", "vue": "framework", "next.js": "framework",
    "node.js": "framework", "django": "framework", "flask": "framework", "fastapi": "framework",
    "spring": "framework", "spring boot": "framework", ".net": "framework", "graphql": "framework",
    "rest": "framework", "grpc": "framework", "tailwind": "framework",
    # platforms (cloud / infra / CI)
    "aws": "platform", "azure": "platform", "gcp": "platform", "kubernetes": "platform",
    "docker": "platform", "terraform": "platform", "ansible": "platform", "jenkins": "platform",
    "ci/cd": "platform", "github actions": "platform", "gitlab ci": "platform", "linux": "platform",
    # datastores
    "postgresql": "datastore", "mysql": "datastore", "mongodb": "datastore", "redis": "datastore",
    "elasticsearch": "datastore", "kafka": "datastore", "rabbitmq": "datastore",
    # data / ML tooling
    "spark": "data_tool", "hadoop": "data_tool", "airflow": "data_tool", "snowflake": "data_tool",
    "databricks": "data_tool", "tableau": "data_tool", "power bi": "data_tool", "looker": "data_tool",
    "excel": "data_tool", "machine learning": "data_tool", "deep learning": "data_tool",
    "nlp": "data_tool", "computer vision": "data_tool", "pytorch": "data_tool",
    "tensorflow": "data_tool", "scikit-learn": "data_tool", "pandas": "data_tool", "numpy": "data_tool",
    # practices
    "agile": "practice", "scrum": "practice", "kanban": "practice", "microservices": "practice",
    "api design": "practice", "system design": "practice", "distributed systems": "practice",
    "unit testing": "practice", "test automation": "practice",
    # domains
    "product management": "domain", "project management": "domain", "leadership": "domain",
    "stakeholder management": "domain", "cross-functional": "domain",
    # tools
    "jira": "tool", "confluence": "tool", "figma": "tool", "sketch": "tool", "adobe xd": "tool",
    "selenium": "tool", "cypress": "tool", "pytest": "tool", "webpack": "tool", "vite": "tool",
    "git": "tool", "github": "tool", "gitlab": "tool", "salesforce": "tool", "hubspot": "tool",
    "sap": "tool", "erp": "tool", "crm": "tool",
}


def term_type(term: str) -> str:
    """The requirement-type category for `term` -- TERM_TYPES.get(canonical
    name, DEFAULT_TERM_TYPE). Case/alias-insensitive via normalize_term."""
    return TERM_TYPES.get(normalize_term(term), DEFAULT_TERM_TYPE)


_CERT_RE = re.compile(r"certif(?:ied|ication|icate)s?", re.IGNORECASE)


def certification_requirements(jd_text: str) -> list[str]:
    """Canonical KNOWN_SKILLS terms appearing on a line that also demands a
    certification (e.g. "AWS Certified Solutions Architect required"),
    line-scoped so a certification mention elsewhere in the JD can't leak
    onto an unrelated term. Empty when no such line exists. Deterministic:
    KNOWN_SKILLS order (not text order -- detect_terms returns a set)."""
    if not jd_text:
        return []
    hit_lines = [line for line in jd_text.splitlines() if _CERT_RE.search(line)]
    if not hit_lines:
        return []
    found: set[str] = set()
    for line in hit_lines:
        found |= detect_terms(line)
    return [s for s in KNOWN_SKILLS if s in found]

REQUIRED_SECTION_HEADERS = re.compile(
    r"(required|requirements|must have|minimum qualifications|what you.?ll need)",
    re.IGNORECASE,
)
PREFERRED_SECTION_HEADERS = re.compile(
    r"(preferred|nice to have|bonus|good to have)",
    re.IGNORECASE,
)

# Headers that END a requirements block: benefits, company blurb, pay, EEO
# boilerplate. Their contents are not requirements and are ignored, so a
# "Benefits: free Kubernetes training" line can't become a must-have.
NON_REQUIREMENT_SECTION_HEADERS = re.compile(
    r"(benefits|perks|what we offer|about us|about the (?:company|team)|who we are|compensation|salary|"
    r"pay range|equal (?:employment )?opportunity|eeo|how to apply|our values|why join)",
    re.IGNORECASE,
)

_BOUND_L = r"(?<![A-Za-z0-9])"
_BOUND_R = r"(?![A-Za-z0-9])"
_SEP = re.compile(r"[\s\-_]+")


# ---------------------------------------------------------------------------
# Normalization and term matching
# ---------------------------------------------------------------------------

def _key(term: str) -> str:
    """Lookup key: lowercase, trimmed, space/hyphen/underscore runs collapsed."""
    return _SEP.sub(" ", str(term).strip().lower()).strip()


_CANON_BY_KEY: dict[str, str] = {}
for _skill in KNOWN_SKILLS:
    _CANON_BY_KEY[_key(_skill)] = _skill
for _alias, _canon in ALIASES.items():
    assert _canon in KNOWN_SKILLS, f"alias {_alias!r} -> unknown canonical {_canon!r}"
    _CANON_BY_KEY.setdefault(_key(_alias), _canon)

_SURFACES_BY_CANON: dict[str, list[str]] = {s: [s] for s in KNOWN_SKILLS}
for _alias, _canon in ALIASES.items():
    _SURFACES_BY_CANON[_canon].append(_alias)


def normalize_term(term: str) -> str:
    """Lowercase/strip a term and map a known alias (or a spacing/hyphen
    variant of a known term) to its canonical name. Unknown terms come back
    lowercased, stripped and with internal whitespace collapsed."""
    key = _key(term)
    if key in _CANON_BY_KEY:
        return _CANON_BY_KEY[key]
    return re.sub(r"\s+", " ", str(term).strip().lower())


def _surface_regex(surface: str) -> re.Pattern:
    """Compiled boundary regex for one surface spelling (canonical or alias)."""
    rule = AMBIGUOUS_TERMS.get(surface)
    if rule:
        alts = "|".join(re.escape(f) for f in rule["forms"])
        before = f"(?<!{rule['reject_before']})" if rule.get("reject_before") else ""
        after = f"(?!{rule['reject_after']})" if rule.get("reject_after") else ""
        return re.compile(f"{_BOUND_L}{before}(?:{alts}){_BOUND_R}{after}")
    words = [re.escape(w) for w in _SEP.split(surface.strip()) if w]
    body = r"[\s\-]+".join(words)
    return re.compile(f"{_BOUND_L}{body}{_BOUND_R}", re.IGNORECASE)


@lru_cache(maxsize=4096)
def _patterns_for(term: str) -> tuple[re.Pattern, ...]:
    canon = normalize_term(term)
    if canon in _SURFACES_BY_CANON:
        return tuple(_surface_regex(s) for s in _SURFACES_BY_CANON[canon])
    if not canon:
        return ()
    return (_surface_regex(canon),)


def term_in_text(term: str, text: str) -> bool:
    """True if `term` (canonical, alias, or any other string) appears in
    `text` on a token boundary -- via its canonical spelling or any curated
    alias, honouring the case rules in AMBIGUOUS_TERMS."""
    if not text or not term:
        return False
    return any(p.search(text) for p in _patterns_for(term))


def detect_terms(text: str) -> set[str]:
    """Canonical KNOWN_SKILLS terms present in `text`."""
    if not text:
        return set()
    return {s for s in KNOWN_SKILLS if term_in_text(s, text)}


def _known_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for skill in KNOWN_SKILLS:
        for p in _patterns_for(skill):
            spans.extend(m.span() for m in p.finditer(text))
    return spans


# Kept for backward compatibility with any internal caller.
_find_skills = detect_terms


# ---------------------------------------------------------------------------
# Unknown (non-KNOWN_SKILLS) requirement detection
# ---------------------------------------------------------------------------

# Compared against token.lower() with dots removed.
_UNKNOWN_STOPLIST = {
    # locations / currencies / legal entities
    "us", "usa", "uk", "eu", "uae", "nyc", "ny", "sf", "la", "ca", "wa", "tx",
    "usd", "inr", "eur", "gbp", "llc", "inc", "ltd", "corp", "co",
    # HR / hiring boilerplate
    "eeo", "eoe", "ada", "hr", "pto", "wfh", "fte", "ot", "dei", "faq", "tbd",
    "na", "asap", "fyi", "eta", "kpi", "kpis", "okr", "okrs", "roi", "b2b", "b2c",
    # degrees / academic
    "gpa", "phd", "bs", "ms", "ba", "ma", "mba", "bsc", "msc", "btech", "mtech",
    "be", "me", "cs", "ece", "eee", "stem",
    # titles / seniority
    "ceo", "cto", "cfo", "coo", "cio", "vp", "svp", "evp", "avp", "sr", "jr",
    "ic", "swe", "sde",
    # time
    "am", "pm", "est", "pst", "cst", "et", "pt", "utc", "ist",
    # abbreviations
    "eg", "ie", "etc", "vs", "ok", "it",
    # companies/sites commonly named in JDs, not requirements
    "linkedin", "youtube", "iphone",
    # capitalized English words (headers like "WHAT YOU WILL NEED")
    "a", "an", "the", "and", "or", "of", "for", "with", "in", "on", "to", "at",
    "by", "as", "is", "are", "be", "we", "you", "our", "your", "will", "must",
    "have", "need", "nice", "role", "job", "team", "about", "bonus", "plus",
    "note", "new", "all", "any", "not", "no", "yes", "key", "top", "what",
    "who", "why", "how", "skills", "years",
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+#]*")
_CAMEL_RE = re.compile(r"[a-z][A-Z]|^[A-Z]{2,}[a-z]")
_INNER_PUNCT_RE = re.compile(r"[A-Za-z0-9][.+#]")
_ALLCAPS_RE = re.compile(r"[A-Z][A-Z0-9]{1,5}")
_UNKNOWN_CAP = 25


def _is_technical_looking(tok: str) -> bool:
    if not re.search(r"[A-Za-z]", tok):
        return False
    if _CAMEL_RE.search(tok):
        return True
    if _INNER_PUNCT_RE.search(tok):
        return True
    if _ALLCAPS_RE.fullmatch(tok) and sum(c.isalpha() for c in tok) >= 2:
        return True
    return False


def find_unknown_requirements(text: str, limit: int = _UNKNOWN_CAP) -> list[str]:
    """Technical-looking tokens in `text` that are not KNOWN_SKILLS or
    ALIASES: CamelCase words, words with an inner `.`/`+`/`#`, and 2-6 char
    ALLCAPS acronyms -- minus a stoplist of common non-tech acronyms and
    seniority terms. Deduplicated (case-insensitive), first-seen order,
    capped at `limit`. Deterministic."""
    if not text:
        return []
    spans = _known_spans(text)
    seniority = {_key(t) for t in SENIORITY_TERMS}
    out: list[str] = []
    seen: set[str] = set()
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0).rstrip(".")
        start, end = m.start(), m.start() + len(tok)
        if not tok or not _is_technical_looking(tok):
            continue
        low = tok.lower()
        if low.replace(".", "") in _UNKNOWN_STOPLIST or low in seniority:
            continue
        if _key(tok) in _CANON_BY_KEY:
            continue
        # skip tokens that are wholly part of a known-term match ("CI" in
        # "CI/CD", "Google" in "Google Cloud") -- but not "Nuxt.js", which
        # merely contains the "js" alias
        if any(s <= start and end <= e for s, e in spans):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# JD structure
# ---------------------------------------------------------------------------

def _split_by_section(text: str) -> tuple[str, str, str]:
    """Return (required_text, preferred_text, rest_text) by best-effort
    splitting on common JD section headers. Handles both a header on its
    own line ("Requirements:" followed by a bulleted list) and a header
    with content inline on the same line ("Required: Python, Django, ...")."""
    lines = text.splitlines()
    required, preferred, rest, ignored = [], [], [], []
    bucket = rest
    for line in lines:
        other_match = NON_REQUIREMENT_SECTION_HEADERS.search(line)
        if other_match and other_match.start() <= 3 and len(line.strip()) < 100:
            bucket = ignored
            continue
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
    jd_text = jd_text or ""
    required_text, preferred_text, rest_text = _split_by_section(jd_text)

    required_skills = detect_terms(required_text)
    preferred_skills = detect_terms(preferred_text)
    rest_skills = detect_terms(rest_text)

    # Anything found only in the general body (no explicit required/preferred
    # split detected) is treated as must_have by default -- most JDs don't
    # actually separate the two sections.
    if not required_skills and not preferred_skills:
        required_skills = rest_skills
    else:
        required_skills |= (rest_skills - preferred_skills)

    if required_text.strip() or preferred_text.strip():
        requirement_text = required_text + "\n" + preferred_text
    else:
        requirement_text = jd_text

    return {
        "title": extract_title(jd_text),
        "seniority": extract_seniority(jd_text),
        "years_experience": extract_years_experience(jd_text),
        "must_have": sorted(required_skills),
        "nice_to_have": sorted(preferred_skills - required_skills),
        "unknown_requirements": find_unknown_requirements(requirement_text),
    }
