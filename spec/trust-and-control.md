# Human control and trust boundaries

**Status: proposed portable requirements. No new wire operations or federation implementation.**
This supplements [PmP core](pmp-core.md) under the [charter](../CHARTER.md). Requirements apply
to selected capabilities; unsupported native operations remain explicitly unavailable.

## First-class human participation

Humans may communicate directly, delegate bounded work, inspect authorized history and redirect
or revoke authority. A human is not merely an agent's approval callback. Implementations MUST
distinguish the actual authenticated actor, the represented principal and the delegation used;
an agent acting for a person MUST NOT be recorded as that person personally taking the action.
Names, employer affiliation, repository URLs and provider labels are not identity proof.

Consent MUST identify the actor and destination context, permitted operations, scope and duration
or revocation policy. Previously approved bounded delegation can permit autonomous work without
a prompt on every message. Message content and model-generated instructions cannot create,
renew or enlarge that delegation. Communication, payload disclosure, execution admission,
native control and resource administration are separate authorities. Reverse replies need their
own permitted disclosure direction. The same rules apply to every individual and organization.

Human-facing controls MUST expose the intended subject, actor/delegate, destination, requested
change, effective broker state, native capability limits and unresolved work. Show unknown,
unsupported, refused and pending distinctly from success, with a recovery path. Authorized
operators can inspect and revoke grants through their chosen integration; PmP does not mandate
one dashboard or IDE. Accessibility and usable status/recovery are implementation obligations.

Attachment, reconnect, retry and provider failover MUST NOT implicitly resume stopped intake or
expand consent. Intent changes require current authority and a current version check. The initial
design uses serialized authorized intent updates, not an unconditional human-priority stop latch:
an authorized delegate can make a later permitted update. To prevent delegated resumption, the
human must be able to revoke that delegation. A future persistent owner-stop policy needs an
explicit authority-precedence contract and tests before being advertised.

Revocation blocks new operations requiring that grant at the canonical authority. It cannot undo
disclosed data or prove offline execution stopped. Native pause/shutdown requires the selected
qualified capability, appropriate custody and an applied outcome. Administrative closure or
risk acceptance never supplies missing cleanup evidence, releases quarantined resources or
authorizes duplicate effects. Keep auditable actor, subject, authority/version, operation identity,
decision, outcome and uncertainty, subject to visibility, retention and legal-hold policy; audit
access is not permission to copy payloads or secrets into logs.

## Current workspace boundary

The initial profile operates within one authorized workspace. People from different organizations
may participate through explicit membership in that workspace; organization names create no grants.
Every project, channel, payload, reply, control and resource operation still checks its own authority.
Membership is not blanket disclosure or execution permission.

Requests across independently administered workspaces are unsupported and MUST reject in this
profile. A shared inbox, familiar alias, transport connection, Agent Card or gateway cannot silently
merge trust boundaries. Existing same-workspace negative tests remain required.

## Future cross-authority profile gate

The charter permits future communication across organizations and independent coordinators.
It does not select a federation mechanism. Before that capability can be frozen or advertised,
a separately reviewed public profile must specify and test all of the following:

| Boundary | Required design and evidence |
|---|---|
| Identity and enrollment | Authenticated issuer-qualified principal/session identities, explicit mappings, issuer/key rotation, revocation and collision/impersonation resistance. A display alias never joins identities. |
| Mutual authority | Both endpoint authorities explicitly authorize participation and separately scoped directional disclosure. Discovery is not admission. Authentication and token audience/exchange rules prevent credential forwarding and ambient access. |
| Durable routing | Stable channel and exact return-path bindings across restart; generation-checked consented handoff, release and correction preserve actual authorship and history. No arbitrary active-session rebinding. |
| Acceptance and ownership | Name the canonical owner of each message/action/resource fact and define reconciliation between authorities. No unproven distributed atomic transaction or exactly-once physical-effect promise. Preserve idempotency, one-successor lineage, admission, finite budgets and fencing. |
| Revocation and partitions | Explicit freshness/expiry, revocation propagation, offline limits and in-doubt states. Specify when new effects must stop and what each endpoint can prove; fail closed where required authority cannot be established. |
| Data and audit | Payload and metadata visibility, approved destinations, retention/deletion/hold conflicts, consent withdrawal and attributable audit obligations at both ends. Revocation cannot retract prior disclosure. |
| Resources and recovery | Owner-controlled resource delegation, local custody, cleanup witnesses and residual/quarantine state. Remote task completion cannot prove physical cleanup or grant another authority infrastructure access. |
| Abuse and interoperability | Replay, issuer confusion, malicious cards/URLs, amplification, loop budgets, downgrade, loss, retry and partial-commit cases against independent implementations, with documented unsupported branches. |

No endpoint, token exchange, new grant or cross-workspace access is introduced by this gate.
Same-workspace v1 qualification can proceed independently after its own design approvals.
