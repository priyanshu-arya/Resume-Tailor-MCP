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

## Links in exported resumes

All contact links (email, website/portfolio, LinkedIn, GitHub, Substack) and
any project with a `github` field render as real hyperlinks across every
export format (PDF/LaTeX, docx, md) -- see `lib/links.py`, `lib/latex.py`,
`lib/export.py`. LinkedIn displays a short label ("in/username") hyperlinked
to the full profile URL. Never fabricate a project's `github` link -- only
add one to `resources/master_resume.yaml` when you've verified the repo
actually exists (e.g. from a local git remote or the candidate confirming
it); otherwise leave the project name as plain text.
