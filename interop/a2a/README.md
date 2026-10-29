# PmP and A2A

**Status: researched fit/gap and proposed optional profile; not an implemented integration.**
PmP reuses A2A where their responsibilities match. A2A discovery and task exchange do not
replace PmP's durable session addressing, action admission, supervision authority or resource
cleanup. The [core requirements](../../spec/pmp-core.md) and [profile](profile.md) define the
portable design; no private document is needed to understand the claims here.

## Pinned upstream baseline

This analysis targets the official [A2A v1.0.1 release](https://github.com/a2aproject/A2A/releases/tag/v1.0.1),
commit `3303592588e388e62e0f69f701af531d2f4e3991`, inspected on 2026-10-29:

- [Specification at that commit](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/specification.md).
- [Protocol definitions](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/specification/a2a.proto).
- [Extension design and activation](https://github.com/a2aproject/A2A/blob/3303592588e388e62e0f69f701af531d2f4e3991/docs/topics/extensions.md).

The current [official specification](https://a2a-protocol.org/latest/specification/) and
[extension guidance](https://a2a-protocol.org/latest/topics/extensions/) are useful navigation,
but the release pin above controls this comparison. A2A's negotiated protocol version is
`1.0`; the specification patch version `1.0.1` is not an `A2A-Version` negotiation value.
Agent software version, PmP draft revision and profile version are separate identifiers.

## Fit and gap

| A2A surface | Reuse in the proposed mapping | PmP requirement that remains |
|---|---|---|
| Agent Card, security schemes, interfaces and skills | Discover an authenticated service and its declared capabilities | Enroll actual principals/installations/sessions; enforce current project and destination rights. A card or skill label is not a runtime binding or grant. |
| Message and structured parts | Exchange task input, notices and replies | Freeze authorized recipients, persist acceptance, bind exact author/parent/return route and apply payload disclosure rules. Content is data, not authority. |
| Task and task ID | Expose one canonical work attempt and its user-visible outcome | Preserve lease, runtime/delivery epochs, mandatory admission, recovery and resource state separately. Task state does not authorize execution. |
| Context ID | Group related messages/tasks and correlate a channel mapping | Treat it as opaque correlation scoped to the authenticated endpoint/context. It is not a session address, project grant, channel-generation CAS or return-route token. |
| Artifacts | Represent authorized outputs/checkpoints | Bind provenance and payload access; independently verify any useful-progress predicate. Artifact existence does not prove cleanup or ownership. |
| Streams, task subscription, push notifications | Deliver updates through an available A2A binding | Reconcile against canonical records, deduplicate and recheck visibility. Stream closure or callback delivery is not task completion, model consumption or a resource receipt. |
| Cancel Task | Request cancellation and expose its actual task result | Preserve approval, admission/effect uncertainty, native control authority and cleanup gates. Cancellation is not a lease release, host shutdown or resource-available certificate. |
| Base operation idempotency | Reuse the caller's stable message identity where suitable | Base Send Message idempotency is optional. PmP requires durable, scoped request-digest deduplication and one-successor acceptance across transports. |
| Extensions and service parameters | Negotiate a versioned PmP semantic profile | Required semantics must fail closed before accepting work. Unknown optional extensions may be ignored by base A2A; a PmP-dependent action cannot rely on that fallback. |
| Custom protocol bindings | Possible future transport adaptation | A custom binding is a separately specified A2A transport mapping with core-operation/data/error/security obligations, not another name for this semantic extension. None is implemented or claimed here. |

The analysis distinguishes an inference from a base guarantee: A2A provides the exchange
surfaces in the middle column; retaining the right-hand safety properties is the proposed
PmP profile's responsibility. A service that only speaks base A2A is not thereby a PmP
session, a managed adapter, or a qualified cleanup witness.

## Smallest useful direction

Keep the existing durable coordinator and adapters as the canonical authority. Add an
optional A2A-facing mapping after portable wire extraction and qualification, initially
using a standard A2A binding rather than inventing another transport. Expose exact/offer
work and its results without replicating messages, claims, queues or resource fences in
an A2A-specific execution engine. Native control and resource features remain separately
negotiated and qualified; a task-only agent need not support them.

The profile is optional for deployments, but mandatory on any operation that claims its
guarantees. Plain A2A interaction can remain separately available under its own declared
semantics; it cannot be an automatic downgrade of a PmP request. This proposal is maintained
by this project and has no claimed A2A endorsement, registry acceptance or certification.
