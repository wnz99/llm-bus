# 261113-f5u — Host hook fixes and host-behaviour docs

## Registration

No GSD tooling wrote this record; it follows the personal feature-flow tracking-record fallback. Private draft: .planning/drafts/host-hook-fixes/ in the primary checkout.

## Decisions

- Separate fix flow, shipped before the coordination-guidance flow, because owner-turn bugs weaken the decision-answer safeguard.
- D1: parametrize the existing child-hook test over both hosts.
- D2: a prompt containing a complete bus notice counts as a wake on Claude.
- D3: `/loop`, scheduled prompts and background-subagent reports are documented only.
- D4: Low items recorded as GitHub issues #80–#84.
- D5 (owner accepted): the same contains-match wake rule applies to Codex; the acknowledged-wake no-op stays exact.
- Docs name Sonnet (Claude Code) and `gpt-6.1-terra` (Codex) as smaller sub-agent models (merged in PR #79).

## Discovery summary

Read-only audits of `origin/main` against official host docs (codex-cli 0.167.2 source, Claude Code 2.1.316 docs) found:

- High: Codex sub-agent hook payloads carry the root `session_id` plus `agent_id`; the hook checked `agent_id` only for Claude, so Codex child prompts re-registered the parent and counted as owner turns.
- Medium: Claude may wrap bus wakes before `UserPromptSubmit`, which would make the exact wake match fail open.
- Medium: Codex `hooks.json` reinstall appended the bus group at the end, which can shift other tools' trust keys.
- Several host-behaviour statements in README, tutorial and examples were wrong or outdated (Codex compaction hook, `prefix_rule`, feature flag, links, sub-agent payloads, inbound limits, sandbox).
