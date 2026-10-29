# PmP governance

Initial maintainer-led process, 2026-10-29. PmP is an open project stewarded by No Compromise AI,
LLC, not a standards body or an independently governed foundation. The [charter](CHARTER.md)
sets its purpose. The [public specification](spec/README.md) states its current design status.

## Participation and decisions

Anyone may propose a change through a public issue or pull request, including implementers
of competing services. No PostMQ subscription, private repository, private planning record,
paid review tool or commercial relationship is required. Contributions follow
[CONTRIBUTING.md](CONTRIBUTING.md) and Apache-2.0. Implementing or forking the published
material does not require a separate commercial agreement with PostMQ; license conditions
still apply. This is not a grant of third-party rights, trademarks or certification.

The founder is the initial steward. Current repository maintainers are identified by
[CODEOWNERS](.github/CODEOWNERS); this file does not itself confer permissions. Maintainers
triage proposals, ensure review and publish decisions. Contributors supply rationale,
compatibility impacts and evidence. Deployment owners separately authorize their participants,
data, resources and live operations. A protocol merge grants none of that operational authority.

For substantive charter, governance or normative protocol changes:

1. Open a public proposal describing the problem, affected capabilities, alternatives, security,
   migration and charter impact. A private planning reference cannot be required to understand it.
2. Obtain independent technical review of the final affected text or diff. Disclose AI-assisted
   reviews as such; the maintainer remains accountable. Document findings and their resolution.
   Seek affected implementers' input when any exist; do not claim ecosystem consensus from silence.
3. The steward or delegated maintainer records acceptance, rejection or requested revision, with
   rationale and unresolved objections, in the public PR. Acceptance must explain vendor-neutral
   availability and any compatibility change. No implementation vendor has privileged semantics.
4. Merge only after required repository checks and applicable approval rules pass. Record the
   exact revision and design/release status. The existing owner PR-review exception does not
   bypass required integrity checks and is not evidence of an independent GitHub approval.

This is accountable maintainer stewardship, not a vote or a claim of independent consensus.
An objection can be raised or appealed in a public issue linking the decision and new evidence;
the steward records the disposition. A dispute cannot silently alter a frozen contract. Governance
and maintainer succession changes use this same public process and preserve published versions
and license grants. Security disclosures follow [SECURITY.md](SECURITY.md); embargoed details may
remain private until coordinated disclosure, with a public advisory and version impact afterward.
That exception does not waive authorization, review or required checks.

## Extensions, versions and compatibility

Each portable release identifies a complete immutable protocol revision, schemas, required
capabilities, optional profiles and conformance cases. Product, plugin, SDK, protocol and
interoperability-profile versions are distinct. A moving `main` link identifies a draft, not
a frozen wire version. No released PmP wire revision exists in this publication.

Extensions must identify an owner, stable versioned identifier, dependencies, security semantics,
wire/error behavior, evidence requirements and compatibility rules. They may add optional
capabilities but cannot weaken the core or turn an unknown required capability into silent success.
Independent extensions are welcome; inclusion in this repository is not required to experiment.
They must not claim core conformance or official endorsement merely from using the name PmP.
Use identifiers under the extension owner's control until a reviewed allocation process exists.

Before effects, peers must explicitly agree on the required revision and capabilities. Unsupported
required versions/features reject without effects; optional features are omitted only when the
request does not depend on them. Missing acknowledgement means unknown acceptance, resolved
through the canonical operation identity rather than fallback execution. An adapter cannot
downgrade required safety semantics to base MCP, A2A or a proprietary transport.

A change that alters accepted messages, authority, ordering, effects, recovery, digest inputs or
required evidence is semantic even if its JSON shape is unchanged. Incompatible semantics require
a new negotiated version/profile and explicit migration, coexistence and rollback rules. Frozen
artifacts are never rewritten. Compatible corrections still receive public review and a dated
erratum identifying affected releases and tests. Names and marketing changes do not rewrite
deployed routes, identifiers or signatures.

## Conformance, adoption and neutrality

Use the [public evidence rules](conformance/README.md) for every compatibility claim. Claims
identify the exact profile, implementation, versions, environment and limitations; they are
self-reported test results unless an independent assessor is explicitly identified. No official
certification program or badge is established here. Anyone may challenge a claim with a sanitized
reproduction; maintainers record the result and correct unsupported listings or guidance. A claim
must not stay advertised as passing after its required evidence is invalidated.

The first portable interoperability release requires the [role-pairing gate](conformance/README.md#independent-implementation-and-release-gate):
two independently built coordinators, one of which may be PostMQ, and two independently built
participant clients, cross-tested in separate single-authority deployments. Each coordinator
passes the same public applicable cases using its own storage/authority, with clients of both
implementations exchanging send, exact reply and supported correction/handoff within its workspace.
Coordinator-to-coordinator or cross-authority exchange is neither required nor permitted by the
initial profile. The independent coordinator must operate from public material without a PostMQ
service or private artifacts; a renamed wrapper around the same protocol engine is not independent.
Publish shared dependencies and limitations. Development candidates may exist before this gate;
they cannot claim that release qualified.

For each release, record independent implementations, third-party adapters, cross-vendor exercised
paths and competing products built on public contracts. Report evidence dates and missing evidence,
not adoption inferred from downloads, folders or announcements. Maintainers must apply the same
technical acceptance criteria to affiliated and competing implementations. A core change that
requires a proprietary PostMQ feature must be rejected or redesigned as an optional product feature.
