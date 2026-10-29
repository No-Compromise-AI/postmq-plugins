# PmP core requirements

**Status: proposed portable design.** This is the bounded normative summary described in
[the specification index](README.md), not a complete wire specification or a release claim.
Requirements below apply when the corresponding capability is selected. Unsupported native
control or resource capabilities must be reported as unavailable rather than simulated.

## Identity and authority

| Identity | Meaning and boundary |
|---|---|
| Principal | Authenticated human or service actor; authority comes from current grants, not a name or role asserted in a message. A logical agent account is a managed identity, not a human-login credential. |
| Workspace | Security boundary. This design routes within one workspace; it does not define cross-workspace federation. |
| Device / installation | Protected enrollment for one installation within a workspace, with key custody, rotation and revocation. A hostname or copied API key is not device proof. |
| Client | Provider/application family and local adapter capability; a mutable label such as a client name is not a unique address or permission. One process may host multiple sessions. |
| Project / context | Explicit authorized project or distinguished general-mail context. Repository and working-directory hints can suggest a mapping but never grant access. |
| Session | Durable routable interactive or headless conversation with a protected installation-local binding and stable identity. Reattachment creates a new runtime epoch without moving the session to another project or installation. |
| Runtime / adapter | Ephemeral execution attachment and local supervisor custody. Runtime epochs and supervisor epochs are distinct; process IDs are observations rather than authority. |

The principal–device–client–project–session hierarchy is a discovery and management model,
not automatic permission inheritance or a requirement to create an account at every level.
Every operation MUST authenticate the actual actor and check current workspace, context,
subject and operation authority. A copied credential, provider-native thread ID, session
label or repository URL MUST NOT select another installation's session or inherit its
runtime authority. Enrollment, attachment and observation MUST NOT implicitly resume a
stopped session. Service actors do not acquire human ownership by enrollment.

## Durable communication

PmP distinguishes three delivery modes: **exact** names one durable session; **offer** freezes
a bounded authorized audience that can all observe but grants one action owner; **pool**
explicitly admits interchangeable workers. An offer MUST NOT become one independently
executable work item per observer. Late enrollment or a shared inbox does not enlarge an
accepted audience.

Resolve authorized aliases once and open a durable channel with explicit endpoint contexts,
roles, consent, audience and route generation. Subsequent sends MUST use that binding and
its current generation rather than resolving the display name again. Every operation still
rechecks current rights. Replies MUST return to the exact parent-message author through
server-bound routing; a shared account, correlation identifier or caller-supplied reply inbox
does not override it. Cross-project disclosure within the workspace requires explicit
directional authorization, separately checked for the reverse direction.

Broad selection excludes the exact source session by default; explicitly authorized self-send
is allowed. It retains causal budgets, deduplication and admission rules. Names, project hints,
forwarded metadata and untrusted agent content MUST NOT become instructions to grant access.

Accepted messages, recipient manifests and disclosure history are durable. Notification,
transport delivery, model consumption, relevance, action claim and completion are different
facts. A UI card or successful webhook MUST NOT be reported as model consumption. Human
announcements must be attributable, coalesced and separately observed. Views and events
MUST respect current visibility, including metadata-only views that do not reveal recipients.

## Work ownership and admission

One canonical action store and its transactional authority MUST own each work attempt.
Mutations require durable operation identity plus an authenticated request commitment;
replay returns the original authorized result, while reusing the identity for changed intent
fails. Neither a retry nor a second transport may resolve a new audience or charge again.
Authorization is rechecked before replay can disclose a result.

A claim reserves work; it is not permission to execute effects. Every actionable attempt
MUST pass admission under its current holder, runtime and delivery epochs, lease, context,
recovery and resource predicates. Required resource claims add their canonical fence and
admission association. Authority checks and transitions MUST be serialized with acceptance;
notifications are emitted after the commit. Clients cannot execute first and record admission
afterward. Clock expiry, lost connectivity and absent observations mean lost authority or
unknown state, not proof that an old execution stopped.

One source work item has at most one accepted successor under the applicable correction or
recovery transition. A safe, known-unstarted return can release reservation under its declared
policy; actual or uncertain effects require reconciliation. Marking a message unread cannot
erase reads, leases, admission, disclosure or effects. Readdressing MUST preserve actual
authorship, immutable work/resource constraints, refusal history, root budgets and predecessor
history, with fresh destination authority and explicit linkage. No manager or bridge silently
impersonates the original session to create a successor.

Causal work needs finite root deadlines, send/attempt budgets and hop limits under bounded
creation authority. Reconnect, new channel IDs, replacement tokens, self-send and retry MUST
NOT replenish those limits. Exhaustion blocks new work but cannot prevent necessary outcome
and cleanup reporting. Same-work continuation must not bypass a canonical successor gate.

## Lifecycle and supervision

Observed presence, startup/readiness, useful progress, execution and provider availability
are independent of desired intake state. A heartbeat is not useful progress; an absent
heartbeat is not a death certificate. Reports, independently verified facts, enforcement
capabilities and current authorization are separate axes. The verifier must establish the
fact for the exact subject, epoch and predicate; a model assertion cannot promote itself.

External observed clients remain useful without native process control. Cooperative and
managed adapters advertise only operations their actual custody, approved manifest, grants
and evidence support. Broker intake pause does not prove native work paused. Controls MUST
distinguish requested, pending approval, claimed, accepted, applying and applied/failed states;
serialize desired-state changes; bind deadlines, cancellation and idempotency; and reconcile
ambiguous effects. A stale-epoch receipt cannot mark a current command applied.

Managed launch/restart is optional. It requires a durable launch intent, approved profile,
reserved attachment and local custody before releasing one launch. Unknown launch results,
adapter crashes and detached descendants require reconciliation rather than a duplicate
launch. Restart or reassignment MUST NOT pass unresolved old-execution or resource-cleanup
gates. Administrative closure can end bookkeeping or abandon work; it is not physical proof
and cannot free quarantined resources or authorize an unsafe retry.

## Resources and evidence

A resource is an exclusive coordinated unit; a resource claim carries a monotonic fence.
The fence orders holders but does not authenticate them or grant infrastructure authority.
Capacity N can be N explicitly bound units; shared quantity scheduling is not implied.
Message leases and resource claims serve different purposes and MUST compose through one
admission decision rather than through parallel ownership engines.

When execution, resource effects or cleanup are required or uncertain, claim expiry,
cancellation, revoke, holder release or a task's terminal state MUST NOT alone make a resource
available. That recovery requires the canonical resource authority's accepted cleanup evidence
from an authorized witness that can inspect the owned execution boundary, including relevant
descendants. Existing canonical queue cancellation and proven never-admitted/no-start paths
retain their own guards; they do not falsely claim physical cleanup. Owner withdrawal remains effective until
the appropriate owner action returns it to service. A zero-utilization metric, empty parent
process, signed model report or generic task result is not resource cleanup proof.

Existing coordinators, claim SDKs, journals and provider seams should be reused. PmP does not
create another lease service, GPU scheduler, model-placement system, provider-rental authority
or universal remote shell. Physical enforcement and native delivery remain explicitly qualified
per provider, operating system, version and ownership boundary.

## Acceptance boundary

A supported profile MUST identify its exact version, selected capabilities, evidence and
limitations. Shape examples, documentation, a model API response and an adapter's declared
capability are not runtime conformance. Negative authority tests, duplicate and race tests,
partition/recovery tests and native-boundary evidence are required before corresponding
behavior is advertised. The [A2A profile](../interop/a2a/profile.md#acceptance-cases) applies
these invariants to its proposed mapping; its cases remain unrun design requirements.
