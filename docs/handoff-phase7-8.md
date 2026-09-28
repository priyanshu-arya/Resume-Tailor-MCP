# Session handoff — Phases 7 & 8 (Monitoring; UX/docs/production hardening)

## Status as of 2026-09-28 (this session, continued)

**Update: 8.2 and 8.6 are now also done; all three gates pass.** Since the
note below was written:

- **8.2 done.** `lib/evidence.py`: `order`/`total` on every prompt,
  `ask_one_at_a_time`/`prompt_count` on `analyze_requirements`'s result,
  `decline_phrasing`/`not_added_phrasing` per prompt (identical text --
  `"I will not add {term} because no evidence was provided."`), and a
  `statement` field on `category="none"` saves (including the dedupe/reuse
  path). `CLAUDE.md` and the `tailor_resume_workflow` prompt both now say
  to ask one at a time and to say the `statement` verbatim, and both now
  point at the `completion` block from 8.1 instead of the old hand-assembled
  10-part list.
- **8.6 done -- all three gates pass, recorded in `docs/baseline.md`'s
  addendum:**
  - **Gate 1**: `pytest -q -rs` -> 1049 passed (now 1054 after Gate 3's own
    test file), 0 failures, 0 `needs_tectonic` skips; `pytest -m
    needs_tectonic -q -rs` -> 54 passed independently.
  - **Gate 2**: ran by hand against a throwaway home (script, not a
    committed test) -- full happy path including the new `order`/`total`
    prompts, a `none`-category save with the exact `statement`,
    `template_version`/`repair_attempt` on `tailor_resume`, the 7-group
    checklist on `validate_version`, a `completion` block on
    `release_resume`, PDF+DOCX export, `system_diagnostics`. Then all 5
    negative arms (experimental template, fabricated metric, flipped PDF
    byte, edited master, second release) -- every one returned a structured
    error or `released: false` naming a check id, zero `INTERNAL_ERROR`,
    zero PII in `monitoring/*.jsonl`.
  - **Gate 3**: new `tests/test_workspace_isolation_e2e.py` (5 tests, kept
    as a committed test unlike Gate 2's throwaway script) -- two live
    workspaces, interleaved workflows, cross-workspace id lookups all raise
    `WORKFLOW_NOT_FOUND`/`VERSION_NOT_FOUND`, each audit log has only its
    own `workspace_id`, exports/release reports don't cross-reference, no
    path under A appears under B, and a genuine two-thread/two-workspace-lock
    concurrent arm completes without deadlock.
- One remaining Phase 7 loose end closed: the "no AI self-healing" boundary
  is now written in all three places the plan asks for -- new
  `docs/architecture.md` has a "Monitoring boundary (v1)" section (plus a
  short, honest "still open" list for the rest of 8.3).

**What's still open:** 8.3 beyond the one section above (`docs/spec.md`,
`docs/roadmap.md`, `docs/validation-reference.md`, `docs/templates.md`, and
README items 3-6 beyond the template table). That's a substantial,
independent effort and is the one clearly unfinished piece of this whole
phase-7/8 handoff.

Full suite: **1054 passed, 0 failures** (`pytest -v`).

## Status as of 2026-09-27 (earlier in this session)

**Phase 7: complete.** D-9 fixed (`repair_attempt` is now a real count, tested
with 3 sequential repairs -> `[1, 2, 3]`); `pdf_compiled` added and emitted
from `lib/release.py` around the compile step, on both success and failure;
bounded log reading (`MAX_READ_BYTES`, `MAX_READ_RECORDS`, `tail_events`)
added to `lib/audit.py`; `lib/diagnostics.py` + `system_diagnostics()` tool
added (read-only by construction, AST-scan-tested); PII audit tests added
(`test_allowed_fields_is_a_closed_set`, `test_no_allowed_field_can_carry_pii`
-- the latter caught and fixed a real gap: `applied`/count/hash fields had no
type check and would have let a free-text value with no comma/paren survive
`_SAFE_RE` unredacted; they're now typed via `_BOOL_FIELDS`/`_INT_FIELDS`/
`_HASH_FIELDS`); the no-self-healing boundary is written in three places
(`lib/diagnostics.py` docstring, `CLAUDE.md`'s Prohibited list; the third,
`docs/architecture.md`, is Phase 8.3 work and not yet created).

**Phase 8: 8.1 and part of 8.4/8.5 done; 8.2, 8.3, 8.6 not started.**
- **8.1 done.** `lib/reporting.py` (`checklist`, `completion_summary`,
  `group_of`, `GROUP_ORDER`, `CHECK_GROUP`/`CHECK_GROUP_PREFIX`) is wired
  into `release_resume` (both the released and blocked returns) and
  `validate_version`. Required adding `ALL_CHECK_IDS` tuples to
  `lib/validators/{content,structure,format_tex}.py` (only `pdf.py` had one)
  plus a **fifth** tuple in `lib/release.py` itself for the checks the
  release gate emits directly (`source.master_hash`, `provenance.replay`,
  `template.releasable`, `release.career_stage`, `template.renderer`,
  `latex.compile`, `pdf.backend`) -- `lib/validators/provenance.py` turned
  out not to participate in this at all (it emits patch-rejection `rule`
  strings at tailor-time, not release-time `Check` objects; don't add an
  `ALL_CHECK_IDS` there). `test_checklist_covers_every_check_id` in
  `tests/test_reporting.py` unions all five.
- **8.2 not started** (`ask_one_at_a_time`/`prompt_count`, `order`/`total` on
  prompts, `decline_phrasing`/`not_added_phrasing`, `statement` on a
  `category="none"` save). Note `reporting.completion_summary`'s `not_added`
  entries already carry a `statement` field (server-generated, "I will not
  add X because ...") since that was needed for the 10-part contract: 8.2's
  remaining work is threading the same phrasing into `save_tailoring_evidence`
  and the `_prompt` question shape.
- **8.3 (docs) not started** beyond one item: the README template table
  (finding 2 -- `executive-brief`/`academic-research`/`creative-tech` don't
  exist) was re-verified **still broken** despite an earlier handoff claiming
  it was fixed in Phase 1, and is now fixed to list the real 8 registered
  ids. `docs/spec.md`, `architecture.md`, `roadmap.md`,
  `validation-reference.md`, `templates.md`, `testing.md` are all still to
  write. README items 3-6 (font-check wording, career-stage wording,
  Known Limitations additions, stale test count, `system_diagnostics` in the
  MCP surface table) also still open.
- **8.4 partly done.** `_to_delete/master.yaml.stray` (real PII -- confirmed
  and removed with the user's explicit go-ahead, not silently) is gone;
  `.gitignore` now uses the catch-all + negation pattern
  (`/data/**`, `!/data/*/`, `!/data/*/.gitkeep`) plus `.obsidian/`/`.idea/`/
  `.vscode/`; `tests/test_repo_hygiene.py` written (13 tests: no personal
  dir tracked, no PII sentinel in a tracked text file, README template table
  matches the registry, every personal path is actually ignored). Not done:
  the install-docs note about deleting repo-local `data/`/`output/` after
  `migrate_legacy_data`.
- **8.5 mostly done.** D-10 (missing released file -> `INTERNAL_ERROR`) fixed
  in `lib/release.py`'s export path (`OSError` -> `NOT_RELEASED`); D-11
  (`lib/export.py:27` bare `ValueError`) fixed -> `TEMPLATE_UNKNOWN`; both
  tested. `TEMPLATE_REGISTRY_INVALID` wrapping around `get_contract` was
  already done by a concurrent session's Phase 5 work (confirmed present in
  `lib/templates.py`) -- not redone here. Deferred: mtime-caching
  `templates._load_all` (explicitly deferred by the plan itself).
- **8.6 (the three release-candidate gates) not run.** This is the largest
  remaining item and should be done once 8.2/8.3 land, since Gate 2's smoke
  test exercises the evidence-prompt phrasing 8.2 adds.

**Note for the next session:** another session was live-editing
`lib/templates.py` and `lib/evidence.py` concurrently while this one ran
(confirmed by file mtimes during the session, not inferred). Its work is
intact -- verify with `git log` / `git status` before assuming this handoff's
"what's done" list is complete, same as the standing instruction in
`docs/handoffs.md`. Two pre-existing failures in `tests/test_evidence.py`
(`test_result_shape_and_no_jd_text`, `test_stored_record_fields`, both
about an undocumented `prompt_reason` field) were present before this
session touched anything and are that session's in-flight Phase 3/4 work,
not addressed here.

Read this first. Full phase detail is in **`docs/full-plan.md`**:
- Phase 7 — lines 1160–1266
- Phase 8 — lines 1268–1436
- Cross-phase totals (useful summary table) — lines 1438–1471

This file is the *delta*: what's already done (mostly from Phase 1, which
anticipated some of Phase 7's audit work), what's left, what to watch for.

## Repo state when this handoff was written

Phases 0–6 assumed complete before starting this one (check `git log` / the
other two handoff docs to confirm — if Phases 3-6 haven't run yet, most of
Phase 7 can still proceed independently since it's additive to `lib/audit.py`,
but Phase 8's `lib/reporting.py` needs Phase 6's `ALL_CHECK_IDS` tuples to
exist in all four validator modules first).

`pytest -v 2>&1 | tail -3` for the current count — this pytest version needs
`-v`/`-rA` to print the summary line, plain `-q` won't.

## Already done from Phase 7 (do not redo)

Phase 1's audit work anticipated part of Phase 7 — check before re-adding:

- **`lib/audit.py`'s `EVENTS`** already has the Phase-1 workspace/master
  lifecycle names (`workspace_selected`, `workspace_switched`,
  `master_discovered`, `master_imported`, `master_created`, `master_loaded`,
  `master_conflict`) grouped with a comment. **`master_loaded` is already
  scoped narrowly to the `get_master_resume` tool only** (server.py's
  `_success_events`), exactly matching Phase 7's reconciliation note at
  `docs/full-plan.md:1173-1176` — do not widen it to fire on every internal
  master read.
- **`ALLOWED_FIELDS`** already has `from_workspace_id`, `to_workspace_id`,
  `candidate_count`, `entries_examined`, `source_hash`, `workspace_count`,
  `state` from Phase 1. Still needs Phase 7's `page_count`, `overfull_count`.
- Phase 3/4's audit work (if that session ran) may have already added
  `evidence_category`, `prompt_reason`, `requirement_type`, `weak_count`,
  `supported_count`, `new_entry_count`, `rules_version`, and the
  `project_entry_added` event — check `lib/audit.py` before re-adding.

## What's NOT done from Phase 7 — pick up here

1. **`pdf_compiled` event** — not added yet. Add to `EVENTS`, emit from inside
   `lib/release.py` (not the server wrapper, which only sees the aggregate) via
   a small best-effort local helper that re-raises `ValueError` and swallows
   everything else. Fields: `status`, `duration_ms`, `page_count`,
   `overfull_count`, `code`, `category="LATEX_PDF"` on failure.
2. **`repair_attempt` defect (D-9) — still present, a real one-line-ish fix.**
   `server.py` around line 105 currently logs
   `"repair_attempt": 1 if args.get("repair_of") else 0` — a bool coerced into
   an int field, so a 3rd repair is indistinguishable from a 1st. Fix: add
   `"repair_attempt": workflow.get("repair_attempts", 0) + (1 if repair_of else
   0)` to `lib/tailoring.py`'s `tailor()` result dict, then read
   `result.get("repair_attempt", 0)` in the server instead of recomputing it
   there. Verify with a test that does 3 sequential repairs and checks the
   logged values are `1, 2, 3`, not `1, 1, 1`.
3. **`MASTER_LOADED` (broad) and `EVIDENCE_REQUESTED` (per-term) — deliberately
   rejected, do not add.** Reasons are in `docs/full-plan.md:1173-1180`
   (mainly: a per-request event's only useful payload is the *term*, and a JD
   term is document content — `term` would be the first free-text field in the
   whitelist).
4. **Bounded log reading — `lib/audit.py`'s `read_events` reads the entire
   file with no cap.** Add `MAX_READ_BYTES = 8 * 1024 * 1024`,
   `MAX_READ_RECORDS = 50_000`, read from the end discarding a partial first
   line, and a `tail_events(limit)` fast path. This is needed before
   `system_diagnostics` (next item) — don't build that on top of the unbounded
   reader.
5. **`lib/diagnostics.py` + `system_diagnostics()` tool.** Read-only by
   construction — full return shape and the `REMEDIATION_HINTS` fixed-table
   design (no free-form text generation) at `docs/full-plan.md:1213-1218`.
   Must NOT extend `get_workflow_status` — that tool calls `rebuild_metrics()`
   which **writes** `metrics.json`, and `system_diagnostics` must never write
   anything (there's a dedicated AST-scan test for this, see below).
6. **PII audit of the new fields.** Only `page_count`/`overfull_count` survive
   review; a full rejected list is at `docs/full-plan.md:1230-1232`. The two
   tests that carry the weight: `test_allowed_fields_is_a_closed_set`
   (asserts the literal set, so any addition is a deliberate, reviewable test
   edit) and `test_no_allowed_field_can_carry_pii` (sweeps a PII corpus
   through every whitelisted field name).
7. **New test file `tests/test_diagnostics.py`** — writes-nothing check (mtime
   snapshot), full-timeline reconstruction, top-failure categories, PII sweep,
   limit clamping, empty-workspace handling, and
   `test_diagnostics_module_has_no_write_calls` (an AST scan for
   `open(...,"w")`/`write_text`/`atomic_write_*`/`mkdir`/`unlink`/`shutil` —
   structural, not behavioural).
8. **Explicitly write down the "no AI self-healing" boundary** — three places:
   `lib/diagnostics.py`'s docstring, a new "Monitoring boundary (v1)" section
   in `docs/architecture.md` (Phase 8 creates this file), and one line added
   to `CLAUDE.md`'s Prohibited list.

## Phase 8 — do these roughly in this order

**8.1 is the highest-leverage item in the whole phase — do it first.**
`lib/reporting.py` (`checklist()`, `completion_summary()`,
`group_of()`/`GROUP_ORDER`/`CHECK_GROUP`) makes the 10-part completion message
and 7-group validation checklist **server-assembled data**, not something
Claude reconstructs from memory across two tool calls (which is exactly when
it drifts — verified: `tailor_resume` today has no `template_version`,
`release_resume` today has no `template_id`/`source`/`added_terms`). Full
field-by-field spec and the `why`-sentence generation rule (deterministic, from
`lib.rules.placement`, never free text) at `docs/full-plan.md:1270-1327`. The
load-bearing test is `test_checklist_covers_every_check_id` — it unions all
`ALL_CHECK_IDS` tuples from Phase 6's validator modules, so this genuinely
needs Phase 6 done first (or at least those tuples to exist).

**8.2** — mostly prose/small additions on top of what's already correct
(`_prompt`'s plain-language question already exists). Add `order`/`total` to
prompts, `decline_phrasing`/`not_added_phrasing` (server-generated so the
exact sentence — *"I will not add Kubernetes because no evidence was
provided"* — can't soften into something vaguer), and a `statement` field on
`category="none"` evidence saves.

**8.3 — docs.** `docs/` already exists (this repo has `baseline.md`,
`spec-map.md`, `full-plan.md`, and this file plus its two siblings). Still
needed: `docs/spec.md` (the actual missing numbered spec reconstruction —
**note `docs/spec-map.md` already exists and does a version of this**; check
whether it satisfies this requirement before building a second one),
`docs/architecture.md`, `docs/roadmap.md`, `docs/validation-reference.md`,
`docs/templates.md`, `docs/testing.md`. Six concrete README fixes are listed
at `docs/full-plan.md:1357-1373` — **items 1 and 2 there (architecture tree,
templates table) were already fixed in Phase 1**; re-check before redoing,
but items 3–6 (font-check wording, career-stage wording from Phase 6, Known
Limitations additions, stale test count) are still open.

**8.4 — repo hygiene.** Verified findings (re-check they're still true, the
working tree may have changed): `_to_delete/master.yaml.stray` has real PII
and should be removed, not gitignored; `data/evidence/`, `data/releases/`,
`data/tailoring_sessions/` and `.obsidian/` are **not gitignored at all**
(confirmed via `git check-ignore` returning nothing for them). New
`tests/test_repo_hygiene.py` is the actual guarantee here, not the `.gitignore`
edit alone — write the tests.

**8.5 — small hardening fixes**, each turning an `INTERNAL_ERROR` into a
diagnosable business error. Check which are already fixed:
- D-10 (missing released file → `INTERNAL_ERROR`) — **status unknown at time
  of writing; check `lib/release.py` around the export path before
  redoing.**
- D-11 (`lib/export.py:27`'s bare `ValueError` on unknown template id) —
  **same, check before redoing.**
- `TEMPLATE_REGISTRY_INVALID` wrapping — this is Phase 5's item; only relevant
  here if Phase 5 hasn't run yet.

**8.6 — the three-gate release-candidate run.** This is the final proof this
whole plan actually holds together. Full detail at
`docs/full-plan.md:1412-1435`. Gate 3 (`tests/test_workspace_isolation_e2e.py`)
is worth doing even if earlier phases' isolation tests already cover pieces of
it — this is the first place a **genuinely concurrent** two-thread,
two-workspace-lock arm gets tested, which nothing else in the suite does.

## Verification

```bash
cd /Volumes/Working/mcp-servers/resume-tailor-mcp
.venv/bin/python -m pytest -v 2>&1 | tail -10
.venv/bin/python -c "
from lib.audit import EVENTS, ALLOWED_FIELDS
print(len(EVENTS), sorted(EVENTS)); print(len(ALLOWED_FIELDS))"
.venv/bin/python -c "
from lib import audit
try: audit.build_record('pdf_rendered', None, {})
except ValueError as e: print('unknown-event guard still works:', e)"
RESUME_TAILOR_HOME=/tmp/rt-diag-check .venv/bin/python -c "
from lib import workspace, diagnostics
workspace.initialize_workspace(); ws = workspace.get_workspace()
before = {p: p.stat().st_mtime_ns for p in ws.root.rglob('*') if p.is_file()}
diagnostics.system_diagnostics()
after = {p: p.stat().st_mtime_ns for p in ws.root.rglob('*') if p.is_file()}
assert before == after, 'system_diagnostics wrote something'; print('read-only ok')"
rm -rf /tmp/rt-diag-check
```

Full Gate 1/2/3 sequences are at `docs/full-plan.md:1412-1435`. Use a
throwaway `RESUME_TAILOR_HOME`, never `~/.resume-tailor/`.

## Reality check on the plan's "cross-phase totals" (lines 1438–1471)

That table was written before any implementation started, so some counts are
now stale — recompute rather than trust the numbers verbatim:
- Tool count: the doc says "19 → 26"; as of this handoff **25 tools already
  exist** (the 6 Phase-1 identity/discovery tools are in) — `system_diagnostics`
  from this phase would make 26.
- Error codes: `WORKSPACE_AMBIGUOUS`, `WORKSPACE_NOT_FOUND`,
  `FOLDER_UNSUPPORTED`, `NO_MASTER_CANDIDATES` are already registered (Phase
  1). `TEMPLATE_REGISTRY_INVALID` is Phase 5's, not this phase's.
- Run `python -c "from lib.errors import ERROR_CATEGORIES; print(len(ERROR_CATEGORIES))"`
  and `python -c "from lib.audit import EVENTS; print(len(EVENTS))"` to get
  current truth before assuming the plan's numbers.

## Critical files

Phase 7: `lib/audit.py`, `lib/metrics.py`, `lib/release.py` (for
`pdf_compiled`), `lib/tailoring.py`/`server.py` (for the `repair_attempt`
fix), new `lib/diagnostics.py`.

Phase 8: new `lib/reporting.py`, `lib/release.py`, `lib/tailoring.py`,
`CLAUDE.md`, `README.md`, `.gitignore`, new `tests/test_repo_hygiene.py` and
`tests/test_workspace_isolation_e2e.py`.
