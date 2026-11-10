# Chaos-Pilot: Project Context

> Read this file at the start of every session before touching code.
> Status last updated: 1 Nov 2026. Anything below marked **[verify]** may have changed since; check the repo before relying on it.

---

## 1. What this project is

Chaos-Pilot is an **AWS incident investigation and controlled-recovery platform**. It:

1. injects a selected failure into an **isolated test workload** (ECS service behind an ALB),
2. **detects** the incident from deterministic AWS signals,
3. asks an **AI Investigator** to diagnose the likely cause,
4. lets a deterministic **Policy Engine** decide AUTO, HUMAN_APPROVAL or DENY,
5. **executes** only the authorized, allow-listed action,
6. **verifies** real recovery (task capacity, ALB health),
7. produces an incident **report** with a full timeline.

**Product distinction:** it does NOT claim to replace native AWS health checks, ECS task replacement, deployment circuit breakers or CloudWatch-alarm rollback. Its value is the layer on top: investigation, correlation, policy-controlled orchestration, recovery verification and an explainable timeline across failure types.

- **Owner:** Neeraja Surati (resume project for cloud / DevOps / backend roles)
- **Repo:** github.com/NeerajaSurati-265/Chaos-Pilot
- **Local path:** `/home/nsurati/chaos-pilot`
- **Stack:** Python, FastAPI, boto3, AWS (ECS, ALB, VPC, security groups, IAM, CloudWatch), Terraform, pytest (mocked AWS), optional Claude-backed investigator

---

## 2. Rules for any AI agent working in this repo (non-negotiable)

1. **Never run `git commit`, `git push` or `git add`.** Make local file changes only. The owner reviews diffs in VS Code and commits herself.
2. **Work on ONLY the task you were given.** No refactors of unrelated code. No databases, queues, multi-account support or extra integrations unless the task demands it.
3. **Never run `terraform apply`, `terraform destroy`, or anything that touches real AWS** unless explicitly asked in that session.
4. **AI never decides whether an incident exists, and never authorizes an action.** Detection and policy stay deterministic code.
5. **Policy is action- and risk-based** (action, risk, reversibility, blast radius, allow-list). AI confidence is one input, never the deciding rule.
6. **Remediation executes only policy-authorized, allow-listed actions.** Anything else is refused.
7. **Never report success without proof.** Recovery is judged by Verification against real AWS state. Failed remediation or verification ends as `FAILED` with evidence.
8. **Every AWS call needs a bounded timeout** and error handling that records `FAILED` rather than pretending success.
9. **Tests use mocked AWS only.** No real AWS calls in tests.
10. **Follow existing conventions; reuse existing modules.** Keep changes to shared files (incident model, policy, remediation registry, pipeline) minimal and list them in your summary.
11. **Never inject into production.** Only the isolated test workload.
12. While working, run only the relevant test files; run the full suite once at the end.
13. When done, report: files changed, what each does, shared files touched, AWS calls added, safety considerations, how to test manually. Keep it short.

---

## 3. Frozen workflow

```
SELECT SCENARIO → INJECT → DETECT (deterministic) → AI INVESTIGATOR (diagnose only)
  → POLICY ENGINE (AUTO / HUMAN_APPROVAL / DENY) → [AWAITING_APPROVAL]
  → REMEDIATION ENGINE (allow-listed execution) → VERIFY (real health) → NOTIFY / REPORT
```

| Component | Responsibility | Key rule |
|---|---|---|
| Scenario / injector | Applies a controlled, reversible fault to the test workload | Allow-listed scenarios, bounded duration |
| Detection | Polls deterministic signals, opens the incident | No AI involved |
| Incident model + store | State machine, timeline, evidence | Steps can't be skipped; RESOLVED only via verification |
| AI Investigator | Sanitized evidence in → diagnosis, confidence, explanation, recommended action out | Advisory only |
| Policy Engine | Maps the proposed action to AUTO / HUMAN_APPROVAL / DENY / ESCALATE with a reason | Deterministic, unit-testable |
| Approval step | Pauses for a human decision, with timeout | Reject or timeout = no execution |
| Remediation Engine | Runs the authorized action via an executor | Allow-list only; makes no decisions |
| Verification | Running == desired, tasks healthy, ALB targets healthy, correct revision | Bounded polling; timeout = FAILED |
| Notification / report | Records outcome and report | Must be truthful |

### Incident lifecycle

`PENDING → INJECTED → DETECTED → INVESTIGATING → POLICY_DECIDED → AWAITING_APPROVAL (if needed) → REMEDIATING → VERIFYING → RESOLVED`

Any failure in remediation or verification → `FAILED` with evidence. `PENDING` is the state before injection; the incident enters `INJECTED` only after `inject()` succeeds, and a failed injection ends `FAILED` (never `INJECTED`) with the captured pre-injection state as evidence. A kill-switch engage aborts a running incident at the next checkpoint → terminal `ABORTED` (cleanup runs, never RESOLVED). DENY or human rejection → terminal, no remediation. `escalate_to_human` → policy decision `ESCALATE` → terminal `ESCALATED` (reached only from `POLICY_DECIDED`), no remediation or verification; the incident records a timeline entry and a log warning only, and the injected fault is reverted by cleanup.

### Resolution report must contain

Incident ID, scenario, detected signals, likely cause, confidence, recommended action, policy decision + reason, executed remediation, verification checks + results, recovery duration, whether human intervention was required. The report also carries `pre_injection_state`, `injected_fault`, `cleanup` (`attempted`, `skipped_reason`, `succeeded`, `error`, `verified`, `verification_detail`), `abort_reason` and `detect_seconds`. `detect_seconds` = `INJECTED` entry → `DETECTED` entry (None if never detected). `recovery_seconds` = `DETECTED` entry → `RESOLVED` entry (excludes time before detection, includes any approval wait; None unless RESOLVED). A failed cleanup verification is recorded in `cleanup` and never changes the terminal state.

---

## 4. Code layout (known modules)

Known from imports in `chaos_pilot/core/pipeline.py`. **[verify]** with `ls -R chaos_pilot` for the full tree.

```
chaos_pilot/
  core/aws.py               # Shared boto3 client factory (timeouts + bounded retries); only place boto3.client is called
  core/pipeline.py          # Pipeline: inject → detect → investigate → policy → (approve) → remediate → verify
  incidents/models.py       # Incident, IncidentState, Decision, Diagnosis, PolicyDecision
  incidents/store.py        # IncidentStore
  investigator/base.py      # Investigator interface (rule-based default; Claude-backed optional, falls back to rules)
  policy/engine.py          # PolicyEngine (deterministic)
  remediation/engine.py     # RemediationEngine, RemediationRefused, executors
  scenarios/base.py         # Scenario base class
app/main.py                 # FastAPI app: JSON API, `/api/*` read-only dashboard endpoints, `GET /` page, `/reports/{id}` page, `/static` mount
app/incident_view.py        # Derived read-only fields (outcome, stage durations, approval deadline)
app/analytics.py            # Pure analytics over incident views (GET /api/analytics) + takeaways
chaos_pilot/demo/           # Demo mode: fake scenarios/executors + sequential seeder (no AWS)
app/catalog.py, reports.py  # Scenario wording; markdown report renderer
app/static/                 # Dashboard: index.html, report.html, css/, js/ (vanilla ES modules, no build step; Chart.js + Google Fonts from CDN)
terraform/                  # Test workload: VPC, ECS, ALB, security groups (task execution role only)
tests/                      # pytest, mocked AWS
decisions.txt               # Running log of design decisions and open questions
```

- `Pipeline.__init__` takes `store`, `investigator`, `policy`, `remediation`, `experiments_enabled`, `approval_timeout` (default 900 s). It uses `threading`.
- FastAPI app exposes endpoints to start scenarios and read incidents as JSON. Experiments are gated by `CHAOS_PILOT_EXPERIMENTS_ENABLED=true` plus target group / security group env vars.
- Original audit notes: `/home/nsurati/.claude/plans/model-sunny-gizmo.md`.

---

## 5. Commands

```bash
# Tests (the plain `pytest` crashes because the shell loads ROS plugins from /opt/ros)
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q

# Run the app and open the dashboard at http://127.0.0.1:8000/
# Add CHAOS_PILOT_DEMO_DATA=true for demo mode (fake incidents, mocked AWS; restart to switch)
# Default host only. NEVER use --host 0.0.0.0: the dashboard and kill switch have no auth.
.venv/bin/uvicorn app.main:app --reload

# Terraform checks (no apply)
cd terraform && terraform fmt -check && terraform validate

# Review before committing (owner does this)
git status && git diff --stat && git diff
```

- Use the repo's own `.venv` (it has pytest). The separate `.env/` virtualenv does not.
- New git worktrees have no `.venv`; create one and install requirements there.

---

## 6. Scenarios and policy

| Scenario | Injected problem | Demonstrates | Default policy | Status |
|---|---|---|---|---|
| **Bad Deployment** | Deploy a deliberately unhealthy task-definition revision | Correlate with new revision; roll back to known-good; verify | **AUTO** (bounded, reversible when prior revision verified) | Built; works end to end **in tests only** |
| **Config Change** | Remove a specific ALB-to-ECS security-group rule | Correlate with the change; restore known-good config after approval | **HUMAN_APPROVAL** | Built; restore executor exists (`restore_security_group_rule`, re-adds the exact rule captured before injection) |
| **Dependency Failure** | Make a simulated internal dependency unavailable | Tell dependency vs application failure; bounded fix or escalate | HUMAN_APPROVAL initially | Not built |
| **Cascading Failure** | One root fault → several downstream symptoms | Correlate symptoms into one incident, find root cause | Risk-dependent | Not built; may be replaced if not distinct from Dependency |
| ECS Task Crash (dev test) | Stop a task | Validate architecture end to end | n/a | Raw stop-task endpoint only; no incident/detection/verification |

**Bad Deployment caveat:** needs the `testapp` image and `health_check_path=/health`. With Terraform defaults (`nginx:alpine`, path `/`) the bad revision never looks unhealthy, so detection times out and the incident ends FAILED.

---

## 7. Current status (1 Nov 2026)

All 290 tests pass (mocked AWS). `terraform fmt -check` / `terraform validate` were clean at the 27 Oct audit and Terraform has not been touched since [verify].

| Component | Status |
|---|---|
| Incident model + lifecycle | Done |
| Deterministic detection | Done for Bad Deployment and Config Change |
| AI Investigator | Done (rule-based default, optional Claude with fallback; never run against real API) |
| Policy Engine | Done, including an incident-type → permitted-actions check and an `ESCALATE` decision that ends as `ESCALATED`; escalation has no notification or hand-off yet |
| Remediation Engine | Done with 2 executors (`rollback_to_previous_revision`, `restore_security_group_rule`), preconditions and structured SUCCEEDED/FAILED results; the allow-listed actions that had no executor were removed |
| Verification | Done, uses real AWS state |
| Notification | Missing (destination is an open question in `decisions.txt`) |
| Dashboard | Done (C1 + C2): sidebar, KPIs, scenario picker, incident page with flowchart, AI vs policy panels, approval card, report downloads and `/reports/{id}`, Analytics page fed by `GET /api/analytics`, opt-in demo mode (`CHAOS_PILOT_DEMO_DATA=true`, separate store, fake scenarios, no boto3). JS is syntax-checked with node but never run in a browser, so behaviour there is unverified. Unauthenticated: local use only |
| Kill switch | Done (in tests): in-memory runtime switch (`core/killswitch.py`) with `GET /kill-switch`, `POST /kill-switch/engage`, `POST /kill-switch/release`; blocks new starts, aborts a running incident at checkpoints, cleans up, ends `ABORTED`. Env gate unchanged. Endpoints unauthenticated; a restart releases it |
| AWS timeouts / retries | Done: every boto3 client comes from `chaos_pilot/core/aws.py` (connect 5 s, read 15 s, standard retries, `max_attempts` 3) |
| Cleanup / restore | Partial: runs only inside the app process (no standalone scripts). Result is a structured report field and the revert is read back afterwards (read-only describe, bounded retry); Bad Deployment checks the service revision only, not rollout completion or deregistration |
| Real AWS runs | None recorded |

### Change made after the audit (committed in `a62fd92`)

`pipeline.py` stores the remediation result on the incident and fails the incident when `result.succeeded` is false (no verification is run). `models.py` gained `remediation_result: Any = None`. `decisions.txt` notes that no Terraform role exists for the IAM permissions below.

### Known weaknesses

1. ~~**Policy doesn't check that the AI's action fits the incident type.**~~ **Fixed:** `SCENARIO_ACTIONS` in `policy/engine.py` maps each scenario to its permitted action; a mismatch is DENY.
2. ~~After REJECTED / FAILED, cleanup silently reverts the fault and nothing verifies the revert worked.~~ **Fixed:** `verify_cleanup()` reads the state back and the result is in the report. Remaining gap: nothing stops a new start while the last cleanup is unverified (see roadmap 7a).
3. **No IAM role for Chaos-Pilot itself**; app runs with local credentials. Code needs: `ecs:UpdateService`, `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:DescribeSecurityGroups` (plus whatever injectors use).
4. ~~**No explicit boto3 timeouts/retries** (60 s defaults).~~ **Fixed:** one factory, `chaos_pilot/core/aws.py`. Polling loops keep their own deadlines; the last check can overrun a deadline by one check's worst-case AWS calls.
5. ~~Incident enters `INJECTED` before injection runs; injected fault and cleanup result have no report fields.~~ **Fixed:** `PENDING` until `inject()` succeeds; `injected_fault`, `pre_injection_state` and `cleanup` are in the report.

---

## 8. Roadmap (in order)

Plan: few, larger sessions run **sequentially** to save usage. One branch per session is fine; `/clear` between sessions.

### Session A: policy and remediation safety (items 1-3 done)
1. Deterministic **incident type → permitted actions** map in the Policy Engine. Mismatched AI action → DENY (or substitute mapped default, whichever fits the design), reason on the timeline. Test the exact rollback-during-Config-Change case.
2. Real **`escalate_to_human` path** (escalated state or AWAITING_APPROVAL with no executable action), evidence preserved, transition validation intact.
3. **`restore_known_good_config` executor**: restore the ALB-to-ECS SG rule; allow-listed, precondition (known-good captured before injection), idempotent, bounded timeouts, structured result. Don't change the HUMAN_APPROVAL default.

### Session B: robustness (item 4 done; 5 and 6 still open)
4. **Done:** shared boto3 client factory (`botocore.config.Config`: explicit connect/read timeouts, bounded retries); all clients use it.
5. **Done:** runtime kill switch (endpoints + `threading.Event`, no DB) checked before inject, before remediation, before verification, inside every polling loop and the approval wait; aborts, cleans up, ends `ABORTED`. Engage sticks for the run in flight; release does not resume.
6. **Done:** accurate report: `PENDING` until injection succeeds (failed injection = `FAILED`), `pre_injection_state`, `injected_fault`, structured `cleanup` result with a read-only revert check, `detect_seconds`.

### Session C: dashboard
7. **Done (C1 + C2):** UI on the existing JSON API (no heavy framework unless already used): pick + trigger scenario, live lifecycle/timeline, **AI diagnosis shown separately from policy decision**, approve/reject buttons, final report with recovery duration and verification result, FAILED shown honestly.

7a. **Not implemented:** refuse new starts while the last incident's cleanup is unverified (`cleanup.verified == false`); needs an explicit acknowledge to clear.

### Manual (owner, no agent)
8. Run Bad Deployment and Config Change **against real AWS**, one at a time, record results (recovery time per scenario). Fix the testapp image / `/health` caveat first.

### Later / if budget allows
9. Terraform-managed **least-privilege operator role** scoped to the test workload (tags/ARNs), output its ARN.
10. **Standalone restore script** per scenario (no in-memory state), verifying recovery after cleanup; idempotent.
11. **Guard**: refuse to inject unless target is tagged as the test workload.
12. **ECS Task Crash** as a full scenario (optional).
13. **Dependency Failure** + `restore_dependency` executor; then decide whether Cascading Failure is distinct enough to build.
14. **Notification**: timeline entry + log line for MVP; optional SNS email.
15. README: architecture, scenario/policy table, safeguards, cost estimate, teardown.

---

## 9. Definition of MVP complete

- [ ] Each supported scenario can be selected and triggered from the dashboard
- [ ] Detection is deterministic; AI diagnoses but does not authorize
- [ ] Policy independently chooses AUTO or HUMAN_APPROVAL under explicit rules (incl. action-fits-incident check)
- [ ] Remediation executes only authorized, allow-listed actions
- [ ] Recovery verified with real health signals and bounded timeouts
- [ ] Dashboard shows full lifecycle and an accurate final report
- [ ] Every scenario has repeatable tests and a safe restore/cleanup procedure
- [ ] At least Bad Deployment and Config Change run and recorded against real AWS

---

## 10. Working method

- One component or small batch per session. Prompt narrowly.
- Owner reviews every diff: changed files, AWS calls, failure paths, timeouts, permissions.
- Mocked tests first, then one controlled end-to-end real-AWS run at a time. Never two experiments on the same workload at once; never two `terraform apply` from different branches.
- Commit message style: `feat(scope): …`, `fix(scope): …`, `test(…)`, `docs(…)`, `chore(…)`.
- Before moving on, the owner must be able to explain what the component does and why it is safe.

## 11. Open questions

- Notification destination (see `decisions.txt`).
- Keep Cascading Failure, or replace it if it overlaps Dependency Failure?
- Fargate vs EC2 launch type and exact detection signals: confirm in Terraform/code when documenting.
