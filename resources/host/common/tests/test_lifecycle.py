"""Behavior tests for admission invalidation, reserve accounting and races."""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import threading
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('lifecycle_test_package', ROOT/'__init__.py',
                                             submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec); sys.modules[spec.name] = package; spec.loader.exec_module(package)
from lifecycle_test_package.policy import Policy
from lifecycle_test_package.state import Interlock
from lifecycle_test_package.samples import mac_capacity, nvidia_sample
from lifecycle_test_package.controller import Controller

GiB = 1024**3
UUID = 'GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000001'
HOST = {'host_uuid':'physical-host', 'boot_uuid':'actual-boot'}


def policy(platform='darwin'):
    return {'version':1, 'platform':platform, 'desktop_reserve_bytes':16*GiB,
        'commit_reserve_bytes':16*GiB if platform == 'win32' else 0,
        'device_reserve_bytes':GiB if platform == 'win32' else 0,
        'minimum_immediate_free_bytes':256*1024**2, 'minimum_pressure_free_percent':65 if platform == 'darwin' else 0,
        'maximum_swapout_growth_bytes':64*1024**2 if platform == 'darwin' else 0,
        'sample_max_age_seconds':8, 'maximum_sample_gap_seconds':6,
        'stable_span_seconds':2, 'stable_samples':3, 'device_uuid':UUID if platform == 'win32' else None,
        'recipes':{'model':{'host_peak_bytes':4*GiB, 'commit_peak_bytes':4*GiB if platform == 'win32' else 0,
                            'device_peak_bytes':GiB, 'maximum_seconds':120}}}


def sample(t=100, platform='darwin'):
    return {'platform':platform, 'boot':copy.deepcopy(HOST), 'observed_continuous':t,
        'total_bytes':64*GiB, 'available_advisory_bytes':25*GiB, 'immediate_free_bytes':GiB,
        'swapout_bytes':0 if platform == 'darwin' else None,
        'pressure_free_percent':80 if platform == 'darwin' else None,
        'commit_available_bytes':25*GiB if platform == 'win32' else None,
        'device':{'uuid':UUID, 'total_bytes':8*GiB, 'free_bytes':4*GiB} if platform == 'win32' else None}


class Capacity(unittest.TestCase):
    def test_mac_uma_one_envelope_without_rss_driver_double_count(self):
        p = Policy(policy()); s = sample(); s['available_advisory_bytes'] = 20*GiB
        self.assertTrue(p.check(s, 100, 'model')['allowed'])
        s['available_advisory_bytes'] -= 1
        self.assertFalse(p.check(s, 100, 'model')['allowed'])

    def test_running_job_needs_reserve_only(self):
        for platform in ('darwin', 'win32'):
            p = Policy(policy(platform)); s = sample(platform=platform)
            s['available_advisory_bytes'] = 17*GiB
            if platform == 'win32': s['commit_available_bytes'] = 17*GiB; s['device']['free_bytes'] = GiB
            self.assertFalse(p.check(s, 100, 'model')['allowed'])
            self.assertTrue(p.check(s, 100, 'model', running=True)['allowed'])

    def test_windows_commit_device_and_physical_are_separate(self):
        for key in ('available_advisory_bytes', 'commit_available_bytes', 'device'):
            s = sample(platform='win32')
            if key == 'device': s['device']['free_bytes'] = 2*GiB-1
            else: s[key] = 20*GiB-1
            self.assertFalse(Policy(policy('win32')).check(s, 100, 'model')['allowed'])

    def test_wrong_device_or_unavailable_counter_is_closed(self):
        cases = [('uuid', 'GPU-'+('0'*36)), ('free_bytes', None), ('total_bytes', -1)]
        for key, value in cases:
            s = sample(platform='win32'); s['device'][key] = value
            self.assertFalse(Policy(policy('win32')).check(s, 100, 'model')['allowed'])

    def test_mac_pressure_free_floor_and_swap_growth(self):
        p = Policy(policy())
        for key, value in [('pressure_free_percent',64), ('immediate_free_bytes',0), ('swapout_bytes',65*1024**2)]:
            s = sample(); s[key] = value
            self.assertFalse(p.check(s, 100, 'model', previous=sample(99))['allowed'])

    def test_explicit_mac_cache_policy_keeps_reserve_pressure_and_swap_checks(self):
        value=policy();value['minimum_immediate_free_bytes']=0;p=Policy(value)
        s=sample();s.update(immediate_free_bytes=128*1024**2,
            available_advisory_bytes=28*GiB,pressure_free_percent=80)
        for running in (False,True):
            self.assertTrue(p.check(s,100,'model',running=running,previous=sample(99))['allowed'])
            for key,bad in [('available_advisory_bytes',16*GiB-1),
                            ('pressure_free_percent',64),('swapout_bytes',65*1024**2)]:
                changed=copy.deepcopy(s);changed[key]=bad
                self.assertFalse(p.check(changed,100,'model',running=running,previous=sample(99))['allowed'])
        # Existing policies retain their selected raw-free veto, including Windows.
        for platform in ('darwin','win32'):
            s=sample(platform=platform);s['immediate_free_bytes']=0
            self.assertFalse(Policy(policy(platform)).check(s,100,'model',running=True)['allowed'])

    def test_malformed_stale_future_bool_and_cross_boot_samples(self):
        p = Policy(policy())
        for key, value in [('observed_continuous',float('nan')), ('observed_continuous',90),
                           ('observed_continuous',101), ('available_advisory_bytes',True),
                           ('pressure_free_percent',None), ('swapout_bytes',-1)]:
            s = sample(); s[key] = value
            self.assertFalse(p.check(s, 100, 'model')['allowed'])
        s = sample(); s['boot']['boot_uuid'] = 'different'
        self.assertFalse(p.check(s, 100, 'model', previous=sample())['allowed'])

    def test_policy_closes_unbudgeted_recipes_and_is_copied(self):
        v = policy(); p = Policy(v); v['recipes']['model']['host_peak_bytes'] = 0
        self.assertEqual(p.value['recipes']['model']['host_peak_bytes'],4*GiB)
        self.assertFalse(p.check(sample(),100,'unapproved')['allowed'])
        v = policy(); v['recipes']['model']['host_peak_bytes'] = 0
        with self.assertRaises(ValueError): Policy(v)

    def test_mach_speculative_and_purgeable_not_added_twice(self):
        v = mac_capacity(page_size=16384, free_pages=100, inactive_pages=200,
                         total_bytes=1000*16384, swapout_pages=7)
        self.assertEqual(v['available_advisory_bytes'],300*16384)
        self.assertEqual(v['immediate_free_bytes'],100*16384)
        self.assertEqual(v['swapout_bytes'],7*16384)
        with self.assertRaises(ValueError): mac_capacity(page_size=16384,free_pages=900,inactive_pages=200,
                                                         total_bytes=1000*16384,swapout_pages=0)

    def test_nvidia_exact_device_closed_numeric_parse(self):
        text = UUID+', 8192, 3000\n'
        self.assertEqual(nvidia_sample(text,UUID)['free_bytes'],3000*1024**2)
        self.assertEqual(nvidia_sample('GPU-aaaaaaaa-aaaa-4aaa-8aaa-000000000002, 16384, 9000\n'+text,
                                       UUID)['free_bytes'],3000*1024**2)
        for text in (text+text, text.replace('3000','N/A'), text.replace('3000','9000'),
                     text.replace(UUID,'GPU-'+'0'*36)):
            with self.assertRaises(ValueError): nvidia_sample(text,UUID)


class Lifecycle(unittest.TestCase):
    def new(self): return Interlock(Policy(policy()), HOST)
    def ready(self):
        state = self.new()
        state.reconciled(host=HOST,server_clean=True,native_clean=True,requests_settled=True)
        for t in (100,101,102): state.observe(sample(t),t)
        self.assertTrue(state.view(102)['ready']); return state

    def test_startup_requires_all_three_cleanup_observations(self):
        state = self.new()
        for field in ('server_clean','native_clean','requests_settled'):
            args = dict(host=HOST,server_clean=True,native_clean=True,requests_settled=True); args[field] = False
            state.reconciled(**args)
            for t in (100,101,102): state.observe(sample(t),t)
            self.assertFalse(state.view(102)['ready'])

    def test_sleep_and_resume_never_reopen_without_reconciliation_and_stability(self):
        state = self.ready(); stamp = state.stamp()
        state.event('will_sleep'); self.assertFalse(state.view(102)['ready'])
        state.event('resume'); state.observe(sample(200),200)
        self.assertFalse(state.view(200)['ready'])
        state.reconciled(host=HOST,server_clean=True,native_clean=True,requests_settled=True)
        for t in (201,202,203): state.observe(sample(t),t)
        self.assertTrue(state.view(203)['ready'])
        self.assertFalse(state.admission('model',sample(203),203,expected_stamp=stamp)['allowed'])

    def test_missing_notification_clock_gap_closes_admission(self):
        state = self.ready(); state.observe(sample(120),120)
        self.assertFalse(state.view(120)['ready']); self.assertFalse(state.settled)

    def test_boot_change_and_backwards_clock_are_not_pid_proofs(self):
        state = self.ready(); changed = sample(103); changed['boot']['boot_uuid'] = 'other'
        state.observe(changed,103); self.assertFalse(state.view(103)['ready'])
        state = self.ready(); state.observe(sample(101),103)
        self.assertFalse(state.view(103)['ready'])

    def test_owner_choice_preserved_across_events(self):
        state = self.ready(); state.owner('interactive'); state.event('resume')
        self.assertEqual(state.desired,'interactive'); self.assertFalse(state.view(103)['ready'])

    def test_stale_ready_view_and_old_dispatch_sample_refused(self):
        state = self.ready(); self.assertFalse(state.view(111)['ready'])
        self.assertFalse(state.admission('model',sample(101),103)['allowed'])

    def test_capacity_must_fit_every_stable_sample(self):
        state = self.new(); state.reconciled(host=HOST,server_clean=True,native_clean=True,requests_settled=True)
        for t in (100,101,102):
            s = sample(t)
            if t == 101: s['available_advisory_bytes'] = 19*GiB
            state.observe(s,t)
        self.assertFalse(state.admission('model',sample(102),102)['allowed'])


class ControllerRaces(unittest.TestCase):
    def setUp(self):
        self.t = 100; self.mode = 'background'; self.cancelled = 0; self.records = []
        self.read = lambda:sample(self.t)
        def cancel(): self.cancelled += 1
        self.controller = Controller(policy=Policy(policy()),host=HOST,sample=lambda:self.read(),now=lambda:self.t,
            settled=lambda:dict(host=HOST,server_clean=True,native_clean=True,requests_settled=True),
            cancel_owned=cancel,publish=self.records.append,desired=lambda:self.mode)
        self.observer = types.SimpleNamespace(healthy=lambda:True)
        self.controller.attach(self.observer)
        for self.t in (100,101,102): self.controller.tick()

    def test_event_while_sampler_blocked_refuses_before_original_store(self):
        self.assertTrue(self.controller.admit('model')['allowed'])
        def interrupted(): self.controller.event('resume'); return sample(self.t)
        self.read = interrupted
        self.assertFalse(self.controller.admit('model')['allowed'])
        self.assertFalse(self.controller.snapshot()['ready'])

    def test_admission_and_tick_counter_reads_keep_observation_order(self):
        entered, release, second = threading.Event(), threading.Event(), threading.Event()
        calls=[]; answer=[]
        def read():
            calls.append(1)
            if len(calls)==1:
                self.t=103;entered.set();release.wait(2);return sample(103)
            self.t=104;second.set();return sample(104)
        self.read=read
        admission=threading.Thread(target=lambda:answer.append(self.controller.admit('model')))
        tick=threading.Thread(target=self.controller.tick)
        admission.start();self.assertTrue(entered.wait(1));tick.start()
        try:self.assertFalse(second.wait(.2),'counter reads ran out of state-observation order')
        finally:release.set();admission.join(2);tick.join(2)
        self.assertFalse(admission.is_alive());self.assertFalse(tick.is_alive())
        self.assertTrue(answer[0]['allowed']);self.assertEqual(self.controller.state.previous['observed_continuous'],104)

    def test_owner_pause_during_sample_refuses(self):
        def paused(): self.mode = 'interactive'; return sample(self.t)
        self.read = paused
        self.assertFalse(self.controller.admit('model')['allowed'])

    def test_running_owner_transition_does_not_invent_capacity_pressure(self):
        self.mode='interactive'
        self.assertFalse(self.controller.running('model'))
        self.assertNotEqual(self.controller.state.reason,'capacity_pressure')

    def test_sampler_failure_closes_and_cancels_owned_only(self):
        def fail(): raise OSError('counter unavailable')
        self.read = fail; self.controller.tick()
        self.assertFalse(self.controller.snapshot()['ready']); self.assertEqual(self.cancelled,1)

    def test_lost_power_observer_closes_even_with_unchanged_sample(self):
        self.observer.healthy = lambda:False; self.controller.tick()
        self.assertFalse(self.controller.snapshot()['ready'])
        self.assertFalse(self.controller.admit('model')['allowed'])

    def test_pressure_requests_cancel_without_signalling_any_numeric_pid(self):
        def pressure():
            s = sample(self.t); s['available_advisory_bytes'] = 15*GiB; return s
        self.read = pressure
        self.assertFalse(self.controller.running('model')); self.assertEqual(self.cancelled,1)
        self.assertFalse(self.controller.snapshot()['ready'])

    def test_event_during_reconciliation_does_not_mark_old_generation_clean(self):
        def unsettled():
            self.controller.event('resume')
            return dict(host=HOST,server_clean=True,native_clean=True,requests_settled=True)
        self.controller.settled = unsettled; self.controller.event('will_sleep'); self.controller.tick()
        self.assertFalse(self.controller.snapshot()['ready'])

    def test_slow_owner_mode_file_cannot_block_native_power_callback(self):
        for operation in ('tick','admit','running'):
            with self.subTest(operation=operation):
                self.setUp()  # Each case starts with its own reconciled ready generation.
                entered,release,delivered=threading.Event(),threading.Event(),threading.Event()
                original=self.controller.desired
                def slow():entered.set();release.wait(2);return 'background'
                self.controller.desired=slow
                action=lambda:getattr(self.controller,operation)(*(() if operation=='tick' else ('model',)))
                worker=threading.Thread(target=action);worker.start()
                self.assertTrue(entered.wait(1))
                callback=threading.Thread(target=lambda:(self.controller.event('will_sleep'),delivered.set()))
                callback.start()
                try:self.assertTrue(delivered.wait(.2),'power callback waited behind disk IO')
                finally:
                    release.set();worker.join(2);callback.join(2);self.controller.desired=original
                self.assertFalse(worker.is_alive());self.assertFalse(callback.is_alive())


if __name__ == '__main__': unittest.main()
