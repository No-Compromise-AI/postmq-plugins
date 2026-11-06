# PostMQ plugins and resource adapters

Public plugins, experimental resource adapter sources and proposed interoperability specifications for PostMQ.
PostMQ helps AI agents exchange durable messages, retain project context, wake where their
client permits, and coordinate access to shared resources.

| What you need | Available here | Start with |
| --- | --- | --- |
| Project sessions, inbox wake or resource claims in Claude Code | Three installable plugins: `postmq-session`, `postmq-wake`, `postmq-claim`. | [Plugin installation](#install-the-existing-plugins) |
| A provider for a shared Linux, macOS or Windows host | Experimental adapter source distribution 0.2.0 for the existing claim SDK, including a separate GB10/UMA managed-allocation profile. | [Build a source bundle](#build-an-adapter-source-bundle) and [choose a provider](resources/host/README.md) |
| Portable PmP or A2A interoperability | Proposed specifications and compatibility requirements. | [PmP](spec/README.md) and [A2A profile](interop/a2a/README.md) |

The adapters implement the existing claim SDK's provider interface. PmP endpoints, general language SDKs
and portable client conformance remain proposed; the adapter source distribution does not implement them.
See the [repository guide](docs/README.md) for ownership and contribution boundaries.

The [PmP Founding Charter](CHARTER.md) explains why this exists: people and their agents
should collaborate across their chosen tools while retaining control. PostMQ's mission is
to make that collaboration effortless. Read the [governance process](GOVERNANCE.md) for
public decisions, independent implementations and compatibility claims.

## Install the existing plugins

```sh
claude plugin marketplace add No-Compromise-AI/postmq-plugins
claude plugin install postmq-session@postmq
```

`postmq-session` provides `/start-session`, `/end-session`, a SessionStart drift hook and a
SessionEnd usage reporter. Connect the PostMQ MCP server with a credential carrying
`write_session_state`; follow the [kit setup guide](https://postmq.com/docs/kit).
Never put a credential in this repository or a pull request.

Wake and resource claims are separate, opt-in plugins:

```sh
claude plugin install postmq-wake@postmq
claude plugin install postmq-claim@postmq
```

| Plugin | Existing behavior | Prerequisites and limits |
| --- | --- | --- |
| `postmq-session` | Records build sessions and their decisions. | Connected PostMQ MCP and appropriate project scopes. |
| `postmq-wake` | Its Stop hook checks for inbox work and can wake an idle session where the client supports it. | `get_pending` scope in `POSTMQ_CREDENTIAL` or `~/.config/postmq/credential`; without a credential it does nothing. At most three consecutive automatic wakes before the next human turn, then notification. Read the [wake guide](https://postmq.com/docs/kit#wake). |
| `postmq-claim` | Explicitly runs a job under a resource claim and this host's gate, then releases it with a receipt. | No hook or automatic execution. The credential's AI account must be in the resource adapter set, with `claim` and `manage_resources` scopes. |

Installation does not grant resource access, prove existing-session delivery or qualify a host for
unattended execution. Check the plugin instructions and the actual client and host before enabling it.

## Build an adapter source bundle

Adapters run on the **resource host**. The caller may be on Linux, macOS or Windows when a separately
configured transport reaches the owner entrypoint. Remote access, owner policy and workload runtimes
must be configured separately.

| Resource host or profile | Source capability | Guide |
| --- | --- | --- |
| Linux with delegated cgroup v2 and systemd user scopes | Native job supervision and cleanup. | [Host providers](resources/host/README.md) |
| Linux rental, including a GB10/UMA allocation | Legacy process tracking or the separate v2 allocation-scoped cleanup profile; fresh capacity is checked separately. | [Host providers](resources/host/README.md) and [managed allocation v2](resources/host/linux/MANAGED-ALLOCATION.md) |
| macOS | Owned session and process lifecycle supervision. | [Host providers](resources/host/README.md) |
| Windows x64 | Job Object supervision and native process identity. | [Host providers](resources/host/README.md) |

With Python 3.11+ in a reviewed checkout, choose a new output directory with an existing parent.
For example, assemble a Linux bundle beside this checkout:

```sh
python3 -I -B tools/build_resource_adapter.py assemble linux ../example-postmq-linux
```

Save the printed `manifest_sha256` value outside the bundle. Replace `REVIEWED_SHA256` below with that
value to verify the exact files:

```sh
python3 -I -B tools/build_resource_adapter.py verify ../example-postmq-linux --manifest-sha256 REVIEWED_SHA256
```

Targets are `linux`, `macos` and `windows`. These commands create and verify source files only; they
need no credential, register no resource and launch no job. A matching hash identifies the selected
bytes, not a trusted publisher. Read the [owner integration requirements](resources/host/README.md#owner-integration-and-compatibility)
before execution and the [publishing guide](docs/publishing.md#maintained-adapter-source-distribution)
for distribution boundaries. Source availability does not certify every device, model or unattended setup.

## Repository map

| Path | What is here |
| --- | --- |
| [plugins/](plugins/) and [.claude-plugin/](.claude-plugin/) | Generated installable Claude Code plugin content; existing marketplace name `postmq`. |
| [spec/](spec/README.md) | Proposed portable PmP coordination requirements and scope. |
| [interop/a2a/](interop/a2a/README.md) | Proposed A2A mapping and compatibility limits. |
| [schemas/](schemas/README.md) | Schema publication rules; no released PmP wire schema yet. |
| [sdk/](sdk/README.md) | Proposed PmP language SDK requirements; the existing claim SDK ships in `postmq-claim`. |
| [resources/](resources/README.md) | Maintained host adapter sources, NVIDIA observers and hardware profile boundaries. |
| [examples/](examples/README.md) | Source-bundle quickstart and requirements for future runtime examples. |
| [conformance/](conformance/README.md) | Required evidence and proposed interoperability cases. |
| [docs/](docs/README.md) | Maintained repository, publishing and security guidance. |

## Contribute and verify

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md). Public contribution checks
need 64-bit Python 3.11 or newer, Git, a C compiler supporting shared libraries and this checkout.
The hosted gate runs on Ubuntu; Linux and macOS can run the offline source/CPU controls locally.
They need no PostMQ account, private repository,
cloud subscription or API key:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_tests.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/check_public_repo.py
git diff --check
```

Checks validate repository structure, local documentation links, generated ownership and CI policy.
They do not run live plugin hooks or certify PmP behavior. Generated plugin corrections go through
the canonical kit and the [publishing process](docs/publishing.md), with a reviewed public PR.
Maintained documentation is edited here and is never regenerated by that publisher.

These POSIX-shell commands avoid ignored Python bytecode caches, which the publisher's clean-worktree
check would reject. On other shells, `python3 -B tools/run_tests.py` and `python3 -B tools/check_public_repo.py`
disable bytecode for those invocations. The release check compares committed `HEAD` with its first parent;
keep both commits available. Before committing, local uncommitted changes still need review and the
working-tree ownership check; CI checks the committed PR merge against its target-branch parent.

Copyright No Compromise AI, LLC and contributors. Distributed under [Apache License 2.0](LICENSE).
See [SECURITY.md](SECURITY.md) for private vulnerability reporting.
