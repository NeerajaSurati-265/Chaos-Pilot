# Chaos-Pilot: Progress

Last updated: 2026-11-01. Sources: `decisions.txt`, `projectcontext.md`, git history.
Tests: **290 passing** (mocked AWS only).

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
| Dashboard | C1 and C2 done (incl. analytics page, demo mode); JS syntax-checked with node but never run in a browser |

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

## Shared AWS client factory and Config Change revoke fix (committed: `f989e3d`, `d304173`)

- Shared boto3 client factory (`chaos_pilot/core/aws.py`): connect 5 s, read 15 s, standard
  retries, `max_attempts` 3. All 8 app clients use it.
- `ConfigChangeScenario.inject()` accepts `InvalidPermission.NotFound` on the revoke only
  after confirming the rule was present and is now gone.
- Files: core/aws.py (new), scenarios/bad_deployment.py, scenarios/config_change.py,
  experiments/task_failure.py, remediation/executors.py, tests/test_aws_client.py (new),
  tests/test_config_change.py, decisions.txt, projectcontext.md, progress.md.

## Runtime kill switch (committed: `a39d853`)

- `chaos_pilot/core/killswitch.py` (new): in-memory switch, `GET /kill-switch`,
  `POST /kill-switch/engage`, `POST /kill-switch/release`; blocks new starts with 403.
- Running incidents abort at checkpoints (before inject, after approval wait, before remediation,
  before verification, every polling iteration), run cleanup, and end in the new terminal state
  `ABORTED`. Engage sticks for the run in flight; releasing does not resume anything.
- Endpoints are unauthenticated and a restart releases the switch (local MVP only).
- Files: core/killswitch.py, tests/test_kill_switch.py (new); core/pipeline.py, core/engine.py,
  incidents/models.py, app/main.py, scenarios/bad_deployment.py, scenarios/config_change.py.

## Accurate report and verified cleanup (committed; last touched by `24d7a6f`)

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

## Dashboard v2, part C2: analytics + demo mode (done, not yet committed)

- `GET /api/analytics` (pure functions in `app/analytics.py`) feeds the Analytics page (7 panels,
  each with a server-built one-sentence takeaway or "Not enough data yet") and now also the
  Dashboard KPIs, trend arrows and the recovery chart with its scenario filter.
- AI accuracy: ground truth is `SCENARIO_ACTIONS`; `escalate_to_human` is its own bucket, not
  correct, kept in the denominator. The page says the rule-based investigator is what is measured.
- Demo mode `CHAOS_PILOT_DEMO_DATA=true`: separate `DemoEngine` (fake scenarios/executors, real
  pipeline/policy/remediation), 13 seeded incidents run sequentially (all six outcomes), banner on
  every page and report, DEMO DATA line in md/json. No boto3 client or real scenario is created.
- Seeder releases the kill switch only if it engaged it itself.
- Shared file: `core/engine.py` got an optional `gate` argument (default unchanged).
- Tests: 290 total (+43). JS modules are checked with `node --input-type=module --check`.
- Not verified in a real browser; durations in demo mode are fake-scale.

## Dashboard v2, part C1 (committed: `1e2854b`)

- Replaced the simple UI: sidebar, KPIs, hero + scenario picker, recent incidents, scenarios band,
  incident page with flowchart / AI vs policy panels / approval card / verification / timeline,
  report card with .md, .json, print-PDF and link, `/reports/{id}` page, reports table.
- New read-only endpoints: `/api/config`, `/api/scenarios`, `/api/reports/{id}.md|.json`,
  `/reports/{id}`; `/incidents` now adds derived fields (outcome, stage_seconds, approval deadline).
- Kill switch: compact Engage (reason required) / Release (confirm) in the sidebar, existing
  endpoints only; the experiments pill is display-only.
- Approval countdown: `approval_deadline` / `approval_seconds_remaining` are computed on the
  server (timeline entry + `Pipeline.approval_timeout`); the page only counts down from them.
- Fixed: scenario labels now normalise `bad_deployment` / `bad-deployment` (v1 showed raw names).
- Chart.js 4.4.1 and Google Fonts load from CDNs (fallback fonts, "Charts are unavailable" note,
  no SRI hash). C1 computed KPIs and the recovery chart in the browser (replaced in C2).
- The commit message for `1e2854b` mentions analytics, but C1 only had a placeholder.

## Dashboard v1 notes (UI replaced by v2; these two points still apply)

- `Incident.report()` gained `explanation` (the AI panel needs it).
- Dashboard and kill-switch endpoints are unauthenticated: run uvicorn on 127.0.0.1 only, never
  `--host 0.0.0.0`.

## Scenarios

- Bad Deployment: done
- Configuration Change: done
- Dependency Failure: proposed, not built
- Cascading Failure: proposed, not built (may be replaced by Dependency Failure)

## Next / known gaps

- NOTIFY step.
- 7a: refuse new starts while the last cleanup is unverified (needs an explicit acknowledge).
- Dashboard JS has never run in a browser (it is only syntax-checked with node and string-checked in tests).
- Escalation hand-off: ESCALATED only logs and records a timeline entry.
- LLM investigator tested with a stubbed client only; not run against the real API.
- `allowed_actions` is built from `DEFAULT_RULES`, not the policy instance's rules.
- No Terraform IAM role for the remediation permissions.
- Cleanup after Config Change remediation makes one redundant, tolerated AWS call.

## Open questions

- What should NOTIFY send, and where?
- Keep Cascading Failure or replace it with Dependency Failure?
