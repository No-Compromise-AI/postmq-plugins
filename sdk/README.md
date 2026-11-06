# Existing claim SDK and proposed PmP SDKs

The existing `postmq-claim` plugin contains a [Python claim SDK](../plugins/postmq-claim/tools/postmq_claim.py)
and [command-line wrapper](../plugins/postmq-claim/tools/postmq-claim.py). Its
[provider contract](../plugins/postmq-claim/skills/claim/SKILL.md) is the interface implemented by the
experimental [host adapters](../resources/host/README.md). Adapter source bundles do not vendor that SDK;
the owner integration must select and verify the SDK/provider combination.

No general PmP language SDK is published here yet. The existing claim SDK and
[Claude plugins](../README.md#install-the-existing-plugins) do not establish the proposed PmP wire protocol.

## Future language packages

A language SDK must implement a versioned [public contract](../spec/README.md), preserve canonical identity,
authorization, idempotency, leases, fencing and recovery, and pass applicable [conformance cases](../conformance/README.md).
A wrapper must not become a second queue or grant authority the service has not issued.

Add a language directory with a reviewed package, supported runtimes, installation/upgrade instructions,
credential/error/cancellation behavior and real dependency evidence. Do not publish empty packages or
install commands for unbuilt SDKs. Dependencies, registry ownership and compatibility policy need review.
