# Repository workflow

- Use `uv` for Python commands. Keep application modules directly in `src/` and tests in `tests/`.
- Run `uv run poe check` before pushing a pull request.
- Use Conventional Commits. Do not add `Co-Authored-By` trailers.

## Branches and pull requests

- Cut ordinary work branches from `develop` and target pull requests into `develop`.
- Promote only `develop` into `staging`, then only `staging` into `main`, using pull requests from this repository. An exception needs explicit approval.
- Merge with a merge commit, preserving individual commits. Do not squash or rebase shared branches.
- Delete temporary feature or fix branches after their pull requests merge. Keep the permanent `develop` and `staging` branches after promotion. Remove a temporary branch's local worktree and branch only after checking for uncommitted or unpushed work.
- A merge into `main` does not itself deploy anything.
