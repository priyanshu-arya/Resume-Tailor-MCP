"""Phase 8.4: repo hygiene guarantees, pure and fast -- no workspace needed.

[ ] nothing personal is tracked by git
[ ] no tracked text file contains a PII sentinel
[ ] the README template table matches the registry
[ ] every gitignore-relevant personal path is actually ignored
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Scoped to the concrete finding: a real personal email address landing in
# a tracked file (as _to_delete/master.yaml.stray's did before it was
# removed). Broader heuristics (a bare name, a loose phone-number shape)
# false-positive constantly on this codebase's own docs/attribution/test
# fixtures (repo URLs, docstring examples, synthetic hashes) -- narrower is
# what's actually reliable here.
PII_PATTERNS = [
    re.compile(r"[A-Za-z0-9._%+-]+@gmail\.com"),
]

# Files allowed to contain PII-*shaped* strings because they're synthetic
# fixtures, documentation about the sentinel itself, or this test file.
ALLOWLIST = {
    "tests/conftest.py",  # synthetic fixture data (alex.example@example.test etc.)
    "tests/test_repo_hygiene.py",
    "tests/test_audit.py",  # PII corpus used to prove redaction, not real PII
    "tests/test_diagnostics.py",
}

TEXT_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".cfg", ".ini", ".toml"}


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout


def _tracked_files() -> list[str]:
    return [line for line in _git("ls-files").splitlines() if line]


def test_no_personal_data_directory_is_tracked():
    tracked = _tracked_files()
    personal_roots = ("data/evidence/", "data/releases/", "data/tailoring_sessions/",
                      "data/versions/", "data/jd_history/", "data/exports/", "output/", "_to_delete/")
    leaked = [f for f in tracked if f.startswith(personal_roots) and not f.endswith(".gitkeep")]
    assert leaked == []


def test_no_tracked_text_file_contains_a_pii_sentinel():
    offenders = []
    for rel in _tracked_files():
        if rel in ALLOWLIST:
            continue
        path = REPO / rel
        if path.suffix not in TEXT_EXTENSIONS or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in PII_PATTERNS:
            if pattern.search(text):
                offenders.append((rel, pattern.pattern))
    assert offenders == [], offenders


def test_readme_template_table_matches_the_registry():
    from lib import templates as _templates
    registry_ids = {t["id"] for t in _templates.list_templates()}
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    table_ids = set(re.findall(r"^\| `([a-z0-9-]+)` \|", readme, re.MULTILINE))
    assert table_ids == registry_ids, (table_ids ^ registry_ids)


@pytest.mark.parametrize("relative_path", [
    "data/evidence/x.yaml", "data/releases/x.json", "data/tailoring_sessions/x.yaml",
    "data/versions/x.yaml", "data/exports/x.pdf", "data/jd_history/x.yaml",
    "_to_delete/x.txt", "output/x.pdf", ".obsidian/x.json",
])
def test_personal_paths_are_git_ignored(relative_path):
    """A file inside each personal directory must be ignored -- the
    directory entry itself may be deliberately un-ignored (via `!/data/*/`)
    so a .gitkeep can hold the directory in git without tracking its
    contents."""
    result = subprocess.run(["git", "check-ignore", relative_path], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, f"{relative_path} is NOT gitignored"


def test_to_delete_directory_does_not_exist():
    """The stray PII file (_to_delete/master.yaml.stray) was removed, not
    gitignored -- gitignoring it would have left real PII sitting in the
    working tree indefinitely."""
    assert not (REPO / "_to_delete").exists()
