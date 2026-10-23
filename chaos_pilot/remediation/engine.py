from dataclasses import dataclass
from typing import Iterable, Protocol

from chaos_pilot.incidents.models import Decision, PolicyDecision


@dataclass(frozen=True)
class RemediationContext:
    """The "before" state a scenario recorded at injection, so a fix can put it back."""
    cluster: str | None = None
    service: str | None = None
    known_good_task_definition: str | None = None
    security_group_id: str | None = None
    source_security_group_id: str | None = None
    port: int | None = None


@dataclass(frozen=True)
class RemediationResult:
    action: str
    executed: bool
    detail: str


class RemediationRefused(Exception):
    pass


class ActionExecutor(Protocol):
    """Runs one remediation action against AWS."""

    action: str

    def execute(self, context: RemediationContext) -> str: ...


class RemediationEngine:
    """Executes only what the policy engine authorized. It decides nothing itself.

    Anything it isn't sure it may run is refused before any executor is touched.
    """

    def __init__(self, executors: Iterable[ActionExecutor], allowed_actions: Iterable[str]):
        self.executors: dict[str, ActionExecutor] = {}
        for executor in executors:
            if executor.action in self.executors:
                raise ValueError(f"Two executors registered for '{executor.action}'")
            self.executors[executor.action] = executor
        self.allowed_actions = frozenset(allowed_actions)

    def execute(
        self, decision: PolicyDecision, approved: bool, context: RemediationContext
    ) -> RemediationResult:
        action = decision.action

        if decision.decision is Decision.DENY:
            raise RemediationRefused(f"Policy denied '{action}'")
        if decision.decision is Decision.HUMAN_APPROVAL and not approved:
            raise RemediationRefused(f"'{action}' needs human approval, none was given")
        if decision.decision not in (Decision.AUTO, Decision.HUMAN_APPROVAL):
            raise RemediationRefused(f"Unknown policy decision {decision.decision!r}")
        if action not in self.allowed_actions:
            raise RemediationRefused(f"'{action}' is not an allow-listed action")
        executor = self.executors.get(action)
        if executor is None:
            raise RemediationRefused(f"No executor is registered for '{action}'")

        return RemediationResult(action, True, executor.execute(context))
