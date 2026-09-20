# Resume Tailor MCP -- project rules

## Tailoring a resume/CV to a job description

Whenever the user asks to tailor their resume/CV to a job description, or
otherwise asks for a finished tailored resume/CV (any phrasing -- "tailor my
resume for this JD", "make me a CV for this role", etc.), the task is not
complete until `export_resume` has been called and its result delivered in
full:

- the compiled **PDF**, as the actual attached/embedded file in the reply
- its **LaTeX source**, as a ```latex code block in the reply

Saving a version with `tailor_resume` or describing the changes in prose is
not the deliverable -- the PDF + LaTeX are. Never stop at a file path.

Prefer the `tailor_resume_workflow` MCP prompt for the full guided flow (JD
gap analysis -> rewrite -> save -> ATS check -> export). For a quick check
without rewriting anything, use `quick_ats_check` instead.

## Content rules

Read the `resume://etiquette` MCP resource (`resources/resume_etiquette.yaml`)
before writing or rewriting any resume content -- golden rule: reorganize and
tailor real evidence, never invent metrics, technologies, titles, dates,
publications, certifications, or responsibilities.

`resources/resume_etiquette.yaml` is the single source of truth and is
distilled from university career-center + ATS/recruiting-system research
(UT Austin, UC Davis, UC Berkeley, Cornell, Harvard, MIT, UCSF, CRA,
USAJOBS, Indeed, Greenhouse). The hard constraints below are pulled out
here so they're enforced even in a session where the resource isn't
re-read -- but they are a summary, not a replacement: always read the full
file for summary formulas, bullet formulas, section order, and career-stage
playbooks before drafting content.

## Strict, non-negotiable rules

These override convenience or the user's phrasing if the two conflict --
if a request would violate one of these, say so and propose the compliant
alternative instead of silently doing it.

- **Never fabricate.** No invented metrics, technologies, titles, dates,
  publications, certifications, responsibilities, or project links. If
  evidence is missing, ask the candidate for it or omit the claim -- do not
  guess or round up to sound better. Every bullet must be defensible in an
  interview.
- **Page length by career stage is a hard cap, not a suggestion:**
  - Fresher / student -> **1 page**
  - ~1-3 years -> **1 page** (2 only if genuinely justified by volume of
    relevant evidence, not to look more senior)
  - ~3-5 years -> **1-2 pages**
  - ~5-10 years, staff/principal, or manager -> **2 pages**
  - Director / VP -> **2-3 pages** (defer to the employer/portal's stated
    limit if one is given)
  - Academic/research CV -> length follows the scholarly record, not this
    table -- do not compress an academic CV to fit a corporate page limit,
    and do not let a 1-2 page target apply to it.
  - **Never shrink margins below 0.5 in or body text below 10 pt to force a
    page count.** Cut low-value content and strengthen evidence instead.
- **Formatting floor:** US Letter (A4 for India/Europe/academic or if the
  portal specifies) · margins 0.5-1.0 in · body font 10-12 pt (never below
  10 pt) · name 14-24 pt · headings 11-14 pt bold · plain/legible fonts only
  (Arial, Calibri/Aptos, Helvetica, Times New Roman or similar -- no
  script/decorative fonts) · single column, standard section headings, no
  tables/text boxes/graphics for essential content (ATS parsers mis-order
  or drop them).
- **Omit by default:** photo, date of birth/age, marital status, religion,
  national ID/passport numbers, full street address, proficiency bars or
  percentage skill ratings. Only include one of these if the specific
  employer/country/portal explicitly requires it.
- **Right document for the audience:** never force a long academic CV into
  a corporate resume, or compress a 1-page corporate resume format onto a
  faculty/research application -- they are evaluated differently.
- **Tailoring/CV-generation delivery is not done** until `export_resume`
  has run and both the PDF and LaTeX source have been delivered per the
  "Tailoring a resume/CV to a job description" section above -- this rule
  and that one are the same requirement stated twice for emphasis.

## Links in exported resumes

All contact links (email, website/portfolio, LinkedIn, GitHub, Substack) and
any project with a `github` field render as real hyperlinks across every
export format (PDF/LaTeX, docx, md) -- see `lib/links.py`, `lib/latex.py`,
`lib/export.py`. LinkedIn displays a short label ("in/username") hyperlinked
to the full profile URL. Never fabricate a project's `github` link -- only
add one to `resources/master_resume.yaml` when you've verified the repo
actually exists (e.g. from a local git remote or the candidate confirming
it); otherwise leave the project name as plain text.
