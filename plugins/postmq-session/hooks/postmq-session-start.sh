#!/usr/bin/env bash
# postmq-session-start — a Claude Code SessionStart hook.
#
# Prints the session protocol reminder and the drift signals that can be computed LOCALLY, from git,
# in well under a second. It deliberately makes no network call and no MCP call: a SessionStart hook
# runs before the agent does anything, and a slow or flaky one is worse than no hook at all.
#
# It prints exactly what it can see, and nothing it cannot. If you extend it, keep that property —
# a hook credited with a signal it does not actually print is worse than a hook without the signal,
# because everyone downstream believes the check happened.
#
# Portable to bash 3.2 (macOS ships it), no globstar, no mapfile, no associative arrays.
set -uo pipefail

say() { printf '%s\n' "$*"; }

say "== PostMQ session protocol =="
say "  - /start-session before ANY material work; /end-session before you stop."
say "  - Material = a feature, a behaviour-changing fix, a schema or migration change,"
say "    a public-contract change, or a scope change."
say "  - Work on a branch off the main branch, not on it."

# Everything below needs a git repository. Outside one, say so and stop rather than printing noise.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "  (not a git repository — no drift signals)"
  exit 0
fi

# The default branch, as this clone actually records it — never assumed to be `main`.
default_ref="$(git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_ref##*/}"
[ -n "$default_branch" ] || default_branch="main"

say ""
say "== drift =="
if git rev-parse --verify --quiet "origin/${default_branch}" >/dev/null 2>&1; then
  age="$(git log -1 --format='%cr' "origin/${default_branch}" 2>/dev/null || echo 'unknown')"
  sha="$(git log -1 --format='%h' "origin/${default_branch}" 2>/dev/null || echo '???????')"
  say "  origin/${default_branch} last commit: ${age} (${sha})"
  behind="$(git rev-list --count "HEAD..origin/${default_branch}" 2>/dev/null || echo '?')"
  say "  this branch is ${behind} commit(s) behind origin/${default_branch}"
else
  say "  no origin/${default_branch} — fetch first, or this clone has no remote"
fi

dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
say "  uncommitted files here: ${dirty}"

# Other working copies: a peer session's uncommitted work is the single most useful thing to see
# before starting, because duplicating it is the most common way parallel sessions waste a day.
if git worktree list >/dev/null 2>&1; then
  count="$(git worktree list --porcelain 2>/dev/null | grep -c '^worktree ' || true)"
  if [ "${count:-0}" -gt 1 ]; then
    say ""
    say "== other working copies =="
    git worktree list 2>/dev/null | sed 's/^/  /'
    # Single quotes: inside double quotes those backticks would be COMMAND SUBSTITUTION, and this
    # line would silently run git instead of printing advice about it.
    say '  (run: git -C <path> status --short — before starting work one of them may already hold)'
  fi
fi

# Open pull requests, only when the GitHub CLI is present AND authenticated. A hook that prints an
# auth error on every session start trains people to ignore its output.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  prs="$(gh pr list --state open --limit 10 --json number,title,mergeStateStatus \
        --template '{{range .}}  #{{.number}} [{{.mergeStateStatus}}] {{.title}}{{"\n"}}{{end}}' 2>/dev/null || true)"
  if [ -n "$prs" ]; then
    say ""
    say "== open pull requests =="
    printf '%s' "$prs"
  fi
fi

exit 0
