# v2 baseline — frozen at `54786a5` on 2026-09-27

This is a reproducible statement of what the v2 branch does today, taken before any
of the 8-phase roadmap's changes. Every number here was reproduced by running the
command shown, not copied from a prior report. Re-run the commands to check for
regressions once later phases land.

## How this was produced

```bash
cd /Volumes/Working/mcp-servers/resume-tailor-mcp
git rev-parse HEAD && git status --porcelain
.venv/bin/python -V && .venv/bin/python -m pytest --version
bin/tectonic --version
.venv/bin/python -c "from lib.validators import pdf; print(pdf.detect_pdf_backend())"

.venv/bin/python -m pytest -v                       # full run, with summary line
.venv/bin/python -m pytest --collect-only -q         # per-file counts

# fresh-clone profile: temporarily hide bin/tectonic, restore immediately after
mv bin/tectonic bin/tectonic.hidden
.venv/bin/python -m pytest -q -rs
mv bin/tectonic.hidden bin/tectonic
bin/tectonic --version                               # confirm restored

# MCP surface
.venv/bin/python -c "
import asyncio, server
print('TOOLS:', [t.name for t in asyncio.run(server.mcp.list_tools())])
print('PROMPTS:', [p.name for p in asyncio.run(server.mcp.list_prompts())])
print('RESOURCES:', [str(r.uri) for r in asyncio.run(server.mcp.list_resources())])
print('TEMPLATES:', [r.uriTemplate for r in asyncio.run(server.mcp.list_resource_templates())])
"
```

**Safety gate observed throughout:** `~/.resume-tailor/config.yaml` binds a real
workspace (id redacted here -- it's a real local identifier, not needed to make
this point) and `resources/master_resume.yaml` holds real personal
data. Every command above and every smoke-test call below ran against a throwaway
`RESUME_TAILOR_HOME` under `/tmp`, created fresh and deleted at the end. The real
config's `active_workspace_id` and `created_at` were confirmed unchanged before and
after this session.

## Environment

| | |
|---|---|
| Git HEAD | `54786a500c12ec13bd49354c8c8d8cf982ef3eba` (branch `v2`) |
| Git status | `.obsidian/` and `_to_delete/` untracked (pre-existing); no tracked-file changes |
| Python | 3.14.6 |
| pytest | 9.1.1 |
| Tectonic | 0.17.0, present at `bin/tectonic` (54 MB, gitignored) |
| PDF backend | `{'available': True, 'backends': ['pypdf', 'pdfplumber'], 'poppler': False}` |

**pytest quirk worth recording:** with pytest 9.1.1, `-q` alone (which is what
`pytest.ini`'s `addopts = -q` sets for every contributor) suppresses the final
`"N passed in Xs"` summary line — only the dot-progress lines print. Verified: the
identical run with no `-q`, or with `-v`, prints the summary. A contributor running
plain `pytest` will see progress dots and no total; add `-v` or `-rA` to see the count.

## Test baseline

### With tectonic (this machine)

```
728 passed, 2 warnings in ~14s
```

Both warnings are `ResourceWarning: unclosed file <_io.FileIO ...>` raised from
`.venv/lib/python3.14/site-packages/yaml/nodes.py:27` — third-party (PyYAML), not
application code.

### Without tectonic (fresh-clone profile, verified by hiding `bin/tectonic` and restoring it)

```
700 passed, 28 skipped, reason "tectonic not available"
```

All 28 skips come from four files, matching their guard style exactly:

| File | Guard | Skips |
|---|---|---|
| `tests/test_compile.py` | module-level `pytestmark` | 9 |
| `tests/test_pdf_validation.py` | per-test `@needs_tectonic` | 8 |
| `tests/test_release.py` | module-level `pytestmark` | 11 |
| `tests/test_monitoring_acceptance.py` | per-test `@pytest.mark.skipif` | 3 |

(`test_pdf_validation.py` and `test_monitoring_acceptance.py` decorate individual
tests rather than the whole module, so both files also contribute passing tests in
the fresh-clone profile.)

### Per-file collection table (26 files, 728 tests)

```
test_audit.py                13    test_migration.py              17
test_compile.py                9    test_monitoring_acceptance.py   9
test_evidence.py             32    test_parsing_limits.py          9
test_filenames.py            52    test_patches.py               109
test_ids.py                  31    test_pdf_validation.py         11
test_locking.py              20    test_phase3_acceptance.py      11
test_master_ops.py           14    test_provenance.py             28
test_matching.py             48    test_release.py                11
test_metrics.py               7    test_server.py                  8
                                    test_source_truth.py           13
                                    test_storage.py                47
                                    test_templates.py              65
                                    test_validators_content.py     21
                                    test_validators_structure.py   14
                                    test_validators_tex.py         23
                                    test_workspace.py              79
                                    test_yaml_safety.py            27
                                                          TOTAL:   728
```

## Current working features

Each of README's 8 numbered claims (README.md:34-41), checked against source and, where
practical, against a live call in the smoke run below.

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Workspace isolation under `~/.resume-tailor/`, resume/cv kept separate | **verified, with a caveat** | `lib/workspace.py`; smoke run steps 1-4. Caveat: the tree is actually `$RESUME_TAILOR_HOME/workspaces/<id>/...`, not the flat layout the README shows — see Doc Drift below, and see D-1. |
| 2 | Evidence-gated tailoring | verified | smoke run steps 5-6; `lib/evidence.py` |
| 3 | Structured patches, no resume body accepted | verified | `server.py` `tailor_resume` signature takes `patches`, not a resume; confirmed by a live malformed-patch rejection in the smoke run (`PATCH_INVALID`) |
| 4 | 8 provenance rules on every patch | verified | `lib/validators/provenance.py`; `tests/test_provenance.py` (28 tests) |
| 5 | Only `classic-minimalist` is `supported` with a renderer | verified | `lib/templates.py`; `resources/templates/templates.yaml` |
| 6 | Release gate compiles + validates + checks page cap + replay + master hash | verified | smoke run steps 8-9; `lib/release.py` |
| 7 | Released versions are immutable | verified | `lib/storage.py` (`VERSION_RELEASED` on `save_version`/`update_version_metadata`); `tests/test_release.py:76` |
| 8 | Audit logs are PII-whitelisted | verified | smoke run audit.jsonl grep for name/email/phone returned 0 matches |

## Current MCP tool inventory

**19 tools, 2 prompts, 3 static resources, 6 resource templates** (enumerated live via
`server.mcp.list_tools()` etc., not read from docs):

```
initialize_workspace, get_workspace, migrate_legacy_data, list_workspace_size,
set_master_resume, analyze_tailoring_requirements, save_tailoring_evidence,
match_resume_to_jd, get_master_resume, tailor_resume, diff_versions, score_ats,
list_templates, recommend_template, validate_version, release_resume, export_resume,
get_workflow_status, list_versions

prompts: tailor_resume_workflow, quick_ats_check
resources: resume://master, resume://templates, resume://etiquette
resource templates: resume://master/{kind}, resume://sections/{name},
  resume://sections/{kind}/{name}, resume://versions/{version_id},
  jd://history/{jd_id}, resume://templates/{template_id}
```

Note: `resume://templates/{template_id}` exists but is not documented in the README's
MCP Surface Reference table.

## Current template behavior

5 registered ids in `resources/templates/templates.yaml`. Only `classic-minimalist`
(`status: supported`, `version: 1.1.0`) has a renderer (`lib/latex.py`) and a fully
specified contract. The other four (`full-stack-modern`, `student-achievements`,
`generic-minimal`, `metrics-driven`) are `status: experimental` with most contract
fields set to the literal `"unknown"`. No silent fallback: requesting an experimental
id is accepted and recorded, but `release_resume` blocks on `template.releasable`
(critical) and `export_resume(mode="draft")` returns `TEMPLATE_NO_RENDERER`.
`max_pages: 2` applies to every career stage today, since `classic-minimalist` is the
only releasable template and it declares that limit regardless of the master's
`career_stage`.

**Defect found (D-8, see below):** a contract field of `"unknown"` degrades its
matching validator check to `not_available`, not `fail`, and
`ValidationReport.passed` only counts `status == "fail"` checks with blocking
severity. So a hand-edited `templates.yaml` that hollows out `classic-minimalist`'s
contract (e.g. sets `page: "unknown"`) would let a release **pass** with the
page-size, margin and font checks silently unenforced — only the hardcoded floors
(`MIN_MARGIN_IN`, `MIN_BODY_PT`) would still catch anything. Not exercised in the
live smoke run (the shipped `templates.yaml` is intact); confirmed by reading
`lib/schemas.py:330-345` and `lib/validators/format_tex.py` / `pdf.py`'s `not_available`
branches.

## Current PDF behavior

Tectonic at `bin/tectonic`, invoked via `lib/export.py`. Two check tiers on release:
inferred checks from the emitted `.tex` source (`lib/validators/format_tex.py`) and
measured checks from the compiled PDF via pypdf + pdfplumber
(`lib/validators/pdf.py`). Poppler is absent on this machine
(`pdf.detect_pdf_backend()['poppler'] == False`), which the code tolerates — pypdf +
pdfplumber alone are sufficient for `detect_pdf_backend()['available'] == True`.
If both pypdf and pdfplumber were missing, PDF validation would be unavailable and
production release would be blocked (`BLOCKED_MESSAGE` in `lib/validators/pdf.py`).

Live smoke-run measurement (`validate_version` on a real 1-page tailored resume):
page count 1, page size 612×792pt (US Letter), embedded fonts all Latin Modern Roman
variants, body font 9.96pt (within tolerance of the 10pt contract), margins
0.57–0.6in on the measured sides. 44 checks ran; 0 critical failures; 2 non-blocking
warnings (`content.quantified_ratio`, `content.dates_present` — the synthetic test
master intentionally omits dates).

## Known limitations (README.md:325-333, verbatim, each annotated)

- **No new project/experience entries via patches.** *(still true — confirmed:
  `add_block` requires an existing `experience`/`project`/`education` parent;
  `replace_block` is capped to summary + bullets. Roadmap Phase 3.5 adds
  `add_project_entry` for projects only; a new employer/role entry remains
  impossible by design in every future phase too.)*
- **No deterministic truncation.** *(still true.)*
- **Font check is by family name.** *(still true — confirmed at
  `lib/validators/pdf.py` prefix match against `pdf_font_prefixes`.)*
- **Unknown-requirement detection is heuristic.** *(still true — confirmed live:
  the smoke-run JD's "LangChain" was flagged `unknown` even though the master's own
  Projects bullet text mentions LangChain, because unknown-requirement detection
  runs on JD text only and is never cross-checked against the master. This is a
  real, reproducible defect the roadmap's Phase 3.1 fixes.)*
- **Repair limit is 3 per workflow.** *(still true — `MAX_REPAIR_ATTEMPTS = 3` in
  `lib/workflows.py:29`.)*
- **`classic-minimalist` only, `max_pages: 2` for every career stage.** *(still
  true — confirmed in the template registry; the roadmap's Phase 6.7 makes this
  visible as a `warning`-severity check rather than a silent `pass` for
  director/academic stages.)*
- **Substring matching replaced by word-boundary regex, with alias normalization.**
  *(still true, not independently re-verified this session.)*

## Known defects found while freezing

All of the following were verified this session either by direct code reading or by
live reproduction in the smoke run (marked accordingly).

| ID | Defect | Location | Verification |
|---|---|---|---|
| D-1 | Deleting `config.yaml` mints a brand-new empty workspace and silently orphans the previous one — the master, the released version, and the evidence all stay on disk but become unreachable | `lib/workspace.py:195-218` | **live-reproduced**: deleted `config.yaml` in a workspace holding a real master + release, re-ran `initialize_workspace()`, got a second empty workspace bound as active while the first (with `masters_present: false` from the new binding's perspective) sat untouched on disk |
| D-2 | `set_master_resume(resume=<version dict>)` can promote a tailored version to master | `lib/schemas.py:173` (`MasterMetadata` is `extra="allow"`) + `lib/ids.py:64` (`normalize_master` forces `metadata.kind`) | code-verified only this session (not exercised live) |
| D-3 | `analyze_tailoring_requirements` doesn't check master readiness, so an unparsed master only fails at `tailor_resume` — after every evidence question has already been answered | `lib/evidence.py:145` vs `lib/tailoring.py:132-137` | code-verified |
| D-4 | `WorkflowMetadata` has no `source_master_hash`; a workflow that never tailors records no master identity | `lib/schemas.py:381-396` | code-verified |
| D-5 | `master/history.yaml` is written on every save and read by nothing | `lib/storage.py:144-148` | code-verified |
| D-6 | `skill-<slug>` IDs are content-derived and excluded from `id_counters`; renaming a skill breaks older versions' refs to it | `lib/ids.py:96` | code-verified; **live-confirmed shape**: the smoke run's imported master produced `skill-python-docker-postgresql-aws` (slug of the whole comma-joined skills line, since the markdown parser did not split it) |
| D-7 | `MigrationInfo.legacy_path` stores a full filesystem path in master metadata, returned to the model by `get_master_resume` | `lib/schemas.py:167` | code-verified |
| D-8 | A hollowed `templates.yaml` lets release pass with contract checks `not_available` instead of `fail` | `lib/schemas.py:330-345` | code-verified (see Current template behavior above) |
| D-9 | `server.py:105` logs `repair_attempt` as `1 if repair_of else 0` — a bool in an int field, so a 3rd repair is indistinguishable from a 1st in the audit log | `server.py:105` vs `lib/tailoring.py:207` | code-verified |
| D-10 | A missing released `pdf`/`tex` file raises `FileNotFoundError` → generic `INTERNAL_ERROR` instead of a business error | `lib/release.py:307` | code-verified |
| D-11 | An unknown template id in `lib/export.py:27-28`'s `_section_order` raises a bare `ValueError` → `INTERNAL_ERROR` | `lib/export.py:27` | code-verified |

## Doc drift

- **README.md:48-58** documents `~/.resume-tailor/master/...` with no `workspaces/<id>/`
  level. Actual layout, confirmed live: `$RESUME_TAILOR_HOME/config.yaml` +
  `$RESUME_TAILOR_HOME/workspaces/<workspace_id>/{master/...,data/...,monitoring/...}`.
  Also missing from the tree: `master/legacy/`, `data/evidence/`, `monitoring/.audit.lock`.
- **README.md:289-296** — the Templates table lists `executive-brief`,
  `academic-research`, `creative-tech` as experimental template ids. **These do not
  exist.** The actual registry (confirmed via `templates.yaml`) has
  `full-stack-modern`, `student-achievements`, `generic-minimal`, `metrics-driven`.
  This is the single most misleading line in the docs: it tells a user to request an
  id that returns `TEMPLATE_UNKNOWN`.
- **README.md:350** — "For development: `pip install -r requirements-dev.txt && pytest`
  (728 tests)." The count is correct today but will rot as the roadmap adds tests;
  it also doesn't mention that plain `pytest` (via `addopts = -q`) won't print the
  total without `-v`/`-rA` (see the pytest quirk above).
- Repo working tree contains v1-era personal/local state that is gitignored but not
  removed: `resources/master_resume.yaml`, `data/{versions,jd_history,exports}/`,
  `output/`, and `_to_delete/master.yaml.stray` (confirmed to contain a real
  `@gmail.com` address and a GitHub handle). None of this is tracked by git
  (`git ls-files data` returns only `.gitkeep` files), but it sits in the working
  tree one `git add -f` away from disclosure.
- An untracked `_incoming/` directory (a `.DS_Store` and a `resume_template_collection/`
  folder) is present in the working tree, predating this session and unrelated to
  the smoke run. Noted for completeness; not investigated further.

## Smoke run transcript

Full end-to-end sequence run against a throwaway `RESUME_TAILOR_HOME`
(`/tmp/rt-smoke1-*`, deleted after this session), calling `server.*` functions
directly — the same functions an MCP client invokes as tools.

| # | Call | Result |
|---|---|---|
| 1 | `initialize_workspace()` | `ok`, new workspace `RT-B6FF0B2A`, `masters_present: {resume: false, cv: false}` |
| 2 | `get_workspace()` | `ok`, matches step 1 |
| 3 | `set_master_resume(kind="resume", file_path=<synthetic .md>, career_stage="3-5")` | `ok`, `applied: true`, 5 sections found, `unparsed_items: 0` |
| 4 | `get_master_resume(kind="resume")` | `ok`, citable block ids confirmed in the shape `exp-001`, `exp-001-b01`, `skg-001`, `skill-<slug>`, `edu-001`, `proj-001` |
| 5 | `analyze_tailoring_requirements(jd_text=<JD requiring Python/FastAPI/AWS/Kubernetes, nice-to-have LangChain>)` | `ok`, `confirmed: [aws, python]`, `missing: [fastapi, kubernetes]`, `unknown_requirements: [LangChain]` — reproduces the unknown-requirement defect above |
| 6 | `save_tailoring_evidence(term="fastapi", category="personal_project", evidence_text=..., confirmed=True)` | `ok`, evidence saved with the "may not go beyond what evidence_text states" note |
| 6b | `save_tailoring_evidence(term="kubernetes", category="none", confirmed=True)` | `ok`, "will be reported as not added" note |
| 7 (attempt 1) | `tailor_resume` with a malformed `add_block` patch (flat `text`/`source_refs` instead of nested `new_content`) | `ok: false`, `PATCH_INVALID`, exact field-level pydantic errors — **this was my own test-script mistake**, kept in the transcript as a genuine example of the server rejecting malformed input rather than guessing |
| 7 (attempt 2) | corrected `tailor_resume` | `ok`, version `smoke-v1` saved, `added_terms` includes fastapi under Projects, `not_added` includes kubernetes. **No `template_version` key in the response** — confirms the Phase 8.1 finding directly |
| 8 | `validate_version("smoke-v1", wf)` | `ok`, `passed: true`, 44 checks, 0 critical, 2 warnings (quantified_ratio, dates_present) |
| 9 | `release_resume("smoke-v1", wf)` | `ok`, `released: true`, `release_report_id: rel-fc101211` |
| 10 (attempt 1) | `export_resume(version_id=..., ...)` | `ok: false`, `INTERNAL_ERROR` — **my own mistake**: the parameter is named `version`, not `version_id` |
| 10 (attempt 2) | `export_resume(version="smoke-v1", workflow_id=wf, format="pdf", mode="release")` | success — PDF + LaTeX delivered with the "RELEASED" label and the client-attachment fallback note |
| 11 | `export_resume(version="master-resume", mode="draft", format="pdf")` | success — draft export of the raw master, correctly labelled |
| 12a | `list_workspace_size()` | `ok`, 104,624 bytes across 8 areas |
| 12b | `get_workflow_status()` (no arg) | `ok`, system-wide metrics: 1 workflow, 100% validation/template/PDF pass rates, `provenance_pass_rate: 0.5` (reflecting my one malformed-patch mistake) |
| 12c | `get_workflow_status(workflow_id=wf)` | `ok`, full workflow detail including the persisted trimmed analysis and a 10-event audit summary |
| 13 | `cat monitoring/audit.jsonl` | 14 events recorded in order; grep for the synthetic candidate's name/email/phone across the whole file returned **0 matches** |
| 14 | `git status --porcelain` (repo) | unchanged from session start (`.obsidian/`, `_to_delete/` only — plus an unrelated pre-existing `_incoming/`) |
| 15 (negative probe) | `analyze_tailoring_requirements(jd)` in a second fresh home with no master | `ok: false`, `MASTER_NOT_FOUND`: *"No master resume in this workspace yet. Create one with set_master_resume (or the create-master-file skill), or migrate_legacy_data."* — recorded verbatim; Phase 1 rewrites this to explicitly refuse memory/chat substitution and point to `discover_masters` |
| 16 (negative probe) | `rm config.yaml; initialize_workspace()` in the first home | **D-1 reproduced live**, as described above |

Real workspace (`~/.resume-tailor/`) and repo tracked files were confirmed byte-for-byte
unchanged before and after this session; both throwaway homes were deleted at the end.

---

## Addendum: Phase 8.6 Gate 1 (2026-09-28)

Recorded per `docs/full-plan.md:1412-1435` ("record the collected count in
docs/baseline.md"). This is a running note, not a re-freeze of the section
above -- the original Phase-0 snapshot stays as written.

```
$ test -x bin/tectonic && echo present   # present (also on PATH: /opt/homebrew/bin/tectonic)
$ pytest -q -rs
1049 passed in 27.16s
$ pytest -m needs_tectonic -q -rs
54 passed, 995 deselected in 17.34s
```

**Acceptance met:** 0 failures, 0 skips in the full run (no `needs_tectonic`
test was gated out), and the `needs_tectonic`-only run independently confirms
all 54 of them pass with tectonic present. Collected count: **1049** (up from
728 at the Phase-0 freeze -- Phases 1-7 and 8.1/8.2/8.4/8.5 added roughly 320
tests across this multi-session effort).
