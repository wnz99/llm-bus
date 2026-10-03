# Repository workflow

- Use `uv` for Python commands. Keep application modules directly in `src/` and tests in `tests/`.
- Run `uv run poe check` before pushing a pull request.
- Use Conventional Commits. Do not add `Co-Authored-By` trailers.

## Branches and pull requests

- `main` is the only permanent branch. Cut ordinary work branches from `main` and open pull requests into `main`. When an issue is named or confidently known, link it in the PR description with `Closes <ISSUE-ID>`.
- Check the pull request's quality and portability results before merging; GitHub does not block merges on failed or pending results.
- Merge PRs with a merge commit and delete their temporary source branches, for example with `gh pr merge --merge --delete-branch`. Do not squash, rebase-merge, or rebase shared branches. Keep `main`.
- After a temporary branch's PR merges, remove its local worktree, prune worktree records, delete its local branch with `git branch -d`, and prune remote-tracking refs. Check for uncommitted or unpushed work first; do not force-delete it.
- A merge into `main` does not itself deploy anything.
