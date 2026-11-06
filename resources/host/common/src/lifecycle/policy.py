"""Pure capacity decisions. These are admission margins, not memory partitions."""
import copy
import math


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, low=0, high=2**63-1):
    require(type(value) is int and low <= value <= high, 'invalid capacity integer')
    return value


def finite(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
            'invalid observation clock')
    return value


class Policy:
    def __init__(self, value):
        require(isinstance(value, dict), 'capacity policy object required')
        self.cpu_fixture=value.get('cpu_fixture',False)
        require(type(self.cpu_fixture) is bool, 'explicit CPU fixture flag required')
        fields={
            'version', 'platform', 'desktop_reserve_bytes', 'commit_reserve_bytes',
            'device_reserve_bytes', 'minimum_immediate_free_bytes', 'minimum_pressure_free_percent',
            'maximum_swapout_growth_bytes', 'sample_max_age_seconds', 'maximum_sample_gap_seconds',
            'stable_span_seconds', 'stable_samples', 'device_uuid', 'recipes'}
        if self.cpu_fixture:fields.add('cpu_fixture')
        require(set(value)==fields, 'closed capacity policy required')
        require(value['version'] == (2 if self.cpu_fixture else 1) and type(value['version']) is int and
                value['platform'] in ('darwin', 'win32'), 'unsupported capacity policy')
        require(not self.cpu_fixture or value['platform']=='win32', 'CPU fixture policy is Windows VM only')
        self.value = copy.deepcopy(value)
        self.platform = value['platform']
        for name in ('desktop_reserve_bytes', 'commit_reserve_bytes', 'device_reserve_bytes',
                     'minimum_immediate_free_bytes', 'maximum_swapout_growth_bytes'):
            integer(value[name])
        integer(value['minimum_pressure_free_percent'], 0, 100)
        integer(value['stable_samples'], 2, 10)
        for name in ('sample_max_age_seconds', 'maximum_sample_gap_seconds', 'stable_span_seconds'):
            require(0 < finite(value[name]) <= 30, 'bounded observation window required')
        require(value['stable_span_seconds'] < value['maximum_sample_gap_seconds'],
                'stable window must fit sample gap')
        require(value['stable_span_seconds'] < value['sample_max_age_seconds'],
                'stable evidence must fit freshness window')
        if self.platform == 'darwin':
            require(value['device_uuid'] is None and value['device_reserve_bytes'] == 0,
                    'Mac UMA has no separate device pool')
        elif self.cpu_fixture:
            require(value['device_uuid'] is None and value['device_reserve_bytes']==0 and
                    value['minimum_pressure_free_percent']==0 and value['maximum_swapout_growth_bytes']==0,
                    'CPU fixture has no device pool or fabricated Windows pressure/swap')
        else:
            import re
            require(isinstance(value['device_uuid'], str) and re.fullmatch(
                r'GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['device_uuid']) and
                value['minimum_pressure_free_percent'] == 0 and value['maximum_swapout_growth_bytes'] == 0,
                'Windows device must be explicit; unavailable pressure/swap counters stay unset')
        recipes = value['recipes']
        require(isinstance(recipes, dict) and 1 <= len(recipes) <= 20, 'bounded recipes required')
        for name, budget in recipes.items():
            require(isinstance(name, str) and 1 <= len(name) <= 64 and isinstance(budget, dict) and
                    set(budget) == {'host_peak_bytes', 'commit_peak_bytes', 'device_peak_bytes',
                                    'maximum_seconds'}, 'closed recipe budget required')
            for field in ('host_peak_bytes', 'commit_peak_bytes', 'device_peak_bytes'):
                integer(budget[field])
            integer(budget['maximum_seconds'], 1, 3600)
            if self.cpu_fixture:
                require(budget['device_peak_bytes']==0, 'CPU fixture cannot budget GPU memory')
            if self.platform == 'darwin':
                require(budget['host_peak_bytes'] >= budget['device_peak_bytes'] and
                        budget['commit_peak_bytes'] == 0 and value['commit_reserve_bytes'] == 0,
                        'Mac shared-memory envelope must cover device once')

    def check(self, sample, now, recipe, *, running=False, previous=None):
        """Return concrete reasons; any unavailable observation refuses admission."""
        try:
            require(type(running) is bool, 'explicit running state required')
            require(recipe in self.value['recipes'], 'recipe has no capacity budget')
            require(sample['platform'] == self.platform, 'capacity platform differs')
            age = finite(now) - finite(sample['observed_continuous'])
            require(0 <= age <= self.value['sample_max_age_seconds'], 'capacity observation stale')
            total = integer(sample['total_bytes'], 1)
            available = integer(sample['available_advisory_bytes'], 0, total)
            immediate = integer(sample['immediate_free_bytes'], 0, total)
            budget = self.value['recipes'][recipe]
            needed = self.value['desktop_reserve_bytes'] + (0 if running else budget['host_peak_bytes'])
            reasons = []
            if available < needed:
                reasons.append('host desktop reserve and recipe envelope do not fit')
            if immediate < self.value['minimum_immediate_free_bytes']:
                reasons.append('immediate free-memory floor breached')
            if self.platform == 'win32':
                require(sample['pressure_free_percent'] is None and sample['swapout_bytes'] is None,
                        'do not fabricate unavailable Windows pressure/swap counters')
                commit = integer(sample['commit_available_bytes'])
                if commit < self.value['commit_reserve_bytes'] + (0 if running else budget['commit_peak_bytes']):
                    reasons.append('host commit reserve and recipe envelope do not fit')
                device = sample['device']
                if self.cpu_fixture:
                    require(device is None, 'CPU fixture must not fabricate a GPU observation')
                else:
                    require(device['uuid'] == self.value['device_uuid'],
                            'device identity unavailable')
                    free = integer(device['free_bytes'], 0, integer(device['total_bytes'], 1))
                    if free < self.value['device_reserve_bytes'] + (0 if running else budget['device_peak_bytes']):
                        reasons.append('device reserve and recipe envelope do not fit')
            else:
                pressure = integer(sample['pressure_free_percent'], 0, 100)
                if pressure < self.value['minimum_pressure_free_percent']:
                    reasons.append('system memory pressure floor breached')
                swap = integer(sample['swapout_bytes'])
                if previous is not None:
                    require(previous['boot'] == sample['boot'], 'swap samples span different boots')
                    old_swap = integer(previous['swapout_bytes'])
                    require(swap >= old_swap, 'swap counter moved backwards')
                    if swap-old_swap > self.value['maximum_swapout_growth_bytes']:
                        reasons.append('system swapout growth exceeds owner policy')
                require(sample['device'] is None and sample['commit_available_bytes'] is None,
                        'Mac UMA must not be counted as separate VRAM/commit pools')
            return {'allowed': not reasons, 'reasons': reasons,
                    'enforcement': 'advisory samples with owned-job response',
                    'accounting': 'one unified host envelope' if self.platform == 'darwin'
                                  else 'host physical and commit envelopes; CPU fixture has no device pool' if self.cpu_fixture
                                  else 'host physical and commit envelopes; separate device sample'}
        except (ValueError, KeyError, TypeError) as error:
            return {'allowed': False, 'reasons': ['capacity evidence unavailable: '+type(error).__name__],
                    'enforcement': 'advisory samples with owned-job response'}
