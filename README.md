# Resume Tailor MCP Server

[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![MCP Protocol](https://img.shields.io/badge/MCP-1.2+-purple.svg)](https://modelcontextprotocol.io)
[![Python Version](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org)
[![Privacy First](https://img.shields.io/badge/Privacy-100%25%20Local-success.svg)](#privacy--zero-api-keys)
[![Engine](https://img.shields.io/badge/LaTeX%20Engine-Tectonic-orange.svg)](https://tectonic-typesetting.github.io/)

A local-first **Model Context Protocol (MCP)** server that tailors your resume or CV to any job description, with enforced provenance, evidence-gated tailoring, structured validation, and a release gate before any PDF is marked production-ready.

**Core invariant**: Claude proposes; Python decides. The server enforces what is allowed, what is true, how it is formatted, and whether it can be released.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Architecture](#architecture)
- [Installation & Quick Start](#installation--quick-start)
- [Client Integration](#client-integration)
- [Workflow Guide](#workflow-guide)
- [MCP Surface Reference](#mcp-surface-reference)
- [Templates](#templates)
- [PDF Validation](#pdf-validation)
- [Known Limitations](#known-limitations)
- [Privacy & Zero API Keys](#privacy--zero-api-keys)
- [Contributing](#contributing)
- [License](#license)

---

## What It Does

1. **Workspace isolation**: masters and all personal data live in `~/.resume-tailor/` (or `$RESUME_TAILOR_HOME`), never in this repository. Two masters: `resume` and `cv`, kept separate.
2. **Evidence-gated tailoring**: before rewriting any bullet, the server asks Claude to record what the candidate actually said about each required skill. Tailoring is only allowed with confirmed evidence; invented metrics, technologies, or scope expansions are rejected structurally.
3. **Structured patches**: Claude sends `[{operation, target, new_content}]`; the server loads the master itself. Passing a full resume body is not accepted.
4. **Provenance validation**: 8 rules checked on every patch (placement by evidence category, claim strength, unsupported tech/metrics/verbs, scope expansion, internship-as-professional). All-or-nothing: partial saves don't exist.
5. **Template contract**: only `classic-minimalist` is `supported` with a LaTeX renderer. Other templates are `experimental` metadata and cannot be released. No silent fallback.
6. **Release gate**: `validate_version` + `release_resume` compile the PDF, run all validators (content, structure, format_tex, pdf), check page cap against `career_stage`, verify provenance replay, and check the master hasn't changed since tailoring. All critical failures block release.
7. **Immutability**: released versions are frozen; further changes require a new version.
8. **Audit logs**: append-only JSONL with an `ALLOWED_FIELDS` whitelist -- structurally prevents PII in logs.

---

## Architecture

```
~/.resume-tailor/                     # Workspace root (RESUME_TAILOR_HOME)
├── master/
│   ├── resume.yaml                   # Canonical master resume
│   ├── cv.yaml                       # Canonical master CV (separate)
│   └── backups/                      # Timestamped backups on every save
├── data/
│   ├── versions/                     # Tailored versions (write-once)
│   ├── exports/                      # Released PDFs + LaTeX source
│   ├── evidence/                     # Per-workflow evidence records
│   ├── tailoring_sessions/           # Workflow state
│   └── releases/                     # Release reports (no resume content)
└── monitoring/
    ├── audit.jsonl                   # Event log (IDs/hashes/counts only)
    ├── errors.jsonl                  # Error log
    └── metrics.json                  # Rebuilt from audit.jsonl

resume-tailor-mcp/                    # This repository
├── server.py                         # FastMCP entrypoint
├── lib/
│   ├── workspace.py                  # Workspace management, path safety
│   ├── locking.py                    # fcntl-based locking, atomic writes
│   ├── storage.py                    # Workspace-backed load/save
│   ├── schemas.py                    # Pydantic v2 models (single field vocab)
│   ├── ids.py                        # Block IDs, high-water mark, index
│   ├── patches.py                    # Patch validation and application
│   ├── evidence.py                   # Evidence workflow
│   ├── tailoring.py                  # Master-only tailoring
│   ├── workflows.py                  # Workflow sessions
│   ├── release.py                    # Validation + release gate
│   ├── audit.py                      # Append-only audit logging
│   ├── metrics.py                    # Metrics rebuilt from audit history
│   ├── keywords.py                   # JD keyword extraction
│   ├── rules.py                      # Etiquette rules loader
│   ├── templates.py                  # Template whitelist, no fallback
│   ├── latex.py                      # classic-minimalist LaTeX renderer
│   ├── export.py                     # compile_tex, export formats
│   ├── migration.py                  # One-time legacy migration
│   └── validators/
│       ├── content.py                # ATS / etiquette checks
│       ├── structure.py              # Section order, provenance completeness
│       ├── format_tex.py             # Pre-compile inferred properties
│       ├── provenance.py             # 8 provenance rules
│       └── pdf.py                    # Post-compile measured properties
├── resources/
│   ├── resume_etiquette.yaml         # Content + formatting rules
│   └── templates/templates.yaml     # Template registry with contracts
└── bin/tectonic                      # Bundled TeX engine
```

### Subsystem responsibilities

| Module | What it enforces |
| :--- | :--- |
| `workspace.py` | Path safety (`safe_child` rejects `../`), workspace isolation |
| `locking.py` | Reentrant `fcntl.flock`, `MASTER_CONFLICT` on stale hash |
| `patches.py` | Header fields not patchable; all-or-nothing rejection |
| `validators/provenance.py` | 8 provenance rules; internship ≠ professional |
| `templates.py` | `TEMPLATE_NO_RENDERER` for anything not `classic-minimalist` |
| `release.py` | Release requires PDF backend, supported template, passing page cap |
| `audit.py` | `ALLOWED_FIELDS` whitelist structurally prevents PII in logs |

---

## Installation & Quick Start

### Prerequisites

- Python 3.10+
- macOS, Linux, or Windows (WSL)

### 1. Clone and install

```bash
git clone https://github.com/priyanshu-arya/Resume-Tailor-MCP.git
cd Resume-Tailor-MCP
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Download Tectonic

PDF export requires [Tectonic](https://tectonic-typesetting.github.io/) -- a self-contained TeX engine with no system TeX Live dependency.

```bash
# macOS Apple Silicon
curl -L -o /tmp/tec.tar.gz "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-aarch64-apple-darwin.tar.gz"
mkdir -p bin && tar -xzf /tmp/tec.tar.gz -C bin && chmod +x bin/tectonic

# macOS Intel
curl -L -o /tmp/tec.tar.gz "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-apple-darwin.tar.gz"
mkdir -p bin && tar -xzf /tmp/tec.tar.gz -C bin && chmod +x bin/tectonic

# Linux x86_64
curl -L -o /tmp/tec.tar.gz "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz"
mkdir -p bin && tar -xzf /tmp/tec.tar.gz -C bin && chmod +x bin/tectonic
```

The first PDF export downloads LaTeX packages (requires internet once). Subsequent runs are fully offline.

### 3. Initialize workspace and add your master

Ask your AI assistant:

> *"Initialize my resume workspace."*

This creates `~/.resume-tailor/` with all required subdirectories.

**If you have an existing master in `resources/master_resume.yaml`** (v1 install):

> *"Migrate my legacy master resume to the workspace."*

**If you're starting fresh**, use the `create-master-file` skill:

> *"Set up my master resume."*

---

## Client Integration

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "resume-tailor": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/resume-tailor-mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop fully after editing.

### Claude Code (CLI)

```bash
claude mcp add resume-tailor -- /absolute/path/to/.venv/bin/python /absolute/path/to/resume-tailor-mcp/server.py
```

Or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "resume-tailor": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/resume-tailor-mcp/server.py"]
    }
  }
}
```

### Cursor / Windsurf / Cline

Add the same `mcpServers` block to your IDE's MCP config file
(`~/.cursor/mcp.json`, `~/.codeium/windsurf/mcp_config.json`, or Cline's settings).

---

## Workflow Guide

### Full tailoring (recommended: `tailor_resume_workflow` prompt)

Use the guided `tailor_resume_workflow` MCP prompt. It walks through:

1. `analyze_tailoring_requirements(jd_text, source_kind)` — extracts keywords, identifies gaps, creates a workflow ID.
2. For each `evidence_prompts` question, ask the candidate in plain words. Only record what they explicitly confirm.
3. `save_tailoring_evidence(workflow_id, term, category, evidence_text, confirmed=True)` — one call per term, or `category="none"` if they don't have it.
4. `get_master_resume(kind)` — to read citable block IDs.
5. `tailor_resume(save_as, patches, workflow_id, evidence_ids=[...])` — structured patches only.
6. `validate_version(version_id, workflow_id)` — runs all validators.
7. `release_resume(version_id, workflow_id)` — blocks if any critical failure.
8. `export_resume(version_id, workflow_id)` — returns the released PDF + LaTeX source.

The task is not complete until `export_resume` delivers both the PDF and the LaTeX source in the reply.

### Quick ATS check (no rewriting)

Use the `quick_ats_check` prompt: `match_resume_to_jd` + `score_ats` without saving or releasing.

### Checking workflow status

```
get_workflow_status(workflow_id)  →  evidence IDs, version IDs, release status
get_workflow_status()             →  global metrics (workflow_count, release_count, …)
```

---

## MCP Surface Reference

### Tools

| Tool | Purpose |
| :--- | :--- |
| `initialize_workspace` | Create workspace at `~/.resume-tailor/` (idempotent) |
| `get_workspace` | Return workspace ID and directory layout |
| `migrate_legacy_data` | Copy legacy `resources/master_*.yaml` into workspace (non-destructive) |
| `list_workspace_size` | Bytes and file counts per subdirectory |
| `get_master_resume` | Return master doc + hash + citable block index |
| `set_master_resume` | Two-step preview/confirm import or update of a master |
| `analyze_tailoring_requirements` | Extract JD keywords, create workflow, return evidence prompts |
| `save_tailoring_evidence` | Record confirmed candidate evidence for one JD term |
| `tailor_resume` | Apply structured patches against the workspace master |
| `validate_version` | Run all validators + compile, return `ValidationReport` |
| `release_resume` | Gate check → immutable release if all validators pass |
| `export_resume` | Return released PDF + LaTeX (or draft with DRAFT label) |
| `get_workflow_status` | Per-workflow state or global metrics rebuilt from audit log |
| `match_resume_to_jd` | Read-only keyword gap analysis (no workflow required) |
| `score_ats` | Read-only ATS / etiquette check |
| `diff_versions` | Side-by-side diff of two versions |
| `list_versions` | List saved version IDs and metadata |
| `list_templates` | List registered templates with status and contract |
| `recommend_template` | Suggest the best supported template for a JD |

### Resources (URI-addressable)

| URI | Content |
| :--- | :--- |
| `resume://master` | Master resume (alias for `resume`) |
| `resume://master/{kind}` | Master of given kind (`resume` or `cv`) |
| `resume://sections/{name}` | One section of the master |
| `resume://versions/{id}` | A saved tailored version |
| `resume://templates` | Template registry |
| `resume://etiquette` | Etiquette + ATS rules (single source of truth) |

### Prompts

| Prompt | Purpose |
| :--- | :--- |
| `tailor_resume_workflow` | Full guided flow: analyze → evidence → tailor → validate → release → export |
| `quick_ats_check` | ATS + keyword gap check without rewriting |

---

## Templates

| ID | Status | Notes |
| :--- | :--- | :--- |
| `classic-minimalist` | **supported** | The only template with a LaTeX renderer. Letter, 11pt, Computer Modern. Single column. `max_pages: 2`. |
| `metrics-driven` | experimental | Metadata only; no renderer; cannot be released. |
| `executive-brief` | experimental | Metadata only; no renderer; cannot be released. |
| `academic-research` | experimental | Metadata only; no renderer; cannot be released. |
| `creative-tech` | experimental | Metadata only; no renderer; cannot be released. |

Requesting an experimental template in `tailor_resume` is accepted for saving (the template ID is recorded). `release_resume` will block with `template.releasable` as a critical failure. `export_resume` in draft mode returns `TEMPLATE_NO_RENDERER`. There is no silent fallback to `classic-minimalist`.

---

## PDF Validation

The release gate runs two classes of checks:

**Inferred (pre-compile, from LaTeX source)** — `lib/validators/format_tex.py`:
- Paper size and font size from `\documentclass[...]`
- Margin adjustments from `\addtolength`
- Forbidden environments (`multicols`, content `minipage`, `\includegraphics`, `textblock`)

**Measured (post-compile, from PDF)** — `lib/validators/pdf.py` using pypdf + pdfplumber (poppler optional):
- Page count ≤ cap (career_stage × template max_pages)
- Page size matches contract ±1pt
- Embedded font family matches contract
- Modal body character size in 10–12pt
- Text extractable (ATS-parseable)
- All section headings present in extracted text
- Name and employer names appear in text
- Left text edge ≥ 0.5 in (margin check)
- No empty pages

If neither pypdf nor pdfplumber is available, `detect_pdf_backend()` returns `available: False` and production release is blocked.

---

## Known Limitations (v2)

- **No new project/experience entries via patches.** `add_block` adds a bullet under an existing entry; adding a brand-new role or project requires editing the master and re-tailoring.
- **No deterministic truncation.** If a version is too long, repair it by dropping bullets (`drop_block`) or reordering sections -- the server will not automatically cut bullets (doing so could drop the metric or claim that makes a bullet true).
- **Font check is by family name.** The validator checks that the PDF's embedded font names include the contract's family string (e.g. "Computer Modern"). It does not verify exact variant names.
- **Unknown-requirement detection is heuristic.** JD terms that aren't in the known-skills vocabulary are flagged by pattern (CamelCase, ALLCAPS 2–6 letters, words with `.`/`+`/`#`) -- this catches most technologies but will have false positives and false negatives.
- **Repair limit is 3 per workflow.** After 3 repair attempts, `tailor_resume(repair_of=...)` is blocked; start a new workflow.
- **`classic-minimalist` only.** `max_pages: 2` applies to every career stage today; the 2–3 page director/VP cap in CLAUDE.md becomes enforceable when a supported template raises its `max_pages` limit.
- **Substring `kw in blob` matching replaced by word-boundary regex.** Short ambiguous terms (`go`, `r`, `rest`, `c`) use case-sensitive or contextual matching; aliases (`k8s`→`kubernetes`, `js`→`javascript`, etc.) are normalized.

---

## Privacy & Zero API Keys

- All data stays on your machine in `~/.resume-tailor/`.
- No analytics, telemetry, or third-party services.
- Audit logs contain only IDs, hashes, counts, error codes and timings -- never names, email, phone, full resume text, or JD text. This is structurally enforced by `ALLOWED_FIELDS` in `lib/audit.py`.
- The only outbound request is Tectonic's one-time LaTeX package download (fully offline after that).

---

## Contributing

Issues and PRs welcome at [github.com/priyanshu-arya/Resume-Tailor-MCP](https://github.com/priyanshu-arya/Resume-Tailor-MCP).

For development: `pip install -r requirements-dev.txt && pytest` (728 tests). PDF/release tests are skipped when tectonic is absent.

---

## License

[MIT](LICENSE)
