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

Detection, the AI investigator, remediation, verification and the dashboard are not built yet.

## Repository layout

```
app/main.py                          FastAPI entry point
chaos_pilot/core/engine.py           Chaos engine and kill switch
chaos_pilot/incidents/               Incident model, lifecycle and in-memory store
chaos_pilot/policy/                  Deterministic policy engine
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
- The service currently runs `nginx:alpine` as a placeholder workload.

Plan files and state are never committed (see `.gitignore`).

Tear everything down when you are done to avoid charges:

```bash
cd terraform
terraform destroy
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

Endpoints:

- `GET /health` reports API health.
- `GET /status` reports engine status and whether experiments are enabled.
- `POST /experiments/task-failure?task_id=<id>` stops one task of the test service. It returns `403` if experiments are disabled or the task is not part of the allowed service.

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
