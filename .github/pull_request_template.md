## Summary

Describe the problem and resulting behavior. Link the issue.

## Increment

Name the build-order number from `docs/prd.md` section 7.

## What changed

List the changes and any scope or contract deviations.

## How it was verified

Record the tested candidate/commit, actual commands, exit codes, and relevant
output in this PR text. Include paths to verification logs and evidence files
(for repository standards, `repo-standards-verification.json` in the delivery run
artifacts).

Include results for `uv run python scripts/check.py static`,
`uv run python scripts/check.py unit`, and `python harness/ci.py`.
Record required runtime and independent holdout evidence separately; ordinary
checks do not replace those gates. Under the staged-delivery rule in `MISSION.md`,
identify unbuilt capabilities with observable evidence and verify built ones.
Explain any checklist item that does not apply.

## Checklist

- [ ] Focused tests cover changed behavior, or file-content observations verify a documentation-only change.
- [ ] Static checks and tests pass with the results recorded above.
- [ ] Tests and runtime checks use `TKA_MODELS=fake` and call no network models.
- [ ] No secrets are committed, logged, persisted, or returned; CI has no `OPENROUTER_API_KEY`.
- [ ] `MISSION.md` invariants are respected and protected governance/evaluator files are preserved.
- [ ] UI changes follow `docs/prd.md` section 5, including tokens, AA contrast, keyboard access, and explicit states.
- [ ] CI workflow `ci` checks `backend`, `frontend`, and `smoke` pass.
