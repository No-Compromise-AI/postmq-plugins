"""Finite query-only comparison inside one retained CUDA context.

This experiment does not qualify cleanup, admission or a pristine host. It
uses the existing probe's context/identity/cleanup path, with no explicit
device or pinned allocation. Kernel/driver IO has no hard deadline.
"""

import argparse
import json
from pathlib import Path
import signal
import sys
import time

if __package__:
    from . import cuda_driver_probe as probe
else:
    # Also work under isolated Python; do not import from the current directory.
    import importlib.util
    import hashlib
    path=Path(__file__).with_name('cuda_driver_probe.py').resolve()
    existing=sys.modules.get('cuda_driver_probe')
    if existing is not None and Path(getattr(existing,'__file__','')).resolve()==path:
        probe=existing
    else:
        name='postmq_cuda_observer_'+hashlib.sha256(str(path).encode()).hexdigest()
        spec=importlib.util.spec_from_file_location(name,path)
        probe=importlib.util.module_from_spec(spec);sys.modules[name]=probe;spec.loader.exec_module(probe)


def observe(library, gpu_uuid, *, sample_fn=probe.sample, reader_factory=probe.Driver,
            proc_root=Path("/proc"), sleep_fn=time.sleep, on_event=None):
    rows = []

    def collect(held):
        canonical = str(Path(library).resolve(strict=True))
        if (held.get("gpu_uuid") != gpu_uuid or held.get("library") != canonical
                or held.get("allocation_bytes_each") != 0 or held.get("phase") != "held"):
            raise probe.ProbeError("held_observer_identity")
        if on_event:
            on_event({**held, "held_observed_at": time.monotonic()})
        # Driver only binds existing function signatures. It neither initializes
        # CUDA nor retains a second context; memory() uses this thread's current
        # context, held by sample_fn throughout the callback.
        reader = reader_factory(canonical)
        for index in range(6):
            if on_event:
                on_event({"phase": "query_intent", "index": index,
                          "query_announced_at": time.monotonic()})
            started = time.monotonic()
            memory = reader.memory()
            ended = time.monotonic()
            os_memory = {}
            for line in (Path(proc_root) / "meminfo").read_text().splitlines():
                key, _, rest = line.partition(":")
                if key not in ("MemFree", "MemAvailable", "Mlocked", "Unevictable", "SUnreclaim"):
                    continue
                fields = rest.split()
                if len(fields) != 2 or fields[1] != "kB" or not fields[0].isdigit():
                    raise probe.ProbeError("meminfo")
                os_memory[key] = int(fields[0]) * 1024
            if not {"MemFree", "MemAvailable"}.issubset(os_memory):
                raise probe.ProbeError("meminfo")
            rows.append({"index": index, "query_start": started, "query_end": ended,
                         "os_read_end": time.monotonic(),
                         **memory, "os_memory_bytes": os_memory})
            if on_event:
                on_event({"phase": "observation", **rows[-1]})
            if index < 5:
                sleep_fn(0.2)

    answer = None
    try:
        answer = sample_fn(library, gpu_uuid, allocation_bytes=0, hold_seconds=1,
                           on_hold=collect, proc_root=proc_root)
        if len(rows) != 6 or answer.get("owned_primary_context_released") is not True:
            raise probe.ProbeError("retained_observation_incomplete")
    except Exception as error:
        error.observations = rows.copy()
        error.probe = answer
        raise
    return {"experiment": "six queries in one retained context; diagnostic only",
            "explicit_allocation_bytes": 0, "observations": rows, "probe": answer}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_DFL)
    signal.pthread_sigmask(signal.SIG_UNBLOCK, (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
    try:
        result = observe(args.library, args.gpu_uuid,
                         on_event=lambda row: print(json.dumps(row), flush=True))
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__,
                          "phase": "error", "code": getattr(error, "code", None),
                          "errno": getattr(error, "errno", None),
                          "stage": getattr(error, "stage", "retained_observer_failed"),
                          "observations_completed": len(getattr(error, "observations", [])),
                          "observations": getattr(error, "observations", []),
                          "probe": getattr(error, "probe", None),
                          "cleanup": getattr(error, "cleanup", None),
                          "primary": getattr(error, "primary", None)}), flush=True)
        return 1
    print(json.dumps({"phase": "complete", **result}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
