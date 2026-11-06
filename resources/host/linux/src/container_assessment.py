"""Local container observations; deliberately NOT a receipt or admission policy.

These v1 observational facts never become a receipt. The separate versioned
managed-allocation provider defines its own explicit cleanup/admission policy.
No process/device controls, broker, reservation, journal or cached proof.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
import os
from pathlib import Path

if __package__:
    from .evidence import BOOT, GPU, claim_identity, integer, utc_now
else:
    from evidence import BOOT, GPU, claim_identity, integer, utc_now


PROFILE = "container-observation-v1"
MAX_AGE_SECONDS = 10


@dataclass(frozen=True)
class LocalBinding:
    claim_id: str
    fence: int
    boot_id: str
    supervisor_pid: int
    supervisor_start: int
    directory_inode: int
    gpu_uuid: str
    total_bytes: int

    def __post_init__(self):
        claim_identity(self.claim_id, self.fence)
        if self.fence > 2**63 - 1 or not BOOT.fullmatch(self.boot_id) or not GPU.fullmatch(self.gpu_uuid):
            raise ValueError("local native identity invalid")
        for name in ("supervisor_pid", "supervisor_start", "directory_inode"):
            integer(getattr(self, name), name, 1)
        if not count(self.total_bytes) or self.total_bytes == 0:
            raise ValueError("device total outside counter bounds")
        if self.supervisor_pid >= 2**31:
            raise ValueError("native PID outside platform bounds")

    @property
    def ref(self):
        return "procscan:" + ":".join(str(getattr(self, k)) for k in (
            "claim_id", "fence", "boot_id", "supervisor_pid", "supervisor_start", "directory_inode"))


def fresh(value, now):
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return observed.tzinfo is not None and 0 <= (now - observed).total_seconds() <= MAX_AGE_SECONDS
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def count(value):
    return type(value) is int and 0 <= value <= 2**63 - 1


def visible_limits(proc=Path("/proc"), cgroup=Path("/sys/fs/cgroup")):
    """Read only a cgroup-v2 namespace root, with no reclaim-credit assumption.

    This intentionally refuses arbitrary non-root membership instead of guessing
    a mount/path mapping. Hidden ancestor limits remain outside this observation.
    """
    proc, cgroup = Path(proc), Path(cgroup)
    at = utc_now()
    boot = (proc / "sys/kernel/random/boot_id").read_text().strip()
    before = (proc / "self/cgroup").read_text()
    if before.strip() != "0::/":
        raise ValueError("namespace-root cgroup v2 required for this observer")
    inode = cgroup.stat().st_ino
    def contains_observer():
        rows = (cgroup / "cgroup.procs").read_text().split()
        return all(x.isdecimal() for x in rows) and str(os.getpid()) in rows
    if not contains_observer():
        raise ValueError("observer is not in the visible cgroup")
    rows = [line.split() for line in (proc / "meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")]
    if len(rows) != 1 or len(rows[0]) != 3 or rows[0][2] != "kB" or not rows[0][1].isdecimal():
        raise ValueError("system availability unavailable")
    system = int(rows[0][1]) * 1024
    raw_limit = (cgroup / "memory.max").read_text().strip()
    raw_high = (cgroup / "memory.high").read_text().strip()
    raw_current = (cgroup / "memory.current").read_text().strip()
    if (not raw_current.isdecimal() or raw_limit != "max" and not raw_limit.isdecimal()
            or raw_high != "max" and not raw_high.isdecimal()):
        raise ValueError("visible cgroup counters invalid")
    current = int(raw_current)
    maximum = None if raw_limit == "max" else int(raw_limit)
    high = None if raw_high == "max" else int(raw_high)
    if (not count(system) or not count(current) or maximum is not None and not count(maximum)
            or high is not None and not count(high)):
        raise ValueError("visible capacity outside counter bounds")
    if ((proc / "self/cgroup").read_text() != before or cgroup.stat().st_ino != inode
            or not contains_observer() or (proc / "sys/kernel/random/boot_id").read_text().strip() != boot):
        raise ValueError("visible cgroup identity changed during observation")
    return {"observed_at": at, "boot_id": boot,
            "system_available_bytes": system, "cgroup_current_bytes": current,
            "cgroup_max_bytes": maximum, "cgroup_high_bytes": high, "cgroup_inode": inode,
            "scope": "visible-cgroup-v2-namespace-root", "hidden_ancestor_limits": "unavailable"}


def assess(binding, inspection, sample, limits, *, memory_mode, required_bytes, reserve_bytes,
           compute_sample=None, _test_now=None):
    """Separate local facts. An enough-capacity result cannot clear live work.

    LocalBinding is the requested native identity, not server admission/profile
    binding. Results never become a cleanup receipt, including in a host PID NS.
    """
    integer(required_bytes, "required_bytes", 1)
    integer(reserve_bytes, "reserve_bytes", 1)
    if memory_mode not in ("discrete", "unified"):
        raise ValueError("explicit memory mode required")
    if required_bytes + reserve_bytes > 2**63 - 1:
        raise ValueError("requested capacity exceeds counter bounds")
    at = utc_now() if _test_now is None else _test_now
    if not isinstance(at, str):
        raise ValueError("assessment timestamp needs an ISO8601 string")
    now = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("assessment timestamp needs timezone")
    out = {"profile": PROFILE, "observed_at": at, "requested_binding": {**asdict(binding), "native_ref": binding.ref},
           "native_identity_observed": False, "device_identity_observed": False, "compute_identity_observed": False,
           "managed_cleanup": {"state": "unknown", "scope": "supervised-descendants-only"},
           "observed_capacity": {"state": "unknown", "required_bytes": required_bytes,
                                 "reserve_bytes": reserve_bytes, "memory_mode": memory_mode},
           "visible_compute": {"state": "unknown"},
           "not_a_receipt": True, "can_authorize_handoff": False,
           "limitations": ["no server-frozen profile binding", "hidden host activity unavailable",
                           "graphics inventory unavailable", "hidden ancestor limits unavailable",
                           "same-UID supervision advisory", "fresh next-job admission still required"]}
    if memory_mode == "unified":
        out["limitations"].append("unified observer creates a transient CUDA context")
    inspection, sample, limits, compute = (x if isinstance(x, dict) else {}
                                          for x in (inspection, sample, limits, compute_sample))
    members = inspection.get("members")
    valid_members = isinstance(members, list) and len(members) <= 512 and all(
        isinstance(x, dict) and type(x.get("pid")) is int and 0 < x["pid"] < 2**31
        and type(x.get("start")) is int and x["start"] > 0 for x in members)
    if valid_members:
        valid_members = (len({x["pid"] for x in members}) == len(members)
                         and all(x["pid"] != binding.supervisor_pid for x in members))
    if (inspection.get("ref") == binding.ref and fresh(inspection.get("observed_at"), now)
            and valid_members and not inspection.get("error")):
        out["native_identity_observed"] = True
        if members or inspection.get("state") == "alive":
            out["managed_cleanup"].update(state="live", member_count=len(members))
        elif inspection.get("state") == "gone" and inspection.get("job_ended") is True:
            exit_code, job_signal = inspection.get("job_exit"), inspection.get("job_signal")
            terminal = ((type(exit_code) is int and 0 <= exit_code <= 255) +
                        (type(job_signal) is int and 1 <= job_signal <= 64) +
                        (inspection.get("job_aborted") is True))
            invalid = (exit_code is not None and not (type(exit_code) is int and 0 <= exit_code <= 255)
                       or job_signal is not None and not (type(job_signal) is int and 1 <= job_signal <= 64))
            if terminal == 1 and not invalid:
                out["managed_cleanup"].update(state="ended", member_count=0)
    device_identity = (sample.get("boot_id"), sample.get("gpu_uuid")) == (binding.boot_id, binding.gpu_uuid)
    compute_identity = (compute.get("boot_id"), compute.get("gpu_uuid")) == (binding.boot_id, binding.gpu_uuid)
    if compute_identity and fresh(compute.get("observed_at"), now):
        out["compute_identity_observed"] = True
        pids = compute.get("unknown_gpu_pids")
        if isinstance(pids, list) and all(type(p) is int and 0 < p < 2**31 for p in pids):
            if compute.get("inventory_namespace_qualified") is True:
                out["visible_compute"]["state"] = "unclassified" if pids else "none_additional_visible"
                out["visible_compute"]["reported_unclassified_count"] = len(pids)
            else:
                out["visible_compute"]["unqualified_reported_rows"] = len(pids)
            out["visible_compute"]["pid_namespace_qualified"] = compute.get("inventory_namespace_qualified") is True
    if device_identity and fresh(sample.get("observed_at"), now):
        out["device_identity_observed"] = True
        free, total = sample.get("free_bytes"), sample.get("total_bytes")
        system, current, maximum = (limits.get(k) for k in (
            "system_available_bytes", "cgroup_current_bytes", "cgroup_max_bytes"))
        high = limits.get("cgroup_high_bytes")
        valid_limits = (limits.get("boot_id") == binding.boot_id and fresh(limits.get("observed_at"), now)
                        and limits.get("scope") == "visible-cgroup-v2-namespace-root"
                        and type(limits.get("cgroup_inode")) is int and limits["cgroup_inode"] > 0
                        and all(count(v) for v in (system, current))
                        and "cgroup_max_bytes" in limits and (maximum is None or count(maximum))
                        and "cgroup_high_bytes" in limits and (high is None or count(high)))
        measurement = sample.get("measurement")
        if (count(free) and count(total) and 0 <= free <= total and total == binding.total_bytes
                and isinstance(measurement, str) and 0 < len(measurement) <= 200
                and (memory_mode == "discrete" or valid_limits)):
            available = free
            scope = "device-memory-only; discrete host RAM request not sized"
            if memory_mode == "unified":
                hard = None if maximum is None else max(0, maximum - current)
                available = min(free, system) if hard is None else min(free, system, hard)
                scope = "device/system/visible-cgroup-minimum; no reclaim credit"
                out["observed_capacity"].update(
                    system_available_bytes=system, visible_cgroup_hard_unused_bytes=hard,
                    visible_cgroup_high_bytes=high,
                    throttling_possible=high is not None and current + required_bytes + reserve_bytes > high)
            out["observed_capacity"].update(
                state="enough" if available >= required_bytes + reserve_bytes else "insufficient",
                available_bytes=available, device_free_bytes=free, measurement=measurement, scope=scope)
    return out
