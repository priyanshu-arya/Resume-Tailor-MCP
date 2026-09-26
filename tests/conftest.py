"""Shared fixtures.

Every test runs against a throwaway RESUME_TAILOR_HOME under tmp_path and a
SYNTHETIC master -- never the developer's real master or ~/.resume-tailor.
"""

from __future__ import annotations

import copy

import pytest

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
