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
