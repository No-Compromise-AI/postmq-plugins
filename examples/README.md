# Examples

Start with the [adapter source-bundle quickstart](../README.md#build-an-adapter-source-bundle) to assemble
and verify a Linux, macOS or Windows source bundle without a credential, registration or job launch.
Provider selection and owner integration requirements are in the [host adapter guide](../resources/host/README.md).
Existing plugin installation and prerequisites are in the [root README](../README.md#install-the-existing-plugins).

No new PmP runtime example is released here yet. The source-only quickstart does not install a provider,
configure remote access or supply a model runtime.

Examples must identify implemented protocol/version, client/host, repeatable disposable setup and cleanup,
and distinguish simulation from live integration. Use generic identities and synthetic data, never keys,
customer messages or real machine addresses. No side effects on import or installation.

Demonstrate errors/recovery as well as success: ambiguous targets, stale leases, revoked grants, cancellation
and safe retry. Link [conformance evidence](../conformance/README.md) and require explicit authority for live
side effects. A diagram or mock is not runtime proof.
