# Resource adapters and observation components

The existing [resource-claim plugin](../README.md#install-the-existing-plugins) runs a job under a PostMQ
claim. The experimental 0.2.0 source distribution contains maintained [host adapters](host/README.md)
and [NVIDIA observation components](nvidia/README.md). Its version is declared in the
[bundle builder](../tools/build_resource_adapter.py), independently of generated plugin versions.
The providers implement the existing claim SDK's interface. Start with the root
[assembly and verification example](../README.md#build-an-adapter-source-bundle).

Choose the provider by the resource host's OS and available supervision. A remote caller can use
Linux, macOS or Windows through a separately configured owner entrypoint and transport. That does not
grant remote access or provide a complete installer, model runtime or hardware certification.

| Namespace | Responsibility | Evidence before support is claimed |
| --- | --- | --- |
| `host/linux` | Linux process custody, supervision, cancellation, cleanup and fencing. | Exact OS/runtime, process-tree and failure/restart probes. |
| [Managed allocation v2](host/linux/MANAGED-ALLOCATION.md) | Optional Linux profile for allocation-visible cleanup of one exclusive Vast UMA allocation, including GB10; fresh capacity is separate. | Qualified visibility, native custody, clean-before-readmission and unknown/fault controls for the selected combination. |
| `host/macos` | macOS process/session custody, lifecycle and cleanup. | Exact OS/client/runtime, sleep/reconnect and custody evidence. |
| `host/windows` | Windows process/session custody, lifecycle and cleanup. | Exact OS/client/runtime, process-tree and termination evidence. |
| `nvidia` | NVIDIA inventory, capacity and workload profile on a qualified host. | Exact device, driver/runtime, workload and cleanup evidence. |
| `apple` | Apple hardware capacity/workload profile on a qualified host. | Exact device, OS/runtime and workload observations. |
| `amd` | AMD inventory, capacity and workload profile on a qualified host. | Exact device, driver/runtime and workload observations. |
| `intel` | Intel inventory, capacity and workload profile on a qualified host. | Exact device, driver/runtime and workload observations. |

The Linux, macOS and Windows source directories are implemented; Apple, AMD, Intel and device-profile
release directories remain proposed. The managed-allocation profile is an implemented Linux provider,
not a separate hardware-profile package. A device profile composes with a qualified
host adapter; it cannot replace custody or infer that a previous job stopped. Idle utilization, a missing
heartbeat, a released lease or API success is not cleanup proof. Support is specific to tested combinations.

The legacy `linux-procscan-v1` rental provider keeps UMA cleanup unknown. The separate
`linux-procscan-v2` profile permits clean receipts only for its documented supervised-job and
allocation-visible device-handle proof. It retains advisory enforcement and does not certify physical
memory cleanup. Existing identities, unresolved receipts and quarantines are preserved; see the
[profile and migration requirements](host/linux/MANAGED-ALLOCATION.md).

Reuse canonical claims, membership, authorization and fencing. Never add a competing lock/queue or
widen approved message audiences. Resource selection, process launch and spending remain explicit
authorities. See [core requirements](../spec/pmp-core.md) and [conformance](../conformance/README.md).
