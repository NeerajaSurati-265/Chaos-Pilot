# Chaos-Pilot: Progress

Last updated: 2026-11-01. Sources: `decisions.txt`, `projectcontext.md`, git history.
Tests: **205 passing** (mocked AWS only).

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
- Config Change restore audit (`69e6fcd`): known-good rule captured before injection, rule
  description preserved on restore.
- Removed dead allow-list entries `restore_known_good_config` and `restore_dependency` (`69e6fcd`).

## Done, not yet committed

- Shared boto3 client factory (`chaos_pilot/core/aws.py`): connect 5 s, read 15 s, standard
  retries, `max_attempts` 3. All 8 app clients use it.
- `ConfigChangeScenario.inject()` accepts `InvalidPermission.NotFound` on the revoke only
  after confirming the rule was present and is now gone.
- Files: core/aws.py (new), scenarios/bad_deployment.py, scenarios/config_change.py,
  experiments/task_failure.py, remediation/executors.py, tests/test_aws_client.py (new),
  tests/test_config_change.py, decisions.txt, projectcontext.md, progress.md.

## Runtime kill switch (done, not yet committed)

- `chaos_pilot/core/killswitch.py` (new): in-memory switch, `GET /kill-switch`,
  `POST /kill-switch/engage`, `POST /kill-switch/release`; blocks new starts with 403.
- Running incidents abort at checkpoints (before inject, after approval wait, before remediation,
  before verification, every polling iteration), run cleanup, and end in the new terminal state
  `ABORTED`. Engage sticks for the run in flight; releasing does not resume anything.
- Endpoints are unauthenticated and a restart releases the switch (local MVP only).
- Files: core/killswitch.py, tests/test_kill_switch.py (new); core/pipeline.py, core/engine.py,
  incidents/models.py, app/main.py, scenarios/bad_deployment.py, scenarios/config_change.py.

## Accurate report and verified cleanup (done, not yet committed)

- New `PENDING` initial state; an incident enters `INJECTED` only after `inject()` succeeds. A failed
  injection ends `FAILED` and never shows `INJECTED`.
- `inject()` returns `Injection(summary, injected_fault)`; scenarios expose `pre_injection_state()`.
  Both go into the report with no new AWS calls.
- Structured `cleanup` result in the report (replaces the free-text notes; a timeline entry is kept).
  New read-only `verify_cleanup()` per scenario (bounded retry, 30 s / 5 s). A failed check never
  changes the terminal state. Bad Deployment checks the revision only.
- `detect_seconds` added; `recovery_seconds` now ends at the RESOLVED entry (cleanup entries added
  after RESOLVED no longer move it).
- Files: models.py, core/pipeline.py, scenarios/base.py, bad_deployment.py, config_change.py,
  tests/test_report_accuracy.py (new); tests updated: test_pipeline.py, test_kill_switch.py,
  test_incident_lifecycle.py.
- Not implemented: refuse new starts while the last cleanup is unverified.

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
- No Terraform IAM role for the remediation permissions.
- Cleanup after Config Change remediation makes one redundant, tolerated AWS call.

## Open questions

- What should NOTIFY send, and where?
- Keep Cascading Failure or replace it with Dependency Failure?
