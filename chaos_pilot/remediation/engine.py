from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Protocol

from chaos_pilot.incidents.models import Decision, PolicyDecision, utc_now


@dataclass(frozen=True)
class RemediationContext:
    """The "before" state a scenario recorded at injection, so a fix can put it back."""
    cluster: str | None = None
    service: str | None = None
    known_good_task_definition: str | None = None
    security_group_id: str | None = None
    source_security_group_id: str | None = None
    port: int | None = None
    # Recorded at injection: the known-good revision was fully rolled out before the change.
    known_good_was_healthy: bool = False
    # Recorded at injection: the ALB-to-ECS rule existed and was captured before it was removed.
    known_good_rule_captured: bool = False
    rule_description: str | None = None


class RemediationStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RemediationResult:
    """What the engine did. SUCCEEDED means the AWS change was applied, not that the
    system recovered; Verification decides that."""
    action: str
    parameters: dict
    started_at: datetime
    finished_at: datetime
    status: RemediationStatus
    detail: str = ""
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is RemediationStatus.SUCCEEDED

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "parameters": self.parameters,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "status": self.status.value,
            "detail": self.detail,
            "error": self.error,
        }


class RemediationRefused(Exception):
    """Nothing ran: policy denied it, approval is missing, or the action is not allowed."""


class PreconditionFailed(Exception):
    """The environment is not in a state where the action is safe to run."""


class ActionExecutor(Protocol):
    """Runs one remediation action against AWS."""

    action: str

    def check_preconditions(self, context: RemediationContext) -> None:
        """Raise PreconditionFailed if running the action now would be unsafe."""
        ...

    def execute(self, context: RemediationContext) -> str: ...


class RemediationEngine:
    """Executes only what the policy engine authorized. It decides nothing itself.

    Anything it isn't sure it may run is refused (RemediationRefused) before any executor
    is touched. Once an action is permitted, preconditions are checked and then the action
    runs; any error there comes back as a FAILED result, never as success.
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

        parameters = {k: v for k, v in asdict(context).items() if v is not None}
        started_at = utc_now()
        try:
            executor.check_preconditions(context)
            detail = executor.execute(context)
        except PreconditionFailed as failure:
            return self._failed(action, parameters, started_at, f"Precondition failed: {failure}")
        except Exception as error:  # AWS errors included: record FAILED, never success.
            return self._failed(action, parameters, started_at, f"{type(error).__name__}: {error}")

        return RemediationResult(
            action, parameters, started_at, utc_now(), RemediationStatus.SUCCEEDED, detail
        )

    @staticmethod
    def _failed(action, parameters, started_at, error) -> RemediationResult:
        return RemediationResult(
            action, parameters, started_at, utc_now(), RemediationStatus.FAILED, error=error
        )
