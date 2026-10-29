# Security reporting

Report suspected vulnerabilities through the repository's
[private GitHub reporting form](https://github.com/No-Compromise-AI/postmq-plugins/security/advisories/new).
Do not put exploit details, credentials or customer data in public issues or PRs. If the form is unavailable,
you may open a public issue saying only that private reporting is unavailable, with no vulnerability details.
Maintainers must restore or provide a private reporting channel before requesting those details.

Include affected version/revision, the security boundary, a minimal sanitized reproduction, expected and
actual behavior and known impact. Remove authentication headers, keys, personal content and private
infrastructure details. Use disposable data and do not test accounts, agents or resources you do not own.
Stop if reproduction would require unauthorized actions.

Maintainers assess current published plugins and proposed specifications. There is no long-term-support
version or response-time promise. A proposed protocol is not a certified implementation. Maintainers
coordinate correction, review, regression evidence and disclosure with the reporter.

Public CI uses disposable hosted runners and no private publishing credentials. See [repository safeguards](docs/repository-security.md)
and [publishing ownership](docs/publishing.md). Static checks do not prove absence of secrets or malicious
behavior. If a credential was exposed, its owner should revoke/rotate it through its issuer; deleting a
Git commit alone does not undo exposure.
