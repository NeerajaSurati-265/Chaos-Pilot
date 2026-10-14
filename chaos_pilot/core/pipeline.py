import threading

from chaos_pilot.incidents.models import Decision, Incident, IncidentState
from chaos_pilot.incidents.store import IncidentStore
from chaos_pilot.investigator.base import Investigator
from chaos_pilot.policy.engine import PolicyEngine
from chaos_pilot.scenarios.base import Scenario

S = IncidentState


class Pipeline:
    """Runs one scenario end to end:
    inject → detect → investigate → policy → (approve) → remediate → verify.
    """

    def __init__(
        self,
        store: IncidentStore,
        investigator: Investigator,
        policy: PolicyEngine,
        experiments_enabled,
        approval_timeout: float = 900,
    ):
        self.store = store
        self.investigator = investigator
        self.policy = policy
        self.experiments_enabled = experiments_enabled
        self.approval_timeout = approval_timeout
        self._approvals: dict[str, threading.Event] = {}
        self._approved: dict[str, bool] = {}

    def start(self, scenario: Scenario) -> Incident:
        incident = self.store.add(Incident(scenario=scenario.name))
        self._approvals[incident.id] = threading.Event()
        return incident

    def answer_approval(self, incident_id: str, approved: bool) -> bool:
        incident = self.store.get(incident_id)
        if incident is None or incident.state is not S.AWAITING_APPROVAL:
            return False
        self._approved[incident_id] = approved
        self._approvals[incident_id].set()
        return True

    def run(self, incident: Incident, scenario: Scenario) -> Incident:
        try:
            self._run(incident, scenario)
        except Exception as error:  # Never report success on an unexpected error.
            if incident.state not in (S.RESOLVED, S.REJECTED, S.FAILED):
                incident.fail(f"{type(error).__name__}: {error}")
        finally:
            try:
                scenario.cleanup()
            except Exception as error:
                incident.add_note(f"Cleanup failed: {error}")
        return incident

    def _run(self, incident: Incident, scenario: Scenario):
        if not self.experiments_enabled():
            incident.fail("Experiments are disabled by the kill switch")
            return

        incident.add_note(scenario.inject())

        detection = scenario.detect()
        incident.signals = detection.signals
        if not detection.incident_found:
            incident.fail("No incident detected within the detection timeout")
            return
        incident.transition(S.DETECTED, f"{len(detection.signals)} signals collected")

        incident.transition(S.INVESTIGATING)
        incident.diagnosis = self.investigator.investigate(scenario.name, incident.signals)

        decision = self.policy.decide(incident.diagnosis, self.experiments_enabled())
        incident.policy_decision = decision
        incident.transition(S.POLICY_DECIDED, f"{decision.decision.value}: {decision.action}")

        if decision.decision is Decision.DENY:
            incident.transition(S.REJECTED, "; ".join(decision.reasons))
            return

        if decision.decision is Decision.HUMAN_APPROVAL:
            incident.transition(S.AWAITING_APPROVAL, "; ".join(decision.reasons))
            incident.human_intervention = True
            if not self._approvals[incident.id].wait(self.approval_timeout):
                incident.fail("Approval timed out")
                return
            if not self._approved.get(incident.id):
                incident.transition(S.REJECTED, "Rejected by a human")
                return

        incident.transition(S.REMEDIATING)
        incident.remediation = scenario.remediate(decision.action)

        incident.transition(S.VERIFYING, incident.remediation)
        incident.verification = scenario.verify()
        if not incident.verification.passed:
            failed = [c.name for c in incident.verification.checks if not c.passed]
            incident.fail(f"Verification failed: {', '.join(failed) or 'no checks ran'}")
            return

        incident.transition(S.RESOLVED, "All verification checks passed")
