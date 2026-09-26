"""set_master: create, preview/confirm, hash protection, ID stability."""

from __future__ import annotations

import copy

import pytest

from lib import master_ops, storage
from lib.errors import ResumeTailorError

MD = """# Casey Sample
casey@example.test | +1 555 0199 | Denver, CO

## Summary
Data engineer building batch pipelines.

## Skills
- Languages: Python, SQL

## Experience
### Data Engineer, Gamma Inc | Denver, CO | Jan 2023 - Present
- Built nightly batch jobs in Python.
- Maintained SQL reporting views.

## Education
### B.S. Statistics, Sample State | 2022
"""


def _code(excinfo) -> str:
    return excinfo.value.code


@pytest.fixture
def md_file(tmp_path):
    p = tmp_path / "resume.md"
    p.write_text(MD, encoding="utf-8")
    return p


def test_first_create_from_markdown_writes_immediately_with_ids(workspace, md_file):
    r = master_ops.set_master("resume", file_path=str(md_file), ws=workspace)
    assert r["applied"] is True and r["master_ready"] is True
    doc, h = storage.load_master("resume", workspace)
    assert h == r["master_hash"]
    assert doc["name"] == "Casey Sample"
    exp = doc["experience"][0]
    assert exp["id"] == "exp-001"
    assert [b["id"] for b in exp["bullets"]] == ["exp-001-b01", "exp-001-b02"]
    assert set(r["sections_found"]) >= {"summary", "skills", "experience", "education"}


def test_replace_without_confirm_writes_nothing_and_returns_diff(master, md_file):
    ws, _, h = master
    r = master_ops.set_master("resume", file_path=str(md_file), ws=ws)
    assert r["applied"] is False
    assert r["current_hash"] == h
    assert "Casey Sample" in r["diff"] and "Alex Example" in r["diff"]
    assert storage.load_master("resume", ws)[1] == h
    assert not list(ws.backups_dir.glob("resume-*.yaml"))


def test_confirm_with_wrong_proposed_hash_is_rejected(master, md_file):
    ws, _, h = master
    with pytest.raises(ResumeTailorError) as e:
        master_ops.set_master("resume", file_path=str(md_file), confirm=True,
                              expected_hash=h, proposed_hash="0" * 64, ws=ws)
    assert _code(e) == "CONFIRMATION_REQUIRED"
    assert storage.load_master("resume", ws)[1] == h


def test_confirm_with_stale_expected_hash_conflicts(master, md_file):
    ws, _, h = master
    preview = master_ops.set_master("resume", file_path=str(md_file), ws=ws)
    with pytest.raises(ResumeTailorError) as e:
        master_ops.set_master("resume", file_path=str(md_file), confirm=True,
                              expected_hash="f" * 64, proposed_hash=preview["proposed_hash"], ws=ws)
    assert _code(e) == "MASTER_CONFLICT"
    assert storage.load_master("resume", ws)[1] == h


def test_correct_confirm_writes_backs_up_and_matches_proposed_hash(master, md_file):
    ws, _, h = master
    preview = master_ops.set_master("resume", file_path=str(md_file), ws=ws)
    r = master_ops.set_master("resume", file_path=str(md_file), confirm=True,
                              expected_hash=preview["current_hash"],
                              proposed_hash=preview["proposed_hash"], ws=ws)
    assert r["applied"] is True
    assert r["master_hash"] == preview["proposed_hash"]
    assert storage.load_master("resume", ws)[1] == preview["proposed_hash"]
    backups = list(ws.backups_dir.glob("resume-*.yaml"))
    assert len(backups) == 1 and h[:8] in backups[0].name
    # replace never reissues IDs the old master already used
    new_doc, _ = storage.load_master("resume", ws)
    assert new_doc["experience"][0]["id"] == "exp-003"


def _confirm(kind, ws, **kw):
    preview = master_ops.set_master(kind, ws=ws, **kw)
    assert preview["applied"] is False
    return master_ops.set_master(kind, ws=ws, confirm=True, expected_hash=preview["current_hash"],
                                 proposed_hash=preview["proposed_hash"], **kw)


def test_update_preserves_ids_and_migration_and_never_reuses_ids(master):
    ws, doc, h = master
    # give the master migration metadata and a career stage to preserve
    seeded = copy.deepcopy(doc)
    seeded["metadata"]["migration"] = {"source": "legacy_repository", "source_hash": "a" * 64,
                                       "migrated_at": "2026-01-01T00:00:00Z",
                                       "legacy_path": "resources/master_resume.yaml"}
    seeded["metadata"]["career_stage"] = "3-5"
    storage.save_master("resume", seeded, h, "seed", ws)
    current, _ = storage.load_master("resume", ws)
    old_ids = [e["id"] for e in current["experience"]]
    assert old_ids == ["exp-001", "exp-002"]

    edited = copy.deepcopy(current)
    edited.pop("metadata")  # caller omits metadata entirely -- it must still be preserved
    edited["experience"].pop()  # delete exp-002
    edited["experience"][0]["bullets"].append({"text": "Added a new, real bullet."})
    edited["experience"].append({"title": "Consultant", "company": "Delta", "bullets": [{"text": "Advised."}]})
    r = _confirm("resume", ws, resume=edited, mode="update")
    assert r["applied"] is True

    after, _ = storage.load_master("resume", ws)
    assert after["metadata"]["migration"] == current["metadata"]["migration"]
    assert after["metadata"]["career_stage"] == "3-5"
    exp1, new_exp = after["experience"]
    assert exp1["id"] == "exp-001"
    assert [b["id"] for b in exp1["bullets"]] == ["exp-001-b01", "exp-001-b02", "exp-001-b03"]
    assert new_exp["id"] == "exp-003"  # exp-002 was deleted and is not reissued
    assert after["skills"][0]["id"] == current["skills"][0]["id"]


def test_resume_and_cv_are_independent(master, md_file):
    ws, _, h = master
    r = master_ops.set_master("cv", file_path=str(md_file), ws=ws)
    assert r["applied"] is True and r["kind"] == "cv"
    cv, _ = storage.load_master("cv", ws)
    assert cv["metadata"]["kind"] == "cv" and cv["name"] == "Casey Sample"
    assert storage.load_master("resume", ws)[1] == h


@pytest.mark.parametrize("kw", [{"career_stage": "senior-ish"}, {"mode": "merge"}])
def test_bad_career_stage_and_mode_rejected(workspace, md_file, kw):
    with pytest.raises(ResumeTailorError) as e:
        master_ops.set_master("resume", file_path=str(md_file), ws=workspace, **kw)
    assert _code(e) == "MASTER_INVALID"
    assert storage.load_master("resume", workspace)[0] is None


def test_argument_combinations_rejected(workspace, md_file, legacy_master):
    for kw in ({}, {"file_path": str(md_file), "resume": legacy_master},
               {"mode": "update", "file_path": str(md_file)}, {"mode": "update"}):
        with pytest.raises(ResumeTailorError) as e:
            master_ops.set_master("resume", ws=workspace, **kw)
        assert _code(e) == "MASTER_INVALID"
    with pytest.raises(ResumeTailorError) as e:
        master_ops.set_master("letter", resume=legacy_master, ws=workspace)
    assert _code(e) == "INVALID_KIND"


def test_unparsed_blocks_readiness_until_accepted(workspace, legacy_master):
    legacy_master["unparsed"] = ["## Hobbies", "chess"]
    r = master_ops.set_master("resume", resume=legacy_master, career_stage="3-5", ws=workspace)
    assert r["applied"] is True and r["master_ready"] is False and r["unparsed_items"] == 2
    assert "accept_unparsed" in r["note"]
    doc, _ = storage.load_master("resume", workspace)
    assert master_ops.master_readiness(doc) == {"ready": False, "unparsed_items": 2, "unparsed_accepted": False}
    assert doc["metadata"]["career_stage"] == "3-5"

    body = {k: v for k, v in doc.items() if k != "metadata"}
    r2 = _confirm("resume", workspace, resume=body, mode="update", accept_unparsed=True)
    assert r2["master_ready"] is True
    doc2, _ = storage.load_master("resume", workspace)
    assert master_ops.master_readiness(doc2)["ready"] is True
    assert doc2["metadata"]["career_stage"] == "3-5"


def test_update_without_master_is_not_found(workspace, legacy_master):
    with pytest.raises(ResumeTailorError) as e:
        master_ops.set_master("resume", resume=legacy_master, mode="update", ws=workspace)
    assert _code(e) == "MASTER_NOT_FOUND"


def test_preview_is_deterministic(master, md_file):
    ws, _, _ = master
    a = master_ops.set_master("resume", file_path=str(md_file), ws=ws)
    b = master_ops.set_master("resume", file_path=str(md_file), ws=ws)
    assert a["proposed_hash"] == b["proposed_hash"]


def test_readiness_helper():
    assert master_ops.master_readiness({"unparsed": []})["ready"] is True
    assert master_ops.master_readiness({"unparsed": ["x"], "metadata": {"unparsed_accepted": True}})["ready"] is True
