import logging
import threading

from chaos_pilot.core.killswitch import Aborted, KillSwitch, RunGuard
from chaos_pilot.incidents.models import (
    TERMINAL_STATES,
    CleanupResult,
    Decision,
    Incident,
    IncidentState,
)
from chaos_pilot.incidents.store import IncidentStore
from chaos_pilot.investigator.base import Investigator
from chaos_pilot.policy.engine import PolicyEngine
from chaos_pilot.remediation.engine import RemediationEngine, RemediationRefused
from chaos_pilot.scenarios.base import Scenario

S = IncidentState
logger = logging.getLogger(__name__)


class Pipeline:
    """Runs one scenario end to end:
    inject → detect → investigate → policy → (approve) → remediate → verify.
    """

    def __init__(
        self,
        store: IncidentStore,
        investigator: Investigator,
        policy: PolicyEngine,
        remediation: RemediationEngine,
        experiments_enabled,
        approval_timeout: float = 900,
        kill_switch: KillSwitch | None = None,
    ):
        self.store = store
        self.investigator = investigator
        self.policy = policy
        self.remediation = remediation
        self.experiments_enabled = experiments_enabled
        self.approval_timeout = approval_timeout
        self._approvals: dict[str, threading.Event] = {}
        self._approved: dict[str, bool] = {}
        self.kill_switch = kill_switch or KillSwitch()
        self.kill_switch.on_engage(self._wake_approvals)
        self._guards: dict[str, RunGuard] = {}
        self._inject_started: set[str] = set()

    def start(self, scenario: Scenario) -> Incident:
        incident = self.store.add(Incident(scenario=scenario.name))
        self._approvals[incident.id] = threading.Event()
        # Fixed here, so an engage that is released before the run's next checkpoint still aborts it.
        self._guards[incident.id] = self.kill_switch.guard()
        return incident

    def _wake_approvals(self):
        for event in list(self._approvals.values()):
            event.set()

    def answer_approval(self, incident_id: str, approved: bool) -> bool:
        incident = self.store.get(incident_id)
        if incident is None or incident.state is not S.AWAITING_APPROVAL:
            return False
        self._approved[incident_id] = approved
        self._approvals[incident_id].set()
        return True

    def run(self, incident: Incident, scenario: Scenario) -> Incident:
        guard = self._guards.pop(incident.id, None) or self.kill_switch.guard()
        if hasattr(scenario, "abort_guard"):
            scenario.abort_guard = guard
        try:
            self._run(incident, scenario, guard)
        except Aborted as abort:
            if incident.state not in TERMINAL_STATES:
                incident.abort(self._abort_note(incident, abort))
        except Exception as error:  # Never report success on an unexpected error.
            if incident.state not in TERMINAL_STATES:
                incident.fail(f"{type(error).__name__}: {error}")
        finally:
            self._cleanup(incident, scenario)
        return incident

    @staticmethod
    def _abort_note(incident: Incident, abort: Aborted) -> str:
        note = f"Aborted by kill switch at {abort.where} (state {incident.state.value}): {abort.reason}"
        if incident.remediation_result is not None:
            note += (
                f"; remediation already executed ({incident.remediation or 'no detail'}), "
                "recovery NOT verified"
            )
        return note

    def _cleanup(self, incident: Incident, scenario: Scenario):
        injecting = incident.id in self._inject_started
        self._inject_started.discard(incident.id)
        if injecting:
            result = self._revert_and_check(scenario)
        else:
            result = CleanupResult(attempted=False, skipped_reason="nothing was injected")
        incident.cleanup = result
        incident.add_note(result.summary())
        if result.attempted and not result.verified:
            logger.error(
                "Incident %s: cleanup revert not verified, the test workload may still be "
                "faulted: %s", incident.id, result.verification_detail,
            )

    @staticmethod
    def _revert_and_check(scenario: Scenario) -> CleanupResult:
        succeeded, error = True, None
        try:
            skipped = scenario.cleanup()
            if skipped:
                return CleanupResult(attempted=False, skipped_reason=skipped)
        except Exception as cleanup_error:
            succeeded, error = False, str(cleanup_error)
        # Checked even when cleanup raised: that is when the evidence matters most.
        try:
            check = scenario.verify_cleanup()
            verified, detail = check.passed, check.detail
        except Exception as check_error:
            verified = False
            detail = f"verification error: {type(check_error).__name__}: {check_error}"
        return CleanupResult(
            attempted=True,
            succeeded=succeeded,
            error=error,
            verified=verified,
            verification_detail=detail,
        )

    def _run(self, incident: Incident, scenario: Scenario, guard: RunGuard):
        if not self.experiments_enabled():
            incident.fail("Experiments are disabled by the kill switch")
            return

        guard.checkpoint("before inject")
        # Flagged before the call so an inject that fails midway still gets cleanup.
        self._inject_started.add(incident.id)
        try:
            injection = scenario.inject()
        finally:
            incident.pre_injection_state = scenario.pre_injection_state()
        incident.injected_fault = injection.injected_fault
        incident.transition(S.INJECTED, injection.summary)

        detection = scenario.detect()
        incident.signals = detection.signals
        if not detection.incident_found:
            incident.fail("No incident detected within the detection timeout")
            return
        incident.transition(S.DETECTED, f"{len(detection.signals)} signals collected")

        incident.transition(S.INVESTIGATING)
        incident.diagnosis = self.investigator.investigate(scenario.name, incident.signals)

        decision = self.policy.decide(
            incident.diagnosis, self.experiments_enabled(), scenario=incident.scenario
        )
        incident.policy_decision = decision
        incident.transition(S.POLICY_DECIDED, f"{decision.decision.value}: {decision.action}")

        if decision.decision is Decision.DENY:
            incident.transition(S.REJECTED, "; ".join(decision.reasons))
            return

        if decision.decision is Decision.ESCALATE:
            # A decision outcome, not a remediation: nothing runs, nothing is verified.
            incident.human_intervention = True
            reason = "; ".join(decision.reasons)
            logger.warning("Incident %s escalated to a human: %s", incident.id, reason)
            incident.transition(S.ESCALATED, f"Escalated to a human: {reason}")
            return

        approved = False
        if decision.decision is Decision.HUMAN_APPROVAL:
            incident.transition(S.AWAITING_APPROVAL, "; ".join(decision.reasons))
            incident.human_intervention = True
            timed_out = not self._approvals[incident.id].wait(self.approval_timeout)
            guard.checkpoint("approval wait")  # An engage beats a racing approve.
            if timed_out:
                incident.fail("Approval timed out")
                return
            if not self._approved.get(incident.id):
                incident.transition(S.REJECTED, "Rejected by a human")
                return
            approved = True

        guard.checkpoint("before remediation")
        incident.transition(S.REMEDIATING)
        try:
            result = self.remediation.execute(
                decision, approved, scenario.remediation_context()
            )
        except RemediationRefused as refusal:
            incident.fail(f"Remediation refused: {refusal}")
            return
        incident.remediation_result = result
        if not result.succeeded:
            incident.fail(f"Remediation failed: {result.error}")
            return
        incident.remediation = result.detail

        guard.checkpoint("before verification")
        incident.transition(S.VERIFYING, incident.remediation)
        incident.verification = scenario.verify()
        if not incident.verification.passed:
            failed = [c.name for c in incident.verification.checks if not c.passed]
            incident.fail(f"Verification failed: {', '.join(failed) or 'no checks ran'}")
            return

        incident.transition(S.RESOLVED, "All verification checks passed")
