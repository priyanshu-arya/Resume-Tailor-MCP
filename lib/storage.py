"""File-system storage helpers for the structured resume data.

Everything is kept as YAML so it's human-readable and diffable with plain
`git diff` / `cat` if you want to inspect it. `resources/` holds
source-of-truth reference material (the master resume, the layout
templates) fetched fresh on every tool call; `data/` holds generated,
mutable state (tailored versions, saved JDs, exports).
"""

from pathlib import Path
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = ROOT_DIR / "data"
VERSIONS_DIR = BASE_DIR / "versions"
JD_DIR = BASE_DIR / "jd_history"

# Resume and CV are two separate canonical master documents -- never mix
# their content. Each is its own YAML file under resources/.
MASTER_PATHS = {
    "resume": ROOT_DIR / "resources" / "master_resume.yaml",
    "cv": ROOT_DIR / "resources" / "master_cv.yaml",
}
MASTER_PATH = MASTER_PATHS["resume"]  # backward-compat alias for existing callers

# Aliases accepted by load_version for reading a master document by its
# conventional "version" name (e.g. so export_resume(version="master-cv")
# works the same way exporting a tailored version does).
_MASTER_VERSION_ALIASES = {
    "master": "resume",
    "master-resume": "resume",
    "master_resume": "resume",
    "master-cv": "cv",
    "master_cv": "cv",
}


def ensure_dirs() -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    JD_DIR.mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path):
    path = Path(path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)


def _master_path(kind: str) -> Path:
    try:
        return MASTER_PATHS[kind]
    except KeyError:
        raise ValueError(f"Unknown master kind '{kind}' -- use 'resume' or 'cv'.") from None


def load_master(kind: str = "resume"):
    return load_yaml(_master_path(kind))


def save_master(data, kind: str = "resume") -> None:
    save_yaml(_master_path(kind), data)


def version_path(version_id: str) -> Path:
    safe_id = version_id.strip().replace("/", "-")
    return VERSIONS_DIR / f"{safe_id}.yaml"


def load_version(version_id: str):
    if version_id in (None, ""):
        return load_master("resume")
    kind = _MASTER_VERSION_ALIASES.get(version_id)
    if kind:
        return load_master(kind)
    return load_yaml(version_path(version_id))


def save_version(version_id: str, data) -> None:
    save_yaml(version_path(version_id), data)


def list_version_ids():
    ensure_dirs()
    return sorted(p.stem for p in VERSIONS_DIR.glob("*.yaml"))


def jd_path(jd_id: str) -> Path:
    safe_id = jd_id.strip().replace("/", "-")
    return JD_DIR / f"{safe_id}.yaml"


def save_jd(jd_id: str, jd_text: str, extracted: dict | None = None) -> None:
    ensure_dirs()
    save_yaml(jd_path(jd_id), {"jd_text": jd_text, "extracted": extracted or {}})


def load_jd(jd_id: str):
    return load_yaml(jd_path(jd_id))


def list_jd_ids():
    ensure_dirs()
    return sorted(p.stem for p in JD_DIR.glob("*.yaml"))
