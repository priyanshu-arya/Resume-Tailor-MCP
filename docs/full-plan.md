# Resume Tailor MCP — 8-phase plan (v2 → production)

## Context

Two specs were handed over: a detailed **user-identification / workspace-binding / master-discovery**
requirement (R-USER-01..17 + an 18-item Definition of Done), and an **8-phase development roadmap**
that orders the remaining work. This plan covers all eight phases in executable detail.

The v2 branch already has the hard parts: workspace isolation under `$RESUME_TAILOR_HOME`,
evidence-gated tailoring, structured patches with provenance, a template contract, a release gate that
compiles and measures the real PDF, and a PII-whitelisted audit log. 728 tests pass across 26 files.
So the job is **not** new architecture. It is: close the identity gap the first spec describes, prove the
existing enforcement with negative tests, and fix the specific holes found while reading the code.

Three findings drive the priority order. All three were verified directly, not inferred:

1. **Deleting `config.yaml` mints a brand-new empty workspace and silently orphans the user's master.**
   `lib/workspace.py:195-218` — `_read_config()` returns `{}`, so `existing` is falsy and the
   "refuse to silently pick another one" guard at `:202` never fires. This is the exact failure the
   identity spec exists to prevent.
2. **A hollowed-out `templates.yaml` lets a release pass with the page-size, margin, font and
   heading contract checks silently unenforced.** Contract fields set to `"unknown"` degrade to
   `not_available`, and `ValidationReport.passed` is `not critical_failures` where
   `blocking = status == "fail"` (`lib/schemas.py:330-345`). Only the hardcoded floors survive.
3. **A tailored version can become the master.** `set_master_resume(resume=<version dict>)` passes:
   `MasterMetadata` is `_Open` (`extra="allow"`, `lib/schemas.py:173`), `ids.normalize_master` forces
   `metadata.kind`, and `version_id`/`released`/`source_master_hash` all survive validation. DoD item
   "previous tailored versions cannot become master" is currently unenforced.

Also confirmed: the README's Templates table names three template IDs that do not exist
(`executive-brief`, `academic-research`, `creative-tech` — the registry has `full-stack-modern`,
`student-achievements`, `generic-minimal`, `metrics-driven`), the README architecture tree omits the
`workspaces/<id>/` level entirely, `_to_delete/master.yaml.stray` holds real PII in the repo working
tree, and `data/evidence/`, `data/releases/`, `data/tailoring_sessions/` and `.obsidian/` are **not**
gitignored (`git check-ignore` returns nothing for them).

**Guiding rule, unchanged from `CLAUDE.md`:** every new guarantee is Python enforcement first, prose
second. A rule that names no enforcement point is a rule nobody can test.

---

## Phase 0 — Freeze the baseline

No code changes. Goal: a reproducible statement of what works today, so later regressions are provable.

**Create** `docs/baseline.md`, `docs/spec-map.md`.

### Tasks

1. Environment fingerprint: `git rev-parse HEAD`, `git status --porcelain`, `python -V`,
   `pip freeze`, `bin/tectonic --version`, `python -c "from lib.validators import pdf;
   print(pdf.detect_pdf_backend())"`. Note: `bin/tectonic` **is** present on this machine (53 MB,
   gitignored) and both pypdf and pdfplumber resolve, so PDF/release tests run here and skip only on a
   fresh clone. Record **both** numbers.
2. Run the suite three ways and record each verbatim: `pytest` (full), `pytest -q -rs` (skip reasons),
   and a "fresh clone" profile with the four tectonic-gated files ignored.
3. Enumerate the MCP surface programmatically via `asyncio.run(server.mcp.list_tools())` etc.
   Current: **19 tools, 2 prompts, 3 static resources, 6 resource templates.**
4. Manual end-to-end smoke run. **Safety gate first:** `~/.resume-tailor/config.yaml` binds a real
   workspace and `resources/master_resume.yaml` is real — never smoke-test against either.
   `export RESUME_TAILOR_HOME="$(mktemp -d)"` then run initialize → set master → analyze → evidence →
   tailor → validate → release → export, recording each result and any error code.
5. Two deliberate negative probes, because they are Phase 1's starting point:
   - No master in a fresh home → record the exact `MASTER_NOT_FOUND` text (Phase 1 rewrites it).
   - `rm config.yaml; initialize_workspace()` → record that it mints a new workspace (defect D-1).
6. Write `docs/baseline.md` with sections: how this was produced · environment · test baseline (both
   profiles + per-file table + warnings verbatim) · current working features (each README claim marked
   verified/partial/unverified with file:line) · MCP inventory · template behavior · PDF behavior ·
   known limitations (README:325-334 verbatim, each annotated still-true/stale) · **known defects
   found while freezing** · doc drift · smoke transcript.

### Defects to record in the baseline (all verified)

| ID | Defect | Location |
|---|---|---|
| D-1 | Deleting `config.yaml` mints a new workspace, orphaning data | `lib/workspace.py:195-218` |
| D-2 | `set_master_resume(resume=<version dict>)` promotes a version to master | `lib/schemas.py:173` + `lib/ids.py:64` |
| D-3 | `analyze_tailoring_requirements` doesn't check master readiness, so an unparsed master only fails at `tailor_resume` — after every evidence question has been answered | `lib/evidence.py:145` vs `lib/tailoring.py:132-137` |
| D-4 | `WorkflowMetadata` has no `source_master_hash`; a workflow that never tailors records no master identity | `lib/schemas.py:381-396` |
| D-5 | `master/history.yaml` is written on every save and read by nothing | `lib/storage.py:144-148` |
| D-6 | `skill-<slug>` IDs are content-derived and excluded from `id_counters`; renaming a skill breaks older versions' refs | `lib/ids.py:96` |
| D-7 | `MigrationInfo.legacy_path` stores a full filesystem path in master metadata, which `get_master_resume` returns to the model | `lib/schemas.py:167` |
| D-8 | A hollowed `templates.yaml` lets release pass with contract checks `not_available` | `lib/schemas.py:330-345` |
| D-9 | `server.py:105` logs `repair_attempt` as `1 if repair_of else 0` — a bool in an int field, so a 3rd repair is indistinguishable from a 1st | `server.py:105` vs `lib/tailoring.py:207` |
| D-10 | A missing released `pdf`/`tex` raises `FileNotFoundError` → `INTERNAL_ERROR` | `lib/release.py:307` |
| D-11 | `lib/export.py:27-28` raises a bare `ValueError` for an unknown template id → `INTERNAL_ERROR` | `lib/export.py:27` |

### The dangling `spec §N` references

40 references across 28 files, spanning §4–§69, citing a document that is not in the repo.
**Recommendation: reconstruct an index (`docs/spec-map.md`); do not strip or reword the references.**
Stripping touches 28 files for zero functional gain and loses the grouping information (`§13-14, §37,
§60` on `tailoring.py` says those clauses are one topic). Build it from
`grep -rno 'spec §[0-9][0-9, §-]*' lib server.py resources`, one row per clause:
`§N | topic | authoritative file(s)`. The table's preamble states the rule: where a clause is cited,
**that file is the specification for it**. New code cites `docs/spec-map.md` + a requirement id
(`R-USER-NN`), never a new `§N`.

**Done when:** both docs exist, every `§` in the grep output appears in the map, `git status` shows
only those two files, and re-running the full suite reproduces the recorded counts.

---

## Phase 1 — Workspace identity, binding, master discovery

The big one. This is the first spec, implemented as server-side Python.

### Design decisions

**D1 — State names in `lib/workspace.py`, the state machine in a new `lib/resolve.py`.**
`workspace.py` imports only `lib.errors` + `lib.schemas` and **must not** import `storage`
(storage imports workspace → cycle). Deciding `WORKSPACE_NEEDS_SETUP` requires loading and
schema-validating a master, which is `storage` + `master_ops`.

**D2 — A new `get_workspace_status()` tool; `get_workspace()` is unchanged.** `get_workspace()`
*raises* `WORKSPACE_NOT_INITIALIZED` and 79 workspace tests plus `tests/test_server.py` depend on that.
The first-run path needs a *state*, not an error, so Claude can ask rather than repair.

**D3 — No binding but workspaces exist → `WORKSPACE_AMBIGUOUS`,** enforced in `get_workspace()` **and**
`initialize_workspace()`, not only in the new tool. This is the D-1 fix. An empty home still yields
`WORKSPACE_NOT_INITIALIZED`, so existing tests keep passing.

**D4 — `select_workspace` gets no `confirm` flag.** A preview would carry almost nothing (`_describe`
exposes only `masters_present`, deliberately no name). Real enforcement is structural: workflows,
evidence and versions live per workspace directory, so a mid-session switch makes `load_workflow` fail.
Add a belt-and-braces `workspace_id` assertion there, audit switches in **both** workspaces, and state
the user-initiated-only rule as R-USER-14.

**D5 — `import_master_from_folder(folder, filename, kind)`: all three required.** This makes
auto-selection structurally impossible, which is stronger than a `MULTIPLE_CANDIDATES` error.
Therefore **do not add that error code** — with `filename` required there is no reachable call site,
and `ResumeTailorError` rejects unregistered codes, so a dead code is pure misdirection.

**D6 — `ImportInfo` carries no timestamp; the import time goes to `master/history.yaml`.**
This is the trap in the spec. `sha256_of(doc)` hashes `metadata`, and the preview/confirm protocol
requires `sha256_of(rebuild())` on call 2 to equal call 1's `proposed_hash`. A wall-clock
`imported_at` inside the hashed document makes every confirm fail with `CONFIRMATION_REQUIRED`.
(`MigrationInfo.migrated_at` only works because `migrate_legacy` bypasses `set_master` entirely.)
Keeping `metadata.imported` deterministic from (file bytes, folder, filename) and putting the *time*
in `history.yaml` also gives D-5's orphaned file a job.

**D7 — Store `source_folder_name` + `source_folder_hash`, not the full path.**
`get_master_resume` returns `metadata` to the model, so a full path leaks the OS username into LLM
context. The folder is never re-read, so the path has no functional use; a sha256 still proves two
imports came from the same folder. `MigrationInfo.legacy_path` is the inconsistent precedent — recorded
as D-7, left alone here to avoid breaking `tests/test_migration.py`.

**D8 — `.yaml`/`.yml` are discoverable and importable** via a new `DISCOVERY_SUFFIXES` plus a `.yaml`
branch in `parse_resume_file`. A YAML file in a user's folder is most likely a master exported from
this tool; refusing it pushes users to paste YAML into `resume=`, which is the bypass Phase 2 closes.
`safe_yaml` already rejects anchors/aliases/python tags and caps depth 20 / size 2 MB (tighter than
`MAX_IMPORT_BYTES` — say so in the error). Leave `ALLOWED_SUFFIXES` unchanged so
`tests/test_parsing_limits.py:52` keeps passing.

### Ordered tasks

1. `lib/errors.py` — register 4 codes.
2. `lib/schemas.py` — `ImportInfo`, `MasterMetadata.imported`, `WorkflowMetadata.source_master_hash`.
3. `lib/workspace.py` — state names, config lock, `existing_workspace_ids`, `list_workspaces`,
   `select_workspace`, `initialize_workspace(create_new=…, label=…)`, the `WORKSPACE_AMBIGUOUS` guards.
4. **`lib/resolve.py`** (new) — `resolve_workspace_state`, `master_status`, `require_tailorable_master`.
5. `lib/parsing.py` — `.yaml` import branch + `YAML_SUFFIXES`.
6. **`lib/discovery.py`** (new) — `_checked_dir`, `safe_basename`, `_checked_child`,
   `classify_candidate`, `discover_masters`, `import_master`.
7. `lib/master_ops.py` — strip server-owned metadata from caller input; reject version documents;
   stamp `imported`; carry it forward on `mode="update"`.
8. `lib/storage.py` — richer `MASTER_NOT_FOUND`; import fields in the history record;
   `read_master_history`.
9. `lib/evidence.py` + `lib/tailoring.py` — both route through `resolve.require_tailorable_master`;
   record and verify the workflow master hash (fixes D-3, D-4).
10. `lib/workflows.py` — assert `workspace_id` on load; store `source_master_hash`.
11. `lib/audit.py` — 7 events, 7 allowed fields.
12. `server.py` — 5 new tools + `initialize_workspace` params + `_success_events` wiring.
13. Docs — `CLAUDE.md` (R-USER-01..17), both `SKILL.md` files, README architecture tree.
14. Tests — unit → integration → acceptance.

### New error codes (`lib/errors.py`)

```python
"WORKSPACE_AMBIGUOUS": "WORKFLOW",   # workspaces exist on disk, none is bound
"WORKSPACE_NOT_FOUND": "WORKFLOW",   # select_workspace(<id>) and that dir does not exist
"FOLDER_UNSUPPORTED": "SOURCE",      # not a dir / unreadable / inside app_root or the repo
"NO_MASTER_CANDIDATES": "SOURCE",    # the folder was scanned and held nothing importable
```

`MASTER_NOT_READY` is **not** added — `lib/tailoring.py:135` already raises `MASTER_INVALID` for
unparsed masters and `tests/test_source_truth.py:160` asserts it. Reuse it.

### New schema models (`lib/schemas.py`)

```python
class ImportInfo(BaseModel):
    """Where a master was imported from. Deterministic by design: no timestamp,
    because metadata is inside sha256_of(doc) and the preview/confirm protocol
    rebuilds the candidate. The import *time* lives in master/history.yaml."""
    source: Literal["user_folder"] = "user_folder"
    source_filename: str          # basename only
    source_folder_name: str       # basename of the folder only
    source_folder_hash: str       # sha256 of the resolved absolute folder path
    source_hash: str              # sha256 of the imported file's bytes

class MasterMetadata(_Open):
    ...
    imported: ImportInfo | None = None          # NEW

class WorkflowMetadata(_Open):
    ...
    source_master_hash: str | None = None       # NEW (Phase 2 item, needed here)
```

### `lib/workspace.py` additions

```python
WORKSPACE_ID_RE = re.compile(r"RT-[0-9A-F]{8}")
MAX_WORKSPACES_LISTED = 200

NO_WORKSPACE = "NO_WORKSPACE"; WORKSPACE_FOUND = "WORKSPACE_FOUND"
WORKSPACE_INVALID = "WORKSPACE_INVALID"; WORKSPACE_NEEDS_SETUP = "WORKSPACE_NEEDS_SETUP"
RESOLUTION_STATES = (NO_WORKSPACE, WORKSPACE_FOUND, WORKSPACE_INVALID, WORKSPACE_NEEDS_SETUP)

def config_lock_path() -> Path: ...        # app_root()/".config.lock"
def _update_config(**changes) -> dict: ...  # read-modify-write under that lock, preserving
                                            # unknown keys; only from write paths (get_workspace
                                            # must still create nothing)
def existing_workspace_ids() -> list[str]:
    """Workspace dirs that physically exist. Never reads config.yaml. Symlinked
    entries and names failing validate_id are skipped, so a planted directory can
    never widen what the server will bind to. Sorted, capped."""

def list_workspaces() -> dict:
    """{"workspaces": [{workspace_id, label, created_at, active, masters_present,
    id_format_ok}], "active_workspace_id", "count", "truncated", "note"}.
    Reads nothing inside a workspace but the presence of master/{resume,cv}.yaml."""

def select_workspace(workspace_id: str) -> dict:
    """Bind to an EXISTING workspace. Creates nothing, copies nothing.
    INVALID_ID/PATH_TRAVERSAL via safe_child; WORKSPACE_NOT_FOUND if absent —
    never a fallback. Returns _describe(...) + previous_workspace_id + switched."""

def initialize_workspace(create_new: bool = False, label: str | None = None) -> dict:
    """create_new=False (default, unchanged except the new branch):
         bound + exists      -> returned unchanged (created=False)
         bound + gone        -> WORKSPACE_NOT_INITIALIZED
         no binding, dirs on disk -> WORKSPACE_AMBIGUOUS  (NEW: was "mint a new one")
         no binding, nothing -> create the first one
       create_new=True: an ADDITIONAL workspace, refused while the bound one is
       missing so it can never be used to walk away from a broken binding.
       Never set implicitly by any code path."""
```

`get_workspace()` gains one branch — when there is no active id but `existing_workspace_ids()` is
non-empty, raise `WORKSPACE_AMBIGUOUS` whose message says: *"Refusing to guess which one is yours:
call list_workspaces and ask the user, then select_workspace(workspace_id). Do NOT create a new
workspace — that would orphan existing data."* with `details={"available_workspace_ids": [...]}`.

`config.yaml` grows a backward-compatible `workspaces: {<id>: {created_at, label}}` map; absence
falls back to the directory scan, which stays authoritative for existence.

### `lib/resolve.py` (new)

```python
def resolve_workspace_state() -> dict:
    """Never raises for a missing, ambiguous or broken binding — it reports the
    state so the caller can ASK instead of repairing.
    {state, reason, workspace_id, root, available_workspace_ids,
     masters: {resume: <master_status>, cv: ...}, next_step, refuse}"""

def master_status(ws, kind) -> dict:
    """{present, valid, ready, master_hash, career_stage, unparsed_items,
    imported, invalid_reason}. Schema-validates without repairing; an invalid
    master is reported, never rewritten."""

def require_tailorable_master(kind, ws=None) -> tuple[dict, str]:
    """require_master + the readiness gate, so analyze and tailor fail
    identically (fixes D-3)."""
```

State table — this is also the test matrix:

| Config | Disk | Master | state | reason |
|---|---|---|---|---|
| no active id | no dirs | — | `NO_WORKSPACE` | `None` |
| no active id | ≥1 dir | — | `WORKSPACE_INVALID` | `no_active_binding` |
| unreadable/unsafe YAML | — | — | `WORKSPACE_INVALID` | `unreadable_config` |
| id fails `validate_id` | — | — | `WORKSPACE_INVALID` | `invalid_workspace_id` |
| id ok | dir missing | — | `WORKSPACE_INVALID` | `missing_workspace_dir` |
| id ok | dir ok | neither file | `WORKSPACE_NEEDS_SETUP` | `no_master` |
| id ok | dir ok | present, fails `MasterDocument` | `WORKSPACE_NEEDS_SETUP` | `master_invalid` |
| id ok | dir ok | valid, `unparsed` outstanding | `WORKSPACE_FOUND` | `None` (+ `ready: False`) |
| id ok | dir ok | valid + ready | `WORKSPACE_FOUND` | `None` |

An unparsed master is a **valid** master → `WORKSPACE_FOUND` with `ready: false`, not `NEEDS_SETUP`;
readiness is a tailoring gate, not a workspace state. `next_step` is where the core rule is enforced
server-side, e.g. for `NO_WORKSPACE`: *"Ask the user for the path of ONE folder containing their
existing resume or CV, then call discover_masters(folder). If they have no file, run the
create-master-file skill. Do not reconstruct their resume from memory, from this conversation, or from
any other workspace, and do not search the filesystem."*

### `lib/discovery.py` (new)

```python
MAX_DIR_ENTRIES = 500      # entries examined before the scan truncates
MAX_CANDIDATES  = 20
SNIFF_BYTES     = 4096     # text formats only; never .pdf/.docx
YAML_SUFFIXES      = (".yaml", ".yml")
DISCOVERY_SUFFIXES = parsing.ALLOWED_SUFFIXES + YAML_SUFFIXES

def folder_token(resolved: Path) -> str:
    """sha256 of the resolved absolute path, first 16 hex. Stable, non-reversible."""

def _checked_dir(folder: str) -> Path:
    """Vet before a single entry is listed. Sibling of parsing._checked_path:
    expanduser; resolve(strict=True); S_ISDIR; readable; NOT inside app_root()
    (a workspace is not an import source) and NOT inside this repository.
    Every failure is FOLDER_UNSUPPORTED naming only the folder basename."""

def safe_basename(name: str) -> str:
    """Accepts a real filename — spaces, parens, unicode — while refusing empty,
    '.', '..', any separator, NUL/control chars, >255 bytes. Deliberately NOT
    workspace.validate_id, which rejects 'My Resume (2).pdf'."""

def _checked_child(directory: Path, filename: str) -> Path:
    """safe_basename, then resolve-and-assert-parent (the same trick that makes
    safe_child symlink-proof), then lstat + S_ISREG so a symlink is never opened,
    then parsing._checked_path for suffix and size."""

def classify_candidate(filename: str, sniff: str | None) -> dict:
    """Pure, so the guessing rules are unit-testable.
    {kind_guess, reason, confidence, score}. First match wins and `reason` always
    names the signal: (1) sniffed metadata.kind; (2) filename \\bcv\\b /
    curriculum vitae; (3) filename \\bresum[eé]\\b; (4) CV-only headings
    (publications/teaching/grants); (5) resume headings; (6) unknown — and for
    .pdf/.docx the reason states 'binary format: filename is the only signal'."""

def discover_masters(folder: str, kind: str | None = None) -> dict:
    """ONE folder, one level. Ranked candidates, NEVER a selection.
    Subdirectories and symlinked entries skipped (lstat + S_ISREG); at most
    MAX_DIR_ENTRIES examined with `truncated` saying so; oversized/unreadable
    files RETURNED with importable=False and a skip_reason rather than silently
    dropped (so "nothing found" is never a lie); zero candidates raises
    NO_MASTER_CANDIDATES; no `selected` field at any count. Ranking: score desc,
    mtime desc, filename asc."""

def import_master(folder, filename, kind, *, career_stage=None, accept_unparsed=False,
                  expected_hash=None, confirm=False, proposed_hash=None, ws=None) -> dict:
    """Copy-in import of ONE named file as ONE named kind — all three required.
    Vets folder + filename, then delegates to master_ops.set_master(file_path=…)
    so validation, preview/confirm, backup and history are byte-for-byte identical
    to every other master write. The source is never modified or re-read."""
```

### `lib/master_ops.py` changes

```python
_SERVER_OWNED_METADATA = ("migration", "imported", "id_counters")
_VERSION_MARKERS = ("version_id", "released", "release_report_id", "source_master_hash",
                    "document_kind", "template_id", "evidence_ids", "repair_of",
                    "workflow_id", "legacy", "summary_source_refs", "summary_claim_strength")

def _reject_version_document(raw: dict) -> None:
    """A tailored version dict passed as resume= currently becomes a valid master
    (D-2). Refuse it: a version is an OUTPUT of the master and can never become
    one — promoting it would make the master a derivative of itself and destroy
    its provenance."""
```

- `_seed_metadata` pops `_SERVER_OWNED_METADATA` from caller input before anything is hashed, and
  carries `imported` forward on `mode="update"` only (on `replace` the document is new, so stale
  provenance would lie).
- `build_candidate(..., import_info=None)` sets `meta["imported"]` **before** `assign_ids` and before
  the caller hashes the candidate, so preview and confirm agree (D6).
- `set_master(..., import_info=None)` calls `_reject_version_document(resume)`; when `file_path` is
  given it derives `import_info` server-side from `parsing._checked_path`'s result, so **every** file
  import records provenance, not only the discovery flow.
- `_applied_result` gains `"import_provenance"` so the skill can read it back without a second call.

### `lib/storage.py` changes

`require_master`'s `MASTER_NOT_FOUND` message becomes actionable and explicitly refuses substitutes:
*"Tailoring cannot proceed: the master is the only source of truth. Do NOT reconstruct it from memory,
from this conversation, from a previous tailored version, or from another workspace. Ask the user for
one folder holding their existing {kind} and call discover_masters(folder), or build one with the
create-master-file skill. If their data may be in a different workspace, call list_workspaces and
ask — never guess."*

The `history.yaml` record gains the import fields (this is where `imported_at` lives), plus a reader:

```python
def read_master_history(kind: str, limit: int = 20, ws=None) -> list[dict]:
    """Newest-first entries for one kind. Read-only, never repairs, must NOT take
    the workspace lock (save_master writes it from inside the lock)."""
```

### New audit events and fields (`lib/audit.py`)

```python
EVENTS |= {"workspace_selected", "workspace_switched", "master_discovered",
           "master_imported", "master_created", "master_loaded", "master_conflict"}
ALLOWED_FIELDS |= {"from_workspace_id", "to_workspace_id",   # how a switch is logged
                   "candidate_count", "entries_examined",    # proves the scan was bounded
                   "source_hash", "workspace_count", "state"}
```

**How to log a switch without breaking the structural-`workspace_id` invariant:** `build_record`
injects `workspace_id` top-level and the `ALLOWED_FIELDS` filter *skips* a caller-supplied one. If it
were whitelisted the field loop would run after the injection and **overwrite** it — a caller could
forge which workspace a record belongs to. So `workspace_id` stays out, and a switch writes two
records: one in the new workspace with `from_workspace_id`, one in the old with `to_workspace_id`.
Add a test asserting `"workspace_id" not in audit.ALLOWED_FIELDS`.

**Spec reconciliation:** 7 events added, not 9. `master_discovery_started` is dropped (it carries
nothing the completion event lacks; a scan that raises is already `tool_error`). `master_selected` is
dropped (the server cannot observe a chat-level choice; the observable moment *is* the import, and
`master_imported` carries `candidate_count` + `source_hash`). `master_loaded` fires only from the
`get_master_resume` tool — analyze/tailor/validate/release/export each load the master, so logging
every load would inflate the log 5× per workflow.

### New tools (`server.py`) — 19 → 25

| Tool | Contract |
|---|---|
| `get_workspace_status()` | **Call first in any session that touches the user's resume.** Reports state without creating, selecting or guessing. |
| `list_workspaces()` | Every workspace on this machine + which is active. Reads no resume content. |
| `select_workspace(workspace_id)` | Bind to an existing workspace. Only when the user explicitly names it — never to "find" their data, never because a master is missing. |
| `discover_masters(folder, kind=None)` | Exactly one folder, one level. Returns ranked candidates and never selects one. |
| `import_master_from_folder(folder, filename, kind, career_stage=None, accept_unparsed=False, expected_hash=None, confirm=False, proposed_hash=None)` | Copies one chosen file in. All three of folder/filename/kind required. |
| `get_master_history(kind="resume", limit=20)` | When the master changed, why, hashes before/after, and for imports the source filename and time. No resume content. |

Plus `initialize_workspace(create_new=False, label=None)`.

### Session binding / no-override audit

**Holds today.** All 19 tools checked: none accepts a workspace path or id. Filesystem paths reach only
`set_master_resume(file_path=…)` → `parsing._checked_path`, read-only. New surface and its containment:

| New surface | Risk | Containment |
|---|---|---|
| `select_workspace(id)` | Rebinding to another person's data | `validate_id` + `safe_child` confine it to `app_root()/workspaces`; `WORKSPACE_NOT_FOUND`; dual audit; per-workspace storage makes an in-flight workflow fail `WORKFLOW_NOT_FOUND` |
| `initialize_workspace(create_new=True)` | Sidestepping a broken binding | Refused while the bound workspace is missing; defaults `False`; a grep test asserts no `lib/` call site passes `True` |
| `discover_masters(folder)` | A read primitive over an arbitrary directory | `_checked_dir` (dir-only, strict resolve, refuses `app_root()` and the repo); one level; `MAX_DIR_ENTRIES`; symlinks skipped; `SNIFF_BYTES` from text only; basenames only in output and errors |
| `import_master_from_folder` | Path construction from model strings | `safe_basename` + resolve-and-compare-parent + `lstat` + `_checked_path` |
| `WORKSPACE_AMBIGUOUS.details` | Hands Claude ids it could select | Opaque `RT-<8hex>`; `list_workspaces` exposes no names or content; selecting still requires the user to name one |

**Residual risk, stated plainly:** nothing in Python can distinguish "the user told me to switch" from
"I decided to switch." The audit trail makes it detectable and the workflow binding makes it loud, but
the authorization itself is prose (R-USER-14). Record this in `docs/baseline.md` rather than implying
it is enforced.

### Doc changes

`CLAUDE.md` — a "User identity and workspace binding" section under "Source of truth", with
R-USER-01..17 **each paired with its enforcement point**. The two rules with no Python enforcement
(R-USER-02 partially, R-USER-14's authorization) are marked as such rather than implied.

| Rule | Enforced by |
|---|---|
| R-USER-01 identity only from the local binding | `workspace.get_workspace` |
| R-USER-02 never from memory or chat history | prose + `require_master` message + `resolve.next_step` |
| R-USER-03 never from a previous tailored version | `master_ops._reject_version_document` |
| R-USER-04 never another workspace's data | `safe_child`, per-workspace dirs, `workflows.load_workflow` |
| R-USER-05 never search the filesystem | `discovery._checked_dir`, one level, `MAX_DIR_ENTRIES` |
| R-USER-06 first call is `get_workspace_status` | prose + every `WORKSPACE_*` error |
| R-USER-07 scan exactly the named folder | `discover_masters(folder)` |
| R-USER-08 >1 candidate → the user picks | `filename` required; no `selected` field |
| R-USER-09 resume and CV separate; if both, ask | `kind` required; `validate_kind`; separate files |
| R-USER-10 the folder is an import source | `import_master` copies in; `_checked_dir` refuses `app_root()` |
| R-USER-11 record and read back provenance | `ImportInfo`, `history.yaml`, `get_master_history` |
| R-USER-12 no master → refuse, substitute nothing | `require_tailorable_master` → `MASTER_NOT_FOUND` |
| R-USER-13 master not ready → resolve first | `require_tailorable_master` → `MASTER_INVALID`, now in analyze too |
| R-USER-14 switch only when the user names one | `select_workspace(id)` + dual audit + workflow binding |
| R-USER-15 several workspaces, no binding → ask | `WORKSPACE_AMBIGUOUS` |
| R-USER-16 a new workspace only on request | `create_new=False` default + guard |
| R-USER-17 never write user data into this repo | workspace paths only; `_checked_dir` refuses the repo; hygiene test |

`.claude/skills/create-master-file/SKILL.md` — replace step 0 (which jumps straight to
`initialize_workspace()`) with the `get_workspace_status()` branch table, and step 3 with
"Which folder on this computer has your current resume or CV?" → `discover_masters` → 1 candidate
confirm by name / >1 list and ask which ONE and which kind / both resume and cv present → ask which
to configure now, run the skill again for the other, never merge / `NO_MASTER_CANDIDATES` → ask for a
different folder or go to the interview, and **do not offer to reconstruct it from memory or from
earlier in this chat**. Then read the provenance back.

`.claude/skills/create-cv/SKILL.md` — a matching preamble: this skill is reached only after
`get_workspace_status()` and after discovery found nothing; its output goes through
`set_master_resume(resume=…)`, which emits `master_created`, not `master_imported`.

`README.md:48-58` — fix the architecture tree (the workspace level is missing entirely); add a
"Workspaces and identity" section (4 states, `list_workspaces`/`select_workspace`, the never-infer
rule); bump the tool inventory 19 → 25.

### Tests

**New conftest fixtures:** `two_workspaces` (two initialized in one home, B active, A holds a master),
`candidate_folder` (one folder, 8 entries covering every discovery branch: resume-by-filename,
cv-by-filename, yaml-with-metadata.kind, ambiguous .txt, binary .pdf, oversized .pdf, wrong suffix
.png, a subdirectory, a symlink pointing outside), `repo_snapshot` (`{path: mtime_ns}` for the repo,
to prove nothing was written there).

- **`tests/test_workspace.py`** (extend, ~15): list/select/switch, symlinked and invalid entries
  skipped, traversal ids rejected, `create_new` makes a second and default never does, `create_new`
  refused with a broken binding, **`test_missing_config_with_existing_workspaces_is_ambiguous`** (the
  D-1 regression), ambiguous creates nothing, config preserves unknown keys, concurrent select under
  the app-root lock.
- **`tests/test_resolve.py`** (new): one test per row of the 9-row state table, plus never-raises,
  creates-nothing, unparsed-is-FOUND-not-NEEDS_SETUP, and
  `test_next_step_text_refuses_memory_substitute` — prose that is actually tested.
- **`tests/test_discovery.py`** (new, ~16): `_checked_dir` rejects file/missing/app_root/repo; only the
  named folder scanned (a sibling folder with a resume is never returned); subdirectory not entered;
  symlink skipped; 600 files → `entries_examined == 500, truncated`; oversized reported not dropped;
  yaml `metadata.kind` guess; no candidates raises; **no auto-selection at any count** (`"selected"`
  absent for 1/2/5); output and errors contain no full path; binary formats not sniffed;
  `classify_candidate` parametrized; `safe_basename` accepts real filenames and rejects separators.
- **`tests/test_master_ops.py`** (extend, 7): import records provenance; **provenance deterministic
  across preview and confirm** (the D6 regression); update carries it forward; replace drops stale;
  caller cannot forge `migration`/`imported`; **tailored version dict rejected** (D-2); import does not
  modify the source file (bytes + mtime).
- **`tests/test_audit.py`** (extend, 5): new events accepted; `workspace_id` not whitelisted; switch
  logged in both workspaces; `from_/to_workspace_id` sanitized; discovery event has no filename/path.
- **`tests/test_identity_acceptance.py`** (new) — the 18-item DoD, one test per item, docstring
  checklist matching the `test_source_truth.py` convention: folder asked on first run · refusal wording
  present in `MASTER_NOT_FOUND` and every `next_step` · only the named folder scanned · multiple
  candidates require a pick (assert via `inspect` that `filename` has no default) · resume and CV land
  in separate files with no content crossover · new workspace only on explicit `create_new` (+ grep
  assertion) · existing workspace loads with a stable hash · A's masters/versions/evidence/workflows/
  audit unreachable from B · two homes with the same synthetic person are unlinked · ambiguous binding
  requires explicit selection and creates nothing · tailoring blocked without a master (both analyze
  and tailor) · **analyze fails before the evidence prompts when the master is unready** (D-3) ·
  a released version cannot be promoted to master · provenance readable via `get_master_history` ·
  switch auditable from both sides · switch mid-workflow breaks the workflow · no tool accepts a
  workspace path (`inspect.signature` over all tools) · discovery paths cannot escape · a full cycle
  writes nothing into the repo · repo resources hold no synthetic user data.

### Verification

```bash
pytest tests/test_discovery.py tests/test_resolve.py -q
pytest tests/test_workspace.py tests/test_master_ops.py tests/test_audit.py -q
pytest tests/test_identity_acceptance.py -q
pytest 2>&1 | tail -20                       # no regressions vs the frozen baseline
# the audit taxonomy is closed and workspace_id stays structural
python -c "from lib import audit; print('workspace_id whitelisted (must be False):',
           'workspace_id' in audit.ALLOWED_FIELDS)"
python -c "import asyncio, server; print(len(asyncio.run(server.mcp.list_tools())))"  # 25
```

MCP sequence against a **fresh** throwaway home: `get_workspace_status` → `NO_WORKSPACE`;
`discover_masters` → 3 candidates, `requires_user_selection`; `import_master_from_folder` → applied +
provenance; status → `WORKSPACE_FOUND`; `get_master_history` → one import entry with its time;
`initialize_workspace(create_new=True)` → new id bound; status → `NEEDS_SETUP`; analyze →
`MASTER_NOT_FOUND` whose message refuses memory; `list_workspaces` → 2; `select_workspace(<first>)` →
`switched: true`; `get_master_resume` → unchanged hash; `rm config.yaml` + status →
`WORKSPACE_INVALID/no_active_binding`; `initialize_workspace()` → **`WORKSPACE_AMBIGUOUS`, not a new
workspace**; recover by selecting; `discover_masters($RESUME_TAILOR_HOME)` and `discover_masters(<repo>)`
→ `FOLDER_UNSUPPORTED`; grep both audit logs → `workspace_switched` on both sides, no filename, no
path, no name/email.

---

## Phase 2 — Master as absolute source of truth

Mostly done. **Verify, don't rebuild.** Already confirmed working: `tailor_resume` takes no resume
body; a resume blob is rejected as a patch; the server loads the master itself; `source_master_hash`
on every version; a master change during tailoring aborts under the workspace lock; release re-checks
the master hash; patch replay proves the version was not hand-edited; a version's blocks cannot be
cited; `require_version` appears only in read-only and repair-metadata paths; released versions are
immutable; ID high-water marks survive replace and update.

Six real gaps:

**Gap 1 — `set_master_resume(resume=…)` promotes a version to master (D-2).** Traced, not assumed.
**Recommendation: `resume=` is a legitimate authoring path — keep it, and close the bypass with
`_reject_version_document` (Phase 1 task 7).** The from-scratch interview has no file and must pass a
dict; removing `resume=` would push users to write YAML to a temp file, which is worse. Be honest
about the limit: a determined caller could strip the markers and pass a version *body*, and no content
check can distinguish that from an authored document. The marker check closes the accidental and
automatic paths, which is where real failures come from. Write that limit down in `docs/baseline.md`
rather than implying the hole is sealed.

**Gap 2 — workflows record no master identity (D-4).** Add `WorkflowMetadata.source_master_hash`, set
it in `analyze_requirements`, verify it in `tailor` → `MASTER_CONFLICT` with *"the master changed since
the gap analysis; re-run analyze_tailoring_requirements — the evidence prompts you asked are stale."*
`source_master_hash` is already in `ALLOWED_FIELDS`, so no audit change.

**Gap 3 — no end-to-end immutability test.** Add to `tests/test_source_truth.py`:
`test_master_unchanged_by_tailoring` (no PDF needed) and `test_master_unchanged_by_full_release_cycle`
(`@needs_tectonic`). Assert bytes, hash, **`mtime_ns`**, and that `master/backups/` gained no file —
`mtime_ns` plus the backup check are what catch a rewrite with identical content, which a hash
comparison alone would miss.

**Gap 4 — `lib/ids.py` coverage.** Verified: all seven families are counter-backed and indexed except
`skill-<slug>`, which is content-derived and excluded at `ids.py:96` (D-6). Two consequences: re-adding
a deleted skill reuses its ID (benign — same name, same block, do not change); **renaming** a skill
changes its ID so an older version's `skill-python` ref stops resolving (a genuine stability hole).
**Recommendation: document it, do not change it** — making skill IDs sequential would renumber every
existing master and invalidate every existing version's refs. Add
`test_skill_item_ids_are_content_derived_not_counter_backed` to pin current behavior so a future
change is deliberate, plus `test_every_section_has_an_id_family` so a new section cannot ship without
IDs, and a README Known Limitations bullet.

**Gap 5 — bypass hunt (done).** `grep` for `source_version`, `load_version|require_version`, `promote`,
`resume=` found only Gap 1. `lib/tailoring.py:154` loads the prior version to check `workflow_id` and
`source_master_hash` then uses the *recorded patches*, not the body. Convert the greps into standing
tests: `test_no_tool_accepts_a_resume_body_for_tailoring` (signature-level) and
`test_version_bodies_never_reach_the_tailoring_path` (static).

**Gap 6 — `master/history.yaml` is written and never read (D-5).** **Recommendation: expose it** — it
is the only durable record of when each master write happened, Phase 1's D6 puts `imported_at` there,
and the DoD requires provenance to be readable. Implement `read_master_history` +
`get_master_history`. Two notes while doing it: the file is read-whole-append-rewrite on every save
(O(n) per write, fine at resume scale — record in the baseline), and it is written *inside*
`workspace_lock`, so the reader must not take the lock.

**Verify:** `pytest tests/test_source_truth.py tests/test_ids.py tests/test_storage.py -q`, then the
MCP sequence: record `H0` → analyze → evidence → tailor (`source_master_hash == H0`) → validate
(`source.master_hash` and `provenance.replay` pass) → release → export → `get_master_resume` still
`H0` → `get_master_history` shows exactly one entry (the import; the cycle wrote nothing) →
`set_master_resume(resume=<the version dict>)` → `MASTER_INVALID` with `version_fields` listed →
`master/backups/` still empty.

---

## Phase 3 — Evidence system

**Already built, do not rebuild:** all four classification axes exist; must-have prioritization exists
and nice-to-have missing terms already get no prompt; the evidence record schema is complete plus
hardening the spec doesn't ask for; **the evidence→section matrix already matches the spec row for
row** (zero edits to `resume_etiquette.yaml:181-191`); metric provenance is enforced twice; skill
provenance is already per-item. Also: `quick_ats_check` does not exist in this repo — the ATS path is
`lib/ats.score_ats` + `match_resume_to_jd`, neither of which reads the workflow analysis.

### 3.1 Keep the four lists; add a derived per-term view

Restructuring is wrong: the trimmed analysis is persisted on disk and read by four independent
consumers (`tailoring.py:116`, `tailoring.py:197`, `get_workflow_status`,
`tests/test_phase3_acceptance.py:170`). A restructure breaks every workflow already on disk for no
gain — the axes are disjoint by construction, so the union is a clean total ordering.

Two real defects to fix while adding the view:
- **Unknown requirements are never checked against the master**, so the user is prompted about terms
  their master already proves. `term_in_text` works on arbitrary strings, so the existing matcher can
  classify them at zero new machinery cost.
- **`_proof_text` ignores project `stack`**, so a term appearing only in a stack is reported `missing`
  instead of `weak`.

```python
# lib/matching.py
REQUIREMENT_STATUSES = ("supported", "weak", "missing", "unknown")
def _mentioned_text(resume) -> tuple[list[str], str]: ...   # skill items + project stacks
def classify_term(term, resume) -> str: ...                 # for ANY string
def requirement_view(result, resume) -> list[dict]:
    """One row per JD requirement, priority-ordered:
    {term, term_display, status, importance, requirement_type, reason}
    importance in ("must_have", "nice_to_have", "unrecognized"). Unknown-axis
    terms run through classify_term, so a token the master proves comes back
    'supported', never 'unknown'."""
```

`analyze_requirements` returns `requirements` + `status_counts` and keeps every existing key
byte-identical in shape. Persist `requirements` trimmed to `[{term, status, importance, reason}]` and
**do not remove `priority_missing` or `unknown_requirements`**. `tailoring.py:113-118` reads
`requirements` when present with a fallback, and uses each row's `reason`. While there, fix the
pre-existing wart that a `weak` term kept on the page but not re-cited is reported as "no supporting
evidence" — it should read *"already listed under Skills; no bullet added"*.

### 3.2 Machine-readable `reason` + `requirement_type` + a prompt budget

Reuse the signals already present (`_split_by_section`, the `NON_REQUIREMENT_SECTION_HEADERS` guard
that already keeps "Benefits: free Kubernetes training" out of must-haves, `KNOWN_SKILLS`/`ALIASES`).
The only missing datum is a term→type map.

```python
# lib/keywords.py
REQUIREMENT_TYPES = ("language","framework","platform","datastore","data_tool",
                     "practice","domain","tool","certification","technology")
TERM_TYPES: dict[str, str] = {...}    # transcription — KNOWN_SKILLS is already grouped this way
def term_type(term) -> str: ...
_CERT_RE = re.compile(r"certif(?:ied|ication|icate)s?", re.I)
def certification_requirements(jd_text) -> list[str]: ...   # line-scoped so it cannot leak

# lib/evidence.py
PROMPT_REASONS = ("must_have_missing","must_have_weak","certification_required",
                  "unrecognized_requirement")
MAX_PROMPTS = 12
MAX_UNKNOWN_PROMPTS = 5
```

Prompts gain `reason`, `requirement_type`, `importance`, and `placement_by_category` — built live from
`rules.placement(cat, section)` for 8 categories × 5 sections, so the spec's "allowed evidence
categories" becomes machine-readable **without duplicating the YAML matrix in code**. Ordering:
must_have missing → must_have weak → certification_required → unknown (filtered to
`classify_term == "missing"`, capped at `MAX_UNKNOWN_PROMPTS`), then the whole list to `MAX_PROMPTS`,
with `prompts_truncated` in the response. **This is the "don't ask about every JD word" fix** — today
`find_unknown_requirements` can emit 25 unknown prompts and all 25 become questions.

### 3.3 Fix the `term_display` wart

`term_display` is bolted on after `model_dump()` and stripped before re-validation — a field that
exists on disk, is consumed by `tailoring.py`, and is invisible to the schema. Add
`term_display: str | None` and `prompt_reason: str | None` to `TailoringEvidence` (both optional, so
every existing YAML validates unchanged), pass them through the model, and delete the strip/re-attach
hack. **Keep** the metric re-check and the confirmed/scoping checks exactly as they are. Do **not** add
`prompt_reason` to the dedupe key, or a second prompt for the same term creates a duplicate record.

### 3.4 Matrix diff result: no changes to lines 181-191

Eight of nine spec rows match exactly; the extra `education` column and the `master_skill`/
`master_summary` pseudo-categories are stricter than the spec and `rules.placement` fails closed on
absent keys — keep them. Internship's "with context" half is not expressible in a placement table and
is already enforced semantically by `provenance.internship_as_professional`.

**The one real gap:** the table cannot say "the project must itself be academic", so academic-only
evidence can today back a bullet on a non-academic project. **Recommendation: do not retrofit that
onto existing project bullets** — academic work legitimately appears under a plain Projects heading,
and it would be stricter than the shipped matrix. Enforce it only where the system makes the
structural claim itself, i.e. on new entries (3.5), via `provenance.project_academic_context`.
Document the residual looseness in Known Limitations.

YAML edits actually required (all additions): bump `rules_version` to `"2026.10-v2.1"`
(`tests/test_provenance.py:96` hardcodes the old string and **must be updated, not worked around**);
add `skill_group_categories` (a closed list — a new group's label is rendered on the page and carries
no provenance) and `scope_inflation_markers`; add three `new_entry_rules` lines. Two new
mtime-cached, fail-closed readers in `lib/rules.py`.

### 3.5 `add_project_entry` — the one real feature addition

A new operation is unavoidable: `add_block` rejects any parent that is not an existing entry and
`replace_block` is capped to summary + bullets. There is no reachable path that appends to
`body["projects"]`.

**Header-mutation containment (the key argument):** existing entries stay immutable not because header
field *names* are banned, but because **no operation accepts a target together with a header field**.
`AddProjectEntry` has no target at all — it can only create. With `extra="forbid"`, adding
`name`/`stack`/`academic` to a create-only op cannot be replayed against an existing entry.

| Field | Decision | Reason |
|---|---|---|
| `name` | required | The entry's identity; goes through the provenance hook, so technologies in it must be cited |
| `stack` | optional | Already how `index_blocks` composes a project's citable text |
| `academic` | optional bool | Flips `project_category`, so it must be declared, not inferred — guarded by `project_academic_context` |
| `github` | **forbidden** | `CLAUDE.md:134-135`: never add an unverified repo URL, and `lib/latex.py:175` would make it a live `\href`. A link belongs in the master |
| `dates` | **forbidden** | A factual claim with no citable source in the evidence model; years would also confuse `_METRIC_RE` |
| `id`, anything else | **forbidden** | IDs are minted server-side only |

**Backing categories:** at least one `type="evidence"` ref whose category has
`placement(cat, "projects") == "yes"` ⇒ `professional`, `internship`, `personal_project`, `academic`.
`coursework` is refused even though its Projects placement is `limited`, because "limited" governs
*wording inside a block* and must not conjure a whole structural entry. `professional` is allowed
because presenting paid work under Projects is a **downgrade** in `CLAIM_RANK`, never inflation.
Master refs may be cited in addition but **never alone** — otherwise one existing experience bullet
could be split into a fabricated standalone project.

```python
class AddProjectEntry(_Strict):
    operation: Literal["add_project_entry"]
    name: str
    stack: str | None = None
    academic: bool = False
    source_refs: list[SourceRef] = Field(default_factory=list)
    claim_strength: ClaimStrength | None = None
    bullets: list[NewContent] = Field(min_length=1, max_length=4)
REPAIR_SAFE_OPERATIONS = ("drop_block", "reorder")   # UNCHANGED — never repair-safe
```

`min_length=1` makes "an entry always has at least one sourced bullet" a *schema* invariant. Add
`source_refs`/`claim_strength` to the `Project` body model but **not** to `Experience` — that absence
is the signal that no experience entry is ever creatable.

Implementation notes that matter:
- An import-time guard `assert set(_CHECKS) == set(_APPLY) == set(_OPERATIONS)` so a future 7th op
  cannot be half-wired. (A missing `_APPLY` entry crashes loudly; a missing `_CONTENT_OPERATIONS`
  entry **silently skips the entire provenance hook** — that is the hazard worth an assertion.)
- Replace `_hook_ctx` with `_hook_ctxs` returning one ctx per piece of new wording: the entry
  (`new_entry=True`, text = name + stack) then one per bullet. `parent_category` for both =
  `project_category({"academic": p.academic})`, so the hook sees exactly what a master project shows.
- In `_apply_add_project`, append the entry to `body["projects"]` **before** minting bullet IDs —
  `_next_id` scans `_all_body_ids(body)`, so otherwise every bullet in the same patch gets the same id.
- `vp-`/`vb-` are version-only prefixes. `storage.save_version` never calls `assign_ids`, so a tailored
  version consumes zero master ID space and cannot advance a high-water mark.
- `provenance.replay` needs **no change** — `validate_and_apply` is deterministic and patches replay in
  recorded order, so `vp-001`/`vb-NNN` reproduce identically. Add a replay test that includes an added
  project.
- `evidence_usage` must walk **entry-level** refs too, or evidence justifying an entry vanishes from
  `added_terms`. Surface `new_entry_ids` in the tailor result.
- Repair safety is already double-blocked (the tool gate and `repair.forbidden_operation`). Add
  explicit tests for both paths; do not touch the tuple.

New rule ids: `patch.duplicate_project`, `patch.too_many_new_entries`,
`provenance.new_entry_requires_evidence`, `provenance.new_entry_placement`,
`provenance.project_academic_context`, `structure.new_entry_provenance`, `structure.unknown_entry`.
The last one — any experience/education entry whose id is not in `master_index` is a hard failure —
closes the hand-edited-version-YAML fake-employer hole independently of the patch layer. No new error
codes: `PATCH_INVALID` and `PROVENANCE_VIOLATION` cover both classes.

**Docs:** rewrite `README.md:327`; amend `CLAUDE.md:126-127` to note the single exception (a
*brand-new* project may set name/stack/academic; existing entries stay immutable); keep the github
prohibition verbatim and reference it; add to `tailor_resume`'s docstring: *"There is no operation
that adds a job, employer or role."*

**Tests (~45):** `test_patches.py` structural (ids, the bullet-id append-order regression, rejected
github/dates/id, bullet cap, duplicate name case-insensitive, entry cap, new project then add_block
then reorder, id outside master space, operation-table coverage); `test_provenance.py` semantic and
adversarial (master-only refs rejected, learning_only and coursework rejected, personal_project and
professional accepted, both directions of the academic flag, technologies in name and stack must be
cited, bullets need their own refs, metric in a project name, high-scope verb, and **messages never
quote the name or stack**); `test_source_truth.py` (no operation can add an employer — an
`add_experience_entry` payload must fail with the neutralized `"Unknown operation."`; a fake employer
smuggled as a project name still needs evidence; not repair-safe on both paths);
`test_validators_structure.py`; `test_release.py` (replay with an added project; a hand-added project
fails replay); `test_phase3_acceptance.py`; `test_compile.py` (renders the stack without an `\href`).

---

## Phase 4 — Tailoring and provenance (verification + four real gaps)

**4.1 Patch op set — done.** The discriminated union, `_CHECKS`, `_APPLY` and `_CONTENT_OPERATIONS`
are consistent; `extra="forbid"` closes field smuggling; `MAX_PATCHES = 200` bounds the surface.
Add the import-time assertion plus `test_every_op_with_source_refs_is_a_content_operation`, which
reflects over the patch classes and asserts any class carrying `source_refs` is in
`_CONTENT_OPERATIONS`.

**4.2 Block provenance — mostly done; two blocks can carry unsourced text.**
- **Gap A (real): a new skill-group category is unvalidated free text with no provenance.**
  `_check_add_skill` validates only length, `SkillGroup` carries no refs and is uncitable by design,
  `_claims` does not walk groups, and `lib/latex.py` renders the category as the bold label. So
  `category: "Kubernetes Expert, 5 Years Production"` puts an unprovenanced claim on the released PDF.
  Fix with rule `patch.skill_group_category`: when no existing group matches (existing ones are
  matched case-insensitively first, so bespoke master group names are unaffected), the category must be
  in `rules.skill_group_categories()` or be a familiarity group, and must contain no metrics and no
  high-scope verb.
- **Gap B (hygiene): `NewContent.metadata` is an unbounded attacker-controlled dict** deep-copied into
  the saved version and into `provenance.replay`'s hash. Fix with `patch.metadata_size`: ≤5 keys,
  scalar values, ≤200 chars serialized.
- Answer to "any block type that can carry a claim without refs": skill *groups* and bullet `metadata`.
  Every text-bearing leaf is covered. Add `test_no_operation_can_add_a_certification` to pin that
  surface shut.

**4.3 Skill-item provenance — done.** One source cannot justify a group because each `add_skill_item`
is its own patch with its own refs **and** rule 4 requires the item's own name to appear in a cited
ref's text or be the evidence's `term`. Add two confirmation tests
(`test_one_evidence_cannot_justify_two_unrelated_skills`,
`test_group_id_cannot_be_used_as_a_source_ref` → `provenance.uncitable_ref`).

**4.4 Claim strength / scope — the spec's example is already caught and already tested; one named gap.**
From `"I deployed my RAG project on AWS EC2"` (`personal_project`): the allowed output passes in
Projects (`deployed` is a safe verb, `aws` is cited, `EC2` is not a metric thanks to `_METRIC_RE`'s
guards); the same text in Experience is rejected twice (`placement` + `claim_strength`);
`"Architected enterprise AWS infrastructure serving thousands of users"` is rejected by
`high_scope_verb` and is covered at `tests/test_provenance.py:193`.

**The gap, verified by direct execution (`check()` returns `[]`):** swap the verb for a safe one and
the inflation survives — `"Deployed enterprise-grade AWS infrastructure serving thousands of users
across multiple regions"` passes every rule. `scope_expansion` only fires when *all* refs are
learning_only/coursework; `unsupported_metric` needs a digit and "thousands" has none;
`unsupported_technology` is satisfied by `aws`; `claim_strength` is within rank. Fix with
`provenance.scope_inflation` (~15 lines), reusing the exact high-scope mechanism: markers from
`rules.scope_inflation_markers()` compiled phrase-tolerantly, and a marker in new text must also
appear in a cited source.

**Explicitly do NOT add semantic/LLM claim detection in v1.** Three reasons rooted in this codebase:
`provenance.replay` re-runs the hook at validate *and* release time and demands byte-identical output,
so a non-deterministic rule makes a valid version randomly unreleasable; `rules_version` is stamped
into every version and release report as an auditable claim that a *fixed, inspectable* rule set was
applied; and the offline/no-API-key guarantee forbids an outbound call while a local model would still
fail the first reason.

**Cross-cutting consequence of any rule tightening — call this out before implementing:** a draft
version validated under `2026.09-v2.0` will fail `provenance.replay` under `2026.10-v2.1` if the new
rule rejects one of its recorded patches, surfacing as a generic "recorded patches no longer validate"
critical failure that repairs cannot fix. Add a non-blocking `release.rules_version_drift` warning so
the cause is legible: *"tailored under an older rule set; if replay fails, re-tailor rather than
repair."*

**4.5 Metric provenance — already enforced twice.** Add adversarial coverage: rounded-up number
(42% → 45%), unit swapped (42% → 42x, 42% → 42ms), percentage invented from a ratio, currency changed
($ → ₹), **currency dropped (allowed — document the deliberate asymmetry so nobody "fixes" it)**, a
metric present only in the JD, and `test_metric_pool_shared_across_refs_is_a_known_looseness` which
asserts the current permissive behavior explicitly so the limitation lives in code plus a README
bullet.

**4.6 Patches as untrusted input — new `tests/test_patch_fuzz.py`** (~22 tests), driven through both
`validate_and_apply` and `server.tailor_resume`. Malformed (not a list, scalars, `operation` as
None/int/list/dict, unknown operation asserting the exact neutralized message, `target` as a bare
string, `source_refs` as a dict, `ref.type == "master "`, missing discriminator, deep nesting);
oversized (201 patches, 10 000-char text, 1 MB metadata, 100 bullets); duplicated; contradictory and
out-of-order (drop then replace, reorder listing a dropped id, reorder before the add that creates it).
Invariants asserted in **every** failing case: the master hash is unchanged and the version does not
exist; `json.dumps(error["details"])` contains no `os.sep`, no tmp path, no `/Users`, no `.yaml`;
no master bullet substring and no evidence sentinel; every rejection has exactly
`{patch_index, operation, rule, message}`. Plus a seeded shuffle loop asserting the outcome is always
either success or a `ResumeTailorError` with a registered code — never a bare exception, never a
partial write.

**4.7 Audit — one trap.** `category` is in `ALLOWED_FIELDS` but `_clean_value` validates it against
`FAILURE_CATEGORIES`, so logging an *evidence* category is **silently dropped**. Use
`evidence_category` instead. Add `evidence_category, prompt_reason, requirement_type, weak_count,
supported_count, new_entry_count, rules_version` to `ALLOWED_FIELDS` and `project_entry_added` to
`EVENTS` — creating a structural entry is the most consequential new capability and deserves its own
line rather than being folded into `tailor_succeeded`. Add `test_events_taxonomy_is_closed`.

**Landing order** (each step independently shippable): 3.3 → 3.1 → 3.2 → 3.4 YAML + readers →
4.2 Gaps A/B → 4.4 `scope_inflation` + `rules_version` bump + drift check → 4.1/4.3/4.5 tests →
4.6 fuzz → **3.5 `add_project_entry` last** (it depends on 3.4's YAML, 4.4's hook shape and 4.1's
table guard) → 4.7 audit → docs.

---

## Phase 5 — Template and document control

**Already done, do not rebuild:** the hard whitelist and exact-string lookup; `TEMPLATE_UNKNOWN`/
`TEMPLATE_NO_RENDERER` with a second guard in `lib/latex.py`; `auto` picking only releasable
templates; an experimental id recorded as-is and blocked at release; a draft export erroring instead
of rendering classic; 65 integrity tests. Phase 5 is three narrow things.

### 5.1 The `unknown` decision — option (c): completeness keyed on `status`

`supported` ⇒ every contract block fully specified; `experimental` ⇒ may be the literal `"unknown"`,
and *must* be `"unknown"` rather than a guess.

Not (a), because the status quo has a demonstrable hole (**D-8**): a contract field of `"unknown"`
degrades to `not_available`, not `fail`, and `passed` only counts `fail` — so hand-editing
`page: unknown` into the classic entry makes `template.page_size`, `template.margins` and
`pdf.page_size` all `not_available` and **release still succeeds** with only the hardcoded floors
surviving. Not (b), because the stubs carry value nothing else holds: `best_for` drives
`recommend_template`, `layout.sections` drives the md/docx exporter, `source_file` ties each entry to
its reference PDF. Demoting them to `unsupported` changes nothing functionally while making
`tests/test_templates.py:45-48` lie.

```python
# lib/schemas.py — replacing 292-309
CONTRACT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "page": ("size","width_pt","height_pt","orientation","margins_in"),
    "typography": ("font_family","pdf_font_prefixes","body","name","heading","size_tolerance_pt"),
    "layout": ("sections","columns","tables_allowed","text_boxes_allowed","graphics_allowed"),
    "sections": ("order","headings"),
    "spacing": ("section","bullet","line_spacing"),
    "limits": ("min_pages","max_pages"),
    "formatting": ("bullet_style","date_style","heading_style","link_style"),
    "latex": ("documentclass_options","layout_only_macros"),
}
def contract_gaps(data: dict) -> list[str]:
    """Dotted paths missing or the literal "unknown". Pure; used by the registry
    validator, the release gate and the tests."""
# TemplateContract gains latex (was an untyped extra) and a model_validator:
# status is supported ⇒ contract_gaps(...) == []
```

The asymmetry is deliberate: the pydantic validator is the **programming-error** net (a malformed
registry in the repo fails a test), while the runtime net is the release check below, because a
user-edited `templates.yaml` must produce a *blocked release with a readable message*, not an
`INTERNAL_ERROR`.

`templates.yaml` changes: state the completeness rule in the header comment; add
`latex.layout_only_macros: [resumeSubheading, resumeProjectHeading]` and a documentation-only
`enforcement:` map (contract path → check id) to the classic entry; bump it to `1.2.0`; **no value
changes to the four experimental entries**.

### 5.2 Explicitly not building the other four renderers

Write it down so it is a decision, not an oversight. `RENDERERS` stays a one-entry map — do not add
stubs. Add to the module docstring: adding a renderer means a new render module, a complete contract,
`status: supported`, a reference PDF whose measured properties match, and Phase 6's full negative-path
suite re-run against it. The user-facing messages are already right; the gap is `recommend_template`.

### 5.3 New release check `template.contract_complete`

In `validate()` right after the `template.releasable` branch: `fail`/`critical`/`TEMPLATE` when a
releasable template has gaps (message: *"marked supported but its contract is incomplete (N fields
unknown); format checks would silently degrade to not_available. Restore
resources/templates/templates.yaml."*), `pass` when complete, `not_available` when not releasable.
Also wrap `get_contract` so a pydantic `ValidationError` becomes a new
`TEMPLATE_REGISTRY_INVALID → TEMPLATE` business error. **This is the one change that converts "the
YAML is authoritative" from a claim into an enforced invariant.**

### 5.4 Registry self-validation

`templates.validate_registry()` — aggregates every problem into one `TEMPLATE_REGISTRY_INVALID`,
called by the registry test, not on every lookup. Rules: id unique and slug-shaped; status in the
enum, version semver-ish; `RENDERERS ⊆ supported ids`; supported ⇒ no gaps **and** has a renderer;
supported ⇒ `columns == 1` and tables/text-boxes/graphics all `False` (the floor is absolute; a
contract must not claim otherwise); supported ⇒ `sections.order == layout.sections`, headings match,
and order is a subsequence of `RENDERER_SECTION_ORDER`; supported ⇒ margins 0.5–1.0, body ≥ 10,
name 14–24, heading 11–14, `max_pages >= min_pages` (moves `tests/test_templates.py:219-226` from a
test-only assertion into the library); non-supported ⇒ unmeasured blocks are exactly `"unknown"`;
`source_file` exists; every `CONTRACT_REQUIREMENTS` key appears in `enforcement`; and the
`latex.*_adjust_in` values recompute to `page.margins_in` within 1e-6 so the two can never drift.

Then make `LAYOUT_MACROS` contract-driven in `format_tex.py`: `_layout_macros(contract)` returns the
declared list, or **whitelists nothing** for an unknown contract. **This closes a real hole** — today
any `.tex` defining `\newcommand{\resumeSubheading}` gets its `tabularx` bodies stripped from the
forbidden scan regardless of what the contract permits.

### 5.5 `recommend_template` must not present an unreleasable template as usable

The server calls it with `supported_only=False`, so it returns `full-stack-modern` with
`releasable: False` and no instruction. Add `usable`, `releasable_alternative`, `warning`
(*"…is experimental metadata with no production renderer: it cannot be rendered or released. Use
'classic-minimalist' for an actual PDF. Nothing is substituted automatically."*) and `next_step`.
Guard the recursion by computing the `supported_only` pick once. Also add `releasable` to each
`list_templates()` row — `has_renderer` alone is not the gate.

### 5.6 Finish `classic-minimalist` — the enforcement gap table

Verified by grep: `spacing`, `formatting`, `typography.name`, `typography.heading`, the three
`*_allowed` flags, `unsupported_sections`, `layout_only_exceptions`, `page.orientation` and the four
`latex.*_adjust_in` values appear **nowhere** in `lib/` outside the schema declarations.

| Contract path | Today | Plan |
|---|---|---|
| `page.size/width/height`, `margins_in`, `font_family`, `body.size_pt`, `layout.columns`, `sections.*`, `limits.*` | enforced | none |
| `page.orientation` | no | implied by the exact width/height compare; record in `enforcement`, no new check |
| `typography.name.size_pt` (20.7) | no | new `template.name_font_size` — resolve the size switch inside `\begin{center}` via `_SIZE_TABLE` (`\huge` → 20.74 at base 11), compare ± `size_tolerance_pt` |
| `typography.heading.size_pt` (12) | no | new `template.heading_font_size` — parse the `\titleformat{\section}` group's size switch |
| `tables_allowed`/`text_boxes_allowed`/`graphics_allowed` | no (floor is hardcoded and **stricter**) | **Do not make the floor contract-driven.** Enforce consistency at registry level so the YAML can never claim what the floor forbids. *Spec conflict: it wants contract-driven allowed-structures; keep the code, constrain the YAML.* |
| `spacing.bullet` (`itemsep=2pt`) | no | new `template.spacing` — parse `\setlist{...}` into k→v and require the contract's pairs |
| `spacing.line_spacing` (`single`) | no | fold into `template.spacing`: no `\linespread{x≠1}`, no `setspace` + `\onehalfspacing`/`\doublespacing`, no `\renewcommand{\baselinestretch}`. **Compressing leading to fit a page cap is exactly the trick `CLAUDE.md` prohibits and nothing catches it today.** |
| `spacing.section` + `formatting.heading_style` | no | new `template.heading_style` — the `\titleformat{\section}` argument must contain `\bfseries`, `\scshape`, `\titlerule`, `\vspace{2pt}`, via a literal style→tokens map; an unrecognized style string ⇒ `not_available`, never `pass` |
| `formatting.bullet_style` (`itemize`) | no | new `template.bullet_style` — ≥1 `itemize`, zero `enumerate`/`description` |
| `formatting.link_style` (`hyperref hidelinks`) | no | new `template.link_style` (warning) — require `[hidelinks]`, fail on `colorlinks` |
| `formatting.date_style` | structurally (dates are not patchable) | record in `enforcement`; **do not invent a fake check for an unmeasurable property** |
| `unsupported_sections` | indirectly | **defer** — would only ever fire for a template that cannot be released |
| `latex.documentclass_options` | partly (`\d+pt` only) | **defer** — also require the paper option verbatim |
| `latex.*_adjust_in` | test-only | promote into `validate_registry` (recompute margins, require equality) |

Add `ALL_CHECK_IDS` to `format_tex.py`, `content.py` and `structure.py` (mirroring `pdf.py:34-47`) so
Phase 8's checklist and the doc-drift test have a machine-readable inventory.

**Tests (~26):** registry validates; supported has no gaps; experimental blocks are `unknown` not
partial; `status: supported` with a hollow block raises; renderers ⊆ supported; adjust values derive
the margins; a supported template may not allow tables or columns; `recommend` carries the warning and
alternative; `list_templates` exposes `releasable`; `enforcement` covers every block. Then one negative
test per new tex check (itemsep changed, `\linespread{0.85}`, `\onehalfspacing`, `enumerate` body,
`colorlinks`, `titleformat` missing `\scshape` and missing `\titlerule`, `\huge`→`\Large`,
`\large`→`\normalsize`), plus **`test_layout_macro_whitelist_is_contract_driven`** (with
`layout_only_macros: []` the untouched renderer output now fails on `tabularx`), plus the extended
`not_available` parametrization, plus `test_all_check_ids_are_emitted`.
And the headline: **`test_hollowed_contract_blocks_release`** — monkeypatch `get_contract` to return
`page: "unknown", typography: "unknown"`, then assert `released is False` and
`template.contract_complete` in `critical_failures`, proving D-8 is gone. Plus
`test_invalid_registry_is_a_business_error` (not `INTERNAL_ERROR`) and
`test_experimental_template_reports_contract_not_available`.

---

## Phase 6 — Validation and release gate (negative-path proof)

**Already covered** (do not duplicate): wrong page size, wrong margins above and below the floor,
`multicol`, `includegraphics`, comment-stripping, decorative and sans fonts, content `tabular`, 9pt
class and `\scriptsize`, contract-`unknown` degradation with floors still failing; PDF too many pages,
A4-when-letter, 9pt body, crushed margins, corrupt PDF, backend missing, content missing; release
unregistered/experimental/immutable/backend/page-cap/hand-edited/master-changed/repair paths; export
requires release and drafts are labelled; all 14 structure tests; all 21 content tests.

**Scoping note:** "fabrication, unsupported tech/metrics/claims, bad evidence placement" are **not** in
`content.py` — they are patch-time rules in `validators/provenance.py`, already covered by
`test_provenance.py` and `test_patches.py`. Do not duplicate them into the content tests. Add instead
one **layering guard**: `check_content` emits no `provenance.*` id and no `FACTUAL` category — every id
starts with `content.`, every category is `FORMAT`, every source is `etiquette`.

### Remaining work

**Content (must-do: 4):** the layering guard; `banned_opener` parametrized over the *full*
`weak_openers()` tuple (only two are exercised today); `quantified_ratio` boundary asserting
`blocking is False` so a low ratio can never block; `test_every_content_check_id_is_emitted`;
`test_rules_unavailable_fails_closed` (a missing etiquette file ⇒ `RULES_UNAVAILABLE` through the gate,
not a silently skipped rule). *Defer:* the boundary sweeps (39/40/600/601, 220/221, 0/1/6/7).

**Structure (must-do: 4):** duplicate and unknown-section entries in `order` (both problem branches are
unexercised); a heading missing for an ordered section; `test_every_structure_check_id_is_emitted`; and
**`test_source_master_hash_mismatch_is_critical_and_skips_replay`** — when the hash differs,
`provenance.replay` is **not appended at all**, so assert both halves, or someone later reads its
absence as a pass.

**LaTeX (must-do: 6).** All pure string mutation of the module-scoped `tex` fixture — no tectonic, no
files, milliseconds: `minipage`; `textblock` (`textpos`); the untested members of `_FORBIDDEN_ENVS`
(`tikzpicture`, `wrapfigure`, `longtable`); **all five `_FORBIDDEN_CMDS` (`parbox`, `fbox`, `framebox`,
`colorbox`, `fcolorbox`), currently entirely untested**; `twocolumn` as a class option and as a
command (the `columns == 2` branch is untested). *Defer:* `\setmainfont` and `\fontfamily` decorative
branches, no-documentclass, escaped-percent, non-standard class size.

**PDF — fixture strategy. Recommendation: three tiers, with a hand-built-PDF helper as the primary
vehicle. Do NOT commit binary fixture PDFs** — a committed PDF cannot be reviewed in a diff, risks
committing bytes from the developer's real resume (exactly what `.gitignore` and the Privacy section
exist to prevent), and rots silently against contract changes.

New `tests/pdf_fixtures.py` (a helper, not a test module): `minimal_pdf(pages, size, text, base_font,
font_size, x, y, blank_pages)`, `image_only_pdf()`, `encrypted_pdf(source)`, `png_1x1()` — hand-assembled
objects with computed xref offsets, so the same call always produces the same bytes and the validator's
*measured* path runs exactly as in production.

- **Tier A — no tectonic, always runs** (`tests/test_pdf_fixtures.py`): encrypted and truncated-xref
  integrity; A4 and mixed page sizes; `pages=3` over `max_pages`, and **`min_pages` with one page —
  a branch that is completely untested today**; a **non-embedded base-14 font**, which tectonic
  cannot produce and is precisely why the hand-built fixture is the right tool (say so in the
  docstring); one-word text failing `text_extractable`; a blank second page; crushed margins and the
  untested "no text found to measure margins" branch; 9pt body and the untested "no text characters
  found" branch; a missing heading while the section is non-empty; `image_only_pdf` tripping
  `text_extractable` + `empty_pages` + `margins` simultaneously (the scanned-resume case); and the
  untested `columns` multi-column warning branch.
- **Tier B — needs tectonic** (stays in `test_pdf_validation.py`): the existing six, plus two only real
  TeX can produce faithfully — `helvet` yielding a *real embedded* wrong family, and a trailing blank
  page.
- **Tier C — committed fixture PDFs: rejected**, documented as a decision in `docs/testing.md`.

**Skip policy.** Four files each re-derive tectonic availability differently. Consolidate into
`tests/conftest.py` as `HAS_TECTONIC` + a `needs_tectonic` marker, register `needs_tectonic` and `slow`
in `pytest.ini`, add `--strict-markers`, and apply them uniformly. **Yes, mark them** — a
release-candidate run needs `pytest -m needs_tectonic` to prove the gated path *ran* rather than
silently skipped.

### Lifecycle DRAFT → VALIDATED → RELEASED

**Finding:** there is no per-version `validated` state. `validate_version` writes nothing
version-scoped; its only persistence is the workflow's `status` field. **Recommendation: keep
validation stateless — do not add a persisted per-version `validated` flag.** A stored `validated:
true` is a lie the moment the master changes, the registry changes, `rules_version` changes, or the
version file is touched. `release_resume` already re-runs the full `validate()` **inside
`workspace_lock`** and re-hashes the copied PDF, so a stored state could only ever be trusted *less*
than what the gate already does. **The spec's three-state lifecycle is weaker than the
implementation; keep the implementation** and document the lifecycle as
`draft → (stateless re-validation at the gate) → released`.

Concrete work: add a derived, non-persisted `lifecycle_state` (`"released"` if `metadata.released` else
`"draft"`) to `get_workflow_status`'s versions list, `list_versions` (which returns bare ids today),
`validate_version` and `release_resume` — with `validated_at`/`validation_report_id` deliberately
absent. Document the decision in `lib/release.py`'s docstring and `docs/architecture.md`.
**Defer** writing an advisory validation report.

**Artefact immutability.** Existing tests prove the *version YAML* is frozen. Not proven: the report
JSON, the released `.pdf`, the released `.tex`, the staging copy. Add (`@needs_tectonic`): the report is
write-once (`exclusive=True` is load-bearing) and a second release writes no second `rel-*.json`; a
flipped byte in the released PDF and in the released `.tex` are both caught on export (the `tex_sha256`
half is currently untested); **a deleted released file must be a business error, not
`INTERNAL_ERROR` — this is D-10, a real code fix**: wrap the two reads and raise `NOT_RELEASED`
("Released files are missing from the workspace; re-release a new version."); and a sentinel sweep
proving the report carries no resume content, extended over `checks[].measurement` payloads — the
highest-risk leak surface. *Defer:* report-survives-version-tampering, unique report ids, staging vs
delivered.

### `max_pages: 2` for every career stage

**Decision: acceptable for now — but make it visible instead of silent.** `cap = min(contract_max,
stage_max)`, so `director` (3) and `academic` (None) both collapse to 2, and the `release.career_stage`
check is emitted as `pass`/`info` — a director whose third page was cut sees a *passing* check. Change
that one branch to a **`warning`** (non-blocking, so no outcome changes) reading *"Page cap 2 comes from
the template, not your career stage: career_stage 'director' allows 3 page(s) but classic-minimalist
declares max_pages 2. It is the only template with a renderer today; content must fit 2 pages. Never
shrink margins or type to fit."* It flows into `warnings`, the report summary, the audit
`warning_count` and Phase 8's checklist for free. Update `README.md:332` and add one clause to
`CLAUDE.md:166-168`: report that warning to the user verbatim rather than implying their stage cap
applied. Tests: director and academic get the warning, manager does not.

**Verify:** `pytest -m "not needs_tectonic" -q` for the fast loop, then `pytest -m needs_tectonic -q -rs`
to prove the gated path ran; per-layer runs; and a check that no `ALL_CHECK_IDS` tuple is orphaned.
MCP: a 3-page fresher master → `validate` fails `pdf.page_count` → `release` blocked with a report →
repair with `drop_block` → release → export → flip a byte → `NOT_RELEASED` → re-release →
`already_released` with no second report.

---

## Phase 7 — Monitoring

**Already done, do not touch the sanitizer.** `lib/audit.py` is complete for its scope: the 14-name
frozenset with `ValueError` on drift (deliberately re-raised), the 41-key whitelist, structural
`workspace_id` injection, `MAX_STR` + `_SAFE_RE` + `REDACTED`, dict/list dropping, enum validation for
`category`/`severity`, its own lock so it works under the workspace lock, per-append `fsync`, and
never-raises semantics. `lib/metrics.py` derives everything from the log and is rebuildable.

### Event coverage — one addition

Of the spec's twelve names, nine already exist (under our lowercase convention) and two are
deliberately rejected:

- **`MASTER_LOADED` — do not add.** Every mutation already records which master it used
  (`tailor_succeeded.source_master_hash`, `master_updated.master_hash`, the gate's `source.master_hash`).
  A read event would fire on every `get_master_resume` and resource read and drown the log.
  (Phase 1 adds a narrow `master_loaded` from the *tool* only — reconcile to that.)
- **`EVIDENCE_REQUESTED` — do not add per-term.** The only payload worth having is the *term*, and a JD
  term is document content; `term` would be the first whitelisted field carrying document text. The
  aggregate `evidence_prompt_count` already feeds metrics. Record the privacy reason in the docstring
  next to the existing §65 note.
- **`pdf_compiled` — ADD.** This is the one genuine gap: a LaTeX compile failure is today
  indistinguishable in the log from a content failure, so `pdf_success_rate` cannot separate "TeX
  broke" from "PDF measured wrong".

Phase 1's workspace/master events are separate; reconcile both lists in one edit and group the
frozenset by lifecycle stage with a comment per group.

### Emission + the `repair_attempt` defect (D-9)

`server.py:105` logs `1 if repair_of else 0` — a bool in an int field, so a 3rd repair is
indistinguishable from a 1st (the *count* is right because metrics test `> 0`, but the *number* is
always 1). Fix by returning the real value from the library: add
`"repair_attempt": workflow.get("repair_attempts", 0) + (1 if repair_of else 0)` to `tailor`'s result
and read it in the server.

`pdf_compiled` must fire from inside `lib/release.py` (the server wrapper only sees the aggregate), via
a small local best-effort helper that re-raises `ValueError` (unknown event = programming error) and
swallows everything else — a logging failure must never change a release outcome. Fields:
`status`, `duration_ms`, `page_count`, `overfull_count`, `code`, `category="LATEX_PDF"` on failure.

New `ALLOWED_FIELDS`: `page_count`, `overfull_count` (integers, no new sanitizer logic).
**Explicitly rejected, recorded in the docstring so a later phase doesn't quietly add them:** `term`,
`career_stage`, `basename`, `path`, `export_basename`, `jd_title`, `company`, `role`. `career_stage` is
candidate-descriptive; **`export_basename` is a live trap** — `deterministic_filename` builds
`First_Last_Role_Resume` and `release_resume` returns it, so someone will be tempted.

### `system_diagnostics()` — a new tool, not an extension

Two reasons it must not extend `get_workflow_status`: that tool calls `rebuild_metrics()`, which
**writes** `metrics.json` — a read-looking tool with a write side-effect; and it is the workflow-state
tool, documented as such, so overloading it with log forensics muddies both.

New `lib/diagnostics.py` (read-only by construction) + the tool
`system_diagnostics(workflow_id=None, limit=200, errors_only=False)`, returning
`{scope, workflow_id, timeline, event_counts, top_failures{by_category,by_code,by_check_id}, metrics,
health, recommendations, truncated, records_read, limit, note}`. `recommendations` come from a fixed
`REMEDIATION_HINTS` table (code/check-id → one human next step) — **no free-form generation**.
`metrics` uses `compute_metrics` (pure), never `rebuild_metrics`.

**Bounded reading — a real gap.** `read_events` reads the **entire** file with no cap, and both
`rebuild_metrics` and `workflow_summary` call it. Add `MAX_READ_BYTES = 8 MiB`,
`MAX_READ_RECORDS = 50_000`, read from the **end** of the file discarding a partial first line, and a
`tail_events(limit)` fast path for diagnostics. Existing callers keep whole-file semantics up to the
byte cap; note in the metrics docstring that beyond it the rates become "recent history" and the
defence is rotation. Clamp `limit` to `[1, MAX_LIMIT]`. **Defer rotation** but add the hook now:
`health()` reports `log_bytes` and sets `log_rotation_recommended` past half the cap.

### PII audit of the new fields

Exactly two additions survive the review: `page_count` and `overfull_count`. Rejected: contract gap
paths (log `rule_ids` instead), `career_stage`, `export_basename`, nested checklist counts (dicts are
dropped anyway; the existing scalars suffice), `term`.

Two tests carry the weight: `test_allowed_fields_is_a_closed_set` (a change-detector on purpose —
adding a field must be a deliberate test edit, which is where a reviewer sees it) and
`test_no_allowed_field_can_carry_pii`, which for **every** name in the whitelist logs each payload from
a PII corpus (name, email, phone, a resume bullet, a JD sentence, a POSIX path, a Windows path, a dict,
a list) and asserts none appears in the written line. Note the design property it proves: `_SAFE_RE`
excludes commas and most punctuation so free text redacts, but an absolute POSIX path *would* match
it — the protection for paths is that **no path-shaped field is whitelisted**, so assert that by name.
Plus `test_every_event_name_is_emitted_somewhere` (catches a name added but never wired, and vice
versa) and byte-bounded / tail-order tests for the reader.

New `tests/test_diagnostics.py`: writes nothing (mtime snapshot — this is what distinguishes it from
`get_workflow_status`); reconstructs a full run's timeline in order including `pdf_compiled`; top
failure categories; no PII in the serialized result; limit clamped; empty workspace returns cleanly;
recommendations come only from the fixed table; an unknown workflow id is a business error.
Plus **`test_diagnostics_module_has_no_write_calls`** — an AST scan for `open(...,"w"/"a")`,
`write_text`, `write_bytes`, `atomic_write_*`, `mkdir`, `unlink`, `rename`, `shutil`. A structural
guarantee, not a behavioural one.

### Explicitly not building AI self-healing

**The v1 boundary is: log → count → identify patterns → recommend to a human. Nothing in this repo may
act on its own diagnosis.** Named as prohibited: no automatic retry/repair loop (repairs stay
user-initiated, capped at 3, restricted to drop/reorder); no self-modification of
`resume_etiquette.yaml`, `templates.yaml`, thresholds or code — the whole `lib/` tree is read-only at
runtime and the only writable root is the workspace; no threshold auto-tuning from metrics
(`compute_metrics` must stay a pure function of the event list); no relaxation switch (the §65 debug
mode stays unimplemented; no flag widens `ALLOWED_FIELDS` or disables `_clean_value`); no
candidate-quality scoring in monitoring. Placement: the `diagnostics.py` docstring, a "Monitoring
boundary (v1)" section in `docs/architecture.md`, and a line in `CLAUDE.md`'s Prohibited list —
*"Changing rules, thresholds, templates or code in response to a validation failure. Report the
failure and the recommended human action."*

---

## Phase 8 — UX, docs, production hardening

### 8.1 The completion message: server-built, not Claude-assembled

**Recommendation: the server returns a ready-made structured `completion` block. Claude renders it;
Claude does not assemble it.** This is the highest-leverage item in the phase, and it is what stops the
completion message from being the one unverified part of an otherwise fully-verified pipeline.

The drift surface is verified: `CLAUDE.md:56-65` mandates 10 parts, but the data is split across two
tool calls and two turns. `tailor_resume` returns `source`, `source_master_hash`, `template_id`, the
changed/new/dropped block ids, `added_terms` and `not_added` — but **no `template_version`**.
`release_resume` returns `released`, the report summary and the hashes — but **no `template_id`, no
`source`, no `added_terms`/`not_added`**. So the model must remember half of it from an earlier turn,
which is exactly when a message drifts from what happened. `added_terms` is especially good data to
not lose: it is derived from the *saved blocks' own* `source_refs`, i.e. it reports what is genuinely
on the page.

New `lib/reporting.py`:

```python
GROUP_ORDER = ("provenance","evidence","content","template","ats","latex","pdf")
CHECK_GROUP: dict[str, str] = {...}          # explicit ids win
CHECK_GROUP_PREFIX = (("content.","content"),("template.","latex"),
                      ("pdf.","pdf"),("structure.","provenance"))
def group_of(check_id) -> str: ...
def checklist(checks) -> dict:
    """All seven groups, always, so the display cannot silently omit one.
    {group: {status, checks, failed, warnings, not_available}}"""
def completion_summary(version_id, workflow_id, *, ws=None) -> dict:
    """The 10-part contract, assembled from the saved version, its recorded
    patches, its workflow and its release report."""
```

The return keys map 1:1 onto `CLAUDE.md:56-65`: `source`, `source_master_hash`, `document_kind`,
`template{id,version,status,releasable}`, `rules_version`, `added_after_confirmation[{term,category,
section,evidence_id,block_id,why}]`, `not_added[{term,reason,statement}]`,
`optimized{reworded,added,dropped,reordered,patch_count}`, `validation{checklist,passed,
critical_failures,warnings,not_available,check_count,measured_properties}`, `released`,
`release_report_id`, `export_basename`, `tex_sha256`, `pdf_sha256`, `lifecycle_state`, `next_step`,
`display_order`.

`why` is generated deterministically from the placement decision, not free text:
`f"{term} was added to {section} because you confirmed it as {category} ({evidence_id}); {note}"`
where `note` comes from `rules.placement(category, section)` (`"limited"` ⇒ "no high-scope verbs or
metrics were used"). That is the sentence `CLAUDE.md:59-61` asks for, produced from the rules table
instead of from the model's recollection.

Wiring: `release_resume` gains `completion` on **both** the success and the blocked return (a blocked
run also needs a truthful message); `validate_version` gains `checklist`; `tailor_resume` gains the
dropped `template_version` and `repair_attempt`; `CLAUDE.md` step 10 becomes "render
`completion.display_order`, do not recompute or add items", with the 10 parts kept as a fallback for an
older server; the `tailor_resume_workflow` prompt says the same and to show the 7-group checklist.

The load-bearing test is **`test_checklist_covers_every_check_id`** — the union of all five
`ALL_CHECK_IDS` tuples must map into `GROUP_ORDER`, which makes a new check id impossible to forget.
Plus: all seven groups always present (including `not_run` for an empty list); worst status wins;
`added_after_confirmation` references only evidence ids in the version's metadata and block ids that
exist in the saved body; a sentinel sweep proving no resume text (section *names* and block *ids* are
permitted; text is not); the blocked-release shape; `why` sentences come from the placement table.

### 8.2 Evidence-question UX

**Mostly already done** — `_prompt` returns per-term `{term, status, question, categories,
allowed_placement_hint}` and `_question` is already plain language ending in *"If not, say so and it
will not be added."* Four small server additions, so the rules become data rather than prose:
`ask_one_at_a_time` + `prompt_count`, and `order`/`total` on each prompt; `decline_phrasing` and
`not_added_phrasing` per prompt — *"I will not add Kubernetes because no evidence was provided"* —
generated server-side so the wording is identical every time and cannot soften into "I've optimized
around Kubernetes"; a `statement` on a `category="none"` save; and `statement` on each `not_added`
entry so the completion block carries the exact sentence.

`CLAUDE.md` gains two lines (ask one at a time and wait — batching invites a single vague yes; say the
server's `statement` verbatim, do not soften or omit it). The workflow prompt gains the same.

### 8.3 Docs

Create `docs/` (it does not exist): `README.md` (index + routing), **`spec.md`** (the missing numbered
spec, reconstructed with the numbering the code already cites so every existing docstring reference
becomes valid without touching a single `.py`; each section: the invariant, the enforcing module, the
check id or error code, the test file; §65 debug mode recorded as deliberately **not** implemented),
`architecture.md` (corrected tree, the 5 validator layers, the release-gate call order, the stateless-
validation decision, the monitoring boundary), `baseline.md`, `roadmap.md` (these 8 phases, each item
tagged must-do-now/deferred, plus the explicit non-goals: four more renderers, self-healing, debug
mode, a per-version VALIDATED state, committed PDF fixtures), `validation-reference.md` (every check
id: severity, category, source, what makes it fail, which contract field drives it, whether it can be
`not_available`), `templates.md` (contract reference + the 5.6 enforcement table + how to add a
renderer), `testing.md` (marker policy, the three-tier fixture policy and why Tier C is rejected, the
release-candidate run). *Defer* `installation.md`.

**README fixes, all verified:**
1. **Lines 48-58: the architecture tree is wrong** — no workspace level. Also missing: `config.yaml`,
   `master/legacy/`, `data/evidence/`, `data/exports/drafts/`, `data/exports/.staging/<version_id>/`,
   `monitoring/.audit.lock`. **must-do-now.**
2. **Lines 289-296: the Templates table names three template IDs that do not exist** —
   `executive-brief`, `academic-research`, `creative-tech`. The registry has `full-stack-modern`,
   `student-achievements`, `generic-minimal`, `metrics-driven`. This is the most misleading line in the
   docs: it tells a user to request an id that returns `TEMPLATE_UNKNOWN`. **must-do-now**, and covered
   by a test so it cannot recur.
3. **Line 314** — "Modal body character size in 10–12pt" is not what the code does
   (`abs(modal - contract) <= 0.1` with a hard floor at 10.0 and a 2% small-char tolerance). Restate.
4. **Line 332** — the career-stage wording from Phase 6.
5. Known Limitations — add: `spacing`/`formatting` now enforced; validation is stateless and
   `release_resume` always re-validates under the lock; monitoring recommends, never remediates;
   the skill-ID rename limitation (D-6); the shared metric pool looseness.
6. Stop quoting a test count that rots; add `system_diagnostics` to the MCP surface table and the
   undocumented `resume://templates/{template_id}` resource.

### 8.4 Repo hygiene

Verified: `git ls-files data` returns only three `.gitkeep` files — **no personal data is tracked.**
But the working tree is another matter. `_to_delete/master.yaml.stray` contains real PII (a `@gmail`
address, a GitHub handle, a home city), and `data/exports/`, `data/versions/`, `data/jd_history/` and
`output/` hold the developer's real resumes, tailored versions, JD history and PDFs. Those are
gitignored, so they are safe from a commit — but they are one `git add -f` or one
archive-the-folder away from disclosure. And `git check-ignore` confirms `data/evidence/`,
`data/releases/`, `data/tailoring_sessions/` and `.obsidian/` are **not ignored at all**.

1. **Remove `_to_delete/`** — not gitignore it. Move `master.yaml.stray` into the workspace under
   `master/legacy/` if it is still wanted, then delete the directory. **must-do-now.**
2. Replace the per-directory data ignores with a catch-all plus negations (`/data/**`, `!/data/*/`,
   `!/data/*/.gitkeep`, `/output/`, `/_to_delete/`) and add `.obsidian/`, `.idea/`, `.vscode/`.
   **must-do-now.**
3. Document the v1 → v2 data move in the install docs: run `migrate_legacy_data`, verify with
   `list_versions`, then delete the repo-local `data/` and `output/` — v2 never writes there. The
   deletion is the user's call.

New `tests/test_repo_hygiene.py` (pure, fast, no workspace) — these tests *are* the guarantee:
nothing personal is tracked; **every tracked text file contains no PII sentinel** (`@gmail.com`,
`priyanshu`, a real `linkedin.com/in/`, a phone regex), with the synthetic fixtures allow-listed;
the README template table matches the registry (catches finding 2 permanently);
`docs/validation-reference.md` lists every check id and no id the code cannot emit (doc drift in both
directions); `git check-ignore` covers every personal path; and a full release flow changes no mtime
under the repo root. CI: **defer** the workflow file; the tests are the actual guarantee.

### 8.5 Production hardening — small fixes found while reading

Each turns an `INTERNAL_ERROR` into a diagnosable business error: a missing released `pdf`/`tex` →
`NOT_RELEASED` (D-10); `lib/export.py:27`'s bare `ValueError` → `TEMPLATE_UNKNOWN` (D-11); wrapping
`get_contract` → `TEMPLATE_REGISTRY_INVALID` (5.3); and a docstring note that
`get_workflow_status()` rebuilds and caches `metrics.json` — use `system_diagnostics()` for a strictly
read-only view (keep the write; `tests/test_monitoring_acceptance.py:101-111` depends on it).
**Defer:** mtime-caching `templates._load_all`, which re-reads and re-parses the YAML on every lookup
(a warm-path cost, not a correctness problem).

### 8.6 Release-candidate run — three gates

**Gate 1 — full pytest with tectonic present.** `test -x bin/tectonic` first; `pytest -q -rs` expecting
**zero `needs_tectonic` skips**; then `pytest -m needs_tectonic -q -rs`. Acceptance: 0 failures, and
the skip block shows no gated test was skipped. Record the collected count in `docs/baseline.md`.

**Gate 2 — end-to-end MCP smoke**, against a throwaway home, happy path plus every blocking arm:
initialize → set master → list/recommend templates → analyze (prompts with order/total) → evidence
(one real, one `none` returning the "will not be added" statement) → tailor (`added_terms`,
`not_added`, `template_version`) → validate (checklist with all 7 groups) → release (`completion`
block) → export pdf + docx → `system_diagnostics`. Then the negative arms: experimental template →
`template.releasable`; a patch inventing a metric → `PROVENANCE_VIOLATION`; a flipped byte →
`NOT_RELEASED`; an edited master → `source.master_hash`; a second release → `already_released`.
**Acceptance: every negative arm returns a structured error or `released: false` naming a check id —
zero `INTERNAL_ERROR` results — and zero PII in `monitoring/*.jsonl`.**

**Gate 3 — two-workspace isolation** (`tests/test_workspace_isolation_e2e.py`): two homes, two masters,
interleaved workflows. Assert A's `workflow_id`, evidence id and version id are all *not found* in B;
each audit log contains only its own `workspace_id`; each `exports/` holds only its own basenames;
release reports do not cross-reference; and no path under A appears in any file under B. Plus one
genuinely concurrent arm (two threads, one `workspace_lock` each) to prove the workspace lock and the
audit lock do not deadlock across workspaces — `tests/test_locking.py` covers single-workspace
contention, not cross-workspace.

---

## Cross-phase totals

**New modules:** `lib/resolve.py`, `lib/discovery.py`, `lib/diagnostics.py`, `lib/reporting.py`,
`tests/pdf_fixtures.py`, and 8 new test files (`test_resolve`, `test_discovery`,
`test_identity_acceptance`, `test_patch_fuzz`, `test_pdf_fixtures`, `test_diagnostics`,
`test_reporting`, `test_repo_hygiene`, `test_workspace_isolation_e2e`).

**New MCP tools (19 → 26):** `get_workspace_status`, `list_workspaces`, `select_workspace`,
`discover_masters`, `import_master_from_folder`, `get_master_history`, `system_diagnostics`.

**New error codes (6):** `WORKSPACE_AMBIGUOUS`, `WORKSPACE_NOT_FOUND`, `FOLDER_UNSUPPORTED`,
`NO_MASTER_CANDIDATES` (Phase 1), `TEMPLATE_REGISTRY_INVALID` (Phase 5). No new codes in Phases 3-4 —
`PATCH_INVALID` and `PROVENANCE_VIOLATION` cover both classes.

**New audit events (9):** 7 workspace/master lifecycle (Phase 1), `project_entry_added` (Phase 3),
`pdf_compiled` (Phase 7). **Rejected with recorded reasons:** `master_discovery_started`,
`master_selected`, `MASTER_LOADED` as a broad event, `EVIDENCE_REQUESTED`.

**New check ids (10):** `template.contract_complete`, `template.spacing`, `template.heading_style`,
`template.bullet_style`, `template.name_font_size`, `template.heading_font_size`,
`template.link_style`, `release.rules_version_drift`, `structure.new_entry_provenance`,
`structure.unknown_entry`. No new PDF ids — the PDF layer's 12 are complete; four of their branches
were simply untested.

**New patch operation (1):** `add_project_entry`. A new experience/employer/role entry remains
impossible by design.

**Where the spec is weaker than the code — keep the code, and say so in the docs:**
the three-state DRAFT/VALIDATED/RELEASED lifecycle (the gate always re-validates under lock; a stored
VALIDATED state could only be trusted less); contract-driven allowed-structures (the
tables/text-boxes/graphics floors are hardcoded and stricter — constrain the YAML to agree rather than
letting the YAML relax the floor); the `master_selected` audit event (a chat-level act the server
cannot observe — enforce by required signature instead); and `imported_at` on master metadata (it
would break the preview/confirm hash protocol — `history.yaml` instead).
