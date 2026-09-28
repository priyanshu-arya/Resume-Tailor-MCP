# Spec clause map

Throughout `lib/` and `server.py`, module and function docstrings cite a numbered spec
(`spec §N`) as their authority. That document is **not in this repository** — it was
never committed, or never existed as a separate file — so every one of those citations
is currently unresolvable for a reader.

This table is reconstructed mechanically from the citing docstrings themselves
(`grep -rno 'spec §[0-9][0-9, §-]*' lib server.py resources`, 40 hits across 28
file:line locations, spanning §4–§69) and is now treated as the authoritative index:

**Where a clause is cited below, the listed file is the specification for it.** If you
need to know what "§13" requires, read `lib/patches.py` and `lib/schemas.py:226` — do
not go looking for a separate spec document.

New code should not add new `spec §N` citations. Instead cite this file plus a
requirement id where one exists (e.g. `R-USER-07`, defined in `CLAUDE.md`'s identity
section once Phase 1 lands).

| § | Topic | Implemented / specified in |
|---|---|---|
| 4-6 | Workspace layout, identity, path safety | `lib/workspace.py` |
| 5 | Path helper interface (`get_master_path`, `get_versions_path`, …) | `lib/workspace.py:244` |
| 8, 63 | Locking, atomic writes, hash-guarded master writes, concurrency | `lib/locking.py`, `lib/storage.py:5` |
| 9 | Hardened YAML loading (no anchors/aliases/python tags, depth and size caps) | `lib/safe_yaml.py`, `lib/parsing.py:49` |
| 10 | `kind` validation (`resume` \| `cv`) | `lib/schemas.py:29` |
| 11 | Field vocabulary for the resume/master schema | `lib/schemas.py:1` |
| 12 | Stable block IDs, high-water marks, block index | `lib/ids.py` |
| 13 | Structured patch schema | `lib/patches.py`, `lib/schemas.py:226` |
| 13-14, 37, 60 | Master-only tailoring (no resume body accepted; repairs re-derive from the master) | `lib/tailoring.py` |
| 15 | Evidence category taxonomy | `lib/schemas.py:36` |
| 16, 24 | Provenance rules (placement, claim strength, scope, metrics) | `lib/validators/provenance.py` |
| 17-20 | Evidence workflow (analyze → prompt → save → cite) | `lib/evidence.py`, `lib/schemas.py:210` |
| 20 | Evidence record schema | `lib/schemas.py:210` |
| 26-30 | Template whitelist and status model | `lib/templates.py`, `resources/templates/templates.yaml` |
| 27, 29 | Template contract fields | `lib/schemas.py:289` |
| 32, 40 | Content / structure / tex validator layers | `lib/validators/{content,structure,format_tex}.py` |
| 33, 34 | Keyword extraction and JD matching | `lib/keywords.py`, `lib/matching.py:5` |
| 38-45, 61 | Validation report and release gate | `lib/release.py`, `lib/schemas.py:313` |
| 40 | Check / severity model | `lib/schemas.py:313`, `lib/validators/content.py:322` |
| 41 | Measured PDF verification | `lib/validators/pdf.py` |
| 44 | Exact LaTeX compilation (tectonic, byte-identical) | `lib/export.py:384` |
| 46 | Deterministic export filenames | `lib/filenames.py` |
| 48 | Workflow sessions | `lib/workflows.py`, `lib/schemas.py:381` |
| 49, 50, 55 | Audit event taxonomy, failure categories, audit record shape | `lib/audit.py`, `lib/errors.py:8`, `server.py:128` |
| 51 | Metrics derived from the audit log | `lib/metrics.py` |
| 57-58 | Completion / evidence-usage reporting | `lib/tailoring.py:91` |
| 59, 63, 64 | Master preview/confirm protocol, concurrency, confirmation hashes | `lib/master_ops.py` |
| 65 | Controlled debug mode — **deliberately NOT implemented** | `lib/audit.py:29` |
| 66 | Import size/format limits | `lib/parsing.py:49` |
| 69 | Uniform tool error model | `server.py:128` |

## Gaps this map exposes

No citation exists anywhere for template *rendering* (`lib/latex.py`), export to
non-PDF formats (`lib/export.py` beyond line 384), the workspace-lock implementation
details beyond §8/§63, or `lib/rules.py` / `resources/resume_etiquette.yaml`'s
etiquette-rule reader. These are either considered self-evident from their calling
context or were never covered by the original spec. Treat their governing document as
`CLAUDE.md` and this repository's tests.
