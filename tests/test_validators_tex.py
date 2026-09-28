"""lib/validators/format_tex.py"""

from __future__ import annotations

import copy

import pytest

from lib.ids import normalize_master
from lib.latex import render_latex
from lib.validators.format_tex import ALL_CHECK_IDS, check_tex
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
           "template.columns", "template.font_family", "template.name_font_size", "template.heading_font_size",
           "template.heading_style", "template.spacing", "template.bullet_style", "template.link_style"}


def test_all_check_ids_are_emitted(tex):
    checks, _ = _run(tex)
    assert set(checks) == set(ALL_CHECK_IDS) == ALL_IDS


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


@pytest.mark.parametrize("env", ["minipage", "textblock", "tikzpicture", "wrapfigure", "longtable"])
def test_forbidden_envs_fail(tex, env):
    t = tex.replace(r"\begin{document}", f"\\begin{{document}}\n\\begin{{{env}}}{{1in}}x\\end{{{env}}}", 1)
    checks, inferred = _run(t)
    assert checks["template.forbidden_structures"].status == "fail"
    assert env in inferred["forbidden"]


@pytest.mark.parametrize("cmd", ["parbox", "fbox", "framebox", "colorbox", "fcolorbox"])
def test_forbidden_cmds_fail(tex, cmd):
    t = tex.replace(r"\begin{document}", f"\\begin{{document}}\n\\{cmd}{{x}}{{y}}", 1)
    checks, inferred = _run(t)
    assert checks["template.forbidden_structures"].status == "fail"
    assert cmd in inferred["forbidden"]


def test_twocolumn_as_class_option_fails(tex):
    t = tex.replace("[letterpaper,11pt]", "[letterpaper,11pt,twocolumn]", 1)
    checks, inferred = _run(t)
    assert checks["template.columns"].status == "fail" and inferred["columns"] == 2


def test_twocolumn_as_command_fails(tex):
    t = tex.replace(r"\begin{document}", "\\begin{document}\n\\twocolumn\n", 1)
    checks, inferred = _run(t)
    assert checks["template.columns"].status == "fail" and inferred["columns"] == 2


def test_includegraphics_fails(tex):
    t = tex.replace(r"\begin{document}", "\\begin{document}\n\\includegraphics{photo.png}", 1)
    c, inferred = _run(t)
    assert (c["template.forbidden_structures"].status, c["template.forbidden_structures"].severity) == ("fail", "error")
    assert inferred["forbidden"] == ["includegraphics"]


def test_tabular_in_content_fails_but_layout_macros_are_allowed(tex):
    assert r"\begin{tabularx}" in tex  # the allowed layout-only macros
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


def test_layout_macro_whitelist_is_contract_driven(tex):
    """With no whitelisted layout-only macros, the untouched renderer output
    (which relies on resumeSubheading's internal tabularx) now fails --
    LAYOUT_MACROS is contract-driven, not a hardcoded assumption."""
    contract = copy.deepcopy(CONTRACT)
    contract["latex"]["layout_only_macros"] = []
    checks, inferred = _run(tex, contract)
    assert checks["template.forbidden_structures"].status == "fail"
    assert "tabularx" in inferred["forbidden"]


def test_name_font_size_matches_huge(tex):
    checks, inferred = _run(tex)
    assert checks["template.name_font_size"].status == "pass"
    assert inferred["name_font_pt"] == pytest.approx(20.74)


def test_name_font_size_fails_when_header_size_switch_changes(tex):
    t = tex.replace(r"\huge \scshape", r"\Large \scshape", 1)
    checks, _ = _run(t)
    assert checks["template.name_font_size"].status == "fail"


def test_heading_font_size_fails_when_titleformat_size_switch_changes(tex):
    t = tex.replace(r"\raggedright\large", r"\raggedright\normalsize", 1)
    checks, _ = _run(t)
    assert checks["template.heading_font_size"].status == "fail"


def test_heading_style_fails_missing_scshape(tex):
    t = tex.replace(r"\bfseries\scshape", r"\bfseries", 1)
    checks, _ = _run(t)
    c = checks["template.heading_style"]
    assert c.status == "fail" and "scshape" in c.message


def test_heading_style_fails_missing_titlerule(tex):
    t = tex.replace(r"\titlerule \vspace{2pt}", r"\vspace{2pt}", 1)
    checks, _ = _run(t)
    assert checks["template.heading_style"].status == "fail"


def test_heading_style_not_available_for_unrecognized_style_string():
    contract = copy.deepcopy(CONTRACT)
    contract["formatting"]["heading_style"] = "some new style nobody wrote a rule for"
    checks, _ = _run("\\documentclass[letterpaper,11pt]{article}\n\\begin{document}\\end{document}", contract)
    assert checks["template.heading_style"].status == "not_available"


def test_spacing_fails_on_changed_itemsep(tex):
    t = tex.replace("itemsep=2pt", "itemsep=8pt", 1)
    checks, _ = _run(t)
    assert checks["template.spacing"].status == "fail"


def test_spacing_fails_on_linespread(tex):
    t = tex.replace(r"\begin{document}", "\\linespread{0.85}\n\\begin{document}", 1)
    checks, _ = _run(t)
    c = checks["template.spacing"]
    assert c.status == "fail" and "linespread" in c.message.lower()


def test_spacing_fails_on_onehalfspacing(tex):
    t = tex.replace(r"\begin{document}", "\\onehalfspacing\n\\begin{document}", 1)
    checks, _ = _run(t)
    assert checks["template.spacing"].status == "fail"


def test_bullet_style_fails_on_enumerate_body(tex):
    t = tex.replace(r"\begin{itemize}[leftmargin=0.18in]", r"\begin{enumerate}[leftmargin=0.18in]", 1)
    t = t.replace(r"\end{itemize}\vspace{6pt}", r"\end{enumerate}\vspace{6pt}", 1)
    checks, inferred = _run(t)
    assert checks["template.bullet_style"].status == "fail"
    assert checks["template.bullet_style"].measurement["other"] == ["enumerate"]


def test_link_style_fails_on_colorlinks(tex):
    t = tex.replace(r"\usepackage[hidelinks]{hyperref}", r"\usepackage[colorlinks]{hyperref}", 1)
    checks, _ = _run(t)
    c = checks["template.link_style"]
    assert c.status == "fail" and c.severity == "warning"


def test_link_style_fails_missing_hidelinks(tex):
    t = tex.replace(r"\usepackage[hidelinks]{hyperref}", r"\usepackage{hyperref}", 1)
    checks, _ = _run(t)
    assert checks["template.link_style"].status == "fail"


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
