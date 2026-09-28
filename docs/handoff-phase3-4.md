# Session handoff — Phases 3 & 4 (Evidence system; Tailoring & provenance)

**Status: complete.** Everything in Phase 3 and Phase 4 of `docs/full-plan.md`
(lines 622–905) is implemented and tested. This file is kept as a record of
what was done and how to verify it; there is nothing left to pick up here.
If a future session finds a gap, re-verify with the commands at the bottom
before assuming this file is stale — another concurrent session's edits to
shared files (`lib/schemas.py`, `lib/audit.py`, `lib/reporting.py`, etc.) are
the most likely source of drift, not missing Phase 3/4 work.

## What's done

**3.1 (per-term view + two matching.py defects)** — `lib/matching.py` has
`_mentioned_text` (skill items + project stacks), `classify_term(term,
resume)`, `requirement_view(result, resume)`, `REQUIREMENT_STATUSES`. Unknown
JD terms are re-checked against the master via `classify_term`, so a term the
master already proves is never reported `unknown`.

**3.2 (reason / requirement_type / prompt budget)** — `lib/keywords.py` has
`REQUIREMENT_TYPES`, `TERM_TYPES`, `term_type()`, `certification_requirements()`.
`lib/evidence.py` has `PROMPT_REASONS`, `MAX_PROMPTS=12`, `MAX_UNKNOWN_PROMPTS=5`,
and `_build_prompts()` orders must_have-missing → must_have-weak →
certification_required → unrecognized (capped), with `prompts_truncated` in
the response. Each prompt carries `reason`, `requirement_type`, `importance`,
`placement_by_category` (built live from `lib.rules.placement`, never a copy
of the YAML matrix). `analyze_requirements` returns `requirements` and
`status_counts` in addition to every pre-existing key (`priority_missing`,
`unknown_requirements`, etc., all still present, byte-identical in shape).
`lib/tailoring.py`'s `evidence_usage` now reports a `weak` term kept but not
re-cited as "already listed under Skills; no bullet added" instead of "no
supporting evidence".

**3.3 (`term_display` wart)** — `TailoringEvidence.term_display` and
`.prompt_reason` are real optional fields (`lib/schemas.py`); no
strip/re-attach hack. `prompt_reason` is validated against `PROMPT_REASONS`
in `save_evidence` and is deliberately **not** part of the evidence dedupe
key.

**3.4 (YAML + readers)** — `resources/resume_etiquette.yaml`'s `rules_version`
is `"2026.10-v2.1"`; `skill_group_categories` (closed list) and
`scope_inflation_markers` are added, plus three `new_entry_rules` prose
lines. `lib/rules.py` has matching mtime-cached, fail-closed readers
`skill_group_categories()` and `scope_inflation_markers()`.
`evidence_placement` (lines ~181-191) is untouched, as the plan specified.

**3.5 (`add_project_entry`)** — the one new patch operation, in
`lib/schemas.py` (`AddProjectEntry`, added to the `Patch` union;
`Project` gained optional `source_refs`/`claim_strength`, `Experience` did
not) and `lib/patches.py` (`_check_add_project_entry`, `_apply_add_project`,
`_hook_ctxs` generalizing the old single-ctx `_hook_ctx` to emit one ctx per
new piece of wording — the entry itself, then each bullet). Create-only, no
`target`, `extra="forbid"` blocks `github`/`dates`/`id`. Backing evidence
must include at least one `professional`/`internship`/`personal_project`/
`academic` ref (never a master ref alone, never `coursework`/
`certification`/`learning_only`); citing `academic` evidence requires
`academic=True` on the entry (`provenance.project_academic_context`). The
entry is appended to `body["projects"]` **before** minting bullet IDs so
`vb-` IDs stay unique. New rule ids: `patch.duplicate_project`,
`patch.too_many_new_entries`, `provenance.new_entry_requires_evidence`,
`provenance.new_entry_placement`, `provenance.project_academic_context`,
`structure.new_entry_provenance`, `structure.unknown_entry` (the last two in
`lib/validators/structure.py`, wired into `check_structure`). `README.md`'s
Known Limitations and `CLAUDE.md`'s header-immutability rule both note the
one exception.

**4.1 (patch op table consistency)** — `lib/patches.py` derives `_OPERATIONS`
from the `Patch` union's discriminator values and asserts
`set(_CHECKS) == set(_APPLY) == _OPERATIONS` at import time.

**4.2 Gap A (unprovenanced skill-group category)** — `patch.skill_group_category`
in `_check_add_skill`: a genuinely new group's category must be in
`rules.skill_group_categories()` or a familiarity group, and must carry no
metric and no high-scope verb.

**4.2 Gap B (unbounded `NewContent.metadata`)** — `patch.metadata_size`:
≤5 keys, scalar values only, ≤200 chars serialized. Wired into
`replace_block`, `add_block` and `add_project_entry`'s bullets.

**4.3 (skill-item provenance)** — confirmed correct; two tests added.

**4.4 (scope inflation)** — `provenance.scope_inflation` in
`lib/validators/provenance.py`: phrase-tolerant matching against
`rules.scope_inflation_markers()`, same "must also appear in a cited source"
mechanism as `high_scope_verb`. `lib/release.py` has a non-blocking
`release.rules_version_drift` warning check comparing a version's recorded
`rules_version` to the current one.

**4.5 (metric provenance)** — adversarial tests added (rounded-up number,
unit swap, invented percentage, currency changed/dropped, JD-only metric,
and `test_metric_pool_shared_across_refs_is_a_known_looseness` documenting
the permissive union-of-refs behavior).

**4.6 (patch fuzz)** — `tests/test_patch_fuzz.py`, 35 tests: malformed,
oversized, duplicated, contradictory/out-of-order patch lists, driven
through both `validate_and_apply` and `server.tailor_resume`; a seeded
shuffle loop asserting every outcome is success or a registered
`ResumeTailorError`, never a bare exception or partial write.

**4.7 (audit)** — `lib/audit.py` has `project_entry_added` in `EVENTS` and
`evidence_category`, `prompt_reason`, `requirement_type`, `weak_count`,
`supported_count`, `new_entry_count`, `rules_version` in `ALLOWED_FIELDS`
(count fields also added to `_INT_FIELDS` for type enforcement).
`server.py`'s `_success_events` emits them from `analyze_tailoring_requirements`,
`save_tailoring_evidence` and `tailor_resume`.

## Verification

```bash
cd /Volumes/Working/mcp-servers/resume-tailor-mcp
.venv/bin/python -m pytest -v 2>&1 | tail -10    # 1146 passing at last check, 0 failing
.venv/bin/python -c "
from lib import rules
print(rules.rules_version(), rules.skill_group_categories(), rules.scope_inflation_markers())
"
.venv/bin/python -c "
from lib.validators.provenance import check
ctx={'target_section':'projects','target_type':'project_bullet','parent_category':'personal_project',
 'text':'Deployed enterprise-grade AWS infrastructure serving thousands of users','claim_strength':'personal_project',
 'ref_infos':[{'type':'evidence','id':'ev-x','category':'personal_project','text':'I deployed my RAG project on AWS EC2','section':None,'term':'aws','metrics':[]}]}
print(check(ctx))"   # non-empty: provenance.scope_inflation
```

An MCP-level happy-path smoke test (gap analysis with a certification
requirement → evidence with `prompt_reason` → `add_project_entry` →
`validate_version` → `release_resume`, all in a throwaway
`RESUME_TAILOR_HOME`) was run manually and passed end to end during this
session. Never point `RESUME_TAILOR_HOME` at anything but a throwaway `/tmp`
directory.

## Critical files

`lib/matching.py`, `lib/keywords.py`, `lib/evidence.py`, `lib/schemas.py`,
`lib/patches.py`, `lib/validators/provenance.py`, `lib/validators/structure.py`,
`lib/rules.py`, `resources/resume_etiquette.yaml`, `lib/tailoring.py`,
`lib/release.py`, `lib/audit.py`, `server.py`.

## Note on concurrent editing during this session

While this work was in progress, another process ran `git reset --hard HEAD`
multiple times in this working tree, destroying all uncommitted changes
(this session's and others') at that point. The work described above was
redone/completed after that event, against the repo state that existed once
things stabilized (which by then already included a completed Phase 3.2 from
elsewhere). If you're picking up fresh and something here looks missing,
check `git log`/`git status` first — don't assume this file over the actual
code.
