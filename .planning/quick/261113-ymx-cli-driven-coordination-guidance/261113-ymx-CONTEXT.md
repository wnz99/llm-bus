# 261113-ymx — CLI-driven coordination guidance

## Registration

No GSD tooling wrote this record; it follows the personal feature-flow tracking-record fallback. Private draft: .planning/drafts/coordination-guidance/ in the primary checkout.

## Decisions

- Agents no longer read `skills/llm-bus/references/coordination.md` in full on coordinate, join or recovery. The CLI prints the rules and next commands for the session's role, state and delegated capabilities; the reference becomes reference material.
- Splitting the reference into separate files was rejected: a coordinator would end up reading most parts anyway.
- Read-heavy, decision-free steps (setup discovery, recovery reading) go to one read-only native subagent on the host's smaller model (Sonnet in Claude Code, `gpt-6.1-terra` in Codex). The main agent keeps every bus command, owner question and consent.
- D1: no section of the reference stays a required read; sections are read only when guidance names them.
- D2: gate-record and decision notice bodies stay unchanged; rules reach agents only through local CLI output.
- D3: a 9000 UTF-16-unit budget covers the whole hook output, with an overflow pointer to `llm-bus --format agent guidance`; four procedures ship as safety sentences plus a section pointer, with a three-step reserve.
- D4: a `guidance_version` marker; an older CLI without it means the agent reads the reference in full.
- `amend` and a new-coordinator `replace` return full guidance; a restore sentence covers returning to the project folder and peer config changes.
- Prerequisite: host-hook-fixes (PR #85, merged a59e0f4).

## Discovery summary

The reference was 725 lines and was read in full by every coordinator and worker at activation and recovery. `src/llm_bus/cli/guidance.py` already builds role- and state-specific text from the store. Host audits (Claude Code 2.1.316, codex-cli 0.167.2) established the hook output limits: Claude Code saves output above 10,000 characters to a file and shows a 2,000-character preview; Codex `additionalContextLimit` saves excess to a file with a preview. Both hosts fire `SessionStart` with source `compact` after compaction.
