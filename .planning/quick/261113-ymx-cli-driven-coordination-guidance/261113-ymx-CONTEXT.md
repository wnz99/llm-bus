# 261113-ymx — CLI-driven coordination guidance

## Registration

No GSD tooling wrote this record; it follows the personal feature-flow tracking-record fallback. Private draft: .planning/drafts/coordination-guidance/ in the primary checkout.

## Decisions

- Guidance is a skill (owner re-plan, approved as plan revision 11). Agents no longer read `skills/llm-bus/references/coordination.md` in full. The CLI and hooks print compact state, `guidance_version` and `guidance_files`, the exact skill files to load; the rule text lives in nine files under `skills/llm-bus/references/coordination/`, and `coordination.md` becomes the index.
- An earlier design (revision 7) put the rule text into CLI guidance with a 9000-unit hook budget; implementation measured the coordinator rules at about 9500 units, so that design was replaced.
- Read-heavy, decision-free steps (setup discovery, recovery reading) go to one read-only native subagent on the host's smaller model (Sonnet in Claude Code, `gpt-6.1-terra` in Codex). The main agent keeps every bus command, owner question and consent.
- D1: existing CLI reminder sentences stay. D2: hooks print a pointer for standing instructions; `llm-bus guidance` and command results print them. D3: one size check on hook output. D4: one version marker in `core.md`; any CLI/skill mismatch or missing file means a full read and telling the owner. D5: `guidance_files` also in the compact form. D6: the example coordination rules file keeps only trigger, prerequisites and a pointer. D7: `coordination.md` is the index. D8: notice bodies unchanged.
- `amend` and a new-coordinator `replace` return full guidance; owner commands without an enrollment load files through one `SKILL.md` dispatch line.
- Prerequisite: host-hook-fixes (PR #85, merged a59e0f4).

## Discovery summary

The reference was 725 lines and was read in full by every coordinator and worker at activation and recovery. `src/llm_bus/cli/guidance.py` already builds role- and state-specific text from the store. Host audits (Claude Code 2.1.316, codex-cli 0.167.2) established the hook output limits: Claude Code saves output above 10,000 characters to a file and shows a 2,000-character preview; Codex `additionalContextLimit` saves excess to a file with a preview. Both hosts fire `SessionStart` with source `compact` after compaction.
