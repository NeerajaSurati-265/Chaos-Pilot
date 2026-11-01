from dataclasses import dataclass
from typing import Protocol

from chaos_pilot.incidents.models import CheckResult, Signal, VerificationResult
from chaos_pilot.remediation.engine import RemediationContext


@dataclass(frozen=True)
class Injection:
    summary: str
    injected_fault: dict


@dataclass(frozen=True)
class Detection:
    incident_found: bool
    signals: list[Signal]


class Scenario(Protocol):
    """One failure scenario: how to inject it, see it and prove it was fixed.

    The fix itself is run by the remediation engine, from the state the
    scenario recorded at injection (remediation_context).
    """

    name: str

    def inject(self) -> Injection: ...

    def pre_injection_state(self) -> dict:
        """The known-good state captured before the fault ({} if nothing was captured yet)."""
        ...

    def detect(self) -> Detection: ...

    def remediation_context(self) -> RemediationContext: ...

    def verify(self) -> VerificationResult: ...

    def cleanup(self) -> str | None:
        """Revert the fault. Returns a reason if it deliberately did nothing, else None."""
        ...

    def verify_cleanup(self) -> CheckResult:
        """Read-only check that the known-good state is back after cleanup()."""
        ...
