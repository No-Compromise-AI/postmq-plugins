# Managed allocation cleanup, version 2

`linux-procscan-v2` is a separate, experimental provider for one exclusively
lent Vast unified-memory allocation. Its SDK entrypoint is
`sdk_provider.py:managed_allocation_factory`. The existing `rental_factory`
continues to mean `linux-procscan-v1`; its unified-memory verdict stays unknown.
Implementation and synthetic controls are not live-device qualification.

The provider ships in the experimental 0.2.1 Linux source bundle; `v2` names its
cleanup profile, not the bundle or plugin version. Use the
[source-bundle quickstart](../../../README.md#build-an-adapter-source-bundle)
and [host integration guide](../README.md#owner-integration-and-compatibility)
before selecting it. Assembly does not register a resource or activate this profile.

## Meaning of a clean receipt

For v2, `verified_clean` means the exact prepared primary has a retained terminal
status, every supervised descendant has ended and been reaped, and three fresh
observations find no unclassified allocation-visible NVIDIA/DRM device handles.
The private supervisor must still be authenticated by process birth, peer
credentials, claim, fence, boot, state-directory inode and allocation scope.
Only then is it dismissed and collected. No cached receipt replaces live proof.
The SDK's ordinary stop request uses its normal grace setting; v2 caps the
exact owned native stop/drain operation at ten seconds. It provides advisory
revocation and does not promise a cooperative checkpoint grace period.
The SDK-facing command status remains pending while supervised descendants
are live. The primary's original terminal status is retained separately and
becomes the command status only when the supervised container is gone.

The scope seal binds the Vast instance, GPU UUID, boot, UID, PID and mount
namespaces, namespace-init birth, visible cgroup inode, proc mount, visible
device-node identities and reviewed native source bytes. The reference is:

`procscan2:<claim>:<fence>:<boot>:<supervisor pid>:<birth>:<directory inode>:<scope seal>`

The complete local `/proc` descriptor scan uses device numbers, so aliases do
not hide a handle. NVML's host PIDs are never interpreted as container PIDs.
Restricted proc visibility, unreadable processes, racing membership, a changed
scope, lost supervisor or unavailable device identity yields unknown. A known
live descendant or unclassified visible device holder prevents clean proof.
The exact authenticated supervisor is the only excluded process: it proves it
inherited no device handles before becoming non-dumpable, and never loads CUDA.

When root lacks permission to inspect a different user's descriptor table,
source distribution 0.2.1 can launch a finite read-only collector under that
process's existing uniform UID/GID. The child drops supplementary groups and
capabilities, disables privilege gain and becomes non-dumpable. It receives
only numeric target/device metadata through private pipes, inherits no other
descriptors or environment, and never opens a device or controls a target.
Every target's birth and credentials are checked before and after inspection;
the child is collected within the existing one-second inventory deadline.
The complete PID set is still checked on both sides. No process is exempted.
Mixed credentials, missing UID-switch support, protected processes, permission
failures, changed identities, malformed results and timeouts remain unknown.
No extra container capabilities or global proc/ptrace settings are required
or changed. This follows the kernel's
[descriptor-access checks](https://man7.org/linux/man-pages/man5/proc_pid_fd.5.html);
it does not bypass them. Collector bytes participate in the allocation scope,
so deployments need a fresh reviewed binding and cannot reinterpret old refs.

This does **not** assert cold physical RAM, a job-local memory charge, complete
physical-host/graphics visibility, cleanup of hidden peers, or enforced
containment. All four enforcement dimensions remain advisory. Trusted jobs
must not deliberately escape supervision or transfer handles to external
services. A credential alone does not establish native proof.

## Capacity is a separate decision

Before every job, require fresh identity-bound CUDA free memory, system
`MemAvailable`, and visible cgroup-v2 namespace-root hard/high-limit headroom.
Use the minimum, with explicit requested bytes plus reserve. Credit neither
reclaimable pages nor swap. Missing counters, hidden membership, an unsupported
profile or insufficient headroom refuses admission. A clean receipt never
certifies that a later or larger job will fit. The CUDA observer's own temporary
primary context is released and its child collected before descriptor scanning.

## Identity and migration

The resource is the **allocation**, not a new alias for a quarantined physical
GPU. Its existing `provider_instance` identity is `vast:<instance>:<boot>`;
the full key is also `provider_host_key`. Only one GPU and one exclusive claim
are supported in this profile. The selected UUID is retained in capabilities
and in the native scope. Capabilities cannot change cleanup meaning.

`receipt_provider` is immutable in PostMQ. There is no in-place v1-to-v2
migration. Existing v1 rows, claims, receipts, quarantines and withdrawals keep
their provider and history. A v2 receipt cannot clear one. Never create a v2
alias to bypass unresolved v1 work or grant concurrent access to the same
allocation. Initial rollout requires a fresh allocation, complete discovery
(including withdrawn rows), explicit canonical-row selection and confirmation
that no conflicting live identity exists. Registration is controlled separately
and an unknown response is inspected rather than blindly retried.

A reconnect uses the same SDK resource gate and append-only journal and the
same fenced native reference. It never creates a second scheduler, repeats an
ambiguous launch, substitutes an owner override for clean evidence, or resets
an unknown epoch. Missing evidence remains retained and unavailable for reuse.

## Qualification boundary

Required live cases are ordinary model completion, exact owned termination,
detached descendants, launcher loss and same-journal reconnect, then a second
claim admitted only after the first clean receipt. Bounded retained/page-locked
memory and insufficient-headroom controls must distinguish cleanup from
capacity. Lost/replaced supervisor, scope change and inaccessible inventory
must remain unknown with no subsequent job. Keep original failures and raw
native/server receipts privately. Privileged global pinned-page OOM, host
resets and cleanup of unrelated processes are outside this provider.
