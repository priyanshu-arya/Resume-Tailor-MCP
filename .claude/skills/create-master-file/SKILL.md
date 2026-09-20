---
name: create-master-file
description: Create or replace a protected canonical master Resume or master CV -- the two separate source-of-truth documents that all tailoring is derived from. Use this whenever the user asks to "set up my master resume/CV", "create my canonical resume", "import this as my master", or asks generally to get a master file in place before tailoring starts. This is the gate that decides which kind (resume vs CV) to build and how (import a file vs. interview from scratch), then delegates the actual content work to parse_resume or the create-cv skill and validates the result. It does not itself write resume content -- it orchestrates.
---

# Create/Replace a Master File (Resume or CV)

Master Resume and master CV are **two separate canonical documents** --
never merge their content, never let one silently stand in for the other.
Everything downstream (`tailor_resume`, `match_resume_to_jd`, `score_ats`,
`export_resume`) reads from whichever master the user is tailoring off of,
so getting the master right, once, matters more than any single tailored
export.

This workflow must **strictly follow `CLAUDE.md`** (project rules,
including the strict page-length/formatting/no-fabrication rules) and
`resume://etiquette` / `resources/resume_etiquette.yaml` (the full content
and formatting rulebook). Read both before writing any content. Don't
restate their rules here -- they're the source of truth and are updated
independently of this skill.

## 0. Check which tools you have

Look for the `resume-tailor` MCP server's tools (`parse_resume`,
`get_master_resume`, `export_resume`, etc.).

- **If connected**: use those tools throughout.
- **If not connected**: use the equivalent Python directly via Bash, per
  the README's "Developer & Standalone Python Usage" section (e.g.
  `lib.storage.save_master(resume, kind)`, `lib.export.to_pdf(...)`).

## 1. Ask: Resume or CV?

Ask which one the user is creating -- **Master Resume** or **Master CV**.
Don't assume. If they say "resume/CV" ambiguously, ask them to pick one;
run this skill twice if they want both. This choice becomes the `kind`
argument (`"resume"` or `"cv"`) passed to every tool call below.

Resume and CV target different audiences (`document_type` in the etiquette
rules) and have different length/section-order defaults -- never force one
kind's format onto the other.

## 2. Check for an existing master of that kind, and protect it

Before writing anything, check whether a master already exists:

- Read `resume://master/{kind}` (or call `get_master_resume(kind=kind)` and
  catch the "no master yet" error).

If one **already exists**:

- **Never overwrite it automatically.** Tell the user a master `{kind}`
  already exists, summarize it briefly (name, most recent role/degree), and
  explicitly ask whether they want to (a) replace it entirely, (b) update
  it in place (see the "Master Update" flow below -- this is a lighter
  edit, not a from-scratch rebuild), or (c) stop and keep the existing one.
- Only proceed with a full rebuild after they confirm (a).

If **none exists**, proceed straight to step 3 -- this is the normal
first-time path.

## 3. Ask for the source material

Ask whether they have an existing file to import, or want to build from
scratch through an interview:

- **Existing file** (PDF/DOCX/MD/TXT): call `parse_resume(file_path=...,
  kind=kind)`. This parses and saves directly -- review the result for
  `unparsed_items` and clean those up by hand (via `get_master_resume` /
  editing the YAML) before moving on.
- **From scratch, no file**: use the `create-cv` skill's interview process
  (section-by-section: contact, experience, projects, education, skills,
  certifications, summary last) to gather content, but save the result with
  `kind=kind` (i.e. `storage.save_master(resume, kind)` /
  `get_master_resume(kind=kind)` instead of the resume-only calls that
  skill's own text shows -- the interview method is the same regardless of
  kind, only the save target differs).

Either way: **use only the material the candidate actually gives you.**
Never invent professional experience, education, skills, achievements,
projects, dates, or metrics to fill a gap -- ask, or leave it out. This is
the one rule that overrides everything else in this skill.

## 4. Build the master document

The saved master should:

- Preserve the candidate's factual information exactly -- reorganized and
  polished, never invented (golden rule in `resume://etiquette`).
- Apply the formatting/structure rules in `resources/resume_etiquette.yaml`
  for the candidate's career stage and the chosen `kind` (e.g.
  `section_order_by_target`, `document_type`).
- Stay general-purpose: this is the canonical, reusable document, not a
  version tailored to one specific job. Don't add JD-specific customization
  here unless the user explicitly asks for that to live in the master too.

Files live inside this repo, not an external folder: the structured source
is `resources/master_resume.yaml` / `resources/master_cv.yaml` (gitignored,
personal data). Don't ask the user where to store it -- there's nowhere
else it goes.

## 5. Generate the compiled master LaTeX + PDF

Once the structured YAML master is saved, compile it so a real `.tex` +
`.pdf` pair exists for this master too (not just tailored versions):

```
export_resume(version="master-resume", format="pdf")   # kind="resume"
export_resume(version="master-cv", format="pdf")        # kind="cv"
```

`version="master-resume"` / `"master-cv"` are recognized aliases that
resolve straight to the respective master YAML (see `lib/storage.py`), so
this produces `data/exports/master-resume.pdf` + `.tex` (or the `-cv`
equivalents) compiled from the exact same source as the structured master --
they can never drift apart. Deliver both to the user in the reply (PDF
embedded, LaTeX in a ```latex code block), same as any other export.

## 6. Validate before declaring success

Before telling the user the master is ready, confirm:

- [ ] `resources/master_{kind}.yaml` exists and has no leftover `unparsed`
      items (or they were reviewed and accepted as genuinely empty).
- [ ] `data/exports/master-{kind}.tex` and `.pdf` were generated
      successfully (no tectonic compile errors).
- [ ] `score_ats(version="master-{kind}")` was run and anything it flags
      (weak openers, generic summary phrases, missing quantification,
      unnecessary personal identifiers) was fixed.
- [ ] Content follows `resources/resume_etiquette.yaml` for the candidate's
      career stage (page length, section order, summary formula).
- [ ] Nothing was fabricated -- every claim traces back to something the
      candidate actually said.

If any check fails, fix it before reporting completion -- don't hand back a
half-built master.

## 7. Report completion

State plainly what was created:

```
Master {Resume|CV} created.

PDF:   data/exports/master-{kind}.pdf   (delivered above)
LaTeX: data/exports/master-{kind}.tex   (delivered above)
YAML:  resources/master_{kind}.yaml     (the editable source of truth)
```

Note anything left blank or vague for lack of truthful detail, so the user
knows what to add later. If they mentioned a specific target job, point
them to the `tailor_resume_workflow` MCP prompt as the natural next step.

## Master Update flow (existing master, explicit edit request)

When the user explicitly asks to *update* (not rebuild) an existing master:

1. Read `CLAUDE.md` and `resume://etiquette`.
2. Load the existing master (`get_master_resume(kind=kind)`).
3. Apply only the specific changes requested -- don't rewrite unrelated
   sections.
4. Re-save (`storage.save_master(updated, kind)` -- there's no separate
   "update" tool; saving is idempotent).
5. Re-run `score_ats(version="master-{kind}")`.
6. Re-export per step 5 above and re-validate per step 6.

The master is only ever modified through this explicit flow or a confirmed
full rebuild (step 2) -- never as a side effect of tailoring. `tailor_resume`
always writes to a new named version under `data/versions/`, and normal
tailoring must never touch the master files.

## Gate: no tailoring before a master exists

If the user asks to tailor a resume/CV to a JD and no master of the
relevant kind exists yet (`get_master_resume(kind=...)` errors), don't
silently create a one-off tailored version and treat it as the master.
Stop, explain that a master `{kind}` needs to exist first, and run this
skill to create one -- then continue with the tailoring request.
