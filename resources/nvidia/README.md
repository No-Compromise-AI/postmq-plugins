# NVIDIA observation components

The experimental [0.2.0 adapter source distribution](../host/README.md) includes
[inventory](src/probe.py), [CUDA Driver API observation](src/cuda_driver_probe.py),
[retained-context comparison](src/observer_context_check.py) and
[allocation-observation controls](src/native_cuda_checks.py). The Linux bundle assembler composes
these with host custody; they do not replace that custody or issue claims themselves.

The driver observer binds typed native signatures and preserves primary and cleanup failures. It must
not silently turn an unavailable CUDA/NVML measurement into clean evidence. The retained-context
comparison is an explicit diagnostic and does not prove admission or whole-host cleanliness. Native
driver calls can block; the owner must apply a separately verified finite execution boundary.

The [CPU ABI fixture](tests/cuda_abi_fixture.c) and synthetic tests need a 64-bit C compiler and no GPU.
The separate Linux [managed allocation v2 profile](../host/linux/MANAGED-ALLOCATION.md) uses NVIDIA
observations for an exclusive Vast UMA allocation, including GB10. It distinguishes allocation-visible
cleanup from fresh capacity and preserves advisory enforcement; NVIDIA inventory or idle utilization
alone never permits reuse. The legacy rental provider keeps its UMA verdict unknown.

No hardware-profile package, driver, model or rental credential is included. Source/CPU controls do not
certify a particular device, model, driver or rental; those require workload and lifecycle qualification.
