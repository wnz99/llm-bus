# Repository workflow

- Use `uv` for Python commands. Keep application modules directly in `src/` and tests in `tests/`.
- Run `uv run poe check` before pushing a pull request.
- Use Conventional Commits. Do not add `Co-Authored-By` trailers.

## Branches and pull requests

- Cut ordinary work branches from `develop` and target pull requests into `develop`.
- Promote only `develop` into `staging`, then only `staging` into `main`, using pull requests from this repository. An exception needs explicit approval.
- Merge with a merge commit, preserving individual commits. Do not squash or rebase shared branches.
- Delete a source branch after its pull request merges. Remove its local worktree and branch only after checking for uncommitted or unpushed work.
- A merge into `main` does not itself deploy anything.
