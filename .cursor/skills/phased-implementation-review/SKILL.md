---
name: phased-implementation-review
description: Review phased implementation work across commits or commit ranges for behavioral bugs, seam failures, trust-boundary regressions, and test coverage quality. Use when reviewing code, tests, deploy assets, or implementation milestones and when the user wants findings written to markdown files with optional polling for follow-up commits until final approval.
disable-model-invocation: true
---

# Phased Implementation Review

## Critical Instruction

When this skill is invoked, follow the workflow it prescribes.

- Do not substitute a different workflow because it seems reasonable.
- Do not guess at alternate intent when the skill already specifies what to do.
- If the request is ambiguous, resolve that ambiguity by following the skill's prescribed behavior as closely as possible.
- Treat the skill as the source of truth for how this review workflow should run.

Use this skill when reviewing the actual implementation of a multi-phase project.

## Scope

Typical areas:

- source code
- deploy/config assets
- unit and integration tests
- supporting docs if they materially affect runtime behavior

This skill is for reviewing **code and tests**, not just the plan.

## Review Method

1. Determine the review scope first:
   - latest commit only
   - full branch range since a base commit
   - specific phase implementation
2. Inspect git context before reading code:
   - current branch
   - recent commits
   - changed files in scope
3. Read the most relevant implementation files and matching tests together.
4. Run the relevant test suites where practical.

## Required Perspectives

Every implementation review must explicitly cover all three perspectives below:

- **Functionality**: Does the code actually implement the intended behavior? Check runtime semantics, state transitions, API behavior, migrations, and whether public contracts match the design.
- **Quality**: Is the code/test/deploy path maintainable, coherent, and sufficiently verified? Check test strength, startup behavior, migration realism, and consistency between implementation and committed assets.
- **Security**: Are trust boundaries, auth behavior, code-loading surfaces, exposed endpoints, secrets handling, and dev-only assumptions implemented safely enough for the intended phase?

Do not treat security as optional or assume quality is covered just because tests pass. A full review is incomplete unless all three perspectives are considered.

## Core Review Priorities

Prioritize:

- seam failures between components
- behavior mismatches between design and implementation
- trust-boundary/security regressions
- async state-machine bugs
- “field exists in model but is never populated/used”
- deployment claims not reflected in committed assets
- test suites that pass while not proving the claimed behavior

Tag each meaningful finding mentally by its primary perspective:

- functionality
- quality
- security

Examples of high-signal findings:

- sync and async paths disagree on failure semantics
- E2E claims cross-pod behavior but tests only hit one pod directly
- a correlation ID is validated for headers but raw input still reaches logs
- a model field like `dispatched_run_ids` exists but runtime never sets it
- compose/Kind assets still use old images despite migration claims

## Test Review Standard

Do not stop at “tests exist.”

Check whether tests prove:

- the public contract, not just internals
- failure paths, not just happy paths
- deployed/runtime behavior, not just isolated helpers
- startup/bootstrap behavior for env-driven or import-time logic

Good questions:

- Does a test cover the caller-side contract?
- Does a failing component become a failed workflow/run state?
- Is the new field asserted anywhere beyond serialization tests?
- Are regression tests added for fixes that were previously missed?
- Does the test coverage support the functionality, quality, and security claims being made?

## Suggested Command Pattern

Use git plus focused test runs.

Common sequence:

```bash
git status --short
git log -3 --stat --decorate
git diff --name-only <base>..HEAD
pytest <relevant test paths> -q
```

If the change is broad, run the most relevant phase-wide suites instead of just tiny slices.

If E2E is part of the claim, also check whether the required services are actually running before claiming end-to-end validation.

## Output Format

Lead with findings and keep them severity-ordered.

Use this structure:

```md
# Review: <phase / commit / range>

## Findings

### 1. <Severity>: <short issue>
<why it matters>
<recommended fix>

## Perspective Check
- Functionality: <covered / no major issues / remaining gaps>
- Quality: <covered / no major issues / remaining gaps>
- Security: <covered / no major issues / remaining gaps>

## Verification
<tests/commands you ran and results>

## Summary
```

If nothing important is wrong, say that clearly and note any remaining verification limits.
If one perspective produced no findings, explicitly say so in `Perspective Check`.

## Writing Review Files

When asked to write the review:

- create a new markdown file near the implementation or design-review artifacts
- use a specific filename that reflects the scope

If a later commit addresses prior findings, write a **new** follow-up file rather than mutating the old review.

## Polling for Follow-up Commits

If the user wants repeated re-review as commits land:

1. Write the initial implementation review first.
2. Start a background watcher on `git rev-parse HEAD`.
3. Do **not** re-review just because a new commit appeared when the review scope is a specific phase. A phase may be implemented across multiple commits, so intermediate commits are not automatically review-ready.
4. For phase-completion polling, only start the next review when one of these is true:
   - the latest commit is at least 15 minutes old, which treats the phase as temporarily settled
   - the latest commit message clearly states that the relevant phase work is done
5. When deciding whether the commit message is a completion signal, be conservative. Treat explicit messages like `phase 5 complete`, `phase 5 done`, `finish phase 5`, or equivalent wording as sufficient. Do not trigger on vague messages like `wip`, `part 2`, `more fixes`, or `continue phase 5`.
6. Once the phase looks settled, re-review the whole requested phase scope rather than only the newest commit unless the user explicitly narrowed the scope.
7. Compare against previously raised findings; do not re-litigate already-fixed issues.
8. Right before writing `LGTM`, do one more fresh review pass over the whole requested batch/scope. Do not limit this pass to only the most recent fixes or commits.
9. Only if that whole-batch pass still looks clean, write a final file containing exactly `LGTM`.
10. Stop the watcher immediately after writing that file.

## LGTM Standard

Only write `LGTM` when:

- the implementation matches the approved design closely enough
- prior significant behavioral/security findings are resolved
- the relevant test suites pass
- you have done one final fresh-pass review over the whole requested batch/scope, not just the latest changes
- functionality, quality, and security have all been re-checked
- no meaningful unresolved seam or contract bugs remain

Do not write `LGTM` if your confidence depends on an unrun but essential verification step unless you clearly decide that gap is non-blocking for the requested review scope.
