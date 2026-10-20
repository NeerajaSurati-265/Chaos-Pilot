from chaos_pilot.incidents.models import Diagnosis, Signal


class RuleBasedInvestigator:
    """Stand-in for the AI investigator so the pipeline runs without an API key.

    It reads the same evidence a model would and returns the same shape of
    answer, so a model-backed investigator can replace it without changes
    elsewhere.
    """

    def investigate(self, scenario: str, signals: list[Signal]) -> Diagnosis:
        by_name = {signal.name: signal.value for signal in signals}

        new_revision_unhealthy = (
            by_name.get("revision_changed") is True
            and by_name.get("new_revision_healthy_tasks", 0) == 0
        )
        previous_revision_known = bool(by_name.get("previous_task_definition"))

        if new_revision_unhealthy and previous_revision_known:
            return Diagnosis(
                likely_cause=(
                    "The latest deployment "
                    f"({by_name.get('current_task_definition')}) has no healthy "
                    "tasks, while the previous revision was healthy."
                ),
                confidence=0.9,
                explanation=(
                    "Health failures started right after a task definition "
                    "change, and no task from the new revision passed health checks."
                ),
                recommended_action="rollback_to_previous_revision",
            )

        if (
            by_name.get("alb_ingress_rule_present") is False
            and by_name.get("unhealthy_targets", 0) > 0
        ):
            return Diagnosis(
                likely_cause=(
                    "The security group "
                    f"{by_name.get('security_group_id')} no longer allows traffic "
                    "from the load balancer, so health checks can't reach the tasks."
                ),
                confidence=0.85,
                explanation=(
                    "Targets turned unhealthy with no new deployment, and the "
                    "ALB-to-ECS ingress rule is missing."
                ),
                recommended_action="restore_security_group_rule",
            )

        return Diagnosis(
            likely_cause="Unclear from the available evidence.",
            confidence=0.3,
            explanation="Signals do not point to a single known cause.",
            recommended_action="escalate_to_human",
        )
