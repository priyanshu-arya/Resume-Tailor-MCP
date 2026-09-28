"""Folder-based master discovery and import (R-USER-05..11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib import discovery
from lib import storage
from lib import workspace as wsmod
from lib.errors import ResumeTailorError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _code(excinfo) -> str:
    return excinfo.value.code


# --------------------------------------------------------------------------
# _checked_dir
# --------------------------------------------------------------------------

def test_checked_dir_rejects_missing_folder(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir("/does/not/exist/anywhere")
    assert _code(ei) == "FOLDER_UNSUPPORTED"


def test_checked_dir_rejects_a_file(workspace, candidate_folder):
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir(str(candidate_folder / "alex-resume.md"))
    assert _code(ei) == "FOLDER_UNSUPPORTED"


def test_checked_dir_rejects_inside_app_root(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir(str(workspace.root))
    assert _code(ei) == "FOLDER_UNSUPPORTED"


def test_checked_dir_rejects_app_root_itself(workspace):
    from lib.workspace import app_root
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir(str(app_root()))
    assert _code(ei) == "FOLDER_UNSUPPORTED"


def test_checked_dir_rejects_inside_repo(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir(str(REPO_ROOT / "lib"))
    assert _code(ei) == "FOLDER_UNSUPPORTED"


def test_checked_dir_rejects_repo_root_itself(workspace):
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir(str(REPO_ROOT))
    assert _code(ei) == "FOLDER_UNSUPPORTED"


def test_checked_dir_accepts_a_real_folder(workspace, candidate_folder):
    resolved = discovery._checked_dir(str(candidate_folder))
    assert resolved == candidate_folder.resolve()


def test_checked_dir_error_names_only_basename(workspace, tmp_path):
    missing = tmp_path / "some-private-subdir" / "does-not-exist"
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_dir(str(missing))
    assert "some-private-subdir" not in str(ei.value.message)
    assert "some-private-subdir" not in repr(ei.value.details)


# --------------------------------------------------------------------------
# discover_masters
# --------------------------------------------------------------------------

def test_only_the_named_folder_is_scanned(workspace, candidate_folder, tmp_path):
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "another-resume.md").write_text("# Someone Else\n", encoding="utf-8")
    r = discovery.discover_masters(str(candidate_folder))
    names = [c["filename"] for c in r["candidates"]]
    assert "another-resume.md" not in names


def test_subdirectory_not_entered(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder))
    names = [c["filename"] for c in r["candidates"]]
    assert "sub" not in names
    assert "inner.md" not in names


def test_symlink_entry_skipped(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder))
    names = [c["filename"] for c in r["candidates"]]
    assert "outside-link.md" not in names


def test_unsupported_suffix_not_a_candidate(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder))
    names = [c["filename"] for c in r["candidates"]]
    assert "photo.png" not in names


def test_oversized_file_reported_not_dropped(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder))
    huge = next(c for c in r["candidates"] if c["filename"] == "huge.pdf")
    assert huge["importable"] is False
    assert huge["skip_reason"] == "file too large"


def test_binary_pdf_is_unknown_kind_no_crash(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder))
    scan = next(c for c in r["candidates"] if c["filename"] == "scan.pdf")
    assert scan["kind_guess"] == "unknown"
    assert scan["importable"] is True


def test_filename_signals_beat_content_signals(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder))
    by_name = {c["filename"]: c for c in r["candidates"]}
    assert by_name["alex-resume.md"]["kind_guess"] == "resume"
    assert by_name["alex-resume.md"]["kind_guess_confidence"] == "high"
    # underscore-adjacent "CV" in the filename must still be recognized
    assert by_name["Alex_CV.md"]["kind_guess"] == "cv"


def test_entry_cap_truncates(workspace, tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    for i in range(discovery.MAX_DIR_ENTRIES + 50):
        (folder / f"f{i}.md").write_text("x", encoding="utf-8")
    r = discovery.discover_masters(str(folder))
    assert r["entries_examined"] == discovery.MAX_DIR_ENTRIES
    assert r["truncated"] is True


def test_no_candidates_raises(workspace, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ResumeTailorError) as ei:
        discovery.discover_masters(str(empty))
    assert _code(ei) == "NO_MASTER_CANDIDATES"


def test_no_auto_selection_at_any_count(workspace, candidate_folder, tmp_path):
    r = discovery.discover_masters(str(candidate_folder))
    assert "selected" not in r
    assert r["requires_user_selection"] is True
    for c in r["candidates"]:
        assert "selected" not in c

    one = tmp_path / "one"
    one.mkdir()
    (one / "resume.md").write_text("# Solo Candidate\n", encoding="utf-8")
    r1 = discovery.discover_masters(str(one))
    assert r1["candidate_count"] == 1
    assert r1["requires_user_selection"] is False
    assert "selected" not in r1


def test_kind_hint_never_hides_candidates(workspace, candidate_folder):
    r = discovery.discover_masters(str(candidate_folder), kind="cv")
    names = {c["filename"] for c in r["candidates"]}
    assert "alex-resume.md" in names  # not a cv match, but still shown


def test_output_and_errors_contain_no_full_path(workspace, candidate_folder, tmp_path):
    r = discovery.discover_masters(str(candidate_folder))
    import json
    blob = json.dumps(r)
    assert str(tmp_path) not in blob
    with pytest.raises(ResumeTailorError) as ei:
        discovery.discover_masters(str(tmp_path / "nope"))
    assert str(tmp_path) not in str(ei.value.message)
    assert str(tmp_path) not in repr(ei.value.details)


def test_classify_candidate_table():
    cases = [
        ("resume.pdf", None, "resume"),
        ("cv.pdf", None, "cv"),
        ("Alex_CV.md", None, "cv"),
        ("Alex_Resume.docx", None, "resume"),
        ("curriculum-vitae.md", None, "cv"),
        ("my_curriculum_vitae.pdf", None, "cv"),
        ("random.pdf", None, "unknown"),
        ("random.md", "## Experience\n## Skills\n## Summary\n", "resume"),
        ("random.md", "## Publications\n## Teaching\n## Grants\n", "cv"),
        ("random.md", "kind: cv\nmetadata:\n  kind: cv\n", "cv"),
        ("random.md", "metadata:\n  kind: resume\n", "resume"),
    ]
    for filename, sniff, expected in cases:
        got = discovery.classify_candidate(filename, sniff)
        assert got["kind_guess"] == expected, (filename, sniff, got)
        assert got["reason"]


def test_classify_candidate_is_pure_no_filesystem(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("classify_candidate touched the filesystem")
    monkeypatch.setattr("builtins.open", boom)
    discovery.classify_candidate("resume.pdf", "## Experience\n")


# --------------------------------------------------------------------------
# safe_basename / _checked_child
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "a\x00b", "x" * 300])
def test_safe_basename_rejects(bad):
    with pytest.raises(ResumeTailorError) as ei:
        discovery.safe_basename(bad)
    assert _code(ei) in ("INVALID_ID",)


@pytest.mark.parametrize("good", ["resume.pdf", "My Resume (2).pdf", "Alex_CV.md",
                                  "résumé.docx", "2026 Resume.md"])
def test_safe_basename_accepts_real_filenames(good):
    assert discovery.safe_basename(good) == good


def test_checked_child_rejects_traversal(workspace, candidate_folder):
    directory = discovery._checked_dir(str(candidate_folder))
    for bad in ("../../etc/passwd", "sub/inner.md", "..", "."):
        with pytest.raises(ResumeTailorError):
            discovery._checked_child(directory, bad)


def test_checked_child_rejects_symlink(workspace, candidate_folder):
    directory = discovery._checked_dir(str(candidate_folder))
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_child(directory, "outside-link.md")
    # resolve()-through-the-symlink lands outside `directory`, so the
    # resolve-then-compare-parent guard (the same one that makes
    # workspace.safe_child symlink-proof) fires first, before lstat.
    assert _code(ei) == "PATH_TRAVERSAL"


def test_checked_child_rejects_unsupported_suffix(workspace, candidate_folder):
    directory = discovery._checked_dir(str(candidate_folder))
    with pytest.raises(ResumeTailorError) as ei:
        discovery._checked_child(directory, "photo.png")
    assert _code(ei) == "IMPORT_UNSUPPORTED"


def test_checked_child_accepts_real_file(workspace, candidate_folder):
    directory = discovery._checked_dir(str(candidate_folder))
    p = discovery._checked_child(directory, "alex-resume.md")
    assert p == (candidate_folder / "alex-resume.md").resolve()


# --------------------------------------------------------------------------
# import_master
# --------------------------------------------------------------------------

def test_import_from_folder_saves_master_with_provenance(workspace, candidate_folder):
    r = discovery.import_master(str(candidate_folder), "alex-resume.md", "resume", career_stage="3-5")
    assert r["applied"] is True
    doc, h = storage.load_master("resume", workspace)
    imported = doc["metadata"]["imported"]
    assert imported["source_filename"] == "alex-resume.md"
    assert imported["source_folder_name"] == candidate_folder.name
    assert "source_folder_hash" in imported
    assert "source_hash" in imported
    assert candidate_folder.name not in repr(imported.get("source_folder_hash"))


def test_import_provenance_is_deterministic_across_preview_and_confirm(workspace, candidate_folder):
    # first master already exists via a previous import -- re-import to exercise preview/confirm
    discovery.import_master(str(candidate_folder), "alex-resume.md", "resume")
    preview = discovery.import_master(str(candidate_folder), "Alex_CV.md", "resume")
    assert preview["applied"] is False
    confirmed = discovery.import_master(
        str(candidate_folder), "Alex_CV.md", "resume",
        confirm=True, expected_hash=preview["current_hash"], proposed_hash=preview["proposed_hash"])
    assert confirmed["ok"] is True
    assert confirmed["applied"] is True


def test_import_does_not_modify_the_source_file(workspace, candidate_folder):
    path = candidate_folder / "alex-resume.md"
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    discovery.import_master(str(candidate_folder), "alex-resume.md", "resume")
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime


def test_import_yaml_candidate(workspace, candidate_folder):
    discovery.import_master(str(candidate_folder), "alex-resume.md", "resume")
    doc, h = storage.load_master("resume", workspace)
    yaml_text = storage.dump_yaml(doc)
    (candidate_folder / "exported.yaml").write_text(yaml_text, encoding="utf-8")

    ws2_info = wsmod.initialize_workspace(create_new=True)
    ws2 = wsmod.get_workspace()
    r = discovery.import_master(str(candidate_folder), "exported.yaml", "resume", ws=ws2)
    assert r["applied"] is True
    doc2, h2 = storage.load_master("resume", ws2)
    assert doc2["name"] == doc["name"]


def test_import_yaml_candidate_strips_untrusted_metadata(workspace, candidate_folder):
    import lib.locking as lk
    forged = {
        "name": "Forged Person",
        "metadata": {"kind": "resume", "career_stage": "director", "id_counters": {"exp-": 9999}},
    }
    lk.atomic_write_yaml(candidate_folder / "forged.yaml", forged)
    r = discovery.import_master(str(candidate_folder), "forged.yaml", "resume")
    assert r["applied"] is True
    doc, h = storage.load_master("resume", workspace)
    # id_counters is server-derived, never trusted from a candidate file
    assert doc["metadata"].get("id_counters", {}).get("exp-") != 9999
    # career_stage was NOT requested by the caller, so it must not come from
    # the stripped file metadata either
    assert doc["metadata"].get("career_stage") != "director"


def test_import_requires_all_three_arguments():
    import inspect
    sig = inspect.signature(discovery.import_master)
    for name in ("folder", "filename", "kind"):
        assert sig.parameters[name].default is inspect.Parameter.empty


def test_import_rejects_tailored_version_dict(workspace, candidate_folder):
    import lib.locking as lk
    version_like = {
        "name": "Alex",
        "metadata": {"kind": "resume", "version_id": "v1", "released": True},
    }
    lk.atomic_write_yaml(candidate_folder / "version.yaml", version_like)
    with pytest.raises(ResumeTailorError) as ei:
        discovery.import_master(str(candidate_folder), "version.yaml", "resume")
    assert _code(ei) == "MASTER_INVALID"
    assert "version_fields" in ei.value.details
