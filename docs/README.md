# Repository guide

Installed plugin artifacts and maintained public design documents have different owners and evidence requirements.

- [Founding charter](../CHARTER.md) and [governance](../GOVERNANCE.md): mission, public decisions and neutrality.
- [Human control and trust](../spec/trust-and-control.md): current scope and future cross-authority requirements.
- [Interoperability strategy](../interop/README.md): MCP, A2A and existing runtimes.
- [Publishing ownership](publishing.md): generated boundaries, versioning and review.
- [Repository safeguards](repository-security.md): desired GitHub protections and live verification.
- [PmP specification](../spec/README.md): proposed portable requirements.
- [A2A interoperability](../interop/a2a/README.md): mapping to a pinned external protocol, including gaps.
- [Conformance](../conformance/README.md): evidence before compatibility claims.
- [Resource integrations](../resources/README.md): host responsibilities and device profiles.
- [Contribution policy](../CONTRIBUTING.md) and [agent guidance](../AGENTS.md).

Public documents stand alone. Link public normative sources, label design status and avoid private
issue/session identifiers as required context. Use generic identities and synthetic data, never customer
incidents, internal transcripts, keys or private host details.

Maintained directories are `spec`, `schemas`, `sdk`, `resources`, `interop`, `examples`, `docs`, `conformance`,
`tools`, `tests` and `.github`. Change them through reviewed public PRs. A directory is not a support
promise: a runtime, SDK, adapter or conformance claim needs an implementation, version, prerequisites
and repeatable evidence before it is advertised.

## Intended implementation layout

Future implementations will land under this structure. The paths below are reserved design examples;
empty packages/directories are not created to imply they work. In particular, `t4.yaml` is an illustrative
profile filename, not NVIDIA T4 qualification or a shipped resource profile.

```text
sdk/
  python/
  typescript/
  dotnet/
resources/
  host/
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
