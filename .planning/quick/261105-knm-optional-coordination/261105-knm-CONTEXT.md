# Optional coordination — approved context

## Registration

Quick ID: 261105-knm. Semantic revision: 1. Registered on 2026-11-05.
This is tracking-only registration under the personal feature workflow, not a full GSD quick execution.
The installed initializer supplied the feature-local paths. ROADMAP.md is absent; the full quick workflow's ROADMAP validation has not passed.
No GSD planner or executor was dispatched during registration. Subsequent approved
implementation completed T1–T5 sequentially. All five independent task reviews
passed with no findings. Integrated verification and planner conformance passed
on 669459e9ddf29bd05dd004311eebabe8ed217d81. PR #60 is open into main:
https://github.com/wnz99/llm-bus/pull/60. Complete-branch aggregate/PR review passed
on 0d98da7263fc2b32c1dd4d5d81632232edce00a2 with no findings. The final summary
records completed implementation and verification; current-head CI and merge
remain premerge gates. Phase 11 remains active until merge; phase 13 follows it.
Canonical task evidence is in the executor ledger.
The approved plan is preserved verbatim in 261105-knm-PLAN.md; its historical planning-only wording remains unchanged.
The execution coordinator records the subsequent user approval and implementation authority in the canonical executor ledger:
 /Users/davidfava/.local/state/llm-bus/planning/optional-coordination/LEDGER.md

## Approved decisions and discovery

- Coordination is optional; ordinary messaging remains unchanged.
- Users start main sessions manually. Native subagents never enroll or use the bus.
- Each receiving owner controls exact coordinator grants locally, including remote peers. Grants default deny; sender flags cannot create authority.
- Grants may persist until revoked or expire. Offline receivers retain their last local policy; global immediate revocation is not promised.
- Structured directions must pass receiver checks at admission, exposure and wake. Ordinary text, monitoring, reports and tickets cannot grant authority.
- Reuse existing identity, storage, transport and hooks. Separate remote direction endpoints prevent old clients from discarding authority metadata.
- Beads remains durable task memory through its official skill and project-scoped bd prime. Do not install, initialize or synchronize it automatically.
- Existing SessionStart/UserPromptSubmit guidance restores current enrollment references; do not inject ticket bodies or invent host events.
- Five approved tasks execute sequentially: local roles and grants; local directions; remote transport; hook recovery; setup and documentation.
- Documentation must cover both hosts, independent remote grants, replacement, revocation, compatibility and optional caveman communication.

Canonical private draft: /Users/davidfava/Projects/Personal/llm-bus/.planning/drafts/optional-coordination
Registration procedure: /Users/davidfava/.local/state/llm-bus/planning/optional-coordination/registration-advice-v1.md
Detailed execution, verification and review evidence belongs in the canonical executor ledger and its reports. This context adds no requirements.
