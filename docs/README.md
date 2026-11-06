# Repository guide

Installable plugin artifacts, maintained adapter sources and proposed design documents have different
owners and evidence requirements. Start with [plugin installation](../README.md#install-the-existing-plugins)
or the [adapter source-bundle quickstart](../README.md#build-an-adapter-source-bundle).

- [Founding charter](../CHARTER.md) and [governance](../GOVERNANCE.md): mission, public decisions and neutrality.
- [Human control and trust](../spec/trust-and-control.md): current scope and future cross-authority requirements.
- [Interoperability strategy](../interop/README.md): MCP, A2A and existing runtimes.
- [Publishing ownership](publishing.md): generated boundaries, versioning and review.
- [Repository safeguards](repository-security.md): desired GitHub protections and live verification.
- [PmP specification](../spec/README.md): proposed portable requirements.
- [A2A interoperability](../interop/a2a/README.md): mapping to a pinned external protocol, including gaps.
- [Conformance](../conformance/README.md): evidence before compatibility claims.
- [Resource adapters](../resources/README.md): implemented host sources and proposed hardware profiles.
- [Host integration](../resources/host/README.md): provider selection, assembly, compatibility and limits.
- [Managed allocation v2](../resources/host/linux/MANAGED-ALLOCATION.md): separate GB10/UMA allocation
  cleanup, fresh capacity and migration requirements.
- [SDKs](../sdk/README.md): existing claim SDK versus proposed PmP language packages.
- [Contribution policy](../CONTRIBUTING.md) and [agent guidance](../AGENTS.md).

Public documents stand alone. Link public normative sources, label design status and avoid private
issue/session identifiers as required context. Use generic identities and synthetic data, never customer
incidents, internal transcripts, keys or private host details.

Maintained directories are `spec`, `schemas`, `sdk`, `resources`, `interop`, `examples`, `docs`, `conformance`,
`tools`, `tests` and `.github`. Change them through reviewed public PRs. A directory is not a support
promise: a runtime, SDK, adapter or conformance claim needs an implementation, version, prerequisites
and repeatable evidence before it is advertised.

## Intended implementation layout

Experimental host implementations now occupy `resources/host/{linux,macos,windows}/src`, with shared
lifecycle callbacks under `resources/host/common/src` and NVIDIA observations under `resources/nvidia/src`.
The [adapter guide](../resources/host/README.md) describes their assembly, tests and limits. The source
distribution version is independent of generated plugin and cleanup-profile versions. The remaining
paths below are reserved design examples; empty packages/directories are not created to imply they work.
In particular, `t4.yaml` is an illustrative
profile filename, not NVIDIA T4 qualification or a shipped resource profile.

```text
sdk/
  python/
  typescript/
  dotnet/
resources/
  host/
    common/
    linux/
    macos/
    windows/
  nvidia/
    src/
    tests/
    profiles/
      t4.yaml
  apple/
  amd/
  intel/
```

Language packages share the public contracts and conformance requirements. Device-specific profiles
compose with host custody implementations; they do not each grow their own messaging or lease service.
