---
name: create-cv
description: Build a brand-new CV/resume from scratch through a structured conversation, for someone who has no existing resume file to import and no master resume saved yet. Use this whenever the user asks to "create my CV", "build my resume from scratch", "make me a resume/CV", "help me write a CV, I don't have one", or similar -- as opposed to importing an existing file (use parse_resume for that) or tailoring an already-saved master resume to a specific job (use the tailor_resume_workflow prompt for that).
---

# Create CV From Scratch

Interview the person section by section and turn their answers into a
complete, schema-correct resume -- then save and export it. This is the
"blank slate" path. Two adjacent things this is *not*:

- They have an existing resume file (PDF/DOCX/MD/TXT) to import -> use the
  `parse_resume` tool instead, don't re-type it from scratch.
- They already have a master resume saved and want it rewritten for one
  specific job posting -> use the `tailor_resume_workflow` prompt instead.

All formatting, bullet, summary, and ATS rules referenced below live in the
`resume://etiquette` MCP resource (or `resources/resume_etiquette.yaml`
directly if MCP isn't connected) -- read it once at the start and apply it
live. Don't restate its rules elsewhere; it's the single source of truth and
gets updated independently of this skill.

## 0. Check which tools you have

Look for the `resume-tailor` MCP server's tools in this session
(`get_master_resume`, `tailor_resume`, `score_ats`, `recommend_template`,
`export_resume`, etc.).

- **If connected**: use those tools throughout, as documented in `server.py`.
- **If not connected** (plain Claude Code session in this repo): use the
  equivalent Python directly via Bash, matching the README's "Developer &
  Standalone Python Usage" section:
  - save: `lib.storage.save_master(resume)` or `lib.storage.save_version(id, resume)`
  - ATS check: `lib.ats.score_ats(resume)`
  - template pick: `lib.templates.recommend_template(jd_text, resume)`
  - export: `lib.export.to_tex(resume, template_id)` / `lib.export.to_pdf(resume, out_path, template_id)`

Either way, read the etiquette rules first (`resume://etiquette` resource,
or `Read resources/resume_etiquette.yaml`).

## 1. One setup question before the interview

Ask, briefly, in one message:

1. **Career stage** -- fresher/student, ~1-3 yrs, ~3-5 yrs, senior/staff,
   manager, director/VP, or academic/research. This picks the summary
   formula and section order (`summary_formula` and
   `section_order_by_target` in the etiquette rules).
2. **Is this becoming their canonical master resume**, or a one-off version
   for a specific role right now? Master -> will be saved to
   `resources/master_resume.yaml` (overwrites any existing one -- warn if
   one already exists and ask before overwriting). One-off -> saved as
   `data/versions/{id}.yaml` via `tailor_resume`/`storage.save_version`.

Don't ask more than this up front -- gather everything else during the
section-by-section interview below.

## 2. Interview, one section at a time

Ask about one section, let them answer in their own words, draft it, show
the draft, move on. Don't dump the whole schema as a form. Target schema
(exactly matches `resources/master_resume.yaml`):

```yaml
name: str
contact: {email, phone, website?, linkedin?, github?, location, ...}
summary: str
skills: [{category: str, items: [str]}]
experience: [{title, company, location, start, end, bullets: [{text}]}]
education: [{degree, school, year}]
projects: [{name, stack, dates, bullets: [{text}]}]
certifications: [str]
```

Order to ask in:

1. **Name + contact.** Only include fields the etiquette rules say to
   include (`header_contact.include`); actively steer away from the ones it
   says to omit by default (photo, DOB, marital status, full street
   address, etc.) unless they say a specific portal requires it.
2. **Experience**, most recent role first. For each role: title, company,
   location, start/end dates, then ask "what did you actually do and change
   in this role, and what changed as a result (numbers, scale, before/after)?"
   Turn raw answers into 3-6 bullets per role using the `bullet_formula` and
   `action_verbs` from the etiquette rules -- action verb + what you built/
   changed/led + how + measurable outcome. **If they don't have a number for
   something, ask once for one; if they still don't have it, write the
   bullet without a fabricated metric.** Never invent a number, tool, title,
   or outcome -- this is the one rule that overrides everything else here.
3. **Projects** (same bullet treatment; include stack + dates).
4. **Education** (degree, school, year; GPA only if strong and it's the
   norm for their stage/region).
5. **Skills**, grouped into categories (mirror the categories already used
   in similar roles, e.g. Languages / Frameworks / Cloud / Data / Tools --
   see `skills_section` rules). Only list something they can actually
   discuss in an interview.
6. **Certifications**, if any.
7. **Summary** last, once everything else is drafted -- it's easier to
   write a good summary after you can see the whole resume. Apply the
   `summary_formula` entry matching their career stage. Never use an
   objective statement or unsupported generic adjectives.

Skip a section entirely (don't force empty placeholders) if it truly
doesn't apply (e.g. no certifications yet).

## 3. Assemble, save, and quality-gate

1. Build the full resume dict in the exact schema above.
2. Save it: `tailor_resume`/`get_master_resume`+`storage.save_master` for a
   master resume, or `tailor_resume(save_as=...)`/`storage.save_version` for
   a named version.
3. Run `score_ats` (tool or `lib.ats.score_ats`) and fix anything it flags
   (weak openers, generic summary phrases, missing quantification, bullet
   length/density, unnecessary personal identifiers) before moving on.

## 4. Pick a template and export

- If they have a target job in mind, ask for the JD text and use
  `recommend_template(jd_text, ...)` / `lib.templates.recommend_template`.
- Otherwise default to `classic-minimalist` (the only layout with a real
  LaTeX/PDF renderer -- `export_resume` falls back to it automatically for
  the PDF regardless of what template is requested).
- Export with `export_resume(version=..., format="pdf")` (or the
  `lib.export.to_tex` / `to_pdf` equivalents), and make sure the PDF and
  LaTeX source actually reach the user in the reply, not just a file path.

## 5. Close out

Summarize what was captured, call out anything left blank or vague because
there was no truthful detail to fill it with (so they know what to add
later), and if they mentioned a specific target job, point them to the
`tailor_resume_workflow` prompt as the natural next step.
