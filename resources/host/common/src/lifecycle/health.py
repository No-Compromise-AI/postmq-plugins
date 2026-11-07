"""Compose owner health from fresh lifecycle and native observations.

This is reporting only. It grants no admission, claim, cleanup or recovery
permission. The embedding owner must validate its canonical row and report
through its existing authenticated transport, without registration/upsert.
"""
import math


def observation(state, native_health, now, maximum_age):
    if (not isinstance(state, dict) or type(now) not in (int, float) or
            not math.isfinite(now) or type(maximum_age) not in (int, float) or
            not math.isfinite(maximum_age) or maximum_age <= 0):
        raise ValueError('bounded lifecycle health observation required')
    stamp = state.get('observed_continuous')
    fresh = (type(stamp) in (int, float) and math.isfinite(stamp) and
             0 <= now - stamp <= maximum_age)
    if not fresh or native_health not in ('healthy', 'degraded', 'unhealthy', 'unknown'):
        health, reason = 'unknown', 'fresh native and lifecycle evidence unavailable'
    elif native_health != 'healthy':
        health, reason = native_health, 'native provider is not healthy'
    elif state.get('ready') is not True:
        health, reason = 'degraded', 'lifecycle recovery or owner pause'
    elif state.get('desired_owner_mode') != 'background':
        health, reason = 'degraded', 'owner is not lending in background mode'
    elif state.get('new_job_capacity_ready') is not True:
        health, reason = 'degraded', 'no advertised recipe has fresh capacity'
    else:
        health, reason = 'healthy', 'fresh native lifecycle and recipe capacity'
    # An old retained diagnostic is not a current error after verified recovery.
    current_error = None if fresh and state.get('ready') is True else state.get('error_type')
    return {'health': health, 'reason': reason, 'fresh': fresh,
            'error_type': current_error, 'last_error_type': state.get('last_error_type',state.get('error_type')),
            'ready': fresh and state.get('ready') is True,
            'new_job_capacity_ready': fresh and state.get('new_job_capacity_ready') is True}
