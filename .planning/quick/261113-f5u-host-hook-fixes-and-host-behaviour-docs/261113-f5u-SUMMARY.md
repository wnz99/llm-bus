# 261113-f5u — Summary

PR #85 (`fix/host-hook-fixes` into `main`).

## Results

- Sub-agent hooks: any hook payload with a non-empty `agent_id` is treated as a child on Claude Code and Codex. It does not register, record a turn or end the parent session, and it gets the sub-agent notice.
- Wake turns: `record_turn` uses `contains_wake_notice` on both hosts (D5), checking the message-ID bound for every match. The acknowledged-wake no-op keeps the exact match.
- Codex `hooks.json` reinstall replaces the bus handler in place. A bus handler inside another tool's group with extra keys moves to an appended standard group; duplicate bus groups merge.
- README, tutorial and agent-instruction docs corrected for Codex compaction, hooks feature default, `prefix_rule` and sandbox `writable_roots`, sub-agent payloads, Claude inbound limits and turn recording.
- Deferred Low items recorded as GitHub issues #80–#84.

## Verification

- `uv run poe check` at 9283bf3: exit 0, 867 tests passed.
- Acceptance proofs: `-k child_hook` 7 passed; `-k wake_turn` 8 passed; `test_decision_turns.py` 5 passed; `test_installation.py` passes including the in-place, matcher-group and duplicate-group cases.
- Claude Code 2.1.316 wake capture (unattended pty probe): idle and mid-turn wakes reach `UserPromptSubmit` as the bare notice; the mid-turn hook fires after the running tool call returns, inside the same turn.
- Independent reviews: every task, final plan conformance (CONFORMS), aggregate verification (PASS), PR review R1 and R2 (Approved).

## Residuals and pending work

- README:70 says reinstall shifts other hooks in "two cases"; a third (a duplicate bus handler inside a later hooks-only group) also shifts later handlers in that group. Low.
- Not run: manual Codex `/hooks` trust check after reinstall and a live Codex sub-agent prompt; they need a post-merge install.
- Follow-up flow: coordination-guidance corrects the remaining Codex compaction text in `skills/llm-bus/references/coordination.md`.
