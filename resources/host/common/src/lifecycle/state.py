"""Local admission interlock; claim/fence/native ownership stays in the SDK."""
import copy
import secrets
from .policy import finite, require


class Interlock:
    def __init__(self, policy, host, desired='background', *, generation=None):
        require(desired in ('background', 'interactive', 'unavailable'), 'invalid owner choice')
        require(isinstance(host, dict) and host, 'complete host/boot identity required')
        self.policy = policy
        self.host = copy.deepcopy(host)
        self.generation = generation or secrets.token_hex(16)
        self.sequence = 0
        self.desired = desired
        self.phase = 'recovering'
        self.reason = 'native and coordinator reconciliation required'
        self.samples = []
        self.previous = None
        self.settled = False

    def stamp(self):
        return {'host': copy.deepcopy(self.host), 'generation': self.generation, 'sequence': self.sequence}

    def invalidate(self, reason):
        self.sequence += 1
        self.settled = False
        self.samples = []
        self.previous = None
        self.phase, self.reason = 'recovering', reason

    def event(self, name):
        require(name in ('will_sleep', 'resume', 'network_changed', 'observer_failed',
                         'clock_gap', 'capacity_pressure', 'coordinator_recovery'), 'unknown lifecycle event')
        self.invalidate(name)
        if name == 'will_sleep':
            self.phase = 'quiescing'
        return {'close_admission': True, 'interrupt_owned_managers': True, 'stamp': self.stamp()}

    def owner(self, desired):
        require(desired in ('background', 'interactive', 'unavailable'), 'invalid owner choice')
        if desired != self.desired:
            self.desired = desired
            self.invalidate('owner mode changed')
        return {'close_admission': desired != 'background',
                'interrupt_owned_managers': desired == 'interactive', 'stamp': self.stamp()}

    def reconciled(self, *, host, server_clean, native_clean, requests_settled):
        require(host == self.host, 'reconciliation belongs to another boot')
        require(type(server_clean) is bool and type(native_clean) is bool and
                type(requests_settled) is bool, 'explicit reconciliation observations required')
        self.settled = server_clean and native_clean and requests_settled
        self.samples = []
        self.previous = None
        self.phase = ('warming' if self.desired=='background' else 'paused') if self.settled else 'recovering'
        self.reason = ('fresh stable samples required' if self.desired=='background' else 'owner is not lending') \
            if self.settled else 'native/server work remains unsettled'

    def observe(self, sample, now):
        now = finite(now)
        if sample.get('boot') != self.host:
            self.invalidate('physical host or boot changed')
            return self.view(now)
        observed = finite(sample['observed_continuous'])
        if self.previous is not None:
            gap = observed - self.previous['observed_continuous']
            if gap < 0 or gap > self.policy.value['maximum_sample_gap_seconds']:
                self.invalidate('clock gap or backwards observation')
                return self.view(now)
            # Duplicate coarse-clock ticks add no stable span, but do not
            # manufacture a backwards clock or completed recovery either.
        self.previous = copy.deepcopy(sample)
        if not self.settled or self.desired != 'background' or self.phase == 'quiescing':
            return self.view(now)
        age = now-observed
        if not 0 <= age <= self.policy.value['sample_max_age_seconds']:
            self.invalidate('stale observation')
            return self.view(now)
        self.samples.append(copy.deepcopy(sample))
        self.samples = self.samples[-self.policy.value['stable_samples']:]
        stable = len(self.samples) == self.policy.value['stable_samples'] and \
            self.samples[-1]['observed_continuous']-self.samples[0]['observed_continuous'] >= self.policy.value['stable_span_seconds']
        self.phase = 'ready' if stable else 'warming'
        self.reason = None if stable else 'fresh stable samples required'
        return self.view(now)

    def view(self, now):
        fresh = self.previous is not None and 0 <= finite(now)-self.previous['observed_continuous'] <= \
            self.policy.value['sample_max_age_seconds']
        return {'phase': self.phase, 'ready': self.phase == 'ready' and fresh and self.desired == 'background',
                'desired_owner_mode': self.desired, 'reason': self.reason,
                'stamp': self.stamp(), 'observed_continuous': None if self.previous is None else self.previous['observed_continuous']}

    def admission(self, recipe, sample, now, *, expected_stamp=None):
        view = self.view(now)
        if expected_stamp is not None and expected_stamp != self.stamp():
            return {'allowed': False, 'reasons': ['lifecycle generation changed']}
        if not view['ready'] or sample.get('boot') != self.host:
            return {'allowed': False, 'reasons': ['lifecycle is not ready on this boot']}
        observed = finite(sample['observed_continuous'])
        if observed < self.previous['observed_continuous'] or \
           observed-self.previous['observed_continuous'] > self.policy.value['maximum_sample_gap_seconds']:
            self.invalidate('dispatch observation discontinuity')
            return {'allowed': False, 'reasons': ['lifecycle observation discontinuity']}
        # Stable means stable capacity too, not merely a sequence of timestamps.
        for item in self.samples:
            result = self.policy.check(item, now, recipe)
            if not result['allowed']:
                return result
        return self.policy.check(sample, now, recipe,
                                 previous=self.samples[0] if self.samples else None)

    def running_check(self, recipe, sample, now, *, expected_stamp):
        if expected_stamp != self.stamp() or sample.get('boot') != self.host or self.desired != 'background':
            return {'allowed':False,'reasons':['running job lifecycle changed']}
        if self.previous is None or not 0 <= sample['observed_continuous']-self.previous['observed_continuous'] <= \
                self.policy.value['maximum_sample_gap_seconds']:
            return {'allowed':False,'reasons':['running job observation discontinuity']}
        return self.policy.check(sample,now,recipe,running=True,previous=self.previous)
