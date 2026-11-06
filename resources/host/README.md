# Host adapters 0.2.0 — experimental source distribution

These host providers implement process custody and cleanup for the existing PostMQ claim SDK.
They execute on the resource host. A caller may use another OS, provided its separately configured
transport reaches the owner entrypoint. Copying an adapter does not grant SSH or resource access.

| Source | Entry in an assembled bundle | Boundary |
| --- | --- | --- |
| [Linux](linux/src/sdk_provider.py) | `sdk_provider.py:factory` | Delegated cgroup v2 and systemd user scopes; exact owned guard/job identities. |
| [Linux rental](linux/src/linux_procscan.py) | `sdk_provider.py:rental_factory` | Cooperative process tracking when cgroups are unavailable; weaker cleanup guarantees. |
| [macOS](macos/src/entry.py) | `entry.py:factory` | Owned session and kqueue fork/exit observations; unknown descendants retain evidence. |
| [Windows](windows/src/entry.py) | `entry.py:factory` | Job Objects, exact process handles/births, owner ACLs and machine-bound kernel identity. |
| [Shared lifecycle](common/src/lifecycle/controller.py) | Library callbacks | Admission closure, cancellation obligations and bounded recovery backoff. |

Hardware observation composes with host custody. Linux's current GPU profile uses the [NVIDIA
components](../nvidia/README.md). A CUDA, MPS or MLX workload requires a separately pinned runtime,
recipe, capacity policy and device qualification. No model, driver or device permission is installed here.

## Assemble and verify without executing a provider

Use Python 3.11+ in this checkout. Choose a new output directory:

```sh
python3 -I -B tools/build_resource_adapter.py assemble macos /tmp/example-postmq-macos
```

The command prints the manifest's SHA256. Save that value outside the bundle and verify it before use:

```sh
python3 -I -B tools/build_resource_adapter.py verify /tmp/example-postmq-macos --manifest-sha256 REVIEWED_SHA256
```

Targets are `linux`, `macos` and `windows`. Assembly creates source files and a closed manifest only.
It refuses an existing output, unrecognized target or source symlink. Verification refuses a changed
manifest, modified source, extra files or symlinks. Use an independently reviewed checkout before
assembly. A hash proves correspondence with the selected bytes; it does not authenticate a publisher.
No SDK is vendored, no resource is registered and no job runs. Existing generated plugin content is unchanged.

Use isolated, no-bytecode Python (`-I -B`) for the SDK and provider helpers. Guards launched here also
use `-B`. The verifier deliberately rejects unexpected caches rather than silently accepting them.
If a write fails, a partial source output may remain; inspect it. Assembly never overwrites that directory
or automatically removes files on a retry.

The assembler retains each provider's sibling-module layout so its guard and source-digest checks
remain intact. Linux combines host custody with the selected NVIDIA observers at assembly time;
the source namespaces remain separate. Shared lifecycle callbacks are library components and are not
automatically attached to a source bundle or OS startup service.

## Owner integration and compatibility

Use the [existing claim plugin](../../plugins/postmq-claim/skills/claim/SKILL.md) and its documented
provider contract. The resource owner must select the exact canonical resource and adapter membership,
configure its private host policy, pin runtime/source/recipe files and verify that SDK/provider combination.
The adapter is not a second queue, credential broker or remote-access installer. Its local gate complements
the canonical claim/fence; it cannot authorize a workload the server did not grant.

Windows `entry.py:factory` is intended for the current stock SDK's native Windows gate. A legacy SDK
platform/network shim is deliberately excluded. Public contract tests use the actual generated SDK's
loader, declaration checks and journal parser with synthetic native observations; that is not a live
Windows launch pass or certification of every SDK version. This is not an in-place upgrade tool for an
existing installation: retain original source, native state and canonical SDK history until a separately
reviewed quiescent migration selects the new combination.

The macOS provider reads its owner-selected `POSTMQ_MACOS_CONFIG`. Windows uses the native owner's
private `owner.json`; Linux uses `POSTMQ_LINUX_CONFIG` for its owner-selected baseline and settings.
`POSTMQ_GB10_CONFIG` is a legacy alias; conflicting values are refused. These are operational
inputs, not repository fixtures. Read the provider's parser for its closed policy fields; do not copy another
installation's identities, journals, state directories or credentials. A complete portable installer and
SDK-version compatibility certification are separate work; this source distribution does not claim them.
The Windows CPU-fixture branch requires an already verified lifecycle lending plan supplied by the owner
integration. The stock SDK's argument-free factory does not initialize that plan; raw CPU-fixture use refuses.

Mac and Windows use the existing `generic` receipt category for their logical host-job identities.
Their native evidence is stronger than an unobserved process group, but the category alone is not proof.
Declarations report stable host identity and the local owner UID/SID to the authorized workspace;
this is operational metadata, never public-repository fixture data. Jobs deliberately do not inherit
`POSTMQ_*` variables, including claim-context variables, to avoid passing credentials into workloads.
These adapters do not implement the optional `observe_execution` hook; do not advertise enforced device
binding. Architecture metadata is observed, not a claim of native qualification on every architecture.

Mac identity is cached per native observer instance because a reboot ends that process; source and epoch
checks remain fresh. Inspection/status failures still fail closed through the SDK's bounded-method
fallback. Slow IO or a stale guard heartbeat can therefore abort a job and require owner recovery.
Snapshot rollback, cloning an active installation and reusing its live state are not supported continuity.

Unknown or interrupted cleanup remains unknown until exact native/server evidence resolves it. A
withdrawn resource is not reopened. GPU idle, host reachability or lease expiry alone never proves cleanup.
Unified-memory rental GPU automatic reuse is not qualified by these sources.

The experimental Linux rental factory also accepts `provider: "vast"`. Its owner configuration must
bind `instance_id` to the current container's `CONTAINER_ID`; workload environments strip `VAST_*`
values. This checks the selected container identity, not provider ownership, host isolation or clean GPU
memory. Supply provider configuration privately. No provider API key, account integration or rental
creation is included.

Windows native observations use x64 layouts. ARM64 Windows execution is unqualified; an observed
architecture field is not an ABI support declaration. macOS reports the OS's architecture vocabulary.
The source-boundary scanner uses heuristics and supplements human review; it cannot certify every
possible credential, hostname or personal identifier.

## Testing and virtual machines

Run the root [public gate](../../README.md#contribute-and-verify). The public fixtures use synthetic
identifiers and no credentials, private checkout, hypervisor, GPU or live PostMQ endpoint. The CUDA ABI
fixture is a small CPU shared library, not a CUDA driver. Linux-owned fork checks run on Linux; other OSes
run the synthetic controls without claiming Linux kernel qualification. Windows parser controls do not
claim native Windows custody. macOS controls use explicit fake observations, not live Metal workloads.

A VM can be represented by the existing resource/claim abstraction. Its adapter must bind the exact
hypervisor and guest incarnation and budget its CPU/RAM/devices. Starting or stopping a guest belongs
to the owner integration; guest custody and host capacity remain separate checks. This repository does
not yet ship a hypervisor lifecycle provider or claim that physical GPU passthrough is configured.
One reusable VM per guest OS is sufficient for ordinary tests. Keep small test receipts; retain another
guest only for a concrete unresolved diagnostic need. Reinstall/restore invalidates prior installation
identity and must never masquerade as continuity of an old claim or native epoch.

## Public boundary and provenance

This tree contains reusable implementation and synthetic tests contributed by No Compromise AI under
the root [Apache-2.0 license](../../LICENSE). Operational profiles, machine addresses, account/claim IDs,
model assets, credentials, journals, private reviews and installation receipts are excluded. Keep those
in the relevant private application or service repository. No private evidence is required to run public checks.

Public tests establish the stated source/CPU contracts. They do not certify a particular GPU, rental,
model, client, sleep/restart configuration or unattended deployment. A release and stronger compatibility
claim require reviewed final sources and repeatable qualification for the stated combination.

The separate experimental [managed allocation v2 profile](linux/MANAGED-ALLOCATION.md)
adds allocation-scoped UMA cleanup through `sdk_provider.py:managed_allocation_factory`.
It preserves v1 semantics and requires separate live qualification; no in-place
provider migration or physical-memory cleanup claim is made.
