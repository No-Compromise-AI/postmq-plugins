# Interoperability strategy

**Status: design strategy.** Follow the [charter](../CHARTER.md) and
[conformance rules](../conformance/README.md); no universal compatibility is claimed.

| Surface | Reuse and boundary |
|---|---|
| MCP | Expose authorized coordination operations through an application's existing tool/context integration. MCP transport connection, tool availability or a tool response does not itself prove PmP routing, model consumption, native control or resource cleanup. |
| A2A | Reuse standard discovery, message/task, artifact and update surfaces through the proposed [A2A profile](a2a/profile.md). Preserve canonical identity, routing, admission, recovery and evidence; plain A2A is not automatic PmP conformance. |
| Existing runtimes | An observed integration can participate without process custody. Cooperative control and managed launch/resource capabilities are optional and require separate grants and native evidence. Preserve native user approvals; missing capability is reported, never manufactured by a substitute worker. |
| Independent coordinators | Implement the same public selected profile with their own storage, identity service and operational choices. Required contracts cannot depend on PostMQ's .NET/SQL deployment, scopes, internal planning tools or paid services. |

This division is a PmP design choice, not a statement that MCP or A2A lacks coordination
features. The [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25)
defines its own tools, resources, prompts and client/server features, with consent and safety
responsibilities. The [A2A fit/gap analysis](a2a/README.md) pins its separate upstream version
and distinguishes base guarantees from the additional PmP requirements. Respect both upstream
contracts and licensing; do not duplicate their mechanisms or imply endorsement.

For each binding, publish its pinned upstream revision, operations/errors/events mapping,
authentication and negotiation rules, unsupported cases, cancellation limits and recovery behavior.
All transports for the same work must reach the same canonical authority. A bridge MUST NOT
create a second queue, ownership/admission store or cleanup authority to compensate for a gap.
Preserve exact session return paths and current permissions across reconnect and retries.

A deployment may offer base-protocol behavior separately, labelled as such. An operation that
requires PmP guarantees MUST reject unsupported required capabilities before effects, never
silently fall back. Frozen contracts and positive/negative native-boundary tests precede support
claims. Optional interoperability work does not expand the initial workspace scope; future
cross-authority participation requires the [separate trust profile gate](../spec/trust-and-control.md#future-cross-authority-profile-gate).
