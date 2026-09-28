# Architecture

This file is a starting point, not the full Phase 8.3 deliverable (`docs/full-plan.md`
also calls for the corrected component tree, the 5 validator layers, and the
release-gate call order living here -- those are still open). What's here now
is the one section Phase 7 requires to exist in three places, plus enough
context to place it.

## Component overview

```
server.py            MCP tool surface; @_safe_tool wraps every tool with the
                      uniform error model and audit-event emission
lib/
  workspace.py        identity/binding (R-USER-01..17), Workspace dataclass
  resolve.py          "give me a tailorable master" -- the R-USER-12/13 gate
  discovery.py         bounded, single-folder import scanning (R-USER-05/07)
  storage.py            master/version/workflow persistence, atomic writes
  evidence.py             gap analysis, evidence prompts, save_tailoring_evidence
  tailoring.py              patch application against the live master (tailor())
  patches.py                  patch schema + provenance-hook wiring
  validators/
    content.py               etiquette/ATS content checks (Check objects)
    structure.py              section/provenance-completeness checks
    format_tex.py               contract checks inferred from generated TeX
    pdf.py                        checks measured from the compiled PDF
    provenance.py                   patch-time claim rules (rejections, not
                                     release-time Check objects -- see below)
  release.py           validate() runs all four validators plus the checks it
                        emits itself (source.master_hash, provenance.replay,
                        template.releasable/renderer, latex.compile,
                        pdf.backend); release_resume() re-validates under the
                        workspace lock and never trusts a cached VALIDATED state
  reporting.py          completion_summary()/checklist() -- server-assembled,
                         not Claude-reconstructed (Phase 8.1)
  audit.py              sanitizing, whitelisted, append-only event log
  metrics.py             pure derivation from the log; rebuild_metrics() writes
  diagnostics.py           read-only forensics over the log (system_diagnostics)
```

### Where a release-time check id maps into `reporting.checklist`'s 7 groups

`lib/validators/provenance.py` does **not** participate in the release-time
checklist -- it emits patch-*rejection* `rule` strings at tailor time
(`PATCH_INVALID`/`PROVENANCE_VIOLATION`), not `Check` objects with a
pass/fail/severity shape. The checklist's "provenance" group is instead
populated by `source.master_hash` and `provenance.replay` (both emitted
directly by `lib/release.py`) plus every `structure.*` check. Five
`ALL_CHECK_IDS` tuples cover every check the release gate can emit:
`lib/validators/{content,structure,format_tex,pdf}.py` and `lib/release.py`
itself (for the checks that need the live master or the compile step, not
just the rendered document). `tests/test_reporting.py`'s
`test_checklist_covers_every_check_id` unions all five against
`reporting.GROUP_ORDER` so a new check id can't go unmapped.

## Monitoring boundary (v1)

**The boundary is: log -> count -> identify patterns -> recommend to a
human. Nothing in this repo may act on its own diagnosis.**

Concretely, `system_diagnostics()` and everything under it:

- reads the audit log (bounded: `MAX_READ_BYTES`/`MAX_READ_RECORDS` in
  `lib/audit.py`) and counts failures by category/code/check-id;
- calls `compute_metrics()` -- a pure function of the event list, never
  `rebuild_metrics()` (which writes `metrics.json`; `system_diagnostics` is
  read-only by construction, checked by an AST scan in
  `tests/test_diagnostics.py` for `open(...,"w")`/`write_text`/
  `atomic_write_*`/`mkdir`/`unlink`/`shutil`);
- looks up recommendations in a **fixed table**, `REMEDIATION_HINTS` --
  never free-form generation. A diagnosis is always traceable to one row a
  human wrote, not something inferred at call time.

What this explicitly excludes, named so a later phase doesn't quietly add
it back:

- **No automatic retry/repair loop.** Repairs stay user-initiated, capped
  at 3 per workflow, restricted to `drop_block`/`reorder` patches.
- **No self-modification** of `resource/resume_etiquette.yaml`,
  `resources/templates/templates.yaml`, thresholds, or code. The `lib/`
  tree is read-only at runtime; the only writable root is the workspace.
- **No threshold auto-tuning from metrics.** `compute_metrics` stays a
  pure function of the event list -- it cannot feed back into what counts
  as a pass.
- **No relaxation switch.** The §65 debug mode stays unimplemented; no
  flag widens `lib.audit.ALLOWED_FIELDS` or disables `_clean_value`.
- **No candidate-quality scoring in monitoring.** These are reliability
  metrics about the software, never a judgment about the candidate or the
  resume's content (`lib/metrics.py`'s `NOTE` constant says this in every
  returned payload).

This rule is stated in three places by design, so it survives whichever one
a future change touches first: `lib/diagnostics.py`'s module docstring,
this section, and `CLAUDE.md`'s Prohibited list ("Changing rules,
thresholds, templates or code in response to a validation failure. Report
the failure and the recommended human action instead.").

## Still open (Phase 8.3)

- The corrected repo-root component tree (workspace level, `config.yaml`,
  `master/legacy/`, `data/evidence/`, `data/exports/drafts/`,
  `data/exports/.staging/<version_id>/`, `monitoring/.audit.lock`).
- `docs/spec.md`, `docs/roadmap.md`, `docs/validation-reference.md`,
  `docs/templates.md` -- none of these exist yet.
- README items 3-6 from `docs/full-plan.md:1366-1373` beyond the template
  table (already fixed): the modal-body-size wording, the career-stage
  warning wording, Known Limitations additions, the stale test count.
