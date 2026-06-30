---
name: phased-plan-review
description: Review phased design and task-planning docs for gaps in semantics, trust boundaries, testability, and implementation realism. Use when reviewing phase/task/architecture proposals, especially when the user wants findings written to markdown files and optionally re-reviewed after document updates.
disable-model-invocation: true
---

# Phased Plan Review

## Critical Instruction

When this skill is invoked, follow the workflow it prescribes.

- Do not substitute a different workflow because it seems reasonable.
- Do not guess at alternate intent when the skill already specifies what to do.
- If the request is ambiguous, resolve that ambiguity by following the skill's prescribed behavior as closely as possible.
- Treat the skill as the source of truth for how this review workflow should run.

Use this skill when reviewing design or task-planning documents for a multi-phase project.

Always start a watcher and continue until you give out a `LGTM`.

## Scope

Primary targets:

- roadmap docs
- phase task breakdowns
- architecture/design proposals
- companion docs that define adjacent constraints or workflows

This skill is for reviewing the **plan/design**, not the shipped code.

## Review Method

1. Read the target plan doc fully.
2. Read the most relevant companion docs:
   - parent roadmap or phase overview
   - prior phase task docs
   - adjacent design docs the target explicitly depends on
3. Review for:
   - semantic contradictions inside the doc
   - scope that exceeds the proposed task breakdown
   - undefined runtime contracts
   - missing persistence / lifecycle / timeout mechanisms
   - recovery behavior after crash boundaries or partial persistence
   - weak trust-boundary or security assumptions
   - cross-task coupling where one task silently depends on another task's naming, persistence, or cleanup contract
   - stale sections that still describe a superseded design after the main task text changed
   - identifier uniqueness / reconstructibility requirements for retries, cleanup, or idempotency
   - acceptance criteria that are not concretely testable
   - E2E claims not supported by the design
   - test plans that only cover the happy path and miss the hardest state transitions
   - two competing sources of truth in the config/schema
4. Prefer findings that would materially affect implementation, testing, or safety.

## Required Perspectives

Every review must explicitly cover all three perspectives below:

- **Functionality**: Does the plan actually achieve the intended user/system behavior? Are the runtime semantics, lifecycle, persistence, routing, and acceptance criteria implementable?
- **Quality**: Is the plan well-scoped, testable, maintainable, and internally consistent? Are migration steps, contracts, and task breakdown realistic and non-contradictory?
- **Security**: Are trust boundaries, exposed APIs, code-loading mechanisms, approvals, secrets, and deployment assumptions explicit and safe enough for the intended phase?

Do not stop at one lens. A plan review is incomplete unless all three are covered, even if one section yields “no issues found.”

## Review Lens

Prioritize these categories:

- **Blockers**: a promised behavior has no implementable mechanism
- **Majors**: ambiguous contracts, mismatched scope, or security boundary gaps
- **Mediums**: misleading wording, under-specified migration path, or weak testability

Good examples:

- approval timeout is described but no component can enforce it
- config has two routing authorities (`dispatch_to` and `dispatch_endpoint`)
- plan promises per-tool metrics but no instrumentation point exists
- trust boundary is described for Kubernetes but not for Podman/local dev
- recovery poller is supposed to clean up jobs, but the job name is not persisted or reconstructible after a crash
- the main task text was updated, but the dependency graph or verification section still describes the old design
- integration tests cover dispatch/execute only, but the riskiest path is retry -> exhaustion -> escalation

For each finding, be clear about which primary perspective it belongs to:

- functionality
- quality
- security

Avoid low-value commentary like style preferences unless they mask a real implementation problem.

## Output Format

Lead with findings, sorted by severity.

Use this structure:

```md
# Review: <doc name>

## Findings

### 1. <Severity>: <short issue>
<why it matters>
<recommended fix>

## Perspective Check
- Functionality: <covered / no major issues / remaining gaps>
- Quality: <covered / no major issues / remaining gaps>
- Security: <covered / no major issues / remaining gaps>

## Open Questions / Assumptions

## Summary
```

Keep findings concrete and actionable. Tie each one back to a specific contract or behavior in the doc.
If one perspective produced no findings, say so explicitly in `Perspective Check`.

## Writing Review Files

When the user asks to write the review out:

- create a new markdown file near the reviewed docs
- use a distinct name that matches the phase and review type

Do not overwrite an existing finalized review unless the user explicitly asks.

## Continuous Re-review Loop

After the first review is written, do not stop at a single pass.

1. Write the current review first.
2. Start a background watcher for the specific file or requested review scope.
3. Use a settle delay before acting:
   - normally 30 seconds for commit polling
   - 2 minutes if the user explicitly asks for a longer settle window while editing a doc
4. On each wake:
   - re-read the doc
   - check whether the previous findings were actually resolved
   - if still not satisfied, write a new review file with only the remaining issues
   - if the previous findings look resolved, do one fresh full-pass review from scratch before deciding `LGTM`
   - right before writing `LGTM`, do one more review that covers the whole requested batch/scope of plan changes, not just the most recent edits
   - in that fresh pass, actively look for newly visible second-order issues rather than only regression-checking old findings
   - if satisfied after the fresh pass, write a final file containing exactly `LGTM`
5. Stop the watcher immediately after writing the final `LGTM` file.

## LGTM Standard

Only write the final `LGTM` file when:

- previously raised blockers are resolved
- you have done one clean-slate pass after those blockers were resolved
- no newly visible coupling, uniqueness, recovery, or stale-text issues appeared in the updated draft
- the remaining trust boundary is explicit enough for the intended phase
- the doc’s contracts match the proposed implementation/test path
- the dependency graph, verification section, and summary are consistent with the current task design
- you have done one final fresh-pass review over the whole requested batch/scope, not just the latest edits
- functionality, quality, and security have all been re-checked
- there are no meaningful unresolved design contradictions left

Do not write `LGTM` just because the latest revision is better than the previous one.
