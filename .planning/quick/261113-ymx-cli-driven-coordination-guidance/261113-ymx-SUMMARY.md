# 261113-ymx — Summary

PR #86 (`feat/coordination-guidance` into `main`).

## Results

- Agents no longer read the 725-line coordination reference in full. CLI and hook guidance carry `guidance_version` (10) and `guidance_files`, chosen from role, coordination state and capabilities in effect (`src/llm_bus/cli/guidance_files.py`).
- Rule text lives in nine skill files under `skills/llm-bus/references/coordination/`; every rule of the old reference (R01–R58) has one home, and `coordination.md` is the index.
- `core.md` carries the version marker; any CLI/skill mismatch or missing file makes the agent read the index and every listed file and tell the owner.
- Owner actions without an enrollment load their files through one `SKILL.md` dispatch line; `amend` and a new-coordinator `replace` return full guidance.
- Hooks print a pointer instead of standing instructions and keep output under 9000 bytes; `llm-bus --format agent guidance` prints the standing text verbatim.
- The setup discovery step delegates reading to one read-only subagent on the host's smaller model; the main agent confirms the memory ticket itself.
- The example coordination rules file keeps only trigger, prerequisites and a pointer.
- Design history: plan revision 7 put the rule text in CLI output under a hook budget; implementation measured the coordinator rules at about 9500 units, and the owner re-planned to skill-driven guidance (revision 11).

## Verification

- `uv run poe check` at 9cc3500: exit 0, 881 tests passed.
- Smoke run against a throwaway bus database: correct `guidance_files` for coordinate, join and amend; SessionStart hooks with 8192-character standing instructions stay at 5–6 KB with the pointer.
- Independent reviews: every task, final plan conformance (CONFORMS), aggregate verification (PASS), PR review R1 (Approved).

## Residuals and pending work

- `core.md`, README and the tutorial list only `coordinate` and `join` as returning standing text verbatim; `amend` and a new-coordinator `replace` do too. Low.
- Hooks query the pending-message count twice; a concurrent send can make the empty-inbox check and the printed count disagree. Low.
- A partly copied skill directory with a current `core.md` is not detected; a forgotten `GUIDANCE_VERSION` bump is not caught by a test.
- Owner action after merge: upgrade the CLI and copy the whole `skills/llm-bus` directory to the installed skill location together.
