"""lib/release.page_cap -- Phase 6 visibility fix: the release.career_stage
check must warn (not silently pass) when the template's own max_pages is
what's actually binding, e.g. a director/academic capped by
classic-minimalist's max_pages: 2 rather than by their career stage. Pure
function, no tectonic required."""

from __future__ import annotations

from lib.release import page_cap

CONTRACT = {"limits": {"min_pages": 1, "max_pages": 2}}


def _master(stage):
    return {"metadata": {"career_stage": stage}} if stage else {"metadata": {}}


def test_no_career_stage_is_a_warning():
    cap, check = page_cap(CONTRACT, _master(None))
    assert cap == 2
    assert (check.status, check.severity) == ("warning", "warning")


def test_director_capped_by_template_is_a_warning():
    cap, check = page_cap(CONTRACT, _master("director"))
    assert cap == 2  # min(contract_max=2, stage_max=3)
    assert check.status == "warning"
    assert "template, not your career stage" in check.message
    assert "director" in check.message and "2" in check.message


def test_academic_capped_by_template_is_a_warning():
    cap, check = page_cap(CONTRACT, _master("academic"))
    assert cap == 2  # stage_max is None (no corporate cap) -> template cap binds
    assert check.status == "warning"
    assert "template, not your career stage" in check.message


def test_manager_matches_template_cap_is_not_a_warning():
    cap, check = page_cap(CONTRACT, _master("manager"))
    assert cap == 2  # stage_max == contract_max == 2: template isn't MORE restrictive
    assert (check.status, check.severity) == ("pass", "info")


def test_fresher_capped_by_stage_is_not_a_warning():
    cap, check = page_cap(CONTRACT, _master("fresher"))
    assert cap == 1  # stage_max=1 < contract_max=2: the stage is what binds
    assert (check.status, check.severity) == ("pass", "info")


def test_no_contract_limit_falls_back_to_stage():
    cap, check = page_cap({"limits": {}}, _master("fresher"))
    assert cap == 1
    assert check.status == "pass"
