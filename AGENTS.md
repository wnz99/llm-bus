# Repository workflow

- Use `uv` for Python commands. Keep application modules directly in `src/` and tests in `tests/`.
- Run `uv run poe check` before pushing a pull request.
- Use Conventional Commits. Do not add `Co-Authored-By` trailers.

## Branches and pull requests

- Cut ordinary work branches from `develop` and open pull requests into `develop`. When an issue is named or confidently known, link it in the PR description with `Closes <ISSUE-ID>`.
- Promote only `develop` into `staging`, then only `staging` into `main`, using separate pull requests from this repository. Never use a temporary branch as a promotion source. An exception needs explicit approval.
- Before opening a promotion PR, check the quality results for the changes merged into `develop`; promotion PRs do not rerun those checks, and GitHub does not block merges on failed or pending results.
- Merge PRs with a merge commit and delete their temporary source branches, for example with `gh pr merge --merge --delete-branch`. Do not squash, rebase-merge, or rebase shared branches. Keep permanent `develop` and `staging` branches after promotion.
- After a temporary branch's PR merges, remove its local worktree, prune worktree records, delete its local branch with `git branch -d`, and prune remote-tracking refs. Check for uncommitted or unpushed work first; do not force-delete it.
- A merge into `main` does not itself deploy anything.
