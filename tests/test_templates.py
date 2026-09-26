"""Template whitelist, contracts and the no-silent-fallback rule (spec §26-30)."""

from __future__ import annotations

import re

import pytest

from lib import latex, templates
from lib.errors import ResumeTailorError
from lib.schemas import TemplateContract

EXPECTED_IDS = {"full-stack-modern", "classic-minimalist", "student-achievements",
                "generic-minimal", "metrics-driven"}
EXPERIMENTAL = sorted(EXPECTED_IDS - {"classic-minimalist"})

# JDs that the unrestricted recommender maps to experimental templates.
FULL_STACK_JD = ("Full-stack web developer. React, Angular, Vue, PHP, REST, MVC, HTML, CSS, "
                 "JavaScript, Node.js, Express. 3 years of experience.")
INTERN_JD = "Software engineering intern for our campus internship program, new grad welcome."
METRICS_JD = ("Senior engineer: scalable microservices, performance optimization, CI/CD, agile, "
              "cloud, machine learning, data, AWS, Azure, GCP. 8+ years experience.")
EMPTY_SIGNAL_JD = "We are looking for a person."


def _err(fn, *a, **k) -> ResumeTailorError:
    with pytest.raises(ResumeTailorError) as ei:
        fn(*a, **k)
    return ei.value


# --- whitelist -------------------------------------------------------------

def test_whitelist_is_exactly_the_registry():
    assert set(templates.registered_ids()) == EXPECTED_IDS
    assert {t["id"] for t in templates.list_templates()} == EXPECTED_IDS


def test_only_classic_is_supported_and_rendered():
    assert set(templates.RENDERERS) == {"classic-minimalist"}
    by_id = {t["id"]: t for t in templates.list_templates()}
    assert by_id["classic-minimalist"]["status"] == "supported"
    assert by_id["classic-minimalist"]["version"] == "1.1.0"
    assert by_id["classic-minimalist"]["has_renderer"] is True
    for tid in EXPERIMENTAL:
        assert by_id[tid]["status"] == "experimental"
        assert by_id[tid]["version"] == "0.1.0"
        assert by_id[tid]["has_renderer"] is False


def test_every_renderer_is_registered_and_supported():
    for tid in templates.RENDERERS:
        assert templates.resolve_template(tid)["status"] == "supported"


@pytest.mark.parametrize("bad", ["../../x", "/etc/passwd", "Template 1.pdf", "Template 2.pdf",
                                 "classic", "CLASSIC-MINIMALIST", " classic-minimalist", "", "auto",
                                 "templates.yaml", None, 7])
def test_unknown_and_path_like_ids_rejected(bad):
    for fn in (templates.resolve_template, templates.get_contract, templates.is_releasable):
        if fn is templates.is_releasable:
            assert fn(bad) is False
            continue
        e = _err(fn, bad)
        assert e.code == "TEMPLATE_UNKNOWN"
        assert set(e.details["registered"]) == EXPECTED_IDS
    assert _err(templates.render_template, bad, {"name": "X"}).code == "TEMPLATE_UNKNOWN"


def test_explicit_unknown_for_tailoring_rejected(legacy_master):
    for bad in ("../../x", "/etc/passwd", "Template 1.pdf", "fancy-new-layout"):
        assert _err(templates.resolve_for_tailoring, bad, legacy_master, FULL_STACK_JD).code == "TEMPLATE_UNKNOWN"


# --- resolution / releasability -------------------------------------------

def test_classic_resolves_releasable():
    assert templates.resolve_template("classic-minimalist") == {
        "id": "classic-minimalist", "status": "supported", "version": "1.1.0",
        "has_renderer": True, "releasable": True,
    }
    assert templates.is_releasable("classic-minimalist") is True


@pytest.mark.parametrize("tid", EXPERIMENTAL)
def test_experimental_resolves_not_releasable(tid):
    r = templates.resolve_template(tid)
    assert r["id"] == tid
    assert r["status"] == "experimental"
    assert r["has_renderer"] is False
    assert r["releasable"] is False
    assert templates.is_releasable(tid) is False


# --- rendering: no silent fallback ----------------------------------------

@pytest.mark.parametrize("tid", EXPERIMENTAL)
def test_render_experimental_raises_no_renderer(tid, legacy_master):
    result = None
    with pytest.raises(ResumeTailorError) as ei:
        result = templates.render_template(tid, legacy_master)
    assert ei.value.code == "TEMPLATE_NO_RENDERER"
    assert ei.value.details["template_id"] == tid
    assert result is None  # nothing (in particular no classic LaTeX) was produced


def test_render_metrics_driven_is_not_latex(legacy_master):
    try:
        out = templates.render_template("metrics-driven", legacy_master)
    except ResumeTailorError as e:
        assert e.code == "TEMPLATE_NO_RENDERER"
    else:  # pragma: no cover - failure path
        pytest.fail(f"metrics-driven rendered something: {out[:80]!r}")


def test_render_classic_is_exact_latex(legacy_master):
    assert templates.render_template("classic-minimalist", legacy_master) == \
        latex.render_latex(legacy_master, "classic-minimalist")


# --- auto / explicit tailoring resolution ---------------------------------

@pytest.mark.parametrize("jd", [FULL_STACK_JD, INTERN_JD, METRICS_JD, EMPTY_SIGNAL_JD, "", None])
@pytest.mark.parametrize("choice", ["auto", "", None])
def test_auto_only_returns_supported(jd, choice, legacy_master):
    tid, version = templates.resolve_for_tailoring(choice, legacy_master, jd)
    assert templates.is_releasable(tid)
    assert version == templates.resolve_template(tid)["version"]


def test_fixture_jds_did_recommend_experimental_unrestricted(legacy_master):
    # Guards the test above: without the restriction these JDs pick experimental templates.
    unrestricted = {templates.recommend_template(jd, legacy_master)["recommended_template"]
                    for jd in (FULL_STACK_JD, INTERN_JD, METRICS_JD, EMPTY_SIGNAL_JD)}
    assert unrestricted - {"classic-minimalist"}
    assert templates.recommend_template(FULL_STACK_JD)["recommended_template"] == "full-stack-modern"


def test_recommend_supported_only_scoreboard(legacy_master):
    rec = templates.recommend_template(FULL_STACK_JD, legacy_master, supported_only=True)
    assert rec["recommended_template"] == "classic-minimalist"
    assert set(rec["all_scores"]) == {"classic-minimalist"}
    assert rec["releasable"] is True


@pytest.mark.parametrize("tid", EXPERIMENTAL)
def test_explicit_experimental_returned_unchanged(tid, legacy_master):
    assert templates.resolve_for_tailoring(tid, legacy_master, FULL_STACK_JD) == (tid, "0.1.0")


def test_explicit_classic_returned(legacy_master):
    assert templates.resolve_for_tailoring("classic-minimalist", legacy_master, None) == ("classic-minimalist", "1.1.0")


# --- contracts ---------------------------------------------------------------

@pytest.mark.parametrize("tid", sorted(EXPECTED_IDS))
def test_every_template_validates_as_contract(tid):
    raw = templates.get_template(tid)
    TemplateContract.model_validate(raw)
    c = templates.get_contract(tid)
    assert c["id"] == tid
    for field in ("page", "typography", "layout", "sections", "spacing", "limits", "formatting"):
        assert field in raw, f"{tid} missing contract field {field}"
    # Existing metadata consumers still work.
    assert isinstance(raw["layout"]["sections"], list)
    assert raw["best_for"] and raw["source_file"]


@pytest.mark.parametrize("tid", EXPERIMENTAL)
def test_experimental_contract_does_not_invent_precision(tid):
    c = templates.get_contract(tid)
    for field in ("page", "typography", "spacing", "limits", "formatting", "latex"):
        assert c[field] == "unknown", (tid, field)
    assert c["sections"]["order"] == c["layout"]["sections"]


def _rendered_section_titles(resume) -> list[str]:
    tex = latex.render_latex(resume, "classic-minimalist")
    return re.findall(r"\\section\{([^}]*)\}", tex)


def test_classic_section_order_matches_renderer(legacy_master):
    c = templates.get_contract("classic-minimalist")
    expected = [c["sections"]["headings"][s] for s in c["sections"]["order"]]
    # The fixture has every section, so the renderer emits all of them.
    assert _rendered_section_titles(legacy_master) == expected
    assert c["sections"]["order"] == c["layout"]["sections"]


def test_classic_contract_margins_agree_with_latex_adjustments():
    c = templates.get_contract("classic-minimalist")
    lx, m = c["latex"], c["page"]["margins_in"]
    # fullpage: 1in margins, 6.5in x 9in text block on 8.5 x 11 letter.
    top = 1 + lx["topmargin_adjust_in"]
    bottom = 11 - top - (9 + lx["textheight_adjust_in"])
    side = 1 + lx["side_margin_adjust_in"]
    assert m["top"] == pytest.approx(top)
    assert m["bottom"] == pytest.approx(bottom)
    assert m["left"] == pytest.approx(side) and m["right"] == pytest.approx(side)
    # textwidth grows by exactly what the two side margins lose.
    assert 8.5 - 2 * side == pytest.approx(6.5 + lx["textwidth_adjust_in"])
    assert c["page"]["width_pt"] == 612 and c["page"]["height_pt"] == 792


def test_classic_contract_latex_block_matches_preamble(legacy_master):
    c = templates.get_contract("classic-minimalist")
    lx = c["latex"]
    tex = latex.render_latex(legacy_master, "classic-minimalist")
    assert f"\\documentclass[{','.join(lx['documentclass_options'])}]{{article}}" in tex
    assert f"\\addtolength{{\\topmargin}}{{{lx['topmargin_adjust_in']}in}}" in tex
    assert f"\\addtolength{{\\textheight}}{{{lx['textheight_adjust_in']}in}}" in tex
    assert f"\\addtolength{{\\oddsidemargin}}{{{lx['side_margin_adjust_in']}in}}" in tex
    assert f"\\addtolength{{\\evensidemargin}}{{{lx['side_margin_adjust_in']}in}}" in tex
    assert f"\\addtolength{{\\textwidth}}{{{lx['textwidth_adjust_in']}in}}" in tex
    assert "\\usepackage[empty]{fullpage}" in tex


def test_classic_formatting_floor():
    c = templates.get_contract("classic-minimalist")
    t = c["typography"]
    assert t["body"]["size_pt"] >= 10
    assert 14 <= t["name"]["size_pt"] <= 24
    assert 11 <= t["heading"]["size_pt"] <= 14
    assert all(0.5 <= v <= 1.0 for v in c["page"]["margins_in"].values())
    assert c["layout"]["columns"] == 1
