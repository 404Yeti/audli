"""AUD-92: worker-local, no-queue account-lease admission. No identity or audio stored."""
import asyncio
import logging
from collections.abc import Awaitable
from threading import Lock


class LeaseAdmission:
    def __init__(self, enabled: bool, limit: int) -> None:
        self.enabled, self.limit = enabled, limit
        self._lock = Lock()
        self.in_use = 0
        self.quarantined = 0
        self.rejected = 0

    def try_acquire(self) -> "Permit | None":
        if not self.enabled:
            return None
        # No await, semaphore waiter, connection checkout or account lookup.
        with self._lock:
            if self.in_use >= self.limit:
                self.rejected += 1
                return None
            self.in_use += 1
        return Permit(self)

    def snapshot(self) -> dict[str, bool | int]:
        with self._lock:
            return {'enabled': self.enabled, 'limit': self.limit, 'in_use': self.in_use,
                    'quarantined': self.quarantined, 'rejected': self.rejected}


class Permit:
    def __init__(self, admission: LeaseAdmission) -> None:
        self.admission = admission
        self.failed = self.released = False

    def quarantine(self) -> None:
        # May run in the connection worker thread. Failed cleanup is not proof
        # that a DB socket/transaction was released: never recycle its slot.
        with self.admission._lock:
            if self.failed:
                return
            self.failed = True
            self.admission.quarantined += 1
        logging.getLogger('audli').error('lease_admission_cleanup_failed; permit quarantined')

    def release(self) -> None:
        with self.admission._lock:
            if self.released:
                return
            self.released = True
            if not self.failed:
                self.admission.in_use -= 1


async def finish_cleanup(awaitable: Awaitable[object], permit: Permit) -> None:
    """Never return a permit while cleanup is running, even on repeated cancel."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            break
    try:
        task.result()
    except BaseException:
        permit.quarantine()
        raise
    if cancelled:
        raise asyncio.CancelledError
