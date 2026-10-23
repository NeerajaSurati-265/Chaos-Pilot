from dataclasses import dataclass
from typing import Protocol

from chaos_pilot.incidents.models import Signal, VerificationResult
from chaos_pilot.remediation.engine import RemediationContext


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

    def inject(self) -> str: ...

    def detect(self) -> Detection: ...

    def remediation_context(self) -> RemediationContext: ...

    def verify(self) -> VerificationResult: ...

    def cleanup(self) -> None: ...
