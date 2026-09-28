"""Shared fixtures.

Every test runs against a throwaway RESUME_TAILOR_HOME under tmp_path and a
SYNTHETIC master -- never the developer's real master or ~/.resume-tailor.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# Consolidated in one place (Phase 6): four files used to each re-derive
# tectonic availability their own way. HAS_TECTONIC/needs_tectonic are the
# one source of truth. needs_tectonic applies the *named* marker (registered
# in pytest.ini) rather than a bare skipif, so `pytest -m needs_tectonic -rs`
# can select exactly the gated tests and prove they ran (not silently
# skipped) rather than just filtering by a condition with no name of its own.
HAS_TECTONIC = (REPO_ROOT / "bin" / "tectonic").exists() or bool(shutil.which("tectonic"))
needs_tectonic = pytest.mark.needs_tectonic


def pytest_collection_modifyitems(config, items):
    if HAS_TECTONIC:
        return
    skip = pytest.mark.skip(reason="tectonic not available")
    for item in items:
        if "needs_tectonic" in item.keywords:
            item.add_marker(skip)

SYNTHETIC_LEGACY_MASTER = {
    "name": "Alex Example",
    "contact": {
        "email": "alex.example@example.test",
        "phone": "+1 555 0100",
        "linkedin": "linkedin.com/in/alexexample",
        "github": "github.com/alexexample",
        "location": "Austin, TX",
    },
    "summary": "Backend engineer with 4 years building Python services and data pipelines on AWS.",
    "skills": [
        {"category": "Languages", "items": ["Python", "SQL", "Go"]},
        {"category": "Cloud", "items": ["AWS", "Docker"]},
    ],
    "experience": [
        {
            "title": "Software Engineer", "company": "Acme Corp", "location": "Remote",
            "start": "Jan 2022", "end": "Present",
            "bullets": [
                {"text": "Built 18 Python/Django REST endpoints, reducing median API latency 42%."},
                {"text": "Led migration of nightly ETL jobs to Airflow on AWS, serving 3 product teams."},
            ],
        },
        {
            "title": "Software Engineering Intern", "company": "Beta Labs", "location": "Austin, TX",
            "start": "May 2021", "end": "Aug 2021",
            "bullets": [{"text": "Implemented unit tests with pytest for a billing service."}],
        },
    ],
    "education": [{"degree": "B.S. Computer Science", "school": "Example University", "year": "2021"}],
    "projects": [
        {"name": "rag-notes", "stack": "Python, LangChain",
         "bullets": [{"text": "Created a retrieval-augmented note search tool over 2,000 personal notes."}]},
    ],
    "certifications": ["AWS Certified Developer (2024)"],
    "unparsed": [],
}


@pytest.fixture
def legacy_master() -> dict:
    return copy.deepcopy(SYNTHETIC_LEGACY_MASTER)


@pytest.fixture
def rt_home(tmp_path, monkeypatch):
    """Point the app root at an empty temp dir (no workspace yet)."""
    home = tmp_path / "rt-home"
    monkeypatch.setenv("RESUME_TAILOR_HOME", str(home))
    return home


@pytest.fixture
def workspace(rt_home):
    """An initialized, empty workspace."""
    from lib import workspace as wsmod
    wsmod.initialize_workspace()
    return wsmod.get_workspace()


@pytest.fixture
def master(workspace, legacy_master):
    """Workspace with a normalized synthetic master resume saved.
    Returns (workspace, master_dict, master_hash)."""
    from lib import storage
    from lib.ids import normalize_master
    doc = normalize_master(legacy_master, "resume")
    h = storage.save_master("resume", doc, None, "test fixture", ws=workspace)
    return workspace, doc, h


@pytest.fixture
def two_workspaces(rt_home, legacy_master):
    """Two initialized workspaces in one RESUME_TAILOR_HOME. A holds a saved
    synthetic master; B is created second and left active (and empty).
    Returns (id_a, id_b)."""
    from lib import storage
    from lib import workspace as wsmod
    from lib.ids import normalize_master

    a = wsmod.initialize_workspace()
    ws_a = wsmod.get_workspace()
    doc = normalize_master(legacy_master, "resume")
    storage.save_master("resume", doc, None, "test fixture", ws=ws_a)

    b = wsmod.initialize_workspace(create_new=True)
    return a["workspace_id"], b["workspace_id"]


@pytest.fixture
def candidate_folder(tmp_path):
    """One folder, covering every discovery branch:
       alex-resume.md      importable, kind_guess resume (filename)
       Alex_CV.md           importable, kind_guess cv (filename, underscore-adjacent)
       notes.txt            importable, kind_guess unknown
       scan.pdf              importable, unknown (binary: no sniff possible)
       huge.pdf               >5 MB -> importable=False, skip_reason set
       photo.png                not a candidate at all (unsupported suffix)
       sub/                       a subdirectory -> never entered
       outside-link.md               symlink to a file outside -> skipped
    Returns the folder Path."""
    folder = tmp_path / "candidates"
    folder.mkdir()
    (folder / "alex-resume.md").write_text(
        "# Alex Example\n\n## Summary\nBackend engineer.\n\n## Experience\n### Role, Co\n- did stuff\n",
        encoding="utf-8")
    (folder / "Alex_CV.md").write_text(
        "# Alex Example\n\n## Publications\n- A paper.\n", encoding="utf-8")
    (folder / "notes.txt").write_text("just some unrelated notes", encoding="utf-8")
    (folder / "scan.pdf").write_bytes(b"%PDF-1.4\n%not a real pdf but has the right suffix\n")
    with open(folder / "huge.pdf", "wb") as f:
        f.truncate(6 * 1024 * 1024)  # sparse; 6 MB > MAX_IMPORT_BYTES
    (folder / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (folder / "sub").mkdir()
    (folder / "sub" / "inner.md").write_text("# Should never be seen\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (folder / "outside-link.md").symlink_to(outside)
    return folder


@pytest.fixture
def repo_snapshot():
    """{path: st_mtime_ns} for every file under the repo except the usual
    caches/build artifacts and known local-state directories. Compare after
    running a full workflow to prove nothing was written into the repo."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", "bin", "output",
                ".obsidian", "_to_delete", "_incoming"}
    skip_data_prefixes = ("data/versions", "data/jd_history", "data/exports")

    def snapshot() -> dict:
        result = {}
        for p in repo_root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(repo_root)
            if set(rel.parts) & skip_dirs:
                continue
            rel_str = str(rel)
            if any(rel_str.startswith(pref) for pref in skip_data_prefixes):
                continue
            result[rel_str] = p.stat().st_mtime_ns
        return result

    return snapshot
