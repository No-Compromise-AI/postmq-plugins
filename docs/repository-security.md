# Repository safeguards

Public visibility allows reading, forking and proposing pull requests. It does not grant write access.
Contributors keep changes in their forks; maintainers review the proposal before it can reach `main`.
Existing access grants are managed separately from this repository's content. No new collaborator access
is granted by the configuration below.

## Protected changes

The desired GitHub configuration is versioned in [repository-settings.json](../.github/repository-settings.json)
and [the rulesets directory](../.github/rulesets/). These files document policy; GitHub must also have the
same settings applied. A local check cannot prove the live settings are active.

- `main-integrity` has **no bypass actor**. It requires a pull request, resolved review conversations,
  current successful `Public repository checks` from GitHub Actions, and linear history. Main cannot
  be deleted or force-pushed. Squash is the permitted merge method.
- `main-review` requires one approving code-owner review, dismisses stale approvals and requires approval
  after the last reviewable push. Its only exception is a pull-request-only bypass for the repository
  owner's `nocompromiseai` account. GitHub does not allow an author to approve their own pull request.
  An owner-authored maintenance change may use this exception only with explicit owner authorization,
  independent review and recorded validation on the final change. It is not a substitute for maintainer
  review of outside contributions. Record use of the exception in the pull request.
- The owner exception belongs only to `main-review`. It cannot bypass the separate integrity ruleset,
  its checks or its pull-request requirement. Automated publishers receive no bypass authority.
- `immutable-tags` prevents updates and deletion of existing tags, without a bypass. Creating a tag
  still requires repository write access. Release tags must identify a verified commit on main.

These are repository ownership controls, not PmP identity or routing rules. The protocol contains no
individual-specific behavior. Administrators retain the ability to change GitHub settings; configuration
changes themselves require a reviewed proposal and a readback of the resulting live policy.

## Untrusted contributions and credentials

The configured policy requires maintainer approval of fork workflows for every external contributor. Approval permits
running code, so inspect workflow and script changes before approving a run. Public CI uses disposable
GitHub-hosted runners, a read-only token, no repository secrets, no persistent credentials and no
`pull_request_target` execution. Never move public pull-request jobs onto private self-hosted runners.

The desired configuration permits only the listed checkout and Python setup actions, pinned to full commit hashes.
It prevents workflow tokens from approving pull requests and enables dependency alerts, automated security fixes,
secret scanning and push protection. Confirm their live state with the readback procedure below; this document
alone is not evidence they remain enabled. These controls reduce risk; they do not prove that a contribution contains no secrets
or malicious behavior. Report suspected vulnerabilities through [SECURITY.md](../SECURITY.md).

Publishing produces a reviewable branch and pull request. Build and check generated content locally;
do not give untrusted workflow code a private source checkout, publishing token or deployment secret.
Changing an installed plugin's content requires a higher plugin version. The maintained source and
documentation directories are outside the publisher's ownership.

## Verification and changes

After applying a reviewed settings change, retrieve the repository's rulesets, effective rules for main,
Actions permissions and selected actions, workflow token permissions, fork contributor approval policy,
security features and private vulnerability reporting status through GitHub's API. Compare them with
the versioned configuration. Record identifiers and verification results in the pull request; do not
commit credentials or token-bearing output. Confirm required checks on the final pull-request head and
verify the merged tree afterward. Recheck permissions and settings before changing the publishing path.

Reference: [GitHub repository rules API](https://docs.github.com/en/rest/repos/rules),
[Actions permissions API](https://docs.github.com/en/rest/actions/permissions), and
[secure use of GitHub Actions](https://docs.github.com/en/actions/reference/security/secure-use).
