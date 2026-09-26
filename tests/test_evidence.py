"""Evidence workflow (spec §17-20): ask, never invent; only explicit user
answers become evidence, and evidence is scoped to its workflow."""

from __future__ import annotations

import shutil

import pytest
import yaml

from lib import evidence as ev
from lib.errors import ResumeTailorError
from lib.workflows import create_workflow, load_workflow

JD = ("Senior Engineer\nRequirements:\n- Python\n- Kubernetes\n- FastAPI\n- Docker\n"
      "- HIPAA compliance\nNice to have: Terraform")

SENTINEL = "ZQX-SECRET-EVIDENCE-7731"


def _code(excinfo) -> str:
    return excinfo.value.code


@pytest.fixture
def analysis(master):
    ws, _doc, _h = master
    return ws, ev.analyze_requirements(JD, "resume", ws=ws)


@pytest.fixture
def wf(master):
    ws, _doc, _h = master
    return ws, create_workflow("resume", JD, None, ws)["workflow_id"]


# --------------------------------------------------------------------------
# analyze_requirements
# --------------------------------------------------------------------------

def test_missing_terms_get_neutral_prompts(analysis):
    _ws, res = analysis
    assert res["ok"] is True
    prompts = {p["term"]: p for p in res["evidence_prompts"]}
    for term in ("kubernetes", "fastapi"):
        assert term in res["missing"]
        assert term in res["priority_missing"]
        p = prompts[term]
        assert p["status"] == "missing"
        assert p["categories"] == [c for c in ev.CATEGORIES]
        assert len(p["categories"]) == 8
        assert p["allowed_placement_hint"]
        q = p["question"].lower()
        assert "?" in q and "have you used" in q
        # never asserts the candidate has it
        for phrase in ("you have used", "as you used", "your experience with", "since you", "you have experience"):
            assert phrase not in q


def test_python_confirmed_not_prompted(analysis):
    _ws, res = analysis
    assert "python" in res["confirmed"]
    assert "python" not in res["priority_missing"]
    assert "python" not in {p["term"] for p in res["evidence_prompts"]}


def test_skills_only_term_is_weak(analysis):
    _ws, res = analysis
    assert "docker" in res["weak"]
    assert "docker" in res["priority_missing"]
    p = next(p for p in res["evidence_prompts"] if p["term"] == "docker")
    assert p["status"] == "weak"
    # missing terms come before weak ones
    assert res["priority_missing"].index("kubernetes") < res["priority_missing"].index("docker")


def test_nice_to_have_is_not_priority(analysis):
    _ws, res = analysis
    assert "terraform" in res["missing"]
    assert "terraform" not in res["priority_missing"]


def test_unknown_requirements_surfaced(analysis):
    _ws, res = analysis
    assert isinstance(res["unknown_requirements"], list)
    from lib import matching
    upgraded = "unknown_requirements" in matching.match_resume_to_jd({}, JD)
    if not upgraded:
        pytest.skip("matching upgrade (unknown_requirements) not present")
    assert any("hipaa" in u.lower() for u in res["unknown_requirements"])
    unknown_prompts = [p for p in res["evidence_prompts"] if p["status"] == "unknown"]
    assert any("hipaa" in p["term"].lower() for p in unknown_prompts)


def test_result_shape_and_no_jd_text(analysis):
    _ws, res = analysis
    assert set(res) == {"ok", "workflow_id", "source", "confirmed", "weak", "missing",
                        "priority_missing", "unknown_requirements", "evidence_prompts",
                        "match_score", "ats_visible_score"}
    assert res["source"] == "workspace master resume"
    assert "HIPAA compliance\nNice" not in repr(res)


def test_workflow_created_analysis_stored_and_reused(analysis):
    ws, res = analysis
    wid = res["workflow_id"]
    stored = load_workflow(wid, ws)
    assert stored["source_kind"] == "resume"
    a = stored["analysis"]
    assert a["priority_missing"] == res["priority_missing"]
    assert a["confirmed"] == res["confirmed"]
    assert all(set(p) == {"term", "status"} for p in a["evidence_prompts"])
    assert JD not in yaml.safe_dump(stored)

    again = ev.analyze_requirements(JD, "resume", workflow_id=wid, ws=ws)
    assert again["workflow_id"] == wid
    from lib.workflows import list_workflow_ids
    assert list_workflow_ids(ws) == [wid]


def test_wrong_kind_workflow_rejected(master):
    ws, _doc, _h = master
    cv_wf = create_workflow("cv", None, None, ws)["workflow_id"]
    with pytest.raises(ResumeTailorError) as e:
        ev.analyze_requirements(JD, "resume", workflow_id=cv_wf, ws=ws)
    assert _code(e) == "INVALID_KIND"


def test_jd_limits(master):
    ws, _doc, _h = master
    for bad in ("", "   ", "x" * (ev.MAX_JD_CHARS + 1)):
        with pytest.raises(ResumeTailorError) as e:
            ev.analyze_requirements(bad, "resume", ws=ws)
        assert _code(e) == "EVIDENCE_INVALID"


def test_missing_master(workspace):
    with pytest.raises(ResumeTailorError) as e:
        ev.analyze_requirements(JD, "cv", ws=workspace)
    assert _code(e) == "MASTER_NOT_FOUND"


def test_no_workspace(rt_home):
    with pytest.raises(ResumeTailorError) as e:
        ev.analyze_requirements(JD)
    assert _code(e) == "WORKSPACE_NOT_INITIALIZED"
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence("wf-20260101-abcdef", "k8s", "professional", "x", confirmed=True)
    assert _code(e) == "WORKSPACE_NOT_INITIALIZED"


# --------------------------------------------------------------------------
# save_evidence
# --------------------------------------------------------------------------

@pytest.mark.parametrize("confirmed", [False, "yes", 1, None, "true"])
def test_save_requires_explicit_true(wf, confirmed):
    ws, wid = wf
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence(wid, "kubernetes", "professional", "Ran clusters", confirmed=confirmed, ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"
    assert "explicitly" in e.value.message
    assert ev.list_evidence(wid, ws=ws) == []


def test_save_bad_category(wf):
    ws, wid = wf
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence(wid, "kubernetes", "hobby", "Ran clusters", confirmed=True, ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"


def test_save_empty_text_for_non_none(wf):
    ws, wid = wf
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence(wid, "kubernetes", "professional", "   ", confirmed=True, ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"


def test_save_term_and_text_limits(wf):
    ws, wid = wf
    for term in ("", "   ", "x" * 81):
        with pytest.raises(ResumeTailorError) as e:
            ev.save_evidence(wid, term, "professional", "did it", confirmed=True, ws=ws)
        assert _code(e) == "EVIDENCE_INVALID"
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence(wid, "kubernetes", "professional", "a" * 1001, confirmed=True, ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"


def test_save_none_with_empty_text(wf):
    ws, wid = wf
    res = ev.save_evidence(wid, "FastAPI", "none", "", confirmed=True, ws=ws)
    assert res["ok"] is True
    assert res["evidence"]["category"] == "none"
    assert "not added" in res["note"]


def test_metrics_must_be_in_text(wf):
    ws, wid = wf
    text = "Maintained 3 Kubernetes clusters serving 40  requests/sec"
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence(wid, "kubernetes", "professional", text, confirmed=True,
                         metrics=["99.9% uptime"], ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"
    res = ev.save_evidence(wid, "kubernetes", "professional", text, confirmed=True,
                           metrics=["3 kubernetes clusters", "40 requests/sec"], ws=ws)
    assert res["evidence"]["metrics"] == ["3 kubernetes clusters", "40 requests/sec"]
    for bad in (["x" * 41], ["1"] * 11, "3", [3]):
        with pytest.raises(ResumeTailorError) as e:
            ev.save_evidence(wid, "kubernetes", "professional", text, confirmed=True, metrics=bad, ws=ws)
        assert _code(e) == "EVIDENCE_INVALID"


def test_alias_term_normalized(wf):
    ws, wid = wf
    from lib import keywords
    res = ev.save_evidence(wid, "K8s", "personal_project", "Deployed a homelab cluster",
                           confirmed=True, ws=ws)
    rec = res["evidence"]
    assert rec["term_display"] == "K8s"
    if not hasattr(keywords, "normalize_term"):
        pytest.skip("keywords.normalize_term not present; alias mapping untested")
    assert rec["term"] == "kubernetes"


def test_duplicate_returns_same_id_and_ids_increment(wf):
    ws, wid = wf
    a = ev.save_evidence(wid, "kubernetes", "professional", "Ran clusters", confirmed=True, ws=ws)
    b = ev.save_evidence(wid, "Kubernetes", "professional", "  Ran clusters ", confirmed=True, ws=ws)
    assert a["evidence"]["id"] == b["evidence"]["id"] == f"ev-{wid}-01"
    c = ev.save_evidence(wid, "fastapi", "internship", "Wrote endpoints", confirmed=True, ws=ws)
    d = ev.save_evidence(wid, "kubernetes", "personal_project", "Ran clusters", confirmed=True, ws=ws)
    assert c["evidence"]["id"] == f"ev-{wid}-02"
    assert d["evidence"]["id"] == f"ev-{wid}-03"
    assert [r["id"] for r in ev.list_evidence(wid, ws=ws)] == [f"ev-{wid}-0{i}" for i in (1, 2, 3)]
    assert load_workflow(wid, ws)["evidence_ids"] == [f"ev-{wid}-0{i}" for i in (1, 2, 3)]


def test_stored_record_fields(wf):
    ws, wid = wf
    res = ev.save_evidence(wid, "fastapi", "academic", "Built a thesis API", confirmed=True, ws=ws)
    eid = res["evidence"]["id"]
    on_disk = yaml.safe_load((ws.evidence_dir / wid / f"{eid}.yaml").read_text())
    assert set(on_disk) == {"id", "workflow_id", "workspace_id", "term", "category", "evidence_text",
                            "confirmed", "metrics", "created_at", "term_display"}
    assert on_disk["workspace_id"] == ws.id and on_disk["confirmed"] is True


def test_save_unknown_workflow(master):
    ws, _doc, _h = master
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence("wf-20260101-abcdef", "k8s", "professional", "x", confirmed=True, ws=ws)
    assert _code(e) == "WORKFLOW_NOT_FOUND"
    with pytest.raises(ResumeTailorError) as e:
        ev.save_evidence("../etc", "k8s", "professional", "x", confirmed=True, ws=ws)
    assert _code(e) == "INVALID_ID"


# --------------------------------------------------------------------------
# load_evidence
# --------------------------------------------------------------------------

def test_load_roundtrip(wf):
    ws, wid = wf
    eid = ev.save_evidence(wid, "fastapi", "internship", "Wrote endpoints", confirmed=True,
                           ws=ws)["evidence"]["id"]
    loaded = ev.load_evidence(wid, [eid], ws=ws)
    assert list(loaded) == [eid]
    assert loaded[eid]["evidence_text"] == "Wrote endpoints"
    assert loaded[eid]["term_display"] == "fastapi"


def test_load_cross_workflow_rejected(master):
    ws, _doc, _h = master
    wa = create_workflow("resume", None, None, ws)["workflow_id"]
    wb = create_workflow("resume", None, None, ws)["workflow_id"]
    ea = ev.save_evidence(wa, "fastapi", "professional", "Wrote endpoints", confirmed=True,
                          ws=ws)["evidence"]["id"]

    # A's ID through B
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wb, [ea], ws=ws)
    assert _code(e) == "EVIDENCE_NOT_FOUND"

    # A's file copied into B's folder under a B-style name
    fake = f"ev-{wb}-01"
    (ws.evidence_dir / wb).mkdir(parents=True, exist_ok=True)
    shutil.copy(ws.evidence_dir / wa / f"{ea}.yaml", ws.evidence_dir / wb / f"{fake}.yaml")
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wb, [fake], ws=ws)
    assert _code(e) == "EVIDENCE_NOT_FOUND"

    # even with id rewritten, the record still says workflow A
    path = ws.evidence_dir / wb / f"{fake}.yaml"
    data = yaml.safe_load(path.read_text())
    data["id"] = fake
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wb, [fake], ws=ws)
    assert _code(e) == "EVIDENCE_NOT_FOUND"


def test_load_missing_and_malformed_ids(wf):
    ws, wid = wf
    for bad in (f"ev-{wid}-01", f"ev-{wid}-1", "ev-../x", 5):
        with pytest.raises(ResumeTailorError) as e:
            ev.load_evidence(wid, [bad], ws=ws)
        assert _code(e) == "EVIDENCE_NOT_FOUND"


def test_tampered_unconfirmed_rejected(wf):
    ws, wid = wf
    eid = ev.save_evidence(wid, "fastapi", "professional", "Wrote endpoints", confirmed=True,
                           ws=ws)["evidence"]["id"]
    path = ws.evidence_dir / wid / f"{eid}.yaml"
    data = yaml.safe_load(path.read_text())
    data["confirmed"] = False
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wid, [eid], ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"


def test_tampered_metric_rejected(wf):
    ws, wid = wf
    eid = ev.save_evidence(wid, "fastapi", "professional", "Wrote 5 endpoints", confirmed=True,
                           metrics=["5 endpoints"], ws=ws)["evidence"]["id"]
    path = ws.evidence_dir / wid / f"{eid}.yaml"
    data = yaml.safe_load(path.read_text())
    data["metrics"] = ["50 endpoints"]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wid, [eid], ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"


def test_other_workspace_record_rejected(wf):
    ws, wid = wf
    eid = ev.save_evidence(wid, "fastapi", "professional", "Wrote endpoints", confirmed=True,
                           ws=ws)["evidence"]["id"]
    path = ws.evidence_dir / wid / f"{eid}.yaml"
    data = yaml.safe_load(path.read_text())
    data["workspace_id"] = "someone-else"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wid, [eid], ws=ws)
    assert _code(e) == "EVIDENCE_NOT_FOUND"


# --------------------------------------------------------------------------
# No leakage of evidence text
# --------------------------------------------------------------------------

def test_errors_do_not_echo_evidence_text(wf):
    ws, wid = wf
    text = f"{SENTINEL} did things"
    attempts = [
        dict(category="professional", evidence_text=text, confirmed=False),
        dict(category="bogus", evidence_text=text, confirmed=True),
        dict(category="professional", evidence_text=text + "x" * 1000, confirmed=True),
        dict(category="professional", evidence_text=text, confirmed=True, metrics=["not there"]),
    ]
    for kw in attempts:
        with pytest.raises(ResumeTailorError) as e:
            ev.save_evidence(wid, "fastapi", ws=ws, **kw)
        assert SENTINEL not in repr(e.value.to_result())

    eid = ev.save_evidence(wid, "fastapi", "professional", text, confirmed=True, ws=ws)["evidence"]["id"]
    path = ws.evidence_dir / wid / f"{eid}.yaml"
    data = yaml.safe_load(path.read_text())
    data["category"] = SENTINEL  # invalid enum value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ResumeTailorError) as e:
        ev.load_evidence(wid, [eid], ws=ws)
    assert _code(e) == "EVIDENCE_INVALID"
    assert SENTINEL not in repr(e.value.to_result())

