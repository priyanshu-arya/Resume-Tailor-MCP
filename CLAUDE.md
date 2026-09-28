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

## User identity and workspace binding

The server never determines who the current user is from Claude memory,
earlier conversations, a previous tailored version, or another workspace.
Identity comes only from the local binding in `config.yaml`
(`$RESUME_TAILOR_HOME/config.yaml`), resolved fresh on every call. Where
that binding is missing, ambiguous or broken, the server reports the state
instead of guessing -- ask the user, never assume.

**Call `get_workspace_status()` first, in any session that touches the
user's resume**, before any other resume-tailor tool. It never raises; it
reports one of four states:

| State | What it means | What to do |
|---|---|---|
| `NO_WORKSPACE` | Nothing on this machine yet | Ask the user for the path of ONE folder holding their existing resume/CV, then `discover_masters(folder)`. If they have no file, run the `create-master-file` skill. |
| `WORKSPACE_FOUND` | Bound and has at least one master | Check `masters[kind].ready`; proceed if ready, otherwise resolve `unparsed_items` first. |
| `WORKSPACE_NEEDS_SETUP` | Bound, but no valid master of the needed kind | `discover_masters(folder)` or the `create-master-file` skill. |
| `WORKSPACE_INVALID` | The binding is broken or ambiguous (`available_workspace_ids` lists what exists) | Call `list_workspaces()`, show the user the options, and only after they say which one is theirs call `select_workspace(workspace_id)`. **Never** call `initialize_workspace()` to "fix" this -- it would create a new, empty workspace and orphan the existing data. |

**R-USER rules** (each is enforced in Python, not only here -- see
`docs/spec-map.md` and the file named):

| Rule | Statement | Enforced by |
|---|---|---|
| R-USER-01 | Identity comes only from the local binding | `lib/workspace.py: get_workspace` |
| R-USER-02 | Never infer the user from memory or chat history | `require_master`'s message; `resolve.next_step` |
| R-USER-03 | Never infer the user from a previous tailored version | `master_ops._reject_version_document` |
| R-USER-04 | Never read another workspace's data | per-workspace dirs; `workflows.load_workflow` checks `workspace_id` |
| R-USER-05 | Never search the filesystem for a resume | `discovery._checked_dir`: one folder, one level, capped entries |
| R-USER-06 | First call of a session is `get_workspace_status` | this section |
| R-USER-07 | Scan exactly the folder the user named | `discover_masters(folder)` |
| R-USER-08 | More than one candidate -> the user picks | `import_master_from_folder`'s `filename` has no default; discovery never returns a `selected` field |
| R-USER-09 | Resume and CV are separate; if both are found, ask which to configure | `kind` is required; separate master files |
| R-USER-10 | The folder is an import source, never a live master | `import_master_from_folder` copies the file in; the folder is never re-read |
| R-USER-11 | Record and read back import provenance | `get_master_history(kind)` |
| R-USER-12 | No master -> refuse to tailor, substitute nothing | `MASTER_NOT_FOUND`'s message |
| R-USER-13 | Master not ready -> resolve before tailoring | checked at both `analyze_tailoring_requirements` and `tailor_resume` |
| R-USER-14 | Switch workspaces only when the user names one | `select_workspace(workspace_id)`; logged in both workspaces |
| R-USER-15 | Several workspaces, no binding -> list and ask | `WORKSPACE_AMBIGUOUS` |
| R-USER-16 | A new workspace only on explicit request | `initialize_workspace(create_new=True)`, never the default |
| R-USER-17 | Never write user data into this repository | workspace paths only; `discovery._checked_dir` refuses the repo |

Two of these are enforced only partly in Python and rely on your judgment:
R-USER-02 (there is no way to verify a claim didn't come from memory) and
R-USER-14's *authorization* (the server can log and audit a switch, but
cannot verify the user actually asked for it -- only call `select_workspace`
when they explicitly named the workspace).

## Required tool order for tailoring

0. First run: `get_workspace_status()`. If `NO_WORKSPACE`, ask for a folder
   and use `discover_masters`/`import_master_from_folder`, or run
   `create-master-file` from scratch. If `WORKSPACE_INVALID`, use
   `list_workspaces`/`select_workspace` -- never `initialize_workspace`.
   Otherwise `initialize_workspace()`, then `migrate_legacy_data` if the
   user has v1 masters. If no master of the needed kind exists, use the
   `create-master-file` skill first.
1. Read `resume://etiquette`.
2. `analyze_tailoring_requirements(jd_text, source_kind)` -> keep the
   `workflow_id` for every later call.
3. Ask the user each `evidence_prompts` question in plain words, one at a
   time, in `order` (`ask_one_at_a_time` is `true` for a reason -- batching
   several questions into one message invites a single vague "yes" that
   covers all of them, which is not confirmation for any one of them). Do
   not answer for them.
4. `save_tailoring_evidence(workflow_id, term, category, evidence_text,
   confirmed=True, metrics=[...])` only with what the user explicitly said.
   If they don't have it, save category `none` and say the returned
   `statement` verbatim (also on each prompt as `decline_phrasing`/
   `not_added_phrasing`) -- do not soften it into something vaguer like
   "I've optimized around X" or omit it.
5. `get_master_resume(kind)` -> use `citable_blocks` IDs.
6. `tailor_resume(save_as, patches, workflow_id, evidence_ids=[...])`.
7. `validate_version(version_id, workflow_id)`.
8. `release_resume(version_id, workflow_id)`. If blocked, repair only with
   `tailor_resume(repair_of=..., patches=[drop_block/reorder only])`
   (max 3 per workflow), or report that release is blocked.
9. `export_resume(version_id, workflow_id)` (mode `release`).
10. `release_resume` and `validate_version` both return a `completion` block
    (`lib/reporting.py`) -- render `completion.display_order`; do not
    recompute or add items, and do not reconstruct this from memory across
    turns. It covers:
    - Source: workspace master {kind}
    - Template: id and version
    - Added after your confirmation: term - category - section - why
      (e.g. "AWS was added to Projects because you confirmed your personal
      project ran on AWS EC2; it was not added to Experience")
    - Not added, and why (say `not_added[].statement` verbatim)
    - What was optimized (reworded/reordered/dropped)
    - Validation: the 7-group `checklist` (provenance, evidence, content,
      template, ats, latex, pdf), critical failures, warnings,
      `not_available` items
    - Released: yes/no
    If an older server has no `completion` block, fall back to assembling
    these same 10 parts from the individual tool results.

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
  project name/stack/dates/github) of an EXISTING entry. The patch schema
  rejects them. The one exception: `add_project_entry` creates a brand-new
  Projects entry and may set its own `name`/`stack`/`academic` at creation
  time (never `github`, `dates` or `id`) -- it has no `target`, so it can
  never touch an existing entry, and there is still no operation that adds
  a job, employer, role or degree.
- Raising claim strength, adding scope ("led", "owned", "architected") or
  numbers the cited sources don't state.
- Changing the master as a side effect of tailoring.
- Shrinking fonts or margins, or switching to an unregistered layout, to fit
  a page cap.
- Claiming success before `released: true`.
- Adding a project `github` link you haven't verified (local git remote or
  the candidate confirming it).
- Changing rules, thresholds, templates or code in response to a validation
  failure. Report the failure and the recommended human action instead
  (`system_diagnostics`'s `recommendations` come from a fixed table, never
  free-form generation, and nothing in this repo acts on its own diagnosis).

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
`academic`; `1-3` is capped at 1 with no exception. When the template's
limit is the one actually binding (stage allows more pages than the
template caps at), `release.career_stage` is reported as a `warning`, not a
silent `pass` -- report that warning to the user verbatim rather than
implying their career-stage cap applied.

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
