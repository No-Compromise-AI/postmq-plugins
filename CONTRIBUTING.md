# Contributing

Issues and pull requests are welcome. Public visibility lets anyone propose a change; it does not
grant write access, execution permission, resource access or publishing authority. Report vulnerabilities
[privately](SECURITY.md), not in a public issue.

Read the [charter](CHARTER.md) and [governance process](GOVERNANCE.md). Protocol proposals
must explain their charter, security and compatibility impact, including how an independent
implementation can meet the same public requirements. Competing implementations are welcome.

## Choose the source

Maintained guidance, proposed specifications, interoperability profiles and repository checks are edited
here. [Generated plugins](docs/publishing.md) come from the canonical PostMQ kit. Report a defect with
a sanitized reproduction; maintainers fix its source and publish a reviewed update. External contributors
need no private kit access, planning records or paid AI reviewer to participate.

Start from current `main` in an isolated branch or worktree. Read the existing solution before adding
another. Keep changes focused; use separate PRs for unrelated repairs. Never reset, clean, stash or
overwrite another contributor's work to prepare a patch.

## Required evidence

Run the three commands in the [README](README.md#contribute-and-verify). State tested commit, commands,
outcomes and limitations in the PR. Keep failures visible; do not weaken checks, hide skips or increase
timeouts merely to get a passing result.

Develop a meaningful regression with executable changes. Demonstrate failure with missing or broken
behavior, restore the fix, and capture green. A syntax error is not behavioral evidence. Integration
behavior needs real dependencies and disposable data; controlled fault simulation supplements it.
Prose changes need link, consistency and semantic review, not invented coverage.

A PR does not authorize live messages, billable calls, active-session interaction, machine or infrastructure
changes. Obtain the owner's authority separately. Never submit credentials, customer content, private
transcripts, hostnames or machine paths as fixtures. Use generic identities and synthetic examples.

## Review and publication

All changes use a PR and `Public repository checks`. Maintainers review behavior, security, compatibility,
license provenance and scope, including CI/tooling. Independent reviewers examine the final affected
change; rerun affected checks after edits. Resolve every real finding or record the owner decision or
unavailable prerequisite that blocks it. AI-assisted authors remain responsible for their work.

Distinguish implemented, verified and proposed work. A protocol document is not a working service;
a local check is not live-client qualification. Preserve existing marketplace names and install paths.
Breaking public contracts need a version and migration plan. Maintainers merge after final checks and
review. Publishing and security settings have [separate verification](docs/publishing.md).

Contributions intentionally submitted for inclusion are under [Apache License 2.0](LICENSE), unless
explicitly stated otherwise. Disclose third-party material and retain required notices. Do not copy
proprietary code, private records or third-party protocol text into this repository.
