"""Critical PDF verification (spec §41/§42) against real compiled PDFs."""

from __future__ import annotations

import copy

import pytest

from lib import export
from lib.errors import ResumeTailorError
from lib.ids import normalize_master
from lib.latex import render_latex
from lib.validators.pdf import ALL_CHECK_IDS, BLOCKED_MESSAGE, check_pdf, detect_pdf_backend

try:
    export._tectonic_path()
    _HAVE_TECTONIC = True
except ResumeTailorError:
    _HAVE_TECTONIC = False

needs_tectonic = pytest.mark.skipif(not _HAVE_TECTONIC, reason="tectonic not available")

CONTRACT = {
    "id": "classic-minimalist", "status": "supported", "version": "1.1.0",
    "page": {"size": "letter", "width_pt": 612, "height_pt": 792,
             "margins_in": {"top": 0.6, "bottom": 0.5, "left": 0.6, "right": 0.6}},
    "typography": {"font_family": "Latin Modern Roman",
                   "pdf_font_prefixes": ["LMRoman", "LMRomanCaps", "CMSY", "CMMI", "CMR"],
                   "body": {"size_pt": 10}, "name": {"size_pt": 20.7, "weight": "bold"},
                   "heading": {"size_pt": 12, "weight": "bold"}, "size_tolerance_pt": 0.1},
    "layout": {"columns": 1, "tables_allowed": False, "text_boxes_allowed": False, "graphics_allowed": False},
    "sections": {"order": ["summary", "experience", "projects", "skills", "education", "certifications"],
                 "headings": {"summary": "Summary", "experience": "Experience", "projects": "Projects",
                              "skills": "Technical Skills", "education": "Education",
                              "certifications": "Certifications"}},
    "limits": {"min_pages": 1, "max_pages": 2},
}


def _by_id(checks):
    return {c.id: c for c in checks}


@pytest.fixture(scope="module")
def doc():
    from conftest import SYNTHETIC_LEGACY_MASTER
    return normalize_master(SYNTHETIC_LEGACY_MASTER, "resume")


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    out = tmp_path_factory.mktemp("pdfval")
    cache: dict[str, dict] = {}

    def _build(name: str, tex: str) -> dict:
        if name not in cache:
            cache[name] = export.compile_tex(tex, out, name)
        return cache[name]
    return _build


@pytest.fixture(scope="module")
def base_tex(doc):
    return render_latex(doc, "classic-minimalist")


@pytest.fixture(scope="module")
def base(build, base_tex):
    return build("base", base_tex)


def test_backend_available_in_venv():
    b = detect_pdf_backend()
    assert b["available"] is True
    assert {"pypdf", "pdfplumber"} <= set(b["backends"])


@needs_tectonic
def test_all_checks_pass_on_synthetic_master(base, doc):
    checks, measured, na = check_pdf(base["pdf_path"], CONTRACT, doc, max_pages=2, compile_info=base)
    by = _by_id(checks)
    assert na == []
    for cid in ALL_CHECK_IDS:
        assert cid in by, cid
        if cid == "pdf.overfull_boxes":
            continue  # driven by the compile log, reported separately
        assert by[cid].status == "pass", (cid, by[cid].message, by[cid].measurement)
        assert by[cid].source == "pdf_measured"
    # The overfull check reflects the compile log exactly.
    expected = "pass" if base["overfull_hbox_count"] == 0 else "warning"
    assert by["pdf.overfull_boxes"].status == expected
    assert not any(c.blocking for c in checks)

    assert measured["page_count"] == 1
    assert measured["page_sizes_pt"] == [[612.0, 792.0]]
    assert abs(measured["body_font_pt_modal"] - 10) <= 0.1
    assert all(f.split("+")[-1].startswith(("LMRoman", "CMSY", "CMMI", "CMR")) for f in measured["fonts"])
    for side in ("top", "bottom", "left", "right"):
        assert measured["margins_in"][side] >= 0.48
    assert abs(measured["margins_in"]["left"] - 0.6) < 0.05
    assert abs(measured["margins_in"]["right"] - 0.6) < 0.05
    assert measured["word_count"] >= 30


@needs_tectonic
def test_max_pages_exceeded(build, doc):
    long_doc = copy.deepcopy(doc)
    long_doc["experience"] = [
        {"id": f"exp-{i:03d}", "title": "Software Engineer", "company": f"Company {i}", "location": "Remote",
         "start": "Jan 2020", "end": "Dec 2020",
         "bullets": [{"text": f"Synthetic bullet {j} describing ordinary engineering work on service {i}, "
                              "written long enough to wrap onto a second line of the page."} for j in range(6)]}
        for i in range(12)
    ]
    info = build("long", render_latex(long_doc, "classic-minimalist"))
    checks, measured, _ = check_pdf(info["pdf_path"], CONTRACT, long_doc, max_pages=1, compile_info=info)
    assert measured["page_count"] > 1
    pc = _by_id(checks)["pdf.page_count"]
    assert pc.status == "fail" and pc.severity == "critical" and pc.blocking


@needs_tectonic
def test_a4_page_size_fails(build, base_tex, doc):
    assert "letterpaper" in base_tex
    info = build("a4", base_tex.replace("letterpaper", "a4paper"))
    checks, measured, _ = check_pdf(info["pdf_path"], CONTRACT, doc, max_pages=2)
    ps = _by_id(checks)["pdf.page_size"]
    assert ps.status == "fail" and ps.severity == "critical"
    assert abs(measured["page_sizes_pt"][0][0] - 595.28) < 1


@needs_tectonic
def test_small_body_font_fails(build, base_tex, doc):
    old = r"\newcommand{\resumeItem}[1]{\item\small{#1}}"
    assert old in base_tex
    tex = base_tex.replace(old, r"\newcommand{\resumeItem}[1]{\item\scriptsize{#1}}")
    info = build("scriptsize", tex)
    checks, measured, _ = check_pdf(info["pdf_path"], CONTRACT, doc, max_pages=2)
    bf = _by_id(checks)["pdf.body_font_size"]
    assert bf.status == "fail" and bf.severity == "critical"
    assert bf.measurement["fraction_below_floor"] > 0.02
    assert measured["min_char_pt"] < 9.9


@needs_tectonic
def test_tampered_margin_fails(build, base_tex, doc):
    old_margin = r"\addtolength{\oddsidemargin}{-0.4in}"
    old_width = r"\addtolength{\textwidth}{0.8in}"
    assert old_margin in base_tex and old_width in base_tex
    tex = base_tex.replace(old_margin, r"\addtolength{\oddsidemargin}{-0.9in}").replace(
        old_width, r"\addtolength{\textwidth}{1.3in}")
    info = build("margin", tex)
    checks, measured, _ = check_pdf(info["pdf_path"], CONTRACT, doc, max_pages=2)
    m = _by_id(checks)["pdf.margins"]
    assert m.status == "fail" and m.severity == "critical"
    assert measured["margins_in"]["left"] < 0.48
    assert "ink" in m.message


@needs_tectonic
def test_backend_unavailable_blocks_and_measures_nothing(base, doc):
    backend = {"available": False, "backends": [], "poppler": False, "message": BLOCKED_MESSAGE}
    checks, measured, na = check_pdf(base["pdf_path"], CONTRACT, doc, max_pages=2, compile_info=base,
                                     backend=backend)
    assert len(checks) == 1
    c = checks[0]
    assert c.id == "pdf.backend" and c.status == "fail" and c.severity == "critical"
    assert c.message == "PDF validation unavailable. Production release blocked."
    assert measured == {}
    assert set(na) == set(ALL_CHECK_IDS)


def test_backend_unavailable_without_a_pdf():
    """The block happens before the file is even touched."""
    checks, measured, na = check_pdf("/nonexistent.pdf", CONTRACT, {}, max_pages=1,
                                     backend={"available": False})
    assert checks[0].message == BLOCKED_MESSAGE and measured == {} and na


@needs_tectonic
def test_missing_company_fails_content_present(base, doc):
    doc2 = copy.deepcopy(doc)
    doc2["experience"].append({"id": "exp-999", "title": "Engineer", "company": "Zyxwvut Nonexistent Holdings",
                               "bullets": []})
    checks, _, _ = check_pdf(base["pdf_path"], CONTRACT, doc2, max_pages=2)
    cp = _by_id(checks)["pdf.content_present"]
    assert cp.status == "fail" and cp.blocking
    assert cp.measurement["missing_block_ids"] == ["exp-999"]
    assert "Zyxwvut" not in cp.message


@needs_tectonic
def test_unknown_contract_blocks_are_not_available(base, doc):
    contract = copy.deepcopy(CONTRACT)
    contract["page"] = "unknown"
    contract["typography"]["body"] = "unknown"
    contract["sections"] = "unknown"
    checks, measured, na = check_pdf(base["pdf_path"], contract, doc, max_pages=2)
    by = _by_id(checks)
    for cid in ("pdf.page_size", "pdf.body_font_size", "pdf.required_sections"):
        assert cid in na
        assert by[cid].status == "not_available"
    assert "pdf.overfull_boxes" in na  # no compile_info passed
    # Measurements still come from the PDF itself.
    assert measured["page_sizes_pt"] == [[612.0, 792.0]]
    assert by["pdf.fonts"].status == "pass"


def test_corrupt_pdf_fails_integrity(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    checks, measured, na = check_pdf(bad, CONTRACT, {}, max_pages=1)
    assert checks[0].id == "pdf.integrity" and checks[0].status == "fail" and checks[0].severity == "critical"
    assert set(na) == set(ALL_CHECK_IDS[1:])
