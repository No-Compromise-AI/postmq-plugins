# SDK publication requirements

No PmP SDK is published here yet. Existing [Claude plugins](../README.md) are not general PmP client libraries.

A language SDK must implement a versioned [public contract](../spec/README.md), preserve canonical identity,
authorization, idempotency, leases, fencing and recovery, and pass applicable [conformance cases](../conformance/README.md).
A wrapper must not become a second queue or grant authority the service has not issued.

Add a language directory with a reviewed package, supported runtimes, installation/upgrade instructions,
credential/error/cancellation behavior and real dependency evidence. Do not publish empty packages or
install commands for unbuilt SDKs. Dependencies, registry ownership and compatibility policy need review.
