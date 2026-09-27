# Resume Tailor MCP -- project rules

## Architecture principle

CLAUDE.md is guidance. Python is enforcement. The master is the truth.
Evidence is explicit user confirmation. The template is the rendering
contract. The validator is the gatekeeper. Audit logs are observability.

You propose; the server decides. If a tool rejects something, fix the input
(the patch, the evidence, the template choice). Never work around a rule, and
never tell the user something passed when the server said it did not.

## Authority hierarchy (highest first)

1. Server enforcement: patch/provenance validation, validators, release gate.
2. The workspace master of the relevant kind (`resume` or `cv`).
3. Evidence the user explicitly confirmed in the current workflow.
4. `resources/resume_etiquette.yaml` (`resume://etiquette`).
5. The template contract (`resources/templates/templates.yaml`).
6. This file and the skills.
7. The user's phrasing or convenience requests. These never override 1-5
   or the strict rules below. If a request conflicts, say so and offer the
   compliant alternative.

## Source of truth

- Tailoring uses only the workspace master (`~/.resume-tailor/...`, or
  `$RESUME_TAILOR_HOME`). The server loads it; `tailor_resume` takes no
  resume body.
- Never use Claude memory, earlier conversations, previous tailored versions,
  or arbitrary files as a source. Legacy `resources/master_*.yaml` files are
  only an input to `migrate_legacy_data`.
- `resume` and `cv` are two separate masters. Never merge them or let one
  stand in for the other. Every master tool takes `kind`.

## Required tool order for tailoring

0. First run: `initialize_workspace`, then `migrate_legacy_data` if the user
   has v1 masters. If no master of the needed kind exists, use the
   `create-master-file` skill first.
1. Read `resume://etiquette`.
2. `analyze_tailoring_requirements(jd_text, source_kind)` -> keep the
   `workflow_id` for every later call.
3. Ask the user each `evidence_prompts` question in plain words. Do not
   answer for them.
4. `save_tailoring_evidence(workflow_id, term, category, evidence_text,
   confirmed=True, metrics=[...])` only with what the user explicitly said.
   If they don't have it, save category `none`.
5. `get_master_resume(kind)` -> use `citable_blocks` IDs.
6. `tailor_resume(save_as, patches, workflow_id, evidence_ids=[...])`.
7. `validate_version(version_id, workflow_id)`.
8. `release_resume(version_id, workflow_id)`. If blocked, repair only with
   `tailor_resume(repair_of=..., patches=[drop_block/reorder only])`
   (max 3 per workflow), or report that release is blocked.
9. `export_resume(version_id, workflow_id)` (mode `release`).
10. Completion message:
    - Source: workspace master {kind}
    - Template: id and version
    - Added after your confirmation: term - category - section - why
      (e.g. "AWS was added to Projects because you confirmed your personal
      project ran on AWS EC2; it was not added to Experience")
    - Not added, and why
    - What was optimized (reworded/reordered/dropped)
    - Validation: critical failures, warnings, `not_available` items
    - Released: yes/no

The `tailor_resume_workflow` MCP prompt walks through the same order. For a
read-only check use `quick_ats_check` (`match_resume_to_jd` + `score_ats`).
`get_workflow_status(workflow_id)` shows a workflow's evidence, versions,
repair count and audit summary.

## Evidence

Categories: `professional`, `internship`, `personal_project`, `academic`,
`coursework`, `certification`, `learning_only`, `none`.

- Silence, memory, earlier chats, "sounds right", "just optimize it" or your
  own reasoning are not confirmation. `save_tailoring_evidence` refuses
  anything but `confirmed=True`; only pass it after the user said it.
- `evidence_text` is the user's own description. Each metric must appear
  word for word in `evidence_text`.
- Evidence is scoped to one workflow; it cannot be reused in another.
- Placement (from `evidence_placement` in the etiquette file): professional
  and internship -> Experience/Projects/Skills/Summary; personal_project ->
  Projects/Skills, Summary limited; academic -> Projects/Skills/Education,
  Summary limited; coursework -> Education/Skills, Projects limited;
  certification -> Skills, Summary limited; learning_only -> Skills only, in
  a familiarity group ("Familiar With", "Currently Learning"); none -> never.
  "Limited" means no high-scope verbs and no metrics.
- Internship evidence stays under the internship role. It never appears
  under a full-time role, and an internship bullet never claims
  `professional` strength.
- Tailoring evidence is never written back to the master.

## Templates

- Only registered template IDs exist. Do not invent layouts, pass custom
  LaTeX, or try to control fonts, margins or spacing.
- Only `classic-minimalist` is `supported` and has a renderer. The other four
  are `experimental` metadata and cannot be released.
- No silent fallback: an experimental template is never rendered as another
  template. Release is blocked instead; tell the user and suggest
  `classic-minimalist`.

## Release

Never say the resume is done, final or ready until `release_resume` returned
`released: true`. A draft export (`mode="draft"`) is labelled
DRAFT / UNVERIFIED and is not a deliverable for submission.

## Delivery rule

A tailoring request (any phrasing) is complete only when the released export
is delivered in the reply:

- the compiled **PDF**, attached/embedded, and
- its **LaTeX source** in a ```latex code block.

A saved version, prose, or a file path alone is not the deliverable. If
release is blocked, say so and list the failures instead.

## Prohibited

- Fabricating metrics, technologies, titles, dates, publications,
  certifications, responsibilities, or project links.
- Patching header fields (name, contact, titles, companies, dates, degrees,
  project name/stack/dates/github). The patch schema rejects them.
- Raising claim strength, adding scope ("led", "owned", "architected") or
  numbers the cited sources don't state.
- Changing the master as a side effect of tailoring.
- Shrinking fonts or margins, or switching to an unregistered layout, to fit
  a page cap.
- Claiming success before `released: true`.
- Adding a project `github` link you haven't verified (local git remote or
  the candidate confirming it).

## Strict rules

These override convenience and the user's phrasing.

- **Never fabricate.** Every bullet must be defensible in an interview.
  If evidence is missing, ask or omit.
- **Page cap by career stage** (`career_stage` on the master):
  fresher 1 · 1-3 yrs 1 · 3-5 yrs 1-2 · 5-10 yrs/staff/manager 2 ·
  director/VP 2-3 · academic CV follows the scholarly record. Cut
  low-value content; never shrink margins or type.
- **Formatting floor:** US Letter (A4 for India/Europe/academic or when the
  portal says so) · margins 0.5-1.0 in · body 10-12 pt · name 14-24 pt ·
  headings 11-14 pt bold · plain fonts only · single column, standard
  headings, no tables/text boxes/graphics for content.
- **Omit by default:** photo, date of birth/age, marital status, religion,
  national ID/passport numbers, full street address, skill bars or
  percentage ratings.
- **Right document for the audience:** don't compress an academic CV into a
  corporate resume or the reverse.

What Python enforces at release (blocking): the page cap (`career_stage`
cap combined with the template's `max_pages`, checked on the measured PDF
page count), page size, body font >= 10 pt (measured), margins >= 0.5 in
(measured ink extents), embedded font family, text extractability, section
headings, and personal identifiers in `contact` (photo, DOB/age, marital
status, religion, national ID/passport). Guidance only: name/heading sizes,
the upper margin bound, the street-address rule, skill bars, and
audience fit. `career_stage` values: `fresher`, `1-3`, `3-5`, `5-10`,
`manager`, `director`, `academic`. Without it only the template limit applies
(and a warning is raised), so set it on the master. `classic-minimalist`
has `max_pages: 2`, so today the released cap is 2 even for `director` and
`academic`; `1-3` is capped at 1 with no exception.

## Changing a master

Only when the user explicitly asks. `set_master_resume(kind, ...)`:

1. The first master of a kind is written immediately.
2. For an existing master, the first call writes nothing and returns a diff
   plus `current_hash` and `proposed_hash`. Show the diff to the user.
3. Only after they approve, call again with `confirm=True`,
   `expected_hash=current_hash`, `proposed_hash=proposed_hash`. The old
   master is backed up.

Use `mode="update"` with the full edited master from `get_master_resume` to
keep block IDs. `mode="replace"` assigns new IDs, so older versions' refs no
longer resolve. A master with `unparsed` items is not ready for tailoring
until they are placed or the user accepts leaving them out
(`accept_unparsed=True`).

## Skills

- `create-master-file` (`.claude/skills/create-master-file/SKILL.md`):
  create, import or replace a master Resume or CV, with overwrite protection
  and draft PDF validation.
- `create-cv` (`.claude/skills/create-cv/SKILL.md`): the from-scratch
  interview that `create-master-file` delegates to.

Contact links and project `github` fields render as hyperlinks in every
export format (`lib/links.py`).
