# 261112-av6 — Coordination worker stall visibility: guidance and per-direction work state

## Registration

Registered 2026-11-12 with the personal feature-workflow tracking-record fallback. No GSD tooling wrote this record. Tracking only.

Approved semantic revision: 3. Independent plan gate: cycle 1, round 1 REVISE (0 High, 1 Medium, 5 Low), round 2 (terminal) REVISE with two Low documentation-only findings and no architecture, requirements, sequencing or migration blocker. The two Low findings are carried as Task 3 notes N1 and N2. The owner directed execution of revision 3 on 2026-11-12. No independent `APPROVED` verdict was recorded.

Source: GitHub issue #76. Branch: `feat/coordination-work-state`.

Canonical private draft: /Users/davidfava/Projects/Personal/llm-bus/.planning/drafts/coordination-work-state/PLAN.md

## Decisions

- Implement owner options A (guidance and refusal remedy) and B (recipient-recorded work state per local direction) together. Self-scheduled wakes and host activity time (option C) are out of scope.
- Communication stays efficient: workers record fixed-code state with `llm-bus report` (no message, no wake). Bus messages to the coordinator carry only blockers and outcomes it must act on or learn, never presence or routine progress.
- Open work operations: `assign`, `review`, `reprioritize`. A state report requires a prior acknowledgement.
- `coordination show` lists only directions that need attention (unacknowledged plus open). `coordination direction-list PROJECT [--open]` lists directions, capped at 100, with `state_changed_at`. The coordinator judges age; the bus does not detect stalls.
- An acknowledged later `cancel` to the same grant closes earlier work directions by derivation. No new report code.
- The decision-answer refusal in a wake turn stays; only its message gains a remedy.
- `docs/machine-policy-plan.md` is rewritten where it says reports on local-only messages fail.

## Discovery summary

Workers could acknowledge a direction and go idle with queued work, and the coordinator could not tell this from busy work. `status ID` showed `work_report` only for outgoing relay messages, and guidance said only "After handling events, return control." The decision-answer refusal named no remedy.
