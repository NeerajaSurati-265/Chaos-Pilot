from typing import Protocol

from chaos_pilot.incidents.models import Diagnosis, Signal


class Investigator(Protocol):
    """Turns sanitized evidence into a diagnosis. It never authorizes actions."""

    def investigate(self, scenario: str, signals: list[Signal]) -> Diagnosis: ...
