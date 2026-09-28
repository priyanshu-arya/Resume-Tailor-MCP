# Session handoff — Phases 5 & 6 (Templates; Validation & release gate)

> **Status update:** Phases 5 and 6 are both done, including the PDF-fixture
> work (item 4). See "What actually landed this session" at the bottom.

Read this first. Full phase detail is in **`docs/full-plan.md`**:
- Phase 5 — lines 907–1043
- Phase 6 — lines 1044–1159

This file is the *delta*: what's already done, what's blocked, what's left.

## ⚠️ Read before touching anything in Phase 5

**Do not start Phase 5 without first checking whether the concurrent template
import work has landed and settled.** At the time this handoff was written, a
separate session (not part of this plan) was actively importing 5 real
open-source LaTeX templates (AltaCV, Awesome-CV resume/academic, Deedy-CV,
LaTeXCV-two-column) into `resources/templates/` and editing
`resources/templates/templates.yaml` / `tests/test_templates.py`. That work was
still in flux — the template count in `tests/test_templates.py` was observed
fluctuating (65 → 90 → 85) across a single session, meaning it was mid-edit,
not finished.

**Before starting Phase 5:**
```bash
cd /Volumes/Working/mcp-servers/resume-tailor-mcp
git log --oneline -5
git status --porcelain -- resources/templates/ tests/test_templates.py
.venv/bin/python -m pytest tests/test_templates.py -v 2>&1 | tail -5
```
If `resources/templates/templates.yaml` or `tests/test_templates.py` show
uncommitted changes, or the person running this session doesn't know whether
that template-import work is finished, **ask before touching those two files
or any `resources/templates/<new-template-name>/` directory.** Phase 5's core
work (the `CONTRACT_REQUIREMENTS`/`contract_gaps` schema change,
`template.contract_complete` release check, `validate_registry()`) touches
`lib/schemas.py` and `lib/templates.py`, which is safe regardless — only the
YAML file and its test file are the collision risk.

If you get a clean go-ahead: proceed with Phase 5 as written in
`docs/full-plan.md:907-1043`. Nothing in Phase 5 has been started as of this
handoff.

## Repo state when this handoff was written

- Phases 0–4 complete (assuming the Phase 3-4 handoff session ran first — check
  `git log` / `docs/handoff-phase3-4.md` to confirm, or run the verification
  commands in that handoff doc). If Phase 3-4 has NOT run yet, Phase 6 can
  still proceed independently (it's mostly negative-path test coverage on
  existing, already-working validators) but Phase 5 should wait for whichever
  runs first to avoid two sessions editing `lib/schemas.py` at once.
- `pytest -v 2>&1 | tail -3` for the current count (this pytest version needs
  `-v` or `-rA` to print the summary line; plain `-q` won't).
- `bin/tectonic` is present and working on this machine (54 MB, gitignored).
  Confirm with `bin/tectonic --version`.

## Phase 5 summary — three narrow things, not a rebuild

Already done (verify, don't rebuild): the hard template whitelist,
`TEMPLATE_UNKNOWN`/`TEMPLATE_NO_RENDERER`, `auto` resolution, no silent
fallback, 65 existing integrity tests in `test_templates.py`.

1. **`CONTRACT_REQUIREMENTS` + `contract_gaps()` + a pydantic model validator**
   in `lib/schemas.py` (replacing lines ~292-309) — `status: supported` must
   have every contract block fully specified; `experimental` may be
   `"unknown"`. This closes a real, verified hole (**D-8** in
   `docs/baseline.md`): today a hollowed `classic-minimalist` contract
   (`page: "unknown"`) lets a release **pass** with page-size/margin/font
   checks silently degraded to `not_available`.
2. **`template.contract_complete`** — a new release-gate check in
   `lib/release.py`, right after `template.releasable`. Also wrap
   `get_contract` so a pydantic `ValidationError` becomes
   `TEMPLATE_REGISTRY_INVALID` (new error code) instead of `INTERNAL_ERROR`.
3. **`templates.validate_registry()`** in `lib/templates.py` — aggregate
   structural checks (id shape, renderer⊆supported, floor consistency,
   section-order subsequence, margin/font bounds, `latex.*_adjust_in`
   recomputes to `page.margins_in`).
4. `recommend_template` gains `usable`/`releasable_alternative`/`warning` so it
   never presents an unreleasable template as though it were usable.
5. **`classic-minimalist`'s enforcement gap table** — `docs/full-plan.md:1001-1040`
   has the full table of which contract keys are enforced today vs. need a new
   `format_tex.py` check (`template.name_font_size`, `template.heading_font_size`,
   `template.spacing`, `template.heading_style`, `template.bullet_style`,
   `template.link_style`). The most important one: **`spacing.line_spacing`
   (no `\linespread`/`setspace` tricks) — this is exactly the "shrink line
   spacing to cram onto one page" move `CLAUDE.md` prohibits and nothing
   catches it today.**
6. **Explicitly do not build the other four renderers.** Write that decision
   into `lib/templates.py`'s module docstring, don't just leave it implicit.

Headline test: `test_hollowed_contract_blocks_release` — proves D-8 is closed.

## Phase 6 summary — negative-path proof, not new features

Already covered (do not duplicate): wrong page size/margins, `multicol`,
`includegraphics`, decorative/sans fonts, 9pt class, contract-`unknown`
degradation, most release-path failure modes, all 14 structure tests, all 21
content tests. See `docs/full-plan.md:1046-1051` for the full "already done"
list.

**Scoping trap to avoid:** "fabrication, unsupported tech/metrics, bad
placement" are NOT content.py's job — they're patch-time rules in
`lib/validators/provenance.py`, already covered by `test_provenance.py` /
`test_patches.py`. Don't duplicate them here. Instead add one layering guard:
`check_content` must never emit a `provenance.*` id or `FACTUAL` category.

Remaining work, in priority order:

1. **Content (4 tests):** the layering guard above; `banned_opener`
   parametrized over the *full* `weak_openers()` tuple (only 2 of however many
   are tested today); `quantified_ratio` boundary asserting `blocking is
   False`; `test_every_content_check_id_is_emitted`;
   `test_rules_unavailable_fails_closed`.
2. **Structure (4 tests):** duplicate/unknown-section entries in `order`;
   missing heading for an ordered section; `test_every_structure_check_id_is_emitted`;
   and the important one — **`test_source_master_hash_mismatch_is_critical_and_skips_replay`**
   (when the master hash differs, `provenance.replay` is not appended at all —
   assert both halves, so its *absence* is never misread as a pass).
3. **LaTeX (6 tests, pure string mutation, no tectonic needed):** `minipage`,
   `textblock`, `tikzpicture`/`wrapfigure`/`longtable`, **all five
   `_FORBIDDEN_CMDS`** (`parbox`/`fbox`/`framebox`/`colorbox`/`fcolorbox` —
   currently entirely untested), `twocolumn` as a class option and as a
   command.
4. **PDF fixture strategy — read `docs/full-plan.md:1080-1103` before writing
   any PDF test.** Recommendation: a hand-built-PDF helper module
   (`tests/pdf_fixtures.py`), **never commit binary fixture PDFs** (can't be
   diff-reviewed, risks accidentally committing real resume bytes, rots
   silently). Three tiers: Tier A (no tectonic, hand-built PDFs, covers
   branches tectonic literally cannot produce like a non-embedded base-14
   font) in a new `tests/test_pdf_fixtures.py`; Tier B (needs real tectonic,
   stays in `test_pdf_validation.py`); Tier C (committed fixtures) —
   explicitly rejected, write that decision into `docs/testing.md`.
5. **Skip-marker consolidation.** Four files currently each re-derive tectonic
   availability their own way. Consolidate into `tests/conftest.py` as
   `HAS_TECTONIC` + a `needs_tectonic` pytest marker; register it (and `slow`)
   in `pytest.ini` with `--strict-markers`.
6. **Lifecycle decision — do NOT add a persisted `validated` state.** Verified:
   there is no per-version validated flag today, and there shouldn't be one —
   `release_resume` already re-validates fully under the workspace lock, so a
   stored flag could only ever be trusted *less*. Add a derived (non-persisted)
   `lifecycle_state` field to `get_workflow_status`, `list_versions`,
   `validate_version`, `release_resume` instead. Document the decision in
   `lib/release.py`'s docstring.
7. **Artefact immutability tests** — the report JSON, the released PDF/`.tex`,
   the staging copy are NOT yet proven immutable (only the version YAML is).
   Includes a **real code fix** (this session's job, not just a test): a
   missing released file currently raises `FileNotFoundError` → generic
   `INTERNAL_ERROR`; wrap it and raise `NOT_RELEASED` instead.
8. **`max_pages: 2` visibility fix** — change `release.career_stage`'s check
   from `pass`/`info` to a **`warning`** when the template's cap is what's
   actually binding (director/academic today). Non-blocking — no release
   outcome changes, just visibility. Update `README.md:332` and
   `CLAUDE.md:166-168`.

## Verification

```bash
cd /Volumes/Working/mcp-servers/resume-tailor-mcp
.venv/bin/python -m pytest -m "not needs_tectonic" -q      # fast loop
.venv/bin/python -m pytest -m needs_tectonic -q -rs         # -rs: prove nothing was skipped
.venv/bin/python -c "
from lib import templates
print(templates.validate_registry())"
.venv/bin/python -c "
from lib.schemas import contract_gaps
from lib import templates
for t in templates.registered_ids():
    c = templates.get_contract(t)
    print(t, c['status'], contract_gaps(c) or 'COMPLETE')"
.venv/bin/python -c "from lib.templates import RENDERERS; assert set(RENDERERS)=={'classic-minimalist'}; print('ok, no accidental new renderer')"
```

Full MCP sequences for both phases (happy path + adversarial arms) are at the
end of each phase's section in `docs/full-plan.md` — search for "MCP
sequence". Use a throwaway `RESUME_TAILOR_HOME` under `/tmp`, never the real
one.

## Critical files

Phase 5: `lib/schemas.py`, `lib/templates.py`, `lib/validators/format_tex.py`,
`resources/templates/templates.yaml` (⚠️ coordinate first, see above).

Phase 6: `lib/validators/{content,structure,format_tex,pdf}.py`,
`lib/release.py`, `tests/conftest.py`, `pytest.ini`, new files
`tests/pdf_fixtures.py` and `tests/test_pdf_fixtures.py`.

## What actually landed this session

The coordination check passed (templates.yaml/test_templates.py's uncommitted
state was confirmed as the finished concurrent import, not in-flight — one
gap found and fixed: an `altacv` entry referenced a `resources/templates/
altacv/` source tree that was never actually added; the entry was dropped
rather than left pointing at a missing file, see the templates.yaml comment
where the five-real-templates block used to say "four").

**Phase 5 — done**, `lib/schemas.py`: `CONTRACT_REQUIREMENTS` +
`contract_gaps()` + `TemplateContract`'s `model_validator` (5.1).
`lib/templates.py`: `get_contract` wraps a hollow-contract `ValidationError`
as `TEMPLATE_REGISTRY_INVALID` (new error code); `validate_registry()` (5.4,
including the id/version/source_file checks, the supported-only structural
floor, and the `latex.*_adjust_in` <-> `margins_in` recompute); module
docstring records the no-new-renderers decision (5.2); `recommend_template`
gained `usable`/`releasable_alternative`/`warning`/`next_step` (5.5);
`list_templates` gained `releasable`. `resources/templates/templates.yaml`:
completeness-rule header comment, `classic-minimalist` bumped to 1.2.0 with
`latex.layout_only_macros` and a full `enforcement` map. `lib/release.py`:
new `template.contract_complete` check right after `template.releasable`
(5.3). `lib/validators/format_tex.py`: `LAYOUT_MACROS` replaced by
contract-driven `_layout_macros()`; six new checks
(`template.name_font_size`, `template.heading_font_size`,
`template.heading_style`, `template.spacing`, `template.bullet_style`,
`template.link_style`) per the 5.6 gap table, plus `ALL_CHECK_IDS`. Tests:
`tests/test_template_contract.py` (27 new), plus ~30 more added across
`tests/test_validators_tex.py` / `tests/test_validators_structure.py`
(existing test-only `CONTRACT` fixtures updated to be complete, matching the
real classic-minimalist contract).

**Phase 6 — done except PDF fixtures (item 4).** The two "real code fix"
items: `NOT_RELEASED` on a missing released file (D-10) was **already
present** in `lib/release.py`'s `export()` before this session (verify with
`git log -p` if that's surprising); `release.career_stage`'s
pass/info-vs-warning visibility fix (item 8) was **not** present and is now
implemented in `lib/release.py:page_cap()` + `tests/test_release_page_cap.py`
(6 new tests) + `CLAUDE.md`/`README.md` updates. Content (item 1): layering
guard, full `weak_openers()` parametrization, `test_every_content_check_id_
is_emitted`, `test_rules_unavailable_fails_closed` — all in
`tests/test_validators_content.py`. Structure (item 2): duplicate/unknown
section entries, missing heading, `test_every_structure_check_id_is_
emitted`, `test_source_master_hash_mismatch_is_critical_and_skips_replay` —
in `tests/test_validators_structure.py` / `tests/test_release.py`. LaTeX
(item 3): all 5 `_FORBIDDEN_CMDS`, `minipage`/`textblock`/`tikzpicture`/
`wrapfigure`/`longtable`, `twocolumn` as option and command — in
`tests/test_validators_tex.py`. Skip-marker consolidation (item 5):
`tests/conftest.py` now has the one `HAS_TECTONIC`/`needs_tectonic` (a
*named*, registered marker + a `pytest_collection_modifyitems` hook, not a
bare `skipif` — `pytest -m needs_tectonic -rs` actually selects and proves
the gated tests ran); `pytest.ini` has `--strict-markers` + registers
`needs_tectonic`/`slow`; all six files that used to re-derive tectonic
availability now import it. Lifecycle (item 6): `lib.release.lifecycle_state()`
+ wired into `validate_version`/`release_resume`/`get_workflow_status`/
`list_versions` (which now returns `{version_id, lifecycle_state}` objects,
not bare strings — no prior test depended on the bare-string shape). Artefact
immutability (item 7, partial): flipped-PDF-byte and flipped-tex-byte export
tests, and a no-second-report-on-re-release test, all in `tests/test_release.py`
— **not done**: the report-is-`exclusive=True`-write-once *unit* test (only
covered indirectly) and the sentinel-sweep-over-`checks[].measurement`
extension (the existing sentinel check on the whole report file likely
already covers it, but it wasn't verified explicitly).

## PDF fixtures (Phase 6 item 4) — now done

`tests/pdf_fixtures.py`: a hand-built PDF-object-graph builder (`minimal_pdf`,
`two_column_pdf`, `image_only_pdf`, `encrypted_pdf` (delegates the actual
RC4/AES to pypdf's own `encrypt()` -- no reimplemented crypto),
`truncated_pdf`, `png_1x1`), own xref table, no external dependencies beyond
pypdf. `tests/test_pdf_fixtures.py` (17 tests, Tier A, no tectonic): encrypted
+ truncated-xref integrity, A4-vs-letter and mixed page sizes, `max_pages`
exceeded and (previously untested) `min_pages` with one page, a
**non-embedded base-14 font** (the one branch tectonic genuinely cannot
produce -- it always embeds), one-word text failing `text_extractable`, a
blank second page, crushed margins and the (previously untested) "no text
found to measure margins" branch (all pages blank, distinct from crushed-
but-present), 9pt body and the (previously untested) "no text characters
found" branch (only punctuation, no alnum chars), a missing heading with a
non-empty section, `image_only_pdf` tripping three checks at once (the
scanned-resume case), and the (previously untested) `pdf.columns`
multi-column warning branch (a hand-built two-column layout).

`tests/test_pdf_validation.py` gained the two Tier B additions (real
tectonic, `needs_tectonic`): a real *embedded* wrong-family font via
`helvet` -- getting this to actually embed under tectonic's default
XeTeX/TU encoding needed an explicit `\usepackage[T1]{fontenc}` first
(without it the font shape silently substitutes back to the default and
nothing changes; verified interactively before writing the test) -- and a
genuine trailing blank page (`\clearpage\mbox{}\clearpage`; a bare
`\newpage` at the very end of a document does not reliably force a real
extra page).

`docs/testing.md` (new): the Tier-C-committed-fixture-PDFs-rejected decision,
with the reasoning (undiffable, real-resume-leak risk, silent rot).

Verify: `pytest tests/test_pdf_fixtures.py tests/test_pdf_validation.py -v`
(30 tests, was 13); `pytest -m needs_tectonic -rs` now selects 54 (was 52).

## Minor/optional, not attempted

`docs/architecture.md` (referenced by the full-plan's lifecycle write-up but
doesn't exist in this repo; the lifecycle decision is documented in
`lib.release.lifecycle_state`'s docstring instead). The report-is-
`exclusive=True`-write-once *unit* test for artefact immutability (item 7)
is only covered indirectly via the no-second-report-on-re-release
integration test; a direct unit test of the `exclusive=True` flag on
`atomic_write_bytes` was not added.

**Pre-existing, unrelated, do not attribute to this session:**
`tests/test_evidence.py::test_result_shape_and_no_jd_text` and
`::test_stored_record_fields` fail/pass depending on test execution order
(confirmed by running with only this session's schemas.py/templates.yaml
changes reverted — same failures). Looks like cross-test state leakage in
Phase 3/4's evidence module, not a Phase 5/6 regression. Full suite:
1045 passed, 2 failed (those two), `git log --oneline -3` /
`git status --porcelain` before starting a follow-up session to confirm this
is still the state.
