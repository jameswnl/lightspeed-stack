# Review Skills

Project review skills for the cloud-agents workstream live here.

## Skills

- `phased-plan-review`
  Use for reviewing design docs, task plans, and architecture proposals in any multi-phase project.

- `phased-implementation-review`
  Use for reviewing actual code, tests, deploy assets, and commit ranges for phased implementation work.

## Review Standard

Both skills require that every review explicitly covers these three perspectives:

1. **Functionality**
   Does the plan/code actually implement the intended behavior?

2. **Quality**
   Is it testable, maintainable, internally consistent, and realistically scoped?

3. **Security**
   Are trust boundaries, code-loading surfaces, exposed endpoints, secrets, and deployment assumptions handled safely enough for the intended phase?

The review is not complete unless all three are considered, even if one perspective produces no findings.
