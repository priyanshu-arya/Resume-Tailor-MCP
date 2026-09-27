---
name: create-master-file
description: Create or replace a protected canonical master Resume or master CV -- the two separate source-of-truth documents that all tailoring is derived from. Use this whenever the user asks to "set up my master resume/CV", "create my canonical resume", "import this as my master", or asks generally to get a master file in place before tailoring starts. This is the gate that decides which kind (resume vs CV) to build and how (import a file vs. interview from scratch), then orchestrates the workspace setup, set_master_resume import and draft PDF validation. It does not itself write resume content.
---

# Create/Replace a Master File (Resume or CV)

Master Resume and master CV are **two separate canonical documents** --
never merge their content, never let one silently stand in for the other.
Everything downstream (`tailor_resume`, `match_resume_to_jd`, `score_ats`,
`export_resume`) reads from whichever master the user is tailoring off of.

This workflow must **strictly follow `CLAUDE.md`** (project rules,
including the strict page-length/formatting/no-fabrication rules) and
`resume://etiquette` / `resources/resume_etiquette.yaml`. Read both before
writing any content.

## 0. Initialize workspace

Call `initialize_workspace()`. It is idempotent -- safe to call when the
workspace already exists. Masters live in `~/.resume-tailor/` (or
`$RESUME_TAILOR_HOME`), not in this repository.

If the user has legacy `resources/master_{resume,cv}.yaml` files, offer to
migrate them with `migrate_legacy_data()`. That copies the master into the
workspace without deleting the legacy file.

## 1. Ask: Resume or CV?

Ask which one the user is creating -- **Master Resume** or **Master CV**.
Don't assume. If they say "resume/CV" ambiguously, ask them to pick one.
Run this skill twice if they want both. This becomes the `kind` argument
(`"resume"` or `"cv"`) for every tool call below.

## 2. Check for an existing master of that kind, and protect it

Call `get_master_resume(kind=kind)`. If one already exists:

- **Never overwrite it automatically.** Tell the user a master `{kind}`
  already exists, summarize it briefly (name, most recent role), and ask
  whether they want to (a) replace it entirely, (b) update it in place
  (see "Master Update flow" below), or (c) keep the existing one.
- Only proceed with a full rebuild after they confirm (a).

If none exists, proceed to step 3.

## 3. Ask for source material

Ask whether they have an existing file to import, or want to build from
scratch through an interview:

- **Existing file** (PDF/DOCX/MD/TXT): use `set_master_resume` (step 4).
- **From scratch, no file**: use the `create-cv` skill's interview process
  to gather content, then continue from step 4 with the assembled dict.

Either way: use only what the candidate actually gives you. Never invent
experience, skills, achievements, projects, dates, or metrics to fill a gap.

## 4. Import or set the master (two-step confirm)

`set_master_resume` takes two calls when overwriting an existing master:

**First call (preview, no confirm):**
```
set_master_resume(kind=kind, resume=<dict>, mode="replace")
```
Returns a diff + `current_hash` + `proposed_hash`. Show the diff to the
user. Set `career_stage` in the master dict before this call -- it drives
the release page cap (values: `fresher`, `1-3`, `3-5`, `5-10`, `manager`,
`director`, `academic`).

**Second call (write, only after user approves the diff):**
```
set_master_resume(kind=kind, resume=<dict>, mode="replace",
                  confirm=True,
                  expected_hash=<current_hash from first call>,
                  proposed_hash=<proposed_hash from first call>)
```

For the very first master of a kind (no existing master), either call is
sufficient -- the preview/confirm flow collapses to a single call.

If the result contains `unparsed_items`, review them with the user before
continuing. The master is not ready for tailoring until unparsed items are
resolved (`accept_unparsed=True` to proceed anyway and note what was left
out).

## 5. Validate: draft PDF export

Compile a draft to confirm the master renders correctly:

```
export_resume("master-resume", mode="draft")   # kind="resume"
export_resume("master-cv",     mode="draft")   # kind="cv"
```

`"master-resume"` / `"master-cv"` are recognized aliases that resolve to
the workspace master of the given kind. The `mode="draft"` export is
labelled **DRAFT / UNVERIFIED** -- it is a validation check only, not a
deliverable.

Check the compiled PDF for:
- Page count within the stage cap (`fresher`/`1-3` → 1 page, etc.)
- No truncated sections or obvious layout errors
- All section headings present
- Name and contact information rendered correctly

Deliver both the PDF and LaTeX source to the user so they can see what
the master looks like.

## 6. Run ATS check

```
score_ats(version="master-resume")   # or master-cv
```

Fix anything flagged (weak openers, generic summary phrases, missing
quantification, excessive personal identifiers) before declaring the master
ready.

## 7. Validate checklist

- [ ] `get_master_resume(kind=kind)` returns no `unparsed_items` (or they
      were reviewed and accepted).
- [ ] Draft PDF and LaTeX compiled without errors.
- [ ] `score_ats` ran and flags were addressed.
- [ ] Content follows `resume://etiquette` for the candidate's career stage.
- [ ] `career_stage` is set on the master.
- [ ] Nothing was fabricated.

If any check fails, fix it first.

## 8. Report completion

```
Master {Resume|CV} created.

Draft PDF:   delivered above (DRAFT / UNVERIFIED label -- not for submission)
YAML master: ~/.resume-tailor/.../master/{kind}.yaml
```

Note anything left blank for lack of truthful detail. If they mentioned a
specific target job, point them to the `tailor_resume_workflow` MCP prompt
as the next step.

## Master Update flow (explicit edit request)

When the user asks to update (not rebuild) an existing master:

1. Load: `get_master_resume(kind=kind)`.
2. Apply only the specific changes requested.
3. Preview + confirm with `set_master_resume(mode="update", ...)` as above
   (two-step). `mode="update"` preserves block IDs; `mode="replace"` assigns
   new ones (older versions' refs no longer resolve).
4. Re-run `score_ats` and fix flags.
5. Re-export draft and re-validate.

The master is only ever modified through this explicit flow or a confirmed
full rebuild -- never as a side effect of tailoring.

## Gate: no tailoring before a master exists

If the user asks to tailor to a JD and no master of the relevant kind
exists, don't create a one-off tailored version and treat it as the master.
Stop, explain that a master `{kind}` must exist first, and run this skill.
