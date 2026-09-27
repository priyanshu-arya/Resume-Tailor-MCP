---
name: create-cv
description: Build a brand-new CV/resume from scratch through a structured interview conversation, for someone who has no existing resume file to import. Use this whenever the user asks to "create my CV", "build my resume from scratch", "make me a resume/CV, I don't have one", or similar. Not for importing an existing file (use set_master_resume for that) and not for tailoring a saved master to a job (use tailor_resume_workflow for that). This skill gathers content; the create-master-file skill handles workspace setup and the final set_master_resume call.
---

# Create CV From Scratch

Interview the person section by section and turn their answers into a
complete, schema-correct master resume or CV, then hand off to the
`create-master-file` skill for workspace setup and saving.

This is the "blank slate" path. Two adjacent things this is **not**:
- They have an existing file to import → use `set_master_resume` instead.
- They already have a master saved and want it tailored → use the
  `tailor_resume_workflow` prompt instead.

All formatting, bullet, summary, and ATS rules live in `resume://etiquette`
(`resources/resume_etiquette.yaml`) -- read it once at the start and apply
it throughout. Don't restate its rules here; it's the single source of truth.

## 0. Set up the workspace first

Before gathering any content, invoke `create-master-file` steps 0–1 (or
run them inline if you're already orchestrating both skills):
- `initialize_workspace()` (idempotent).
- Ask Resume or CV? (`kind = "resume"` or `"cv"`).
- Check whether a master of that kind already exists via
  `get_master_resume(kind=kind)` and warn if so.

## 1. One setup question before the interview

In a single message, ask:

1. **Career stage** -- fresher/student, ~1-3 yrs, ~3-5 yrs, senior/staff,
   manager, director/VP, or academic/research. This picks the summary
   formula and section order from the etiquette rules. Map the answer to
   the `career_stage` value required on the master:
   `fresher | 1-3 | 3-5 | 5-10 | manager | director | academic`.

Don't ask more than this up front -- gather everything else during the
section interview.

## 2. Interview, one section at a time

Ask about one section, let them answer in their own words, draft it, show
the draft, move on. Don't dump the whole schema as a form.

Target schema (must be schema-correct for `set_master_resume`):

```yaml
name: str
contact: {email, phone, website?, linkedin?, github?, location, ...}
summary: str
skills: [{category: str, items: [{name: str}]}]
experience:
  - title: str
    company: str
    location: str
    start: str
    end: str
    bullets: [{text: str}]
education:
  - degree: str
    school: str
    year: str
projects:
  - name: str
    stack: str
    dates: str
    bullets: [{text: str}]
certifications: [{text: str}]
metadata:
  career_stage: <value from step 1>
```

Interview order:

1. **Name + contact.** Include only fields the etiquette rules say to
   include; actively steer away from photo, DOB, marital status, full
   street address, national ID, etc., unless a specific portal requires it.
2. **Experience**, most recent role first. For each role: title, company,
   location, start/end dates, then "what did you actually build, change or
   own in this role, and what changed as a result (scale, before/after)?"
   Turn raw answers into 3–6 bullets using the `bullet_formula` and
   `action_verbs` from the etiquette rules -- action verb + what + how +
   outcome. **If they don't have a number, ask once; if still none, write
   the bullet without a fabricated metric.** Never invent a number, tool,
   title, or outcome.
3. **Projects** (same bullet treatment; include stack + dates; `github`
   only if the user confirms the repo URL).
4. **Education** (degree, school, year; GPA only if strong and the norm
   for their stage/region).
5. **Skills**, grouped by category (e.g. Languages / Frameworks / Cloud /
   Tools). Only list things they can actually discuss in an interview.
6. **Certifications**, if any.
7. **Summary last**, once everything else is drafted. Apply the
   `summary_formula` for their career stage. No objective statements or
   unsupported generic adjectives.

Skip sections that genuinely don't apply (no empty placeholders).

## 3. Hand off to create-master-file

Once the full dict is assembled, continue with `create-master-file` from
**step 4** (Import / set the master) onwards:
- Preview with `set_master_resume(kind=kind, resume=<dict>, mode="replace")`.
- Show the diff to the user, get approval.
- Confirm with `set_master_resume(..., confirm=True, expected_hash=...,
  proposed_hash=...)`.
- Export draft PDF for validation.
- Run `score_ats` and fix flags.

## 4. Close out

Summarize what was captured, note anything left blank or vague for lack of
truthful detail, and if they mentioned a specific target job, point them to
the `tailor_resume_workflow` prompt as the next step.
