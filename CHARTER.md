# PmP Founding Charter

Founding charter, 2026-10-29. This states the project's purpose and commitments; the
[specification](spec/README.md) separately identifies proposed and released capabilities.

## Mission

**PmP exists so humans and AI agents can communicate, collaborate, and coordinate across any platform, provider, or environment—without surrendering control of how they work.**

**PostMQ's mission: Make that collaboration effortless.**

## Principles

1. **Interoperability over ownership.** Bring existing agents, tools, models, providers and
   environments. Participation must not require one framework, IDE, model provider or platform.
2. **Open protocol, independent implementations.** Publish contracts that others can implement
   without PostMQ, supported by public conformance cases and interoperable SDKs and adapters.
3. **Humans and agents are first-class participants.** Enable collaboration, bounded delegation,
   interruption, redirection and oversight within selected capabilities, with explicit identity,
   permission and audit boundaries.
   Autonomy operates within granted authority; it cannot enlarge its own authority.
4. **Coordination without dictating orchestration.** Standardize reusable coordination primitives.
   Adopters choose their workflows, application policy and how much control to delegate.
5. **Compete on implementation, not lock-in.** PostMQ earns adoption through ease of use,
   reliability and operations. Required PmP capabilities cannot be exclusive to PostMQ.

## Responsibilities and scope

| Layer | Responsibility |
|---|---|
| PmP | Portable identity and discovery, durable addressing and communication, lifecycle, supervision, resource coordination, and the security and evidence rules that connect them. |
| PostMQ | One product and implementation: hosting, persistence, integrations, administration and a useful experience of those contracts. Its implementation choices are not universal protocol requirements. |
| Applications | Workflows, task planning, business rules, scheduling strategy and user experience, within the participants' authority and selected protocol guarantees. |

Affiliated products such as LoopThat, GPCloud and RackCompass, and competing products, are held
to the same public contracts. None receives privileged protocol semantics. No PostMQ account,
private specification or internal planning service may be required to independently implement
a released PmP profile. A proprietary runtime cannot be a universal prerequisite; an optional
integration may require its named runtime, with that dependency and support limits explicit.

PmP is designed to complement protocols including MCP and A2A. Integration does not imply
permission, native control or interoperability beyond what has been demonstrated.

Cross-organization collaboration is part of the mission. The initial design operates within
one explicitly authorized workspace; participants may belong to different organizations.
Communication between independently administered workspaces requires a future reviewed trust
profile. Discovery does not grant membership, disclosure rights or access to another party's resources.

## Commitments and non-goals

Publish versioned contracts, extension rules, review decisions and reproducible evidence.
Keep delegation, revocation, human oversight, privacy and resource-owner authority explicit.
Report requested actions separately from applied controls and verified outcomes. Preserve
unknown effects and cleanup obligations; an administrative decision is not physical proof.

PmP does not require a particular software factory or workflow engine, replacement of existing
tools, or PostMQ as its sole implementation. The mission is not a promise of universal
interoperability today. Proposed documents, repository folders and passing documentation checks
do not establish a working protocol or qualified client.

The stable name is **PmP**. There is no canonical expansion. Optional interpretations such as
Post Messaging Protocol, Post Modern Protocol and PostMQ Protocol are nonnormative editorial
choices; they do not change the specification or require PostMQ. Campaign language such as
“PmP Your Agent” carries no compatibility, security or standards-body claim.

## Putting the charter into practice

The [governance process](GOVERNANCE.md), [human control and trust requirements](spec/trust-and-control.md),
[interoperability strategy](interop/README.md) and [conformance and adoption measures](conformance/README.md)
make these commitments reviewable. Original material in this repository is published under
[Apache-2.0](LICENSE); third-party protocols and runtimes retain their own terms.

Every design decision should answer:

**Does this help existing developers and their existing agents work together without forcing them to change how they work?**
