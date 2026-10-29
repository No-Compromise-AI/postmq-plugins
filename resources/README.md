# Resource integration boundaries

The existing [resource-claim plugin](../README.md#install-the-existing-plugins) runs a job under a PostMQ
claim. This directory contains no new host adapter, driver, hardware certification or launch capability.
It describes how future integrations separate operating-system custody from device-specific behavior.

| Future namespace | Responsibility | Evidence before support is claimed |
| --- | --- | --- |
| `host/linux` | Linux process custody, supervision, cancellation, cleanup and fencing. | Exact OS/runtime, process-tree and failure/restart probes. |
| `host/macos` | macOS process/session custody, lifecycle and cleanup. | Exact OS/client/runtime, sleep/reconnect and custody evidence. |
| `host/windows` | Windows process/session custody, lifecycle and cleanup. | Exact OS/client/runtime, process-tree and termination evidence. |
| `nvidia` | NVIDIA inventory, capacity and workload profile on a qualified host. | Exact device, driver/runtime, workload and cleanup evidence. |
| `apple` | Apple hardware capacity/workload profile on a qualified host. | Exact device, OS/runtime and workload observations. |
| `amd` | AMD inventory, capacity and workload profile on a qualified host. | Exact device, driver/runtime and workload observations. |
| `intel` | Intel inventory, capacity and workload profile on a qualified host. | Exact device, driver/runtime and workload observations. |

These are directory design names, not working integrations. A device profile composes with a qualified
host adapter; it cannot replace custody or infer that a previous job stopped. Idle utilization, a missing
heartbeat, a released lease or API success is not cleanup proof. Support is specific to tested combinations.

Reuse canonical claims, membership, authorization and fencing. Never add a competing lock/queue or
widen approved message audiences. Resource selection, process launch and spending remain explicit
authorities. See [core requirements](../spec/pmp-core.md) and [conformance](../conformance/README.md).
