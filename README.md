# Chaos-Pilot

Chaos-Pilot is an AWS incident investigation and controlled-recovery platform. It injects selected failures into an isolated test workload, detects incidents from deterministic signals, uses an AI investigator to diagnose the likely cause, lets a deterministic policy engine decide whether remediation runs automatically or needs human approval, then verifies recovery and produces an incident report.

Chaos-Pilot does not replace native AWS health checks, ECS task replacement or ECS deployment rollback. Its value is investigation, correlation, policy-controlled orchestration, recovery verification and an explainable incident timeline.

```
SELECT SCENARIO → INJECT FAILURE → DETECT → AI INVESTIGATOR → POLICY ENGINE
  → REMEDIATE / REQUEST APPROVAL → VERIFY RECOVERY → NOTIFY
```

## Status

Early MVP. What exists today:

- Terraform for the test environment: VPC across two availability zones, public and private subnets, an ALB, an ECS Fargate service (2 tasks) and a NAT instance.
- A FastAPI app with a task-failure experiment that stops one ECS task of the test service.
- The incident model and lifecycle (`chaos_pilot/incidents`): every step from INJECTED to RESOLVED is recorded on a timeline, steps can't be skipped, and failures are recorded as FAILED rather than hidden.
- The deterministic policy engine (`chaos_pilot/policy`): decides AUTO, HUMAN_APPROVAL or DENY from an allow-list of actions, their reversibility and blast radius, the AI's confidence and the kill switch.
- The pipeline (`chaos_pilot/core/pipeline.py`) that runs a scenario end to end, including waiting for human approval with a timeout.
- The remediation engine (`chaos_pilot/remediation`): executes only what the policy engine authorized, using the state the scenario recorded at injection. It refuses denied, unapproved, non-allow-listed or unknown actions without running anything, and a refusal ends the incident as FAILED.
- The **Bad Deployment** scenario (`chaos_pilot/scenarios/bad_deployment.py`).
- The **Configuration Change** scenario (`chaos_pilot/scenarios/config_change.py`).
- A rule-based investigator (`chaos_pilot/investigator`) standing in for the AI investigator. It returns the same diagnosis shape a model would, so a model-backed one can replace it later.

Dependency Failure, Cascading Failure and the dashboard are not built yet.

## Repository layout

```
app/main.py                          FastAPI entry point
chaos_pilot/core/engine.py           Chaos engine and kill switch
chaos_pilot/incidents/               Incident model, lifecycle and in-memory store
chaos_pilot/policy/                  Deterministic policy engine
chaos_pilot/remediation/             Remediation engine and AWS action executors
chaos_pilot/investigator/            Investigator interface and rule-based stand-in
chaos_pilot/scenarios/               Failure scenarios (inject, detect, verify, cleanup)
testapp/                             Small workload used as the experiment target
chaos_pilot/experiments/             Failure experiments
terraform/                           Test environment infrastructure
tests/                               Unit tests (AWS calls are stubbed)
```

## Infrastructure

```bash
cd terraform
terraform init
terraform plan -out chaos-pilot.tfplan
terraform apply chaos-pilot.tfplan
```

Design notes:

- **NAT instance instead of NAT Gateway.** A `t3.micro` NAT instance keeps the test environment cheap. It is a single point of failure, which is acceptable for a test workload.
- **ECS deployment circuit breaker is off on purpose.** Chaos-Pilot is the component that detects a bad deployment and decides on rollback, so ECS's automatic rollback is left disabled for the Bad Deployment scenario.
- The service runs `nginx:alpine` until you point `app_image` at the test app (see below).
- Terraform ignores later changes to the service's task definition, because Chaos-Pilot registers bad revisions and rolls back during experiments.

Plan files and state are never committed (see `.gitignore`).

Tear everything down when you are done to avoid charges:

```bash
cd terraform
terraform destroy
```

## Test app

`testapp/` is a tiny HTTP server with a `/health` endpoint. With `HEALTHY=false` its health check returns 500, which is how the Bad Deployment scenario creates a bad revision.

```bash
cd terraform
REPO=$(terraform output -raw test_app_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build -t "$REPO:1" ../testapp
docker push "$REPO:1"
terraform apply -var "app_image=$REPO:1" -var "health_check_path=/health"
```

## Running the API locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

The app uses your normal AWS credentials (`AWS_PROFILE` or environment variables).

| Variable | Default | Purpose |
|---|---|---|
| `CHAOS_PILOT_EXPERIMENTS_ENABLED` | `false` | Kill switch. Experiments are refused unless this is `true`. |
| `CHAOS_PILOT_CLUSTER` | `chaos-pilot-cluster` | ECS cluster experiments may target. |
| `CHAOS_PILOT_SERVICE` | `chaos-pilot-service` | Only tasks of this service may be stopped. |
| `CHAOS_PILOT_TARGET_GROUP_ARN` | none | ALB target group used for health checks. `terraform output -raw target_group_arn` |
| `CHAOS_PILOT_ECS_SECURITY_GROUP_ID` | none | ECS tasks' security group, for Configuration Change. `terraform output -raw ecs_security_group_id` |
| `CHAOS_PILOT_ALB_SECURITY_GROUP_ID` | none | ALB's security group, for Configuration Change. `terraform output -raw alb_security_group_id` |

Endpoints:

- `GET /health` reports API health.
- `GET /status` reports engine status and whether experiments are enabled.
- `POST /experiments/task-failure?task_id=<id>` stops one task of the test service. It returns `403` if experiments are disabled or the task is not part of the allowed service.
- `POST /scenarios/bad-deployment` or `POST /scenarios/config-change` starts that scenario in the background and returns the incident ID. Only one scenario runs at a time.
- `GET /incidents` and `GET /incidents/<id>` return incident reports: signals, diagnosis, policy decision and reasons, remediation, verification checks, recovery time and timeline.
- `POST /incidents/<id>/approve` and `POST /incidents/<id>/reject` answer an incident waiting for human approval.

## Bad Deployment scenario

1. **Inject:** records the service's current task definition as known-good, registers a copy with `HEALTHY=false`, and deploys it.
2. **Detect:** polls ECS and the ALB until the new revision shows failed tasks, unhealthy targets or a failed rollout (default timeout 10 minutes).
3. **Investigate:** the investigator links the failure to the revision change and recommends `rollback_to_previous_revision`.
4. **Policy:** rollback is allow-listed, reversible and low blast radius, so it runs automatically when confidence is high enough; otherwise it waits for approval.
5. **Remediate:** the remediation engine updates the service back to the known-good task definition.
6. **Verify:** passes only when the service runs the known-good revision, the rollout is complete, running tasks match the desired count and every ALB target is healthy.
7. **Cleanup:** always makes sure the service is back on the known-good revision and deregisters the bad one, even if a step failed.

## Configuration Change scenario

1. **Inject:** checks the rule letting the ALB reach the tasks on port 80 exists (it refuses to start otherwise), then removes it.
2. **Detect:** polls until ALB targets turn unhealthy, and records whether the rule is present.
3. **Investigate:** with no new deployment and the rule missing, the investigator recommends `restore_security_group_rule`.
4. **Policy:** security-group changes always need **human approval**. The incident waits in AWAITING_APPROVAL until `POST /incidents/<id>/approve` or `/reject`, or fails after the approval timeout (15 minutes).
5. **Remediate:** the remediation engine re-adds the exact rule that was removed.
6. **Verify:** passes only when the rule is back, running tasks match the desired count and every ALB target is healthy.
7. **Cleanup:** always re-adds the rule if it is still missing, including after a rejection or failure.

## AWS permissions

The AWS identity running the API needs `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition`, `ecs:DeregisterTaskDefinition`, `ecs:UpdateService`, `ecs:DescribeTasks`, `ecs:StopTask`, `elasticloadbalancing:DescribeTargetHealth`, `iam:PassRole` on the task execution role, and for Configuration Change `ec2:DescribeSecurityGroups`, `ec2:RevokeSecurityGroupIngress` and `ec2:AuthorizeSecurityGroupIngress` on the ECS security group.

## Tests

```bash
pytest
```

AWS calls are stubbed with `botocore.stub.Stubber`, so tests need no AWS account.

## Safety rules

- Never point Chaos-Pilot at production.
- Experiments only touch the configured test cluster and service.
- Experiments are disabled unless explicitly enabled.
- Every change must be reversible and have a cleanup step.
