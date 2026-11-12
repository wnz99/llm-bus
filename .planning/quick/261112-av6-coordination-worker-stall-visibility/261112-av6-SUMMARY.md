# 261112-av6 — Summary

PR: #78 (`feat/coordination-work-state` into `main`). Closes #76.

## Results

- Task 1: `llm-bus report` records fixed-code work state for a received, acknowledged local coordination direction in `coordination_work_reports` (schema 15). `status ID` shows it as `work_report`. Recording inserts no message, requests no wake and changes no hook guidance. The relay report path is unchanged; the transition rule is shared as `reports.lifecycle.transition_conflicts`.
- Task 2: `coordination show PROJECT` adds `directions` (unacknowledged plus open work directions). New `coordination direction-list PROJECT [--open]` lists directions with `state_changed_at`, capped at 100 after filtering. An acknowledged later `cancel` under the same grant closes earlier work directions by derivation.
- Task 3: worker and coordinator guidance (`GUIDANCE_VERSION` 9), the wake-turn `decision-answer` refusal remedy, and README, tutorial, remote-bus, skill, coordination reference, examples, simplified flow and machine-policy-plan updates. Implementation notes N1 and N2 applied.
- PR review R1 fixes: an outgoing relay direction acknowledged without any relay report keeps its acknowledgement time in `state_changed_at`; README points to `direction-list` and local `report`.

Accepted deviations from plan revision 3: `enrolled_setup` moved to `tests/coordination/conftest.py`; `GUIDANCE_VERSION` bumped; duty text held in `WORKER_DUTIES` and `COORDINATOR_DUTIES`; shared `work_state.table_names` used by `diagnostics/messages.py`; `test_legacy_compat` accepts `directions` in `show`; the relay `state_changed_at` refinement above.

## Verification

- `uv run poe check` on ddbcafc: exit 0, 853 tests passed; ruff, basedpyright, pylint, deptry and package check clean.
- Plan conformance on 9d37ee9: CONFORMS for AC1–AC9 and F1–F4.
- Independent task reviews: Task 1 (2 rounds), Task 2 (2 rounds), Task 3 (1 round), all approved. Aggregate PR review: R1 approved with 2 Low (fixed), R2 approved with Nits only.
- GitHub checks on ddbcafc: Relay/quality and Repository/quality passed.
- Manual two-session smoke test not run.

## Residuals

- No guidance next step when report sending is disabled for an imported direction (`report` fails with the existing policy error).
- Nits: single-use `TABLE` constant, `direction-list` parser placed in the decision parser helper, `table_names` helper located in `work_state`.

## Pending

- Owner rollout after merge: upgrade the installed llm-bus (mailbox moves to schema 15; rollback steps in the PR).
