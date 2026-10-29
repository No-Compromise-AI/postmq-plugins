# PmP design specification

**Status: proposed design requirements, not an implemented or frozen wire protocol.**

PmP is the portable protocol design for durable agent addressing, communication, lifecycle,
supervision and resource coordination. PostMQ is a product that can implement and host these
contracts. The name is **PmP**, without an expanded name. Product releases, plugin versions,
protocol revisions and interoperability profiles have separate versions.

## Source of truth

[PmP core](pmp-core.md) is the authoritative public statement of the portable requirements
published at this design status. [The A2A profile](../interop/a2a/profile.md) adds proposed
interoperability requirements; [its fit/gap analysis](../interop/a2a/README.md) explains what
is reused. A product mapping may add implementation details but cannot weaken these public
requirements while claiming the corresponding PmP profile.

These documents are standalone. They do not depend on a private repository, internal issue,
customer account, transcript or unpublished credential. They deliberately publish a bounded
normative design summary rather than an incomplete copy of a product's wire specification.
MUST and MUST NOT describe the proposed contract; they do not claim running endpoints,
certified adapters, SDK support or a passing conformance suite. Existing plugins in this
repository retain their separately documented capabilities.

## What is and is not published

Published here: identity and authority boundaries, channel and work-ownership invariants,
lifecycle and supervision guarantees, resource-safety composition, and the A2A mapping and
negative acceptance cases. There is no executable PmP protocol implementation, portable
wire schema, profile-enabled Agent Card, custom A2A binding or interoperability certification
in this design publication.

Before a portable wire release, maintainers must publish and review together:

1. A complete operation/type/error inventory, immutable version identifiers and binding rules.
2. Exact field encodings, canonicalization and signature/digest domains, idempotency scope,
   retention, deadlines, pagination and event-resynchronization behavior.
3. Cross-record authorization, ordering, lease/admission/fence and recovery predicates, with
   transition tables and migration/rollback rules; shape validation alone is insufficient.
4. A public mapping for every required capability and every unsupported branch, complete
   positive/negative fixtures, and executable conformance with fault and native-boundary evidence.
5. Independent review and a release freeze tying the published requirements, schemas,
   reference implementations and evidence to the same version.

This extraction gate prevents two competing specifications: public portable requirements
remain the design authority, while product-specific drafts remain product mappings until
their portable wire content is reviewed and moved here. Neither an unpublished draft nor a
repository layout is a hidden prerequisite for the claims made in these public documents.

## Compatibility

Publication under the PmP name does not rename existing API routes, message fields, plugin
IDs, MCP tool names, persisted identifiers or signed bytes. In particular, any deployed or
previously specified `postmq.v2` digest-domain strings retain their exact bytes in the
PostMQ mapping. A future portable namespace requires a new explicit version, test vectors,
negotiation and migration; text replacement is not a protocol migration.

The repository's license applies to this original design text. A2A remains a separately
maintained standard; this work does not claim its endorsement or official extension status.
