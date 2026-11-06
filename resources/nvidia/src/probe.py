"""Read-only discrete CUDA device observations; no framework/model initialization.

GB10 unified-memory observations must use a separately qualified CUDA probe.
NVML/nvidia-smi free memory is not substituted for that probe.
"""

from pathlib import Path
import csv
import io
import subprocess

if __package__:
    from .evidence import GPU, utc_now
else:  # Direct source-test invocation from this directory.
    from evidence import GPU, utc_now


class ProbeError(RuntimeError):
    pass


COMMAND_TIMEOUT = 2.0


def command(argv):
    # Never include subprocess output or argv in an exception: runtime text may
    # contain secrets. No shell, no input, finite timeout, and no environment dump.
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=COMMAND_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        raise ProbeError("native observation command failed") from None
    missing_unit = (argv[:3] == ["systemctl", "--user", "show"]
                    and result.returncode == 4 and "LoadState=not-found" in result.stdout.splitlines())
    if result.returncode and not missing_unit:
        raise ProbeError("native observation command refused")
    return result.stdout


class NvidiaProbe:
    def __init__(self, gpu_uuid, *, runner=command, proc_root=Path("/proc")):
        if not GPU.fullmatch(gpu_uuid):
            raise ValueError("full GPU UUID required")
        self.gpu_uuid, self.runner, self.proc_root = gpu_uuid, runner, Path(proc_root)

    def snapshot(self, owned_cgroup=None):
        boot = (self.proc_root / "sys/kernel/random/boot_id").read_text().strip()
        out = self.runner(["nvidia-smi", "-i", self.gpu_uuid,
                           "--query-gpu=uuid,memory.free,memory.total", "--format=csv,noheader,nounits"])
        rows = list(csv.reader(io.StringIO(out)))
        if len(rows) != 1 or len(rows[0]) != 3 or rows[0][0].strip() != self.gpu_uuid:
            raise ProbeError("device identity is not a single matching GPU")
        try:
            free, total = [int(value.strip()) * 1024 ** 2 for value in rows[0][1:]]
        except ValueError:
            raise ProbeError("device memory counters unavailable") from None
        if not 0 <= free <= total or total == 0:
            raise ProbeError("device memory counters invalid")
        apps = self.runner(["nvidia-smi", "-i", self.gpu_uuid,
                            "--query-compute-apps=pid,gpu_uuid", "--format=csv,noheader,nounits"])
        unknown = []
        for row in csv.reader(io.StringIO(apps)):
            if len(row) != 2 or row[1].strip() != self.gpu_uuid:
                raise ProbeError("GPU process inventory invalid")
            try:
                pid = int(row[0].strip())
                if pid <= 0:
                    raise ValueError
                text = (self.proc_root / str(pid) / "cgroup").read_text()
            except (ValueError, OSError):
                # A racing or inaccessible process is not assumed gone.
                raise ProbeError("GPU process ownership cannot be established") from None
            paths = [line[3:] for line in text.splitlines() if line.startswith("0::")]
            if len(paths) != 1:
                raise ProbeError("GPU process has no unique cgroup v2 path")
            if owned_cgroup is None or not (paths[0] == owned_cgroup or paths[0].startswith(owned_cgroup + "/")):
                unknown.append(pid)
        return {"observed_at": utc_now(), "boot_id": boot, "gpu_uuid": self.gpu_uuid,
                "free_bytes": free, "total_bytes": total, "unknown_gpu_pids": sorted(unknown),
                "measurement": "nvidia-smi-discrete", "graphics_process_inventory": "unavailable"}
