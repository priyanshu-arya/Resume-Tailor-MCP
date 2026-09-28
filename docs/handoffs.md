# Session handoffs — index

This repo is being brought from v2-as-shipped to the full 8-phase roadmap
(workspace identity → source-of-truth → evidence → provenance → templates →
release gate → monitoring → UX/hardening). The work is split across sessions;
start here.

## Read in this order

1. **`docs/baseline.md`** — the frozen v2-as-shipped state (what worked before
   any of this began), plus every defect found while freezing it (D-1
   through D-11).
2. **`docs/spec-map.md`** — resolves the `spec §N` citations scattered through
   module docstrings to the modules that actually implement them (the
   original numbered spec document was never in the repo).
3. **`docs/full-plan.md`** — the full, detailed, phase-by-phase implementation
   plan (this is the authoritative spec for every phase — the three handoff
   files below are deltas on top of it, not replacements).
4. One of the three handoff files, matching which phases you're picking up:

| File | Phases | Status as of the last update |
|---|---|---|
| `docs/handoff-phase3-4.md` | 3 (Evidence system) + 4 (Tailoring & provenance) | **Complete.** All of 3.1-3.5 and 4.1-4.7 done and tested. |
| `docs/handoff-phase5-6.md` | 5 (Templates) + 6 (Validation & release gate) | Not started. **Phase 5 has a coordination note — read it before touching `resources/templates/`** |
| `docs/handoff-phase7-8.md` | 7 (Monitoring) + 8 (UX/docs/hardening) | Some of Phase 7's audit groundwork done via Phase 1; most of both phases open |

Phases 0, 1 and 2 (workspace identity/discovery, source-of-truth enforcement)
are complete. See `docs/baseline.md` for what Phase 0 froze, and the git
history / `CLAUDE.md`'s "User identity and workspace binding" section for
what Phase 1 built.

## Before starting any session

```bash
cd /Volumes/Working/mcp-servers/resume-tailor-mcp
git log --oneline -10
git status --porcelain
.venv/bin/python -m pytest -v 2>&1 | tail -5   # this pytest version needs -v/-rA for the summary line
```

Confirm the test count matches what the relevant handoff file expects before
starting — if it doesn't, another session has landed work since that handoff
was written; re-read `git log` and adjust rather than assuming the handoff's
"what's already done" section is still accurate.

**Never point `RESUME_TAILOR_HOME` at anything but a throwaway `/tmp` directory
while testing.** The real workspace lives at `~/.resume-tailor/` and holds
real personal data — every test and every manual smoke run in this project
uses a fresh temp directory.
