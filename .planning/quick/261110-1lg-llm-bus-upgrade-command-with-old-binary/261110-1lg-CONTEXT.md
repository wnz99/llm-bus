# 261110-1lg — llm-bus upgrade command with old-binary pruning

## Registration

Registered 2026-11-10 with the personal feature-workflow tracking-record fallback. No GSD tooling wrote this record. Tracking only.

Approved semantic revision: 3. Independent plan gate: cycle 1, round 2 APPROVED with two Low residuals (L-A, L-B). Owner approved the plan on 2026-11-10.

Canonical private draft: /Users/davidfava/Projects/Personal/llm-bus/.planning/drafts/bus-upgrade/PLAN.md

## Decisions

- Add a product command `llm-bus upgrade`, run from a checkout. It builds the wheel into `~/.local/share/llm-bus/packages/<id>/`, installs it with `uv tool install --force`, verifies the installed binary, and only after successful verification deletes every other `packages/*` entry and everything under `~/.local/state/llm-bus/releases/`.
- Retention: keep none. Only the active version remains. Rollback means rebuilding from Git.
- On verification failure: delete nothing, keep the previous install active, exit non-zero.
- Never touch `backups/`, `deploy-backups/` or other state.
- Base bus behaviour stays unchanged; the command is additive.
- Older binaries need not work against a store migrated by a newer version; upgrading every session on the machine is acceptable.
- Lands before the `coordinator-delegation` flow so later PRs install through it.

## Discovery summary

No product install or upgrade command existed. README documented manual `uv tool install` commands, and past releases used one-off rollout scripts that left wheels under `~/.local/share/llm-bus/packages/` and release worktrees and artifacts under `~/.local/state/llm-bus/releases/`. The active install is a uv tool whose receipt points at a wheel in `packages/` with extra `collab`. Old release worktrees, packages and artifacts were removed manually with owner approval on 2026-11-10.
