"""Pure native evidence rules. No coordinator, process or device side effects."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re


ULID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}\Z")
BOOT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
GPU = re.compile(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(name + " must be an integer >= " + str(minimum))
    return value


def positive_seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be finite and positive")
    return value


def claim_identity(claim_id, fence):
    if not isinstance(claim_id, str) or not ULID.fullmatch(claim_id):
        raise ValueError("invalid claim id")
    integer(fence, "fence", 1)


@dataclass(frozen=True)
class Baseline:
    boot_id: str
    gpu_uuid: str
    free_bytes: int
    total_bytes: int
    tolerance_bytes: int
    observed_at: str

    def __post_init__(self):
        if not BOOT.fullmatch(self.boot_id) or not GPU.fullmatch(self.gpu_uuid):
            raise ValueError("baseline requires a boot id and full GPU UUID")
        integer(self.free_bytes, "free_bytes", 1)
        integer(self.total_bytes, "total_bytes", 1)
        integer(self.tolerance_bytes, "tolerance_bytes")
        if self.free_bytes > self.total_bytes or self.tolerance_bytes >= self.free_bytes:
            raise ValueError("invalid baseline memory bounds")
        date = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("baseline needs a timezone")


def memory_returned(baseline, sample):
    """Identity mismatches are unknown, never a successful zero-byte reading."""
    if (sample.get("boot_id"), sample.get("gpu_uuid"), sample.get("total_bytes")) != (
            baseline.boot_id, baseline.gpu_uuid, baseline.total_bytes):
        return None
    free = sample.get("free_bytes")
    if type(free) is not int or not 0 <= free <= baseline.total_bytes:
        return None
    return free >= baseline.free_bytes - baseline.tolerance_bytes


def cleanup_verdict(baseline, inspection, sample, charge_tolerance):
    """Live process evidence dominates a journal entry saying nothing ran."""
    if inspection.get("state") == "alive":
        return "cleanup_failed", "owned scope is still populated"
    if inspection.get("state") != "gone":
        return "unknown", "scope identity or counters cannot be established"
    current = inspection.get("memory_current")
    if type(current) is not int or current < 0:
        return "unknown", "final cgroup memory.current is unavailable"
    returned = memory_returned(baseline, sample)
    if returned is None or sample.get("unknown_gpu_pids") is None:
        return "unknown", "device identity or process inventory is unavailable"
    if sample["unknown_gpu_pids"]:
        return "cleanup_failed", "unknown GPU occupancy prevents a clean receipt"
    if current > charge_tolerance or not returned:
        return "residue", "scope charge or device memory has not returned to its recorded baseline"
    return "verified_clean", "scope empty; memory.current within tolerance; device at baseline"


def selected_config(environment):
    """Prefer the generic Linux setting; reject conflicting legacy configuration."""
    current=environment.get('POSTMQ_LINUX_CONFIG')
    legacy=environment.get('POSTMQ_GB10_CONFIG')
    if current and legacy and current!=legacy:
        raise ValueError('conflicting Linux provider configuration paths')
    value=current or legacy
    if not isinstance(value,str) or not value:
        raise ValueError('POSTMQ_LINUX_CONFIG must select an owner configuration')
    return value
