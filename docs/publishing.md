# Publishing ownership

## Generated and maintained files

The existing Claude marketplace/plugins come from the canonical PostMQ kit. This public repository is
their distribution and review surface. Maintained guidance and proposed specifications are authored here;
contributors need no private kit access to edit or check them.

`.postmq-generated.json` records `schema_version: 1`, generator `postmq/claude-plugin`, three
`plugin_versions`, and a `files` map with each file's SHA-256 and executable flag. It excludes itself.
Permitted destinations are `.claude-plugin/marketplace.json`, optional `.claude-plugin/README.md`, and
the complete trees under `plugins/postmq-session`, `plugins/postmq-wake` and `plugins/postmq-claim`.
Paths are normalized relative regular files, without symlink traversal. Public checks reject unowned
files in those namespaces, missing files, byte/mode drift and ownership of maintained files.

The current manifest owns 19 files: the marketplace, the generated `.claude-plugin/README.md`, and
the three plugin trees at version 1.11.0. The publisher must never overwrite root `README.md`, license,
policy, specs, schemas, SDK/resource guidance, examples, conformance, checks or `.github`. Removing obsolete generated files
requires evidence of prior ownership and unchanged content; never sweep directories or discard edits.

## Release procedure

1. Fix generated behavior in the canonical kit and verify the integration there. Export only explicitly
   public artifacts, never private planning records, credentials or transcripts.
2. Generate into an isolated checkout and compare ownership before applying changes. Refuse unexpected
   files, changed generated content, symlinks, path escapes and version regressions. Changing an installed
   plugin requires a higher plugin version; preserve marketplace/install identities.
3. Run the [public gate](../README.md#contribute-and-verify), inspect the full diff and obtain independent
   review of the final change. Publishing credentials stay outside public CI.
4. Open a PR. Publishers cannot bypass integrity checks or write directly to protected main. Verify
   final-head CI, obtain required review, merge with authority and verify the resulting tree.
5. Release tags identify that verified main commit and are immutable. Verify downloadable manifests and
   installation paths before announcing availability. Publication does not authorize installing into
   user sessions or enabling wake/resource access for them.

Future SDK packages, adapters and PmP schemas require their own reviewed publishing design and evidence.
This procedure creates no registries, deployed services or implementation of proposed PmP/A2A behavior.

## Dependency and license maintenance

Dependabot proposes GitHub Actions updates. Verify each full hash against its official repository/release,
review and test it. Initial checkout is [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1),
commit `3d3c42e5aac5ba805825da76410c181273ba90b1`. The public gate uses hosted Python without third-party
Python packages. Versioned settings do not prove live settings; use the [readback procedure](repository-security.md#verification-and-changes).

The workflow and Dependabot files use JSON syntax, which is valid YAML, so their exact policy can be
checked with Python's standard library. CI fetches two commits and checks the proposed commit's patch
against its first parent, including GitHub's synthetic PR merge commits. Locally, `git diff --check`
also checks your uncommitted patch. The test runner rejects empty discovery, skipped tests and failures.

[LICENSE](../LICENSE) is the unmodified [official Apache 2.0 text](https://www.apache.org/licenses/LICENSE-2.0.txt).
No third-party implementation is imported by this foundation. Later imports require provenance,
compatibility and notice review. Do not invent attribution or imply licensing grants access to external
services, trademarks or proprietary client runtimes.
