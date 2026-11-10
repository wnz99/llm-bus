# llm-bus upgrade command with old-binary pruning: summary

PR #65 implements approved plan revision 3 in one task. `llm-bus upgrade`, run
from a clean checkout, builds a wheel from `HEAD` into
`~/.local/share/llm-bus/packages/<sha8>/`, reinstalls the uv tool with the
current extras on Python 3.12, verifies the receipt, the binary under a
restricted `PATH` and the `collab` import, and rolls back to the previous wheel
on failure without deleting anything. Only after verification does it delete
older `packages/*` entries and everything under `releases/`, without following
symlinks. `backups/`, `deploy-backups/` and other bus state are never touched.

Reviews: T1 independent task review approved requirements and quality on
db2af46; its Low findings were fixed in e0ef9ff. Final planner conformance:
CONFORMS. Full-branch aggregate/PR review R1 approved requirements and quality
on 3c36fe4 with Low/Nit residuals only (literal receipt path comparison fails
safe; untested `git worktree prune` failure message; minor readability notes).
This summary commit changes only tracking records; reviewed code, configuration
and tests are unchanged.

Verification: `uv run poe check` passed (485 tests, static checks, package
build); `uv build` produced a wheel containing the command; PR quality CI passed
on 3c36fe4.

Pending after merge (owner approval required for each step): run the first real
`llm-bus upgrade`, run `llm-bus install`, restart the launchd bridge and confirm
a new stable PID with a clean stderr tail. The first upgrade moves the tool from
Python 3.14 to 3.12. How uv records the wheel path in its receipt is proven only
by that run; a mismatch rolls back safely.

Canonical evidence (outside the repository):
~/.local/state/llm-bus/planning/bus-upgrade/ LEDGER.md, impl-t1-a1.md,
review-t1-a1.md, fix-res-a1.md, conformance-a1.md, verify-agg-a1.md and
pr-review-r1.md.
