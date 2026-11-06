"""linux-procscan-v2: supervised work and allocation-visible device holders.

Cleanup is not a physical-memory reset and never certifies next-job capacity.
No broker, gate, journal, cached clean proof or control of an unrelated process.
The existing SDK and native prepared-child/subreaper remain the authorities.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time

if __package__:
    from . import allocation_scope as scope
    from . import linux_procscan as legacy
    from .container_assessment import visible_limits, count
    from .evidence import Baseline, selected_config, utc_now, claim_identity
else:
    import allocation_scope as scope
    import linux_procscan as legacy
    from container_assessment import visible_limits, count
    from evidence import Baseline, selected_config, utc_now, claim_identity

PROVIDER = scope.PROVIDER
SEAL = re.compile(r"[0-9a-f]{32}\Z")


def fresh(value):
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - at).total_seconds() <= 10
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def parts(ref, expected_scope):
    if not isinstance(ref, str) or len(ref) > 200 or not ref.startswith("procscan2:"):
        raise legacy.RentalError("versioned allocation reference required")
    base, sep, seal = ref.rpartition(":")
    if not sep or not SEAL.fullmatch(seal) or seal != expected_scope:
        raise legacy.RentalError("allocation scope differs")
    return legacy.parts("procscan:" + base.split(":", 1)[1])


def clean_verdict(baseline, inspection, sample, expected_scope):
    if inspection.get("scope_seal") != expected_scope:
        return "unknown", "allocation scope identity unavailable"
    if inspection.get("state") == "alive" or inspection.get("members"):
        return "cleanup_failed", "supervised descendants remain"
    if sample.get("scope_seal") != expected_scope:
        return "unknown", "allocation device observation unavailable"
    terminal = ((type(inspection.get("job_exit")) is int and 0 <= inspection["job_exit"] <= 255)
                + (type(inspection.get("job_signal")) is int and 1 <= inspection["job_signal"] <= 64)
                + (inspection.get("job_aborted") is True))
    malformed = (inspection.get("job_exit") is not None and not (
        type(inspection["job_exit"]) is int and 0 <= inspection["job_exit"] <= 255)
        or inspection.get("job_signal") is not None and not (
        type(inspection["job_signal"]) is int and 1 <= inspection["job_signal"] <= 64))
    if (inspection.get("state") != "gone" or inspection.get("job_ended") is not True or malformed
            or inspection.get("members") != [] or terminal != 1 or inspection.get("error")):
        return "unknown", "primary terminal status or native membership unavailable"
    if ((sample.get("boot_id"), sample.get("gpu_uuid"), sample.get("total_bytes")) != (
            baseline.boot_id, baseline.gpu_uuid, baseline.total_bytes) or not fresh(sample.get("observed_at"))
            or sample.get("allocation_inventory_qualified") is not True
            or not isinstance(sample.get("unknown_gpu_pids"), list)):
        return "unknown", "fresh allocation device inventory unavailable"
    if sample["unknown_gpu_pids"]:
        return "cleanup_failed", "unclassified allocation-visible device handles remain"
    return "verified_clean", "allocation scope: primary collected; descendants gone; no visible device holders; capacity not certified"


class AllocationProbe(legacy.RentalDeviceProbe):
    def __init__(self, *args, profile, scope_seal, **kwargs):
        super().__init__(*args, **kwargs)
        self.profile, self.scope_seal = profile, scope_seal

    def snapshot(self, owned=(), *, guard=None):
        # The CUDA observer is a finite collected child. Only after it exits do
        # we scan local device handles; no reliance on NVML's host PID numbers.
        at = utc_now()
        begin = time.monotonic()
        if scope.binding(self.profile) != self.scope_seal:
            raise legacy.ProbeError("allocation scope changed")
        memory = self.memory_snapshot()
        nodes = scope.devices()
        holders = scope.visible_holders(nodes, guard=guard)
        if scope.binding(self.profile) != self.scope_seal:
            raise legacy.ProbeError("allocation changed during observation")
        known = {member["pid"]: member["start"] for member in owned}
        owned_holders = []
        for pid in holders:
            if known.get(pid) == scope.native.process(pid)["start"]:
                owned_holders.append(pid)
        if time.monotonic() - begin > 3:
            raise legacy.ProbeError("allocation snapshot time budget exceeded")
        return {**memory, "observed_at": at, "scope_seal": self.scope_seal,
                "unknown_gpu_pids": [pid for pid in holders if pid not in owned_holders],
                "owned_gpu_pids": owned_holders, "snapshot_seconds": time.monotonic() - begin,
                "allocation_inventory_qualified": True,
                "physical_host_inventory": "unavailable", "inventory_scope": "allocation-visible-device-handles"}


class AllocationProvider(legacy.ProcscanProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.provider != "vast" or self.mode != "unified":
            raise ValueError("v2 currently requires an explicit Vast unified-memory allocation")
        self.profile = {"provider": self.provider, "instance_id": self.instance_id, "gpu_uuid": self.baseline.gpu_uuid}
        self.scope_seal = scope.binding(self.profile)
        self.allocation_key = "vast:" + self.instance_id + ":" + self.baseline.boot_id

    def _host(self):
        super()._host()
        if scope.binding(self.profile) != self.scope_seal:
            raise legacy.RentalError("allocation binding changed")

    def parse_ref(self, ref):
        return parts(ref, self.scope_seal)

    def native_config(self):
        return {"allocation_profile": self.profile, "scope_seal": self.scope_seal}

    def native_environment(self):
        return {"CONTAINER_ID": self.instance_id}

    def declare(self):
        out = super().declare()
        out.update(receipt_provider=PROVIDER, identity_kind="provider_instance",
                   identity_key=self.allocation_key, provider_host_key=self.allocation_key)
        out["capabilities"].update(native_provider=PROVIDER, gpu_uuid=self.baseline.gpu_uuid,
            cleanup_scope="supervised-job-and-allocation-visible-device-handles-v2",
            physical_memory_cleanup="not asserted", physical_host_inventory="unavailable",
            capacity="fresh CUDA/system/visible-cgroup minimum; no reclaim credit",
            scope_seal=self.scope_seal)
        return out

    def preflight(self, claim):
        try:
            claim_identity(claim["claim_id"], claim["fence"])
            if claim.get("state") != "granted":
                raise legacy.RentalError("claim is not granted")
            self._host()
            sample = self.probe.snapshot()
            limits = visible_limits()
            if ((sample.get("boot_id"), sample.get("gpu_uuid"), sample.get("total_bytes")) != (
                    self.baseline.boot_id, self.baseline.gpu_uuid, self.baseline.total_bytes)
                    or sample.get("scope_seal") != self.scope_seal
                    or sample.get("allocation_inventory_qualified") is not True
                    or not fresh(sample.get("observed_at")) or not fresh(limits.get("observed_at"))
                    or limits.get("boot_id") != self.baseline.boot_id
                    or limits.get("scope") != "visible-cgroup-v2-namespace-root"
                    or type(limits.get("cgroup_inode")) is not int or limits["cgroup_inode"] <= 0):
                raise legacy.RentalError("fresh allocation observation unavailable")
            if sample.get("unknown_gpu_pids") != []:
                return {"result": "refused", "reason": "allocation device occupancy", "cleanup": "pristine", "health": "degraded"}
            free, system, current, maximum, high = (sample.get("free_bytes"), limits.get("system_available_bytes"),
                limits.get("cgroup_current_bytes"), limits.get("cgroup_max_bytes"), limits.get("cgroup_high_bytes"))
            if (not all(count(n) for n in (free, system, current)) or free > self.baseline.total_bytes
                    or "cgroup_max_bytes" not in limits or maximum is not None and not count(maximum)
                    or "cgroup_high_bytes" not in limits or high is not None and not count(high)):
                raise legacy.RentalError("allocation counters invalid")
            available = min(free, system, max(0, maximum - current) if maximum is not None else system,
                            max(0, high - current) if high is not None else system)
            self._host()
            if available < self.required + self.reserve:
                return {"result": "refused", "reason": "insufficient observed allocation headroom", "cleanup": "pristine", "health": "degraded"}
            return {"result": "admitted"}
        except Exception:
            return {"result": "refused", "reason": "allocation capacity observation unavailable", "cleanup": "pristine", "health": "unknown"}

    def cleanup_sample(self, inspection, ref):
        guard = self.parse_ref(ref)[3:5]
        try:
            return self.probe.snapshot(guard=guard)
        except Exception as error:
            # Known live owned work remains negative even if CUDA/proc reads
            # fail. Missing device evidence can never become a positive clean.
            return {"observed_at": utc_now(), "observation_error": type(error).__name__}

    def inspect(self, handle_or_ref):
        answer = super().inspect(handle_or_ref)
        if answer.get("state") != "gone":
            # The actual SDK treats a command return code as completion. Keep
            # the supervised container active while a detached descendant is
            # live; the original primary status remains in the guard and here.
            answer = {**answer, "primary_exit": answer.get("job_exit"), "primary_signal": answer.get("job_signal"),
                      "job_exit": None, "job_signal": None}
        return answer

    def stop(self, handle, grace_seconds):
        # The SDK normally requests 30 seconds; the native operation already
        # stops exact owned descendants immediately and only bounds its drain.
        # Keep that existing <=10s native bound instead of rejecting the SDK's
        # default and forcing an unknown receipt. Legacy v1 remains unchanged.
        if (type(grace_seconds) not in (int, float) or not math.isfinite(grace_seconds)
                or grace_seconds < 0):
            raise legacy.RentalError("finite nonnegative SDK stop grace required")
        return super().stop(handle, min(float(grace_seconds), legacy.MAX_DRAIN_SECONDS))

    def cleanup_verdict(self, inspection, sample):
        return clean_verdict(self.baseline, inspection, sample, self.scope_seal)

    def assess_container(self, handle_or_ref, **kwargs):
        raise legacy.RentalError("v1 observational assessment cannot interpret a v2 scope")

    def telemetry(self):
        try:
            self._host()
            with self.lock:
                refs = list(self.created)
            if len(refs) > 1:
                raise legacy.RentalError("ambiguous native telemetry authority")
            inspection = self._rpc(refs[0]) if refs else None
            guard = self.parse_ref(refs[0])[3:5] if refs else None
            sample = self.probe.snapshot(inspection["members"] if inspection else (), guard=guard)
            return {**sample, "profile": PROVIDER, "baseline": asdict(self.baseline)}, "degraded" if sample["unknown_gpu_pids"] else "healthy"
        except Exception:
            return {"observed_at": utc_now(), "profile": PROVIDER, "observation": "unavailable"}, "unknown"


def factory():
    config = json.loads(Path(selected_config(os.environ)).read_text())
    if config.get("receipt_provider") != PROVIDER:
        raise ValueError("explicit linux-procscan-v2 owner selection required")
    baseline = Baseline(**config["baseline"])
    profile = {"provider": config["provider"], "instance_id": config["instance_id"], "gpu_uuid": baseline.gpu_uuid}
    seal = scope.binding(profile)
    probe = AllocationProbe(baseline.gpu_uuid, mode=config["memory_mode"], library=config["cuda_library"],
                            profile=profile, scope_seal=seal)
    return AllocationProvider(baseline, state_root=config["native_state_root"], probe=probe,
        memory_mode=config["memory_mode"], provider=config["provider"], instance_id=config["instance_id"],
        required_bytes=config["required_bytes"], reserve_bytes=config["reserve_bytes"],
        drain_seconds=config.get("drain_seconds", 10), allowed_job_env=config.get("allowed_job_env", ()))
