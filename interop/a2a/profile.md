# Proposed PmP profile for A2A

**Status: design requirements only; no frozen wire schema, running endpoint, conformance
result or official A2A extension registration.** Read [the fit/gap analysis](README.md) for
the pinned upstream sources and [PmP core](../../spec/pmp-core.md) for portable invariants.

## PmP draft 0.1

Provisional profile identifier:
`https://github.com/No-Compromise-AI/postmq-plugins/blob/main/interop/a2a/profile.md#pmp-draft-01`.
This is this project's proposed namespaced documentation URI, not a deployed capability or
an A2A-owned identifier. It must be frozen to a durable published version before an
implementation advertises it. Breaking semantics require a new versioned URI; clients must
not silently fall back to an older profile. PmP capitalization and this proposed name do not
rename any existing PostMQ route, field, plugin ID or digest domain.

This profile adds semantics through A2A's extension mechanism over a standard binding.
It does not define a custom transport binding. A future custom binding would separately
need the pinned A2A specification's operation, model, error, service-parameter, security
and streaming mappings. Merely putting A2A objects into a durable queue does not meet that
obligation or authorize an A2A binding label in an Agent Card.

## Negotiation and acceptance

A future implementation must declare its exact supported profile URI and capabilities in
the appropriate Agent Card extension entry. For a service/operation that requires PmP
guarantees, required extension support must be enforced before business acceptance. Clients
request activation through the standard binding's extension service parameter; message
metadata alone is not negotiation. The profile requires an explicit activation confirmation
in the response, stronger than relying on an optional unknown extension being ignored.

Missing required support must use A2A's required-extension failure. Incompatible version,
missing required PmP capability, stale authority or unmet admission preconditions must fail
without accepting or executing the requested work. Exact error payloads and any additional
profile parameters remain part of the wire-freeze gate below. No proxy may strip extension
requirements and resubmit as ordinary A2A. A client that loses the response or receives no
activation confirmation treats acceptance as unknown, reconciles with the same operation
identity and never creates an automatic replacement attempt.

Advertised support is not authorization or physical evidence. Discovery must authenticate
the service and apply its current access policy; execution still checks the actual actor,
workspace/context/session binding and selected capability. This design supports one
workspace per authorized interaction and no cross-workspace federation.

## Identity, channel and task mapping

The bridge must durably map authenticated endpoint identity and workspace/context to PmP
principal, installation, client, project, stable session and runtime epoch. It cannot derive
those rights from an Agent Card name, task ID, context ID, API-key label or model statement.
A remote endpoint that cannot bind an intended session cannot advertise exact delivery to it.

Agent Cards, extension parameters, artifact references and push URLs are untrusted inputs.
They cannot add workspace members, choose token audiences or request forwarding of existing
credentials. Endpoint/callback access requires configured allowed schemes/destinations,
network-boundary and address checks on resolution and every redirect, and protection against
DNS rebinding and unapproved private/link-local/metadata-service access. Private LAN endpoints
require explicit configured authorization; a discovered card cannot grant it. Authenticate each receiver using
the approved audience-scoped flow; changing a URL does not transfer a credential to it. A card
may describe an auth mechanism but cannot authorize a login, permission change or secret export.

For the initial profile, map a channel at an authenticated bridge endpoint to an opaque A2A
context ID, retaining the PmP channel and route generation in canonical state. Explicit
authorized channel creation may allocate that mapping; an unknown client-provided context
must not silently create or select another session. A2A task/context mismatch checks remain
in force. A known context ID still does not authorize its reader or identify the return peer.
Replies follow the authenticated parent author's session and the current permitted route.

The durable mapping binds the authenticated peer, workspace, channel/generation, task/attempt
and exact session/runtime authority. Restart must reload and reconcile it, never bind the
context to whichever local session is active. An authorized handoff uses canonical consent,
generation/CAS and eligibility gates, retains prior mapping history and rejects old-epoch
traffic. A known-unstarted correction that creates a linked channel/task gets a distinct
mapping; admitted or uncertain work cannot be moved by editing contextId. Each participant
and each referenced message/artifact part retains its own current visibility/payload rights;
access to a task container does not reveal every part or peer.

Map each canonical executable work attempt to one A2A Task. Offers may have many authorized
observers but one task/attempt and one action owner; do not create an executable task per
observer. A2A messages can contribute context or carry non-actionable replies. New executable
work requires a new canonical admitted attempt; continuing a context or task does not silently
grant another execution. A direct Message response without a Task is suitable for non-actionable
exchange, not evidence of completed PmP work.

Idempotent sends bind stable Message.messageId to the actual principal, workspace, operation
and normalized request commitment, including profile, context, intended route/generation and
payload identity. Same identity with changed intent rejects; replay returns the same authorized
task/message mapping and charges no second attempt. The bridge must use the coordinator's
canonical acceptance/idempotency transaction, not a process-local A2A cache. Cross-binding
retries share that authority. Freeze exact key validation, canonicalization, replay retention
and non-send mutation identities before interoperable implementation.

## State, cancellation and resource safety

| A2A observation | Permitted interpretation | Forbidden shortcut |
|---|---|---|
| Submitted | Task accepted into its canonical workflow | Treating it as claimed, admitted or running |
| Working | Task processing reported; map the canonical admitted action and separately grade progress | Treating status text/heartbeat as verified progress, a current lease, or native custody |
| Input/auth required | Task needs interaction or authorization | Extending leases indefinitely, revealing credentials, or silently widening grants |
| Completed/failed/rejected/canceled | Task-level outcome according to the actual authoritative result | Clearing unresolved execution, retiring another epoch, freeing a resource or creating a successor |
| Stream end, disconnect or task not found | Transport/visibility observation | Proving the task stopped or its descendants are clean |

The bridge must preserve PmP's orthogonal desired intake, observed execution, control
approval/acceptance/application and reconciliation states alongside the task projection.
An A2A status enum cannot encode or replace that full state machine. Do not report an
unapplied native command as applied or map a pending/unknown cancellation optimistically
to a terminal result. If a task becomes logically terminal while resource reconciliation
continues, expose that separation without claiming the resource is available.

Cancel Task requests may use the existing safe unstarted cancellation path or the separately
authorized native control path when supported. They never bypass holder/epoch/admission,
approval, fence, deadline or recovery predicates. A cancellation response, artifact or signed
model report is not cleanup proof. Canonical resource reconciliation requires the authorized
qualified witness and current resource/fence facts, including relevant descendants. Optional
managed restart waits for surviving old-execution and cleanup gates; an A2A retry is not a
second launch scheduler.

Artifacts remain authorized payload references with provenance. Progress credit requires
the PmP verifier's accepted useful milestone for the exact action/epoch and approved profile;
untrusted artifact metadata cannot self-certify it. Streaming and push are delivery mechanisms:
validate origin and current subject visibility, handle duplicates, and resynchronize from
canonical task/channel/action state after gaps. No callback URL is fetched or trusted merely
because another agent supplied it; retain A2A's authentication and webhook security requirements.

## Wire-freeze gate

The public design fixes the required semantics but intentionally does not invent a parallel
set of lease/control endpoints. Before an implementation claims this profile, publish the
complete public extension specification: namespaced typed metadata, identifiers and mappings,
capability/version negotiation, request/response/receipt/error shapes, state projections,
authorization/redaction rules, canonicalization/signature domains, idempotency and retention,
event cursors/resync, and mappings to the chosen standard A2A binding. Every required semantic
must have an accessible operation and conformance case; an inaccessible private wire draft
cannot fill a gap. Preserve the core's single authoritative action/resource machinery.

Any required feature without a complete frozen mapping remains unsupported. Do not advertise
this provisional URI as implemented, send illustrative metadata as if interoperable, or infer
support from a working generic A2A SDK. Native surfaces require their own qualification in
addition to protocol conformance. This gate is part of the authorized design scope, not an
implementation performed by these documents.

## Acceptance cases

These are required future conformance scenarios, **not tests run or passed by this publication**.
Each must assert the relevant record/field, demonstrate a meaningful failure with the guard
removed, then pass against the real implementation/dependency. Fault simulations supplement
native evidence. See the [conformance overview](../../conformance/README.md).

| Case | Scenario | Required result |
|---|---|---|
| PA-01 | Missing activation, unsupported required profile, proxy strips requirement, response lost | No silent plain-A2A execution; preacceptance failures leave no work; ambiguous response reconciles the original identity without duplicate acceptance. |
| PA-02 | A2A specification patch vs protocol version; incompatible PmP profile | A2A 1.0 negotiation remains distinct from pinned release 1.0.1; incompatible profile rejects without downgrade. |
| PA-03 | Two installations share account/credential labels or client-native IDs | Only the protected intended session/runtime can read, claim and execute; copied labels never remap the target. |
| PA-04 | Repo hint, different project, reversed disclosure grant, foreign workspace | Explicit current context/forward/reverse rights enforced; cross-workspace requests reject. |
| PA-05 | Reused/unknown context ID, task/context mismatch, bridge restart selects another active session | No implicit session/channel creation, reassignment or hijack; canonical endpoint-scoped mapping remains intact. |
| PA-06 | Several same-client sessions, reverse reply, authorized handoff then old-epoch traffic | Reply reaches the exact parent author; consented generation changes preserve history and stale routes fail without resolving another display name. |
| PA-07 | Duplicate message across bindings, changed payload with same identity, lost accept response | One durable accepted task/attempt/result; changed commitment conflicts; retry does not re-resolve recipients or charge twice. |
| PA-08 | Offer observed by N sessions, simultaneous claims, late enrollment | Frozen audience and one canonical action owner; observations do not become N executable tasks. |
| PA-09 | Task says working but no admission, old epoch/lease/fence, reused resource association | Execution is denied before effects; task status cannot substitute for current canonical authority. |
| PA-10 | Cancel before/after admission, native denial, detached descendant, terminal task with unknown cleanup | Truthful task/control outcome; no unsafe reassignment or resource availability until required reconciliation. |
| PA-11 | Bridge crash after acceptance or launch, partition, reconnect, duplicate update | Existing task/launch mapping recovered; no second launch; stale receipts cannot apply to a successor epoch. |
| PA-12 | Owner stop, desired-state CAS race, pending approval and control deadline | Intake and native control remain distinct; reconnect does not resume stopped work; ambiguous effects reconcile. |
| PA-13 | Forged artifact progress/cleanup, mixed-ACL parts, cross-context output, revoked payload access | No self-certified progress/cleanup; each content dereference requires current directional and payload rights without peer/part disclosure through task access. |
| PA-14 | Stream closes, push duplicates/reorders, replay gap, revoked observer, malicious card/callback URL or redirect/rebinding | Canonical resync preserves work; no false completion/consumption, unauthorized data, SSRF or credential transfer to another audience. |
| PA-15 | Wrong initial claimant, claimed work returned, effect-uncertain handoff, fresh linked reoffer | Safe known-unstarted recovery only; history/actual author/refusals retained and old authority fenced where required. |
| PA-16 | Existing resource claim, queued grant, owner withdrawal, competing A2A caller | One canonical claim/fence/admission authority; withdrawal/reconciliation gates survive bridge activity. |
| PA-17 | Base-A2A-only agent, documented native feature but absent qualified bridge | Required PmP capability unavailable; no managed-control or exact native delivery claim from a card/SDK alone. |
| PA-18 | Self-send loop, same-work continuation, exhausted root, new channel/token | Existing causal limits and one-successor gates hold; IDs and transports cannot reset budgets. |
| PA-19 | PmP naming release with legacy route/ID or postmq.v2 digest fixtures | Existing bytes and verification remain unchanged; a future namespace change requires explicit new-version vectors/migration. |
| PA-20 | Count-only sender/controller reads task/action state after claim, cancel or reoffer | Limited projection supplies lawful status/CAS/opaque linkage without holder identities, tokens, payload or discovery escalation. |
