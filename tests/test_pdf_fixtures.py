"""Tier A: lib/validators/pdf.check_pdf against hand-built PDFs
(tests/pdf_fixtures.py). No tectonic needed -- these run always. Covers
branches Tier B (real tectonic-compiled PDFs, tests/test_pdf_validation.py)
either cannot reach at all (a non-embedded base-14 font -- tectonic always
embeds) or has never exercised (min_pages with one page, "no text found to
measure margins", "no text characters found", the columns warning branch).

See docs/testing.md for why no binary fixture PDF is ever committed to the
repo -- everything here is generated at test time from source you can read
and diff.
"""

from __future__ import annotations

import copy

from lib.validators.pdf import check_pdf
from tests import pdf_fixtures as fx

CONTRACT = {
    "page": {"size": "letter", "width_pt": 612, "height_pt": 792,
             "margins_in": {"top": 0.6, "bottom": 0.5, "left": 0.6, "right": 0.6}},
    "typography": {"font_family": "Latin Modern Roman",
                   "pdf_font_prefixes": ["LMRoman", "LMRomanCaps", "CMSY", "CMMI", "CMR"],
                   "body": {"size_pt": 10}, "size_tolerance_pt": 0.1},
    "layout": {"columns": 1, "tables_allowed": False, "text_boxes_allowed": False, "graphics_allowed": False},
    "sections": {"order": ["summary", "experience", "projects", "skills", "education", "certifications"],
                 "headings": {"summary": "Summary", "experience": "Experience", "projects": "Projects",
                              "skills": "Technical Skills", "education": "Education",
                              "certifications": "Certifications"}},
    "limits": {"min_pages": 1, "max_pages": 2},
}

DOC = {"name": "Alex Example", "summary": "A summary paragraph.",
       "experience": [{"id": "exp-001", "company": "Acme"}]}


def _by_id(checks):
    return {c.id: c for c in checks}


def _run(pdf_bytes: bytes, tmp_path, contract=None, doc=None, max_pages: int = 2, compile_info=None):
    path = tmp_path / "test.pdf"
    path.write_bytes(pdf_bytes)
    checks, measured, not_available = check_pdf(path, contract or CONTRACT, doc if doc is not None else DOC,
                                                max_pages, compile_info)
    return _by_id(checks), measured, not_available


# --- integrity ---------------------------------------------------------------

def test_encrypted_pdf_fails_integrity(tmp_path):
    checks, _, _ = _run(fx.encrypted_pdf(fx.minimal_pdf()), tmp_path)
    c = checks["pdf.integrity"]
    assert (c.status, c.severity) == ("fail", "critical")
    assert "encrypted" in c.message.lower()


def test_truncated_pdf_fails_integrity(tmp_path):
    checks, _, _ = _run(fx.truncated_pdf(fx.minimal_pdf()), tmp_path)
    assert checks["pdf.integrity"].status == "fail"


# --- page size / page count ---------------------------------------------------

def test_a4_page_fails_against_letter_contract(tmp_path):
    checks, measured, _ = _run(fx.minimal_pdf(size="a4"), tmp_path)
    assert checks["pdf.page_size"].status == "fail"
    assert measured["page_sizes_pt"][0] != [612, 792]


def test_mixed_page_sizes_fail(tmp_path):
    letter = fx._PAGE_SIZES_PT["letter"]
    a4 = fx._PAGE_SIZES_PT["a4"]
    pdf = fx.minimal_pdf(pages=2, page_sizes=[letter, a4])
    checks, measured, _ = _run(pdf, tmp_path)
    c = checks["pdf.page_size"]
    assert c.status == "fail"
    assert measured["page_sizes_pt"][0] == [612, 792]
    assert measured["page_sizes_pt"][1] != [612, 792]


def test_pages_over_max_fails(tmp_path):
    checks, measured, _ = _run(fx.minimal_pdf(pages=3), tmp_path, max_pages=2)
    c = checks["pdf.page_count"]
    assert (c.status, c.severity) == ("fail", "critical")
    assert measured["page_count"] == 3


def test_min_pages_with_one_page_fails(tmp_path):
    """Untested branch: fewer pages than the contract's min_pages."""
    contract = copy.deepcopy(CONTRACT)
    contract["limits"]["min_pages"] = 2
    checks, _, _ = _run(fx.minimal_pdf(pages=1), tmp_path, contract=contract, max_pages=2)
    c = checks["pdf.page_count"]
    assert (c.status, c.severity) == ("fail", "error")
    assert "at least 2" in c.message


# --- fonts: the branch tectonic cannot produce --------------------------------

def test_non_embedded_base14_font_fails_contract(tmp_path):
    """tectonic always embeds fonts; a non-embedded base-14 font like this
    one is only reachable by hand-building the PDF."""
    checks, measured, _ = _run(fx.minimal_pdf(base_font="Helvetica"), tmp_path)
    c = checks["pdf.fonts"]
    assert c.status == "fail" and "Helvetica" in str(c.measurement)
    assert measured["fonts"] == ["Helvetica"]


def test_non_embedded_base14_font_matches_when_contract_expects_it(tmp_path):
    contract = copy.deepcopy(CONTRACT)
    contract["typography"]["pdf_font_prefixes"] = ["Helvetica"]
    checks, _, _ = _run(fx.minimal_pdf(base_font="Helvetica"), tmp_path, contract=contract)
    assert checks["pdf.fonts"].status == "pass"


# --- text extraction / empty pages --------------------------------------------

def test_one_word_text_fails_extractable(tmp_path):
    checks, measured, _ = _run(fx.minimal_pdf(text="Hi"), tmp_path)
    assert checks["pdf.text_extractable"].status == "fail"
    assert measured["word_count"] == 1


def test_blank_second_page_fails_empty_pages(tmp_path):
    checks, _, _ = _run(fx.minimal_pdf(pages=2, blank_pages={2}), tmp_path, max_pages=2)
    c = checks["pdf.empty_pages"]
    assert c.status == "fail" and c.measurement == [2]


# --- margins -------------------------------------------------------------------

def test_crushed_margins_fail(tmp_path):
    checks, measured, _ = _run(fx.minimal_pdf(x=5.0, y=5.0), tmp_path)
    c = checks["pdf.margins"]
    assert (c.status, c.severity) == ("fail", "critical")
    assert measured["margins_in"]["left"] < 0.5


def test_no_text_found_to_measure_margins(tmp_path):
    """Untested branch: every page is blank, so per_page ends up empty
    entirely (distinct from crushed-but-present margins)."""
    checks, _, _ = _run(fx.minimal_pdf(pages=1, blank_pages={1}), tmp_path)
    c = checks["pdf.margins"]
    assert c.status == "fail" and "No text found" in c.message


# --- body font size --------------------------------------------------------------

def test_9pt_body_fails_contract(tmp_path):
    checks, measured, _ = _run(fx.minimal_pdf(font_size=9.0), tmp_path)
    c = checks["pdf.body_font_size"]
    assert c.status == "fail"
    assert measured["body_font_pt_modal"] == 9.0


def test_no_text_characters_found_branch(tmp_path):
    """Untested branch: chars exist but none are alphanumeric (only
    punctuation/symbols), so `alnum` is empty."""
    checks, _, _ = _run(fx.minimal_pdf(text="!!! --- ..."), tmp_path)
    c = checks["pdf.body_font_size"]
    assert c.status == "fail" and "No text characters found" in c.message


# --- required sections ------------------------------------------------------------

def test_missing_heading_while_section_nonempty(tmp_path):
    checks, _, _ = _run(fx.minimal_pdf(text="A resume with no matching section words at all."), tmp_path)
    c = checks["pdf.required_sections"]
    assert c.status == "fail"
    assert "experience" in c.measurement["missing"] or "summary" in c.measurement["missing"]


# --- the scanned-resume case: image only ------------------------------------------

def test_image_only_pdf_trips_three_checks_simultaneously(tmp_path):
    checks, measured, _ = _run(fx.image_only_pdf(), tmp_path)
    assert checks["pdf.text_extractable"].status == "fail"
    assert checks["pdf.empty_pages"].status == "fail"
    assert checks["pdf.margins"].status == "fail"
    assert measured["word_count"] == 0


# --- columns (heuristic) -----------------------------------------------------------

def test_columns_multicolumn_warning_branch(tmp_path):
    checks, _, _ = _run(fx.two_column_pdf(), tmp_path)
    c = checks["pdf.columns"]
    assert (c.status, c.severity) == ("warning", "warning")
    assert "parallel columns" in c.message
