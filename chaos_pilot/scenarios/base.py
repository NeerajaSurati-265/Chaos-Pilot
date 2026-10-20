from dataclasses import dataclass
from typing import Protocol

from chaos_pilot.incidents.models import Signal, VerificationResult


@dataclass(frozen=True)
class Detection:
    incident_found: bool
    signals: list[Signal]


class Scenario(Protocol):
    """One failure scenario: how to inject it, see it, fix it and prove the fix."""

    name: str

    def inject(self) -> str: ...

    def detect(self) -> Detection: ...

    def remediate(self, action: str) -> str: ...

    def verify(self) -> VerificationResult: ...

    def cleanup(self) -> None: ...
