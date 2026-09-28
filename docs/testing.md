# PDF test fixture strategy

Decision, recorded once rather than re-litigated per PR: this repo never
commits a binary PDF file as a test fixture.

## Why not

A committed fixture PDF:

- **can't be reviewed in a diff.** A reviewer sees "binary file changed",
  not what changed or why. Every other fixture in this repo (the synthetic
  master in `tests/conftest.py`, the CONTRACT dicts in the validator tests)
  is readable Python; a PDF blob would be the one exception.
- **risks accidentally committing real content.** The project's own privacy
  rules (`.gitignore`, `CLAUDE.md`'s "never write user data into this
  repository") exist precisely to keep a developer's real resume out of the
  repo. A screenshot-and-save-as-fixture workflow is exactly the kind of
  accident those rules exist to prevent -- a hand-tweaked "test" PDF is one
  copy-paste away from being someone's actual CV.
- **rots silently.** A `pdf.py` contract check changes (a new tolerance, a
  new field) and the old fixture no longer exercises the branch it was
  meant to, with no diff to notice it in.

## What we do instead

**Tier A -- hand-built, no tectonic needed** (`tests/pdf_fixtures.py`,
`tests/test_pdf_fixtures.py`). A small PDF-object-graph builder
(`minimal_pdf`, `two_column_pdf`, `image_only_pdf`, `encrypted_pdf`,
`truncated_pdf`, `png_1x1`) that assembles readable Python source into PDF
bytes at test time -- every byte is derived from the call's arguments, so
the same call always produces the same bytes, and nothing is ever read from
disk. This is also the *only* way to reach a few branches at all: a
**non-embedded base-14 font** is not something tectonic can produce (it
always embeds whatever font it uses), so `test_non_embedded_base14_font_
fails_contract` only exists because the fixture is hand-built rather than
compiled.

**Tier B -- real tectonic-compiled PDFs** (`tests/test_pdf_validation.py`,
gated behind the `needs_tectonic` marker from `tests/conftest.py`). For
branches that need a real, TeX-produced document to be faithful: the happy
path, page-size/margin/font-size contract mismatches produced by mutating
the actual `classic-minimalist` renderer output, and the two cases only
real TeX can produce convincingly -- a *real embedded* wrong-family font
(`helvet`, with an explicit `\usepackage[T1]{fontenc}` -- tectonic's default
XeTeX/TU encoding otherwise silently substitutes the font shape back to the
default rather than embedding Helvetica) and a genuine trailing blank page
(`\clearpage\mbox{}\clearpage`).

**Tier C -- committed fixture PDFs: rejected.** See "why not" above. If a
future check genuinely cannot be reached by either tier, that is a signal
to extend the Tier A builder (it is plain Python; adding a new PDF
structural quirk is a function, not a binary), not to commit a PDF.
