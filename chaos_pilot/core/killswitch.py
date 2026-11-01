import logging
import threading
from datetime import datetime

from chaos_pilot.incidents.models import utc_now

logger = logging.getLogger(__name__)


class Aborted(Exception):
    """Raised at a checkpoint when the kill switch has stopped the run."""

    def __init__(self, reason: str, where: str):
        super().__init__(f"{reason} (at {where})")
        self.reason = reason
        self.where = where


class KillSwitch:
    """In-memory runtime kill switch shared by the app, the pipeline and scenarios.

    Not persisted: a restart releases it. engage_count increases on every
    released → engaged change, so a run that started earlier still sees an
    engage that was released again before its next checkpoint.
    """

    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = []
        self.engage_count = 0
        self.reason: str | None = None
        self.engaged_at: datetime | None = None

    @property
    def engaged(self) -> bool:
        return self._event.is_set()

    def on_engage(self, callback):
        self._callbacks.append(callback)

    def engage(self, reason: str) -> bool:
        """True if this call engaged it, False if it already was (first reason kept)."""
        with self._lock:
            if self._event.is_set():
                logger.warning(
                    "Kill switch engage ignored at %s, already engaged since %s: %s",
                    utc_now().isoformat(), self.engaged_at.isoformat(), reason,
                )
                return False
            self.engage_count += 1
            self.reason = reason
            self.engaged_at = utc_now()
            self._event.set()
            logger.warning(
                "Kill switch ENGAGED at %s: %s", self.engaged_at.isoformat(), reason
            )
        for callback in self._callbacks:
            callback()
        return True

    def release(self) -> bool:
        with self._lock:
            if not self._event.is_set():
                logger.info("Kill switch release at %s: was not engaged", utc_now().isoformat())
                return False
            self._event.clear()
            self.engaged_at = None
            logger.warning(
                "Kill switch RELEASED at %s (was engaged for: %s)",
                utc_now().isoformat(), self.reason,
            )
            return True

    def status(self) -> dict:
        return {
            "engaged": self.engaged,
            "reason": self.reason if self.engaged else None,
            "engaged_at": self.engaged_at.isoformat() if self.engaged_at else None,
        }

    def guard(self) -> "RunGuard":
        return RunGuard(self)


class RunGuard:
    """The kill switch as seen by one run, fixed at the engage count when it started.

    An engage sticks for the run: it aborts even if released before the next checkpoint.
    """

    def __init__(self, switch: KillSwitch):
        self.switch = switch
        self.start_count = switch.engage_count

    @property
    def engaged(self) -> bool:
        return self.switch.engaged or self.switch.engage_count != self.start_count

    def checkpoint(self, where: str):
        if self.engaged:
            raise Aborted(self.switch.reason or "kill switch engaged", where)

    def pause(self, seconds: float, where: str):
        """Sleep that ends immediately on engage, then abort."""
        self.switch._event.wait(seconds)
        self.checkpoint(where)
