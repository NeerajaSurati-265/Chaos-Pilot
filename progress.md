# Chaos-Pilot: Progress

Last updated: 2026-10-31. Sources: `decisions.txt`, `projectcontext.md`, git history.
Tests: **119 passing** (mocked AWS only).

## Workflow status

`inject -> detect -> AI investigator -> policy engine -> remediate / approval -> verify -> notify`

| Step | Status |
|---|---|
| Inject | Done (Bad Deployment, Config Change, task-failure experiment) |
| Detect | Done, deterministic, no AI |
| Investigator | Done: rule-based (default) and Claude-backed LLM |
| Policy engine | Done: allow-list, reversibility, blast radius, confidence, kill switch, scenario-to-action map |
| Approval | Done: human approve/reject with timeout |
| Remediation engine | Done: executors, preconditions, structured SUCCEEDED/FAILED results |
| Verify | Done: real recovery signals, not API success |
| Notify | **Not built** |
| Dashboard | **Not built** |

## Done and committed

- Terraform test environment (VPC, ALB, ECS, IAM, NAT) and a task-failure experiment.
- Repo hygiene, kill switch (off by default), requirements files.
- Incident lifecycle with enforced transitions and timeline, in-memory store.
- Policy engine (AUTO / HUMAN_APPROVAL / DENY).
- Pipeline, incident API, Bad Deployment scenario, test app.
- Configuration Change scenario (removes ALB-to-ECS ingress rule).
- Remediation engine, split out of the scenarios.
- Preconditions and structured FAILED results for remediation.
- Claude-backed LLM investigator (recommends only; falls back to rules on any error).
- ESCALATED terminal state for `escalate_to_human`.

## Done, not yet committed

- Config Change restore audit: known-good rule captured before injection, rule description
  preserved on restore.
- Removed dead allow-list entries `restore_known_good_config` and `restore_dependency`.
- Files: policy/engine.py, remediation/engine.py, remediation/executors.py,
  scenarios/config_change.py, decisions.txt, and four test files.

## Scenarios

- Bad Deployment: done
- Configuration Change: done
- Dependency Failure: proposed, not built
- Cascading Failure: proposed, not built (may be replaced by Dependency Failure)

## Next / known gaps

- NOTIFY step and dashboard.
- Escalation hand-off: ESCALATED only logs and records a timeline entry.
- LLM investigator tested with a stubbed client only; not run against the real API.
- `allowed_actions` is built from `DEFAULT_RULES`, not the policy instance's rules.
- Scenario and experiment AWS clients lack explicit timeouts.
- No Terraform IAM role for the remediation permissions.
- `projectcontext.md` still says the `restore_known_good_config` executor is missing (stale).
- Cleanup after Config Change remediation makes one redundant, tolerated AWS call.

## Open questions

- What should NOTIFY send, and where?
- Keep Cascading Failure or replace it with Dependency Failure?
