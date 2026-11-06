"""In-process custodian interlock; no claim, fence, registry or PID authority."""
import copy
import threading
from .policy import require
from .state import Interlock


class RecoveryDeferred(RuntimeError):
    """Local admission/drain is busy; no recovery transaction was attempted."""


class Controller:
    def __init__(self, *, policy, host, sample, now, settled, cancel_owned, publish, desired):
        self.state = Interlock(policy, host, desired())
        self.sample, self.now, self.settled = sample, now, settled
        self.cancel_owned, self.publish, self.desired = cancel_owned, publish, desired
        self.lock = threading.RLock()
        # Counter reads and their state checks stay in observation order.
        # Native callbacks never take this IO lock.
        self.sample_lock = threading.Lock()
        self.pending = []
        self.events = 0
        self.last_event = None
        self.observer = None
        self.last_failure = None
        self.recovery_failures = 0
        self.recovery_after = None
        self.recovery_floor = 0
        self.interrupt_sequence = 0
        self.cancelled_sequence = 0

    def event(self, name):
        # Called by native notifications. No disk, network, wait or cleanup in
        # the notification callback; the bit closes admission immediately.
        with self.lock:
            require(name in ('will_sleep', 'resume', 'network_changed', 'observer_failed',
                             'clock_gap', 'capacity_pressure', 'coordinator_recovery'), 'invalid lifecycle event')
            self.events += 1
            self.last_event = name
            if name != 'coordinator_recovery':self.interrupt_sequence = self.events
            # Notifications close admission immediately but cannot erase a
            # failed recovery's deadline. Flapping Wi-Fi must not turn bounded
            # backoff into one coordinator transaction per sampling tick.
            self.pending = (self.pending+[name])[-16:]  # Coalesced; events counts every notification.

    def attach(self, observer):
        require(self.observer is None and observer.healthy(), 'healthy power subscription required')
        self.observer = observer

    def backoff(self):
        """Caller holds lock. Charge attempted or failed evidence, even on a race."""
        self.recovery_failures = min(6,self.recovery_failures+1)
        self.recovery_after = self.now() + min(60, 2 ** min(self.recovery_failures, 6))

    def evidence_backoff(self):
        # A failing sampler/publication during a wait cannot keep pushing the
        # same deadline forward. Real attempted transactions are always charged.
        if self.recovery_after is None or self.now() >= self.recovery_after:self.backoff()

    def cancel_obligation(self):
        with self.lock:
            target = self.interrupt_sequence
            needed = target > self.cancelled_sequence or self.state.desired == 'interactive'
        if needed and self.cancel_owned() is not False:
            with self.lock:self.cancelled_sequence = max(self.cancelled_sequence,target)

    def publish_current(self, *, recovery_attempted=False):
        """Publish even on an event race; transient IO cannot kill the observer."""
        try:
            self.cancel_obligation()
            result = self.snapshot()
            self.publish(result)
            return result
        except Exception as error:
            with self.lock:
                self.state.invalidate('lifecycle publication or cancellation unavailable')
                self.last_failure = type(error).__name__
                if recovery_attempted:self.backoff()
                else:self.evidence_backoff()
            self.event('observer_failed')
            return self.snapshot()

    def tick(self):
        """One finite iteration. Call outside the native callback and server lock."""
        charged = False
        recovered = False
        attempted = False
        try:
            require(self.observer is not None and self.observer.healthy(), 'power observer unavailable')
            with self.lock:
                sequence = self.events
                pending, self.pending = self.pending, []
                for name in pending: self.state.event(name)
            desired = self.desired()  # Disk IO must never hold the notification lock.
            with self.lock:
                raced = sequence != self.events
                self.state.owner(desired)
                needs_recovery = not self.state.settled
                due = self.now() >= self.recovery_floor and \
                    (self.recovery_after is None or self.now() >= self.recovery_after)
            if raced:return self.publish_current()
            self.cancel_obligation()
            if needs_recovery and due:
                # Existing native/SDK/server checks only. No automatic
                # registration, receipt editing, retirement or reset here.
                with self.lock:
                    self.recovery_floor = self.now()+2
                    self.recovery_after = self.recovery_floor
                attempted = True
                try:
                    proof = self.settled()
                except RecoveryDeferred as error:
                    with self.lock:self.last_failure = type(error).__name__
                    return self.publish_current()
                except Exception:
                    with self.lock:
                        self.backoff()
                        charged = True
                    raise
                with self.lock:
                    raced = sequence != self.events
                    if raced:
                        self.backoff();charged=True
                    else:
                        self.state.reconciled(host=proof['host'], server_clean=proof['server_clean'],
                            native_clean=proof['native_clean'], requests_settled=proof['requests_settled'])
                        recovered = self.state.settled
                        if not recovered:
                            self.backoff();charged=True
                if raced:return self.publish_current()
            with self.sample_lock:
                sample = self.sample(); now = self.now()
                with self.lock:
                    if sequence == self.events:self.state.observe(sample, now)
            result = self.publish_current(recovery_attempted=attempted and not charged)
            with self.lock:
                if recovered and sequence == self.events and self.state.settled:
                    self.recovery_failures = 0
                    self.recovery_after = None
                    self.last_failure = None
            return result
        except Exception as error:
            with self.lock:
                self.state.invalidate('lifecycle evidence unavailable')
                self.last_failure = type(error).__name__
                if not charged:
                    if attempted:self.backoff()
                    else:self.evidence_backoff()
            self.event('observer_failed')
            return self.publish_current()

    def snapshot(self):
        with self.lock:
            result = self.state.view(self.now())
            if self.pending: result.update(ready=False, reason='unprocessed lifecycle event')
            budgets = {}
            if result['ready']:
                for name in self.state.policy.value['recipes']:
                    budgets[name] = self.state.admission(name,self.state.previous,self.now())
            return {**result, 'event_sequence':self.events, 'last_event':self.last_event,
                    'error_type':self.last_failure,
                    'recovery_failures':self.recovery_failures,
                    'recovery_attempt_after_continuous':self.recovery_after,
                    'minimum_recovery_attempt_after_continuous':self.recovery_floor,
                    'capacity_per_recipe':budgets,
                    'new_job_capacity_ready':any(b['allowed'] for b in budgets.values())}

    def admit(self, recipe):
        with self.lock:
            stamp, events = self.state.stamp(), self.events
            if self.pending or self.observer is None or not self.observer.healthy():
                return {'allowed':False, 'reasons':['lifecycle subscription is not ready']}
        with self.sample_lock:
            sample = self.sample(); now = self.now(); desired = self.desired()
            with self.lock:
                if self.pending or self.events != events or desired != 'background':
                    return {'allowed':False, 'reasons':['lifecycle changed during admission']}
                return self.state.admission(recipe, sample, now, expected_stamp=stamp)

    def running(self, recipe):
        """Reserve-only pressure check; do not count the running recipe twice."""
        with self.lock:
            stamp, events = self.state.stamp(), self.events
        try:
            with self.sample_lock:
                sample = self.sample(); now = self.now(); desired = self.desired()
                with self.lock:
                    unchanged = not self.pending and self.observer is not None and self.observer.healthy() and \
                        desired == 'background' and stamp == self.state.stamp() and events == self.events
                    allowed = unchanged and self.state.running_check(recipe,sample,now,expected_stamp=stamp)['allowed']
            if not allowed:
                self.event('capacity_pressure' if unchanged else 'observer_failed')
                self.publish_current()
            return bool(allowed)
        except Exception:
            self.event('observer_failed'); self.publish_current(); return False
