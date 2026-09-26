"""Imports are untrusted input (spec §9, §66): bounded, vetted, never echoed."""

from __future__ import annotations

import pytest

from lib import parsing
from lib.errors import ResumeTailorError

SENTINEL = "SENTINEL-7f3a9c-DO-NOT-ECHO"

MD = """# Casey Sample
casey@example.test | +1 555 0199 | Denver, CO

## Summary
Data engineer building batch pipelines.

## Skills
- Languages: Python, SQL

## Experience
### Data Engineer, Gamma Inc | Denver, CO | Jan 2023 - Present
- Built nightly batch jobs in Python.
continuation line

## Hobbies
chess

## Certifications
- Example Cert (2024)
"""


def _err(path) -> ResumeTailorError:
    with pytest.raises(ResumeTailorError) as e:
        parsing.parse_resume_file(str(path))
    return e.value


def _leaks(err: ResumeTailorError) -> bool:
    return SENTINEL in err.message or SENTINEL in repr(err.details) or SENTINEL in str(err)


def test_oversized_file_rejected_before_reading(tmp_path):
    p = tmp_path / "big.md"
    with open(p, "wb") as f:  # sparse: cheap to create
        f.truncate(parsing.MAX_IMPORT_BYTES + 1)
    err = _err(p)
    assert err.code == "IMPORT_TOO_LARGE"


def test_bad_extension_rejected(tmp_path):
    p = tmp_path / "resume.exe"
    p.write_text(SENTINEL)
    err = _err(p)
    assert err.code == "IMPORT_UNSUPPORTED" and not _leaks(err)


def test_directory_rejected(tmp_path):
    d = tmp_path / "folder.md"
    d.mkdir()
    assert _err(d).code == "IMPORT_UNSUPPORTED"


def test_missing_file_rejected(tmp_path):
    err = _err(tmp_path / "nope.md")
    assert err.code == "IMPORT_UNSUPPORTED"
    assert str(tmp_path) not in err.message  # basename only


def test_non_utf8_markdown_rejected_without_echo(tmp_path):
    p = tmp_path / "latin.md"
    p.write_bytes(f"# {SENTINEL}\nCaf\xe9 \xff\xfe".encode("latin-1"))
    err = _err(p)
    assert err.code == "IMPORT_UNSUPPORTED" and not _leaks(err)


def test_extracted_char_cap(tmp_path):
    p = tmp_path / "long.txt"
    n = parsing.MAX_EXTRACTED_CHARS + 10
    assert n < parsing.MAX_IMPORT_BYTES
    p.write_text(SENTINEL + "a" * n, encoding="utf-8")
    err = _err(p)
    assert err.code == "IMPORT_TOO_LARGE" and not _leaks(err)


def test_valid_markdown_parses_exactly_as_before(tmp_path):
    p = tmp_path / "resume.markdown"
    p.write_text(MD, encoding="utf-8")
    parsed = parsing.parse_resume_file(str(p))
    assert parsed == parsing.parse_markdown(MD)
    assert parsed["unparsed"] == ["## Hobbies"]  # unplaceable content is kept, not dropped
    assert parsed["experience"][0]["bullets"][1] == {"text": "continuation line"}


def test_docx_char_cap_and_parse(tmp_path):
    docx = pytest.importorskip("docx")
    small = tmp_path / "ok.docx"
    d = docx.Document()
    for line in ("Casey Sample", "Skills", "- Languages: Python"):
        d.add_paragraph(line)
    d.save(small)
    assert parsing.parse_resume_file(str(small))["skills"][0]["items"] == ["Python"]

    big = tmp_path / "big.docx"
    d = docx.Document()
    chunk = SENTINEL + "b" * 50_000
    for _ in range(parsing.MAX_EXTRACTED_CHARS // len(chunk) + 2):
        d.add_paragraph(chunk)
    d.save(big)
    err = _err(big)
    assert err.code == "IMPORT_TOO_LARGE" and not _leaks(err)


def test_corrupt_docx_and_pdf_rejected_without_echo(tmp_path):
    for name in ("bad.docx", "bad.pdf"):
        p = tmp_path / name
        p.write_text(SENTINEL)
        err = _err(p)
        assert err.code == "IMPORT_UNSUPPORTED" and not _leaks(err)
