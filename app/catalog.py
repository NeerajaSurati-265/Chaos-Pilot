"""Plain-English scenario descriptions for the dashboard.

Only the wording lives here. The default policy is derived from the policy engine's own
tables so it cannot drift, and run counts come from the incident store.
"""
from chaos_pilot.incidents.models import Incident, IncidentState
from chaos_pilot.policy.engine import DEFAULT_RULES, SCENARIO_ACTIONS

CATALOG = [
    {
        "key": "bad-deployment",
        "incident_scenario": "bad_deployment",
        "name": "Bad Deployment",
        "description": "A faulty release goes live and the service stops being healthy.",
        "we_will": "Deploy a deliberately unhealthy version of the test app to the isolated test service.",
        "symptom": "New tasks fail their health checks and the load balancer sees no healthy targets.",
        "fix": (
            "Notice the new version, let the AI diagnose it, roll back to the previous "
            "known-good version, then double-check that tasks and targets are healthy again."
        ),
    },
    {
        "key": "config-change",
        "incident_scenario": "config_change",
        "name": "Config Change",
        "description": "A network rule is removed, so the load balancer can no longer reach the app.",
        "we_will": "Remove the security-group rule that lets the load balancer reach the test service.",
        "symptom": "Targets turn unhealthy although nothing was deployed: health checks cannot reach the tasks.",
        "fix": (
            "Notice the missing rule, let the AI diagnose it, ask a human to approve, "
            "re-add the exact rule captured before the change, then verify recovery."
        ),
    },
    {
        "key": "dependency-failure",
        "incident_scenario": None,
        "name": "Dependency Failure",
        "description": "A service the app depends on becomes unavailable.",
    },
    {
        "key": "cascading-failure",
        "incident_scenario": None,
        "name": "Cascading Failure",
        "description": "One root fault causes several downstream symptoms.",
    },
]


def default_policy(incident_scenario: str) -> str | None:
    """AUTO or HUMAN_APPROVAL, from the allow-list rules. AI confidence can still force approval."""
    actions = SCENARIO_ACTIONS.get(incident_scenario)
    if not actions:
        return None
    rules = [DEFAULT_RULES[a] for a in actions if a in DEFAULT_RULES]
    if not rules:
        return None
    return "AUTO" if all(r.auto_allowed and r.reversible for r in rules) else "HUMAN_APPROVAL"


def build_scenarios(incidents: list[Incident], runnable_keys: set[str]) -> list[dict]:
    cards = []
    for entry in CATALOG:
        scenario = entry["incident_scenario"]
        if scenario is None:
            cards.append({**entry, "runnable": False, "policy": None, "runs": None,
                          "recovered": None, "avg_recovery_seconds": None})
            continue
        runs = [i for i in incidents if i.scenario == scenario]
        times = [
            i.recovery_seconds for i in runs
            if i.state is IncidentState.RESOLVED and i.recovery_seconds is not None
        ]
        cards.append({
            **entry,
            "runnable": entry["key"] in runnable_keys,
            "policy": default_policy(scenario),
            "runs": len(runs),
            "recovered": len(times),
            "avg_recovery_seconds": sum(times) / len(times) if times else None,
        })
    return cards
