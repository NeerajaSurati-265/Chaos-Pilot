from threading import Lock

from chaos_pilot.incidents.models import Incident


class IncidentStore:
    """In-memory store. The MVP scope guard rules out a database for now."""

    def __init__(self):
        self._incidents: dict[str, Incident] = {}
        self._lock = Lock()

    def add(self, incident: Incident) -> Incident:
        with self._lock:
            self._incidents[incident.id] = incident
        return incident

    def get(self, incident_id: str) -> Incident | None:
        return self._incidents.get(incident_id)

    def list(self) -> list[Incident]:
        return list(self._incidents.values())
