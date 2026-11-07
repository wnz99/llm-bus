# Agent labels and command-driven coordination: source/staging summary

Source and staged artifacts implement approved revision 3. Tasks 1–6 have Clean
independent task reviews. Final planner conformance is CONFORMANT_SOURCE_AND_STAGING.
Full-branch aggregate/PR review approved requirements and quality on
a9259fe91853c740233396e64faa0ab68c706059, with no High/Medium findings or
requirement gaps. Its sole Low documentation finding is corrected: unsupported
availability uses `unsupported_availability`; direction errors retain
`unsupported_coordination`. Final tracking and this diagnostic correction leave
reviewed executable code, configuration and tests unchanged. Prior review/test
receipts are reused under the documentation-followup exception; the new head is
not independently code-reviewed. Current-head CI and merge remain pending.
The Quick Tasks Completed row references PR #62 for source/staging delivery;
phase 11 remains active until merge. This summary is prepared before merge.

Labels identify main sessions for display and addressing while immutable native
identities retain authority. Direct owner coordinate/join commands support bounded
session consent and recovery. Remote availability requires receiver-owner binding
and both endpoints' policy; ordinary messages cannot authorize enrollment.

Final verification: `uv run poe check` passed with 463 Python tests, static checks
and package validation. Relay typecheck and 41 tests plus deployment dry-run passed
on unchanged relay source. Fresh product hashes, approved-plan equality, Markdown
links/fences and canonical CLI parser checks passed. Canonical evidence lives in
/tmp/llm-bus-agent-labels.RRRIOf/: final-verify.md, final-conformance.md and
task6-review-r1.md, all task1–task6 review receipts and pr-review-r1.md. PLAN
remains the verbatim approved revision. Final documentation validation and exact
reviewed-state equality evidence are recorded in finalize-merge.md there.

Installation and live proof remain pending. Installed guidance is version 6 and
has no label option; source guidance is version 8 and local schema is 12.
Global instruction edits are staged, not installed. No native lifecycle,
compaction, live wake, remote deployment or cross-machine reachability proof is
claimed. Close affected hosts before a separately authorized installation.
Relay migrations 0005 and 0006 must precede the matching Worker. Stop bridges and
retain pre-upgrade backups before rollback; older clients reject schema 12.
