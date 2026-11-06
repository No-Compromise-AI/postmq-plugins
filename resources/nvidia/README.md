# NVIDIA observation components

Version 0.1.0 contains [inventory](src/probe.py), [CUDA Driver API observation](src/cuda_driver_probe.py)
and [retained-context comparison](src/observer_context_check.py) components. The Linux bundle assembler
composes these with host custody; they do not replace that custody or issue claims themselves.

The driver observer binds typed native signatures and preserves primary and cleanup failures. It must
not silently turn an unavailable CUDA/NVML measurement into clean evidence. The retained-context
comparison is an explicit diagnostic and does not prove admission or whole-host cleanliness. Native
driver calls can block; the owner must apply a separately verified finite execution boundary.

The [CPU ABI fixture](tests/cuda_abi_fixture.c) and synthetic tests need a 64-bit C compiler and no GPU.
No hardware-specific capacity profile, driver, model, rental credential or automatic GB10 UMA cleanup
guarantee is published here. Those require separate workload and lifecycle qualification.
