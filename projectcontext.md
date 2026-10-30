# Chaos-Pilot: Project Context

> Read this file at the start of every session before touching code.
> Status last updated: 27 Oct 2026 (code audit). Anything below marked **[verify]** may have changed since; check the repo before relying on it.

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
| Policy Engine | Maps the proposed action to AUTO / HUMAN_APPROVAL / DENY with a reason | Deterministic, unit-testable |
| Approval step | Pauses for a human decision, with timeout | Reject or timeout = no execution |
| Remediation Engine | Runs the authorized action via an executor | Allow-list only; makes no decisions |
| Verification | Running == desired, tasks healthy, ALB targets healthy, correct revision | Bounded polling; timeout = FAILED |
| Notification / report | Records outcome and report | Must be truthful |

### Incident lifecycle

`INJECTED → DETECTED → INVESTIGATING → POLICY_DECIDED → AWAITING_APPROVAL (if needed) → REMEDIATING → VERIFYING → RESOLVED`

Any failure in remediation or verification → `FAILED` with evidence. DENY or human rejection → terminal, no remediation.

### Resolution report must contain

Incident ID, scenario, detected signals, likely cause, confidence, recommended action, policy decision + reason, executed remediation, verification checks + results, recovery duration, whether human intervention was required. (Planned additions: injected fault details, cleanup result.)

---

## 4. Code layout (known modules)

Known from imports in `chaos_pilot/core/pipeline.py`. **[verify]** with `ls -R chaos_pilot` for the full tree.

```
chaos_pilot/
  core/pipeline.py          # Pipeline: inject → detect → investigate → policy → (approve) → remediate → verify
  incidents/models.py       # Incident, IncidentState, Decision, Diagnosis, PolicyDecision
  incidents/store.py        # IncidentStore
  investigator/base.py      # Investigator interface (rule-based default; Claude-backed optional, falls back to rules)
  policy/engine.py          # PolicyEngine (deterministic)
  remediation/engine.py     # RemediationEngine, RemediationRefused, executors
  scenarios/base.py         # Scenario base class
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
| **Config Change** | Remove a specific ALB-to-ECS security-group rule | Correlate with the change; restore known-good config after approval | **HUMAN_APPROVAL** | Built; **no `restore_known_good_config` executor yet** [verify] |
| **Dependency Failure** | Make a simulated internal dependency unavailable | Tell dependency vs application failure; bounded fix or escalate | HUMAN_APPROVAL initially | Not built |
| **Cascading Failure** | One root fault → several downstream symptoms | Correlate symptoms into one incident, find root cause | Risk-dependent | Not built; may be replaced if not distinct from Dependency |
| ECS Task Crash (dev test) | Stop a task | Validate architecture end to end | n/a | Raw stop-task endpoint only; no incident/detection/verification |

**Bad Deployment caveat:** needs the `testapp` image and `health_check_path=/health`. With Terraform defaults (`nginx:alpine`, path `/`) the bad revision never looks unhealthy, so detection times out and the incident ends FAILED.

---

## 7. Current status (audit, 27 Oct 2026)

All 66 tests passed; `terraform fmt -check` and `terraform validate` clean.

| Component | Status |
|---|---|
| Incident model + lifecycle | Done |
| Deterministic detection | Done for Bad Deployment and Config Change |
| AI Investigator | Done (rule-based default, optional Claude with fallback; never run against real API) |
| Policy Engine | Done, but `escalate_to_human` ends as REJECTED (no real escalation path) |
| Remediation Engine | Done with 2 executors; `restore_dependency` and `restore_known_good_config` allowed by policy but have no executor |
| Verification | Done, uses real AWS state |
| Notification | Missing (destination is an open question in `decisions.txt`) |
| Dashboard | Missing; API returns JSON only |
| Kill switch | Partial: env var only, can't be flipped at runtime or abort a running experiment |
| Cleanup / restore | Partial: only inside app process; no standalone scripts; revert not verified |
| Real AWS runs | None recorded |

### Change made after the audit [verify whether committed]

`pipeline.py` now stores the remediation result on the incident and fails the incident when `result.succeeded` is false (no verification is run). `models.py` gained `remediation_result: Any = None`. That session also noted in `decisions.txt` that no Terraform role exists for the IAM permissions below, and that boto3 clients still have no explicit timeouts.

### Known weaknesses

1. **Policy doesn't check that the AI's action fits the incident type.** If the AI recommends `rollback` with confidence ≥ 0.7 during a Config Change, policy auto-approves it. It only fails safely by accident (no known-good revision → error → FAILED).
2. After REJECTED / FAILED, cleanup silently reverts the fault and **nothing verifies the revert worked**.
3. **No IAM role for Chaos-Pilot itself**; app runs with local credentials. Code needs: `ecs:UpdateService`, `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`, `ec2:AuthorizeSecurityGroupIngress`, `ec2:DescribeSecurityGroups` (plus whatever injectors use).
4. **No explicit boto3 timeouts/retries** (60 s defaults).
5. Incident enters `INJECTED` **before** injection runs; injected fault and cleanup result have no report fields.

---

## 8. Roadmap (in order)

Plan: few, larger sessions run **sequentially** to save usage. One branch per session is fine; `/clear` between sessions.

### Session A: policy and remediation safety
1. Deterministic **incident type → permitted actions** map in the Policy Engine. Mismatched AI action → DENY (or substitute mapped default, whichever fits the design), reason on the timeline. Test the exact rollback-during-Config-Change case.
2. Real **`escalate_to_human` path** (escalated state or AWAITING_APPROVAL with no executable action), evidence preserved, transition validation intact.
3. **`restore_known_good_config` executor**: restore the ALB-to-ECS SG rule; allow-listed, precondition (known-good captured before injection), idempotent, bounded timeouts, structured result. Don't change the HUMAN_APPROVAL default.

### Session B: robustness
4. **Shared boto3 client factory** (`botocore.config.Config`: explicit connect/read timeouts, bounded retries); switch all clients to it.
5. **Runtime kill switch** (endpoint + flag/`threading.Event`, no DB) checked before inject, before remediation, and inside every polling loop; aborts, cleans up, marks FAILED/ABORTED.
6. **Accurate report**: pre-injection state (or failed injection = FAILED), injected-fault field, cleanup-result field.

### Session C: dashboard
7. Simple UI on the existing JSON API (no heavy framework unless already used): pick + trigger scenario, live lifecycle/timeline, **AI diagnosis shown separately from policy decision**, approve/reject buttons, final report with recovery duration and verification result, FAILED shown honestly.

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
