# Conformance and evidence

This directory contains no released PmP conformance runner and certifies no client, SDK, host or adapter.
Repository checks validate documentation, ownership and workflow policy, not runtime behavior.

Start with [core requirements](../spec/pmp-core.md) and proposed [A2A cases PA-01 through PA-20](../interop/a2a/profile.md#acceptance-cases).
These are design acceptance requirements, not executed results. Qualification needs a versioned implementation
and reproducible runner.

Every claim identifies source revision, protocol/schema/profile, client/adapter, host/runtime, capabilities,
authority/prerequisites and commands. Retain behavioral failures and corrected passing evidence. Missing
prerequisites/timeouts are not passes. Use disposable synthetic data and sanitized logs, never credentials,
customer transcripts or private environment identifiers.

Distinguish discovery/observation, routed delivery, model consumption, admission, process custody,
cancellation acknowledgement and cleanup proof. Model completion alone proves none of the later boundaries.
Exercise duplicates, stale grants/leases, competing claims, fencing, lost acknowledgements, restart, expiry,
return and recovery. Client-dependent capabilities need native boundary tests, not a new mock.

Report passed, failed, skipped and unsupported cases separately with reasons/artifacts. Required cases
cannot pass with skips. Qualification is scoped to the actual tested combination. Publishing a profile,
implementing it and authorizing deployment are separate decisions.

## Independent implementation and release gate

The [charter](../CHARTER.md) requires practical independence, beyond a permissive license.
Before the first portable interoperability release, complete stage 1 (candidate wire freeze)
in the [specification index](../spec/README.md#publication-stages), including public operation/schema/error,
authority, state, transport, migration and case/fixture definitions. Under separate implementation
authority, stage 2 builds the implementations and runner; stage 3 qualifies them as follows:

1. Identify roles: a **coordinator** owns canonical acceptance, authority and persisted state;
   a **participant client/adapter** originates or receives operations under that coordinator;
   an optional **gateway** maps a declared external binding to that same authority. Build two
   independent coordinators (A and B) and two independent participant implementations (a and b)
   for the selected profile. One set may be PostMQ. Coordinator B must run from public artifacts
   with its own authority/storage and no PostMQ service or private dependency. Participants
   use their selected coordinator; they do not need their own competing authority store.
   Disclose teams, shared libraries and engines. A second wrapper around the same protocol
   engine is not independent. Shared transport/cryptographic libraries are not by themselves
   disqualifying; coordinator state/authority and participant protocol behavior are independently built.
2. In deployment A, enroll a and b into one authorized disposable workspace under coordinator A;
   test a→b send/exact reply and b→a send/exact reply, plus supported correction/handoff. Repeat
   in a separate deployment under coordinator B. Each deployment has its own grants, records
   and resource authority; no message crosses coordinators or workspaces. Run the same public
   applicable positive/negative, persistence/restart, adverse-timing and recovery cases on both.
   Coordinator-to-coordinator exchange is neither required nor permitted for initial-profile
   qualification. Optional gateways and native control/resource capabilities require their own
   selected-role boundary evidence. A mock, schema validator or documentation gate is insufficient.
3. Publish sanitized commands, revisions, fixtures, results, limitations and an issue/erratum path.
   A new implementer must be able to reproduce the cases without internal accounts or instructions.
   Fix ambiguities in the public specification through review; do not hide them in a private test.
4. Review the final evidence and publish the release decision under [governance](../GOVERNANCE.md).
   Required failures or skips block qualification. Clearly separate development candidates,
   qualified profiles and deployment authorization; no automatic certification is created.

## Charter acceptance cases

These are additional design cases, not executed tests. They supplement the core and A2A cases.

| Case | Required acceptance evidence |
|---|---|
| CH-01 Independent implementability | Independent coordinator uses only public contracts and its own authority/storage; participants cross-test against each coordinator's separate authorized workspace as above. No PostMQ service, private schema or vendor-specific required feature for the independent coordinator, and no cross-authority traffic. |
| CH-02 Direct and delegated humans | Direct human action and agent-on-behalf action preserve distinct actors, exact subjects and bounded grants; names or model instructions cannot enroll or authorize. |
| CH-03 Revocation and visible limits | Race revoke against admission/control; reject new unauthorized effects. Show broker intent, actual native outcome, uncertainty and residual resources separately. Reconnect cannot resume intake implicitly; later intent requires current authority/version. |
| CH-04 Compatible evolution | Unknown required revision/profile rejects without effects; changed semantics cannot replay under the old operation identity or silently downgrade to another protocol. |
| CH-05 Current boundary | Two organizations' members can use explicit shared-workspace grants. A foreign workspace, card or alias cannot bypass the same-workspace rejection or directional disclosure rules. |
| CH-06 Equal ecosystem access | An unaffiliated implementation or competing application uses exactly the public capabilities and evidence criteria used by an affiliated product. Required capabilities have no private-only route. |
| CH-07 Human recovery and resources | Safe return/handoff preserves authorship and disclosure history. Human closure cannot erase unknown execution, free quarantined resources or authorize duplicate effects; unsupported native controls remain unavailable. |

Future cross-authority cases are gated by the complete [trust profile design](../spec/trust-and-control.md#future-cross-authority-profile-gate);
CH-05 proves the initial boundary, not federation.

## Adoption measures

For each dated protocol release or assessment, publish the following with links to evidence,
exact versions and assessment scope. Count distinct implementations or adapters once, not each
deployment or fork. Unknown is not zero; proposals and marketing are not qualifying evidence.

| Measure | What qualifies | Status at this charter publication |
|---|---|---|
| Independent implementations | Distinct protocol engines passing the selected public cases, with dependencies disclosed | No released PmP profile or qualifying evidence published here. |
| Third-party adapters | Maintained external adapters with tested profile/runtime versions and native limits | Not assessed; existing plugin distribution is not adapter qualification. |
| Cross-vendor use | Reproducible exchanges across named vendor/runtime combinations, including negative and recovery paths | Not qualified by this documentation publication. |
| Competing products | Publicly evidenced unaffiliated products using the same contracts, distinguished from announced plans | Not assessed; no third-party adoption claim. |
| Portability friction | Public ambiguities, private prerequisites discovered, migrations and unsupported required features | Track through public issues and release decisions; no measured baseline yet. |

Reassess when a release, adapter qualification or substantiated claim changes the evidence.
Report adoption separately from product usage, revenue and repository popularity.
