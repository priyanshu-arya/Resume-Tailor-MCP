"""URL normalization shared by every resume exporter (LaTeX/PDF, Markdown,
docx) so contact/project links are hyperlinked consistently across formats.
"""

from __future__ import annotations


def normalize_url(url: str) -> str:
    """Ensure a URL has an explicit scheme, for use as a hyperlink target."""
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


def linkedin_label(url: str) -> str:
    """Short, recognizable display label for a LinkedIn URL, e.g.
    'linkedin.com/in/priyanshu-arya' -> 'in/priyanshu-arya'."""
    trimmed = url.split("://", 1)[-1]
    for prefix in ("www.linkedin.com/", "linkedin.com/"):
        if trimmed.lower().startswith(prefix):
            return trimmed[len(prefix):]
    return trimmed
