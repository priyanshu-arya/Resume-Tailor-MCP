"""lib/validators/format_tex.py"""

from __future__ import annotations

import copy

import pytest

from lib.ids import normalize_master
from lib.latex import render_latex
from lib.validators.format_tex import check_tex
from tests.conftest import SYNTHETIC_LEGACY_MASTER
from tests.test_validators_structure import CONTRACT


@pytest.fixture(scope="module")
def tex() -> str:
    return render_latex(normalize_master(copy.deepcopy(SYNTHETIC_LEGACY_MASTER), "resume"))


def _run(tex, contract=CONTRACT):
    checks, inferred = check_tex(tex, contract)
    ids = [c.id for c in checks]
    assert len(ids) == len(set(ids))
    return {c.id: c for c in checks}, inferred


ALL_IDS = {"template.page_size", "template.base_font_size", "template.margins", "template.margin_floor",
           "template.body_font_size", "template.body_font_floor", "template.forbidden_structures",
           "template.columns", "template.font_family"}


def test_real_renderer_output_passes(tex):
    checks, inferred = _run(tex)
    assert set(checks) == ALL_IDS
    assert all(c.status == "pass" for c in checks.values()), [c for c in checks.values() if c.status != "pass"]
    assert all(c.source == "tex_inferred" for c in checks.values())
    assert inferred["page_size"] == "letter"
    assert inferred["base_font_pt"] == 11
    assert inferred["body_font_pt"] == 10
    assert inferred["margins_in"] == {"top": 0.6, "bottom": 0.5, "left": 0.6, "right": 0.6}
    assert inferred["columns"] == 1
    assert inferred["forbidden"] == []


def test_a4paper_fails_page_size(tex):
    checks, inferred = _run(tex.replace("letterpaper", "a4paper"))
    c = checks["template.page_size"]
    assert (c.status, c.severity) == ("fail", "error") and inferred["page_size"] == "a4"
    # fullpage margins are paper-independent, so they still match
    assert checks["template.margins"].status == "pass"


def test_9pt_fails_base_size_and_body_floor(tex):
    checks, inferred = _run(tex.replace("11pt", "9pt"))
    assert checks["template.base_font_size"].status == "fail"
    # 9pt is not a standard-class size: LaTeX falls back to 10pt, so \small body text is 9pt
    assert inferred["base_font_pt"] == 10 and inferred["body_font_pt"] == 9
    c = checks["template.body_font_floor"]
    assert (c.status, c.severity, c.category) == ("fail", "critical", "FORMAT")


def test_top_margin_below_half_inch_is_critical(tex):
    checks, inferred = _run(tex.replace(r"\addtolength{\topmargin}{-0.4in}", r"\addtolength{\topmargin}{-0.9in}"))
    assert inferred["margins_in"]["top"] == pytest.approx(0.1)
    c = checks["template.margin_floor"]
    assert (c.status, c.severity, c.category) == ("fail", "critical", "FORMAT") and c.blocking
    assert checks["template.margins"].status == "fail"


def test_side_margin_off_contract_but_above_floor(tex):
    checks, _ = _run(tex.replace(r"\addtolength{\textwidth}{0.8in}", r"\addtolength{\textwidth}{0.7in}"))
    assert checks["template.margins"].status == "fail"
    assert checks["template.margin_floor"].status == "pass"


def test_multicols_fails(tex):
    t = tex.replace(r"\section{Experience}", "\\begin{multicols}{2}\n\\section{Experience}", 1)
    t = t.replace(r"\section{Projects}", "\\end{multicols}\n\\section{Projects}", 1)
    checks, inferred = _run(t)
    assert checks["template.forbidden_structures"].status == "fail"
    assert "multicols" in inferred["forbidden"]
    assert checks["template.columns"].status == "fail" and inferred["columns"] == 2


def test_includegraphics_fails(tex):
    t = tex.replace(r"\begin{document}", "\\begin{document}\n\\includegraphics{photo.png}", 1)
    c, inferred = _run(t)
    assert (c["template.forbidden_structures"].status, c["template.forbidden_structures"].severity) == ("fail", "error")
    assert inferred["forbidden"] == ["includegraphics"]


def test_tabular_in_content_fails_but_layout_macros_are_allowed(tex):
    assert r"\begin{tabular*}" in tex  # the allowed layout-only macros
    t = tex.replace(r"\section{Education}", "\\begin{tabular}{ll} a & b \\\\ \\end{tabular}\n\\section{Education}", 1)
    checks, inferred = _run(t)
    assert checks["template.forbidden_structures"].status == "fail" and inferred["forbidden"] == ["tabular"]


def test_scriptsize_body_items_fail_floor(tex):
    t = tex.replace(r"\newcommand{\resumeItem}[1]{\item\small{#1}}", r"\newcommand{\resumeItem}[1]{\item\scriptsize{#1}}")
    assert t != tex
    checks, inferred = _run(t)
    assert inferred["body_font_pt"] == 8
    assert checks["template.body_font_floor"].status == "fail"
    assert checks["template.body_font_size"].status == "fail"


def test_commented_out_structures_are_ignored(tex):
    t = tex.replace(r"\begin{document}", "\\begin{document}\n% \\includegraphics{photo.png}", 1)
    assert _run(t)[0]["template.forbidden_structures"].status == "pass"


def test_decorative_font_fails(tex):
    t = tex.replace(r"\usepackage{latexsym}", "\\usepackage{latexsym}\n\\usepackage{calligra}", 1)
    c = _run(t)[0]["template.font_family"]
    assert (c.status, c.severity) == ("fail", "error")


def test_plain_font_off_contract_fails(tex):
    t = tex.replace(r"\usepackage{latexsym}", "\\usepackage{latexsym}\n\\usepackage{helvet}", 1)
    c, inferred = _run(t)
    assert c["template.font_family"].status == "fail" and inferred["font_family"] == "Helvetica"


def test_geometry_margins_are_inferred(tex):
    t = tex.replace(r"\usepackage[empty]{fullpage}", r"\usepackage[margin=0.3in]{geometry}")
    checks, inferred = _run(t)
    assert inferred["margins_in"] == {"top": 0.3, "bottom": 0.3, "left": 0.3, "right": 0.3}
    assert checks["template.margin_floor"].status == "fail"


@pytest.mark.parametrize("path,check_id", [
    (("page",), "template.page_size"),
    (("page", "size"), "template.page_size"),
    (("page", "margins_in"), "template.margins"),
    (("latex",), "template.base_font_size"),
    (("typography",), "template.body_font_size"),
    (("typography", "body", "size_pt"), "template.body_font_size"),
    (("typography", "font_family"), "template.font_family"),
    (("layout",), "template.forbidden_structures"),
    (("layout", "columns"), "template.columns"),
])
def test_unknown_contract_field_is_not_available(tex, path, check_id):
    contract = copy.deepcopy(CONTRACT)
    node = contract
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = "unknown"
    checks, _ = _run(tex, contract)
    assert checks[check_id].status == "not_available"


def test_floor_checks_still_fail_with_unknown_contract(tex):
    contract = {k: "unknown" for k in CONTRACT}
    t = tex.replace(r"\addtolength{\topmargin}{-0.4in}", r"\addtolength{\topmargin}{-0.9in}")
    t = t.replace(r"\begin{document}", "\\begin{document}\n\\includegraphics{x}", 1)
    checks, _ = _run(t, contract)
    assert checks["template.margin_floor"].status == "fail"
    assert checks["template.forbidden_structures"].status == "fail"
    assert checks["template.page_size"].status == "not_available"
    assert checks["template.margins"].status == "not_available"
