#!/usr/bin/env bash
# postmq-session-start — the session-start drift signals, for every client the kit installs into.
#
# A SessionStart hook on Claude Code and Codex; wrapped by postmq-session-start.zcode.sh on ZCode,
# which discards plain-text hook output; and on Antigravity not a hook at all — nothing fires there,
# so the start-session SKILL runs this script itself.
#
# The hook clients run it the same way: they hand it a JSON payload on stdin and put whatever it
# prints into the model's context before the first turn. This script reads nothing from that payload
# and needs no client-specific variable — it runs with its working directory set to the project root
# on all of them, so plain `git` is all it needs. Keep it that way: the moment it reaches for
# $CLAUDE_PROJECT_DIR or a per-client equivalent, it stops being one file.
#
# Prints the session protocol reminder and the drift signals that can be computed LOCALLY, from git,
# in well under a second. It makes no MCP call, and exactly ONE network call: the open pull-request
# list, through the GitHub CLI, and only under a hard deadline (PR_LIST_DEADLINE_TENTHS below). A
# SessionStart hook runs before the agent does anything, and a slow or flaky one is worse than no
# hook at all, so the deadline is what lets the PR list stay: when GitHub is slow, the list is
# dropped and the session starts on time.
#
# It used to claim "no network call" while running `gh auth status` and `gh pr list` synchronously,
# both of which go to the network, and `gh auth status` checks EVERY configured account (GitHub
# #1387 item 15). The auth probe is gone, not bounded: `gh pr list` already fails without
# credentials, and its error goes to /dev/null, so the probe only ever added a second round trip.
#
# It prints exactly what it can see, and nothing it cannot. If you extend it, keep that property —
# a hook credited with a signal it does not actually print is worse than a hook without the signal,
# because everyone downstream believes the check happened.
#
# Portable to bash 3.2 (macOS ships it), no globstar, no mapfile, no associative arrays.
set -uo pipefail

say() { printf '%s\n' "$*"; }

say "== PostMQ session protocol =="
say "  - start-session before ANY material work; end-session before you stop."
say "    (Claude Code: /start-session, /end-session.  Codex: \$start-session, \$end-session.)"
say "  - Material = a feature, a behaviour-changing fix, a schema or migration change,"
say "    a public-contract change, or a scope change."
say "  - Work on a branch off the main branch, not on it."

# Which kit this is, read from the install stamp one directory above this script — `.claude/`,
# `.codex/`, `.zcode/` or `.agents/` depending on the client, and the plugin's own root in the Claude
# Code plugin. Found from the script's OWN location, so it needs no per-client variable, and read
# BEFORE the git check because a kit outside a repository is still a kit.
#
# It prints what is INSTALLED and never claims to know what is current: that would need a network
# call this script does not make. The PostMQ server names the current versions in the instructions
# it sends an agent on connect, and the agent compares the two. A kit from before 1.4.0 prints no
# block at all, which is how the server's instructions recognise one.
#
# The stamp is project content, so a cloned repository can put anything in it, and this text lands
# in an agent's context. Nothing from it is printed unless it has the shape of a component id or a
# MAJOR.MINOR.PATCH version, and no more than its first 64 KiB is read — a real stamp is a few hundred
# bytes, and a hook must not be stalled by a file somebody made huge.
kit_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || kit_dir=""
stamp="${kit_dir}/postmq-kit.json"
say ""
say "== PostMQ kit =="
if [ -n "$kit_dir" ] && [ -f "$stamp" ]; then
  stamp_name="$(basename "$kit_dir")/postmq-kit.json"
  flat="$(head -c 65536 "$stamp" 2>/dev/null | tr -d '\n\r')"
  kit_version="$(printf '%s' "$flat" | sed -n 's/.*"kit_version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  case "$kit_version" in
    *[!0-9.]*|'') kit_version="" ;;
  esac
  # `components` is a flat id -> version map by contract, so the first `}` after it closes it.
  installed="$(printf '%s' "$flat" \
    | sed -n 's/.*"components"[[:space:]]*:[[:space:]]*{\([^}]*\)}.*/\1/p' \
    | tr ',' '\n' \
    | sed -n 's/^[[:space:]]*"\([a-z0-9][a-z0-9-]*\)"[[:space:]]*:[[:space:]]*"\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)"[[:space:]]*$/\1 \2/p' \
    | tr '\n' ',' | sed 's/,$//; s/,/, /g')"
  if [ -n "$kit_version" ] && [ -n "$installed" ]; then
    say "  installed: kit ${kit_version} — ${installed} (${stamp_name})"
  else
    say "  installed: unreadable — ${stamp_name} does not say which versions it holds"
  fi
else
  say "  installed: unknown — no postmq-kit.json beside the kit's scripts, so nothing records which"
  say "  version this is. Install the kit again to record one."
fi
say "  current versions: https://postmq.com/kit/manifest.json — and an agent connected to PostMQ is told"
say "  them when it connects. If any installed one is older, the kit here is out of date."

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

# Open pull requests, only when the GitHub CLI is present, and only if it answers inside the deadline.
# Its stderr goes to /dev/null: a hook that prints an auth error on every session start trains people
# to ignore its output, and an unauthenticated `gh` simply prints no list.
#
# The deadline is hand-rolled because macOS ships no `timeout`: run the call in the background, poll
# it every tenth of a second, and kill it at the deadline. The output goes to a temporary file rather
# than through $(…), because a command substitution waits for the call however long it takes, which
# is the exact thing the deadline exists to stop. A list cut off at the deadline is printed as
# nothing, never as part of a list.
#
# The override is normalised before anything compares it: a non-number made every `-ge` test fail,
# so a hanging gh was never killed, and `08` passed the test but broke the later arithmetic as an
# octal literal, skipping the cleanup. Anything but digits falls back to the default; `10#` reads
# the digits as decimal whatever their leading zeros. (Both found by Codex.)
PR_LIST_DEADLINE_TENTHS="${POSTMQ_PR_LIST_DEADLINE_TENTHS:-20}"
case "$PR_LIST_DEADLINE_TENTHS" in
  ''|*[!0-9]*) PR_LIST_DEADLINE_TENTHS=20 ;;
esac
PR_LIST_DEADLINE_TENTHS=$((10#$PR_LIST_DEADLINE_TENTHS))
if command -v gh >/dev/null 2>&1; then
  prs_file="$(mktemp "${TMPDIR:-/tmp}/postmq-session-start.XXXXXX" 2>/dev/null)" || prs_file=""
  if [ -n "$prs_file" ]; then
    gh pr list --state open --limit 10 --json number,title,mergeStateStatus \
      --template '{{range .}}  #{{.number}} [{{.mergeStateStatus}}] {{.title}}{{"\n"}}{{end}}' \
      >"$prs_file" 2>/dev/null </dev/null &
    gh_pid=$!
    waited=0
    finished=1
    while kill -0 "$gh_pid" 2>/dev/null; do
      if [ "$waited" -ge "$PR_LIST_DEADLINE_TENTHS" ]; then
        finished=0
        break
      fi
      sleep 0.1
      waited=$((waited + 1))
    done
    # One group with stderr discarded, from the kill through the wait: bash announces a killed
    # background job ("Terminated: 15 gh pr list ...") on its OWN stderr when it next reaps it, which
    # can be any command after the kill, so redirecting the wait alone does not silence it.
    {
      if [ "$finished" -eq 0 ]; then
        # TERM first, and KILL if it is still there half a second later: a bare `wait` after a TERM
        # the process ignores blocks for as long as it likes, which is the wait this deadline exists
        # to stop. (Found by Codex.)
        kill "$gh_pid"
        grace=0
        while kill -0 "$gh_pid" && [ "$grace" -lt 5 ]; do
          sleep 0.1
          grace=$((grace + 1))
        done
        kill -9 "$gh_pid"
      fi
      wait "$gh_pid"
      status=$?
    } 2>/dev/null
    if [ "$finished" -eq 1 ] && [ "$status" -eq 0 ] && [ -s "$prs_file" ]; then
      say ""
      say "== open pull requests =="
      cat "$prs_file"
    elif [ "$finished" -eq 0 ]; then
      say ""
      say "  (open pull requests: GitHub did not answer within $((PR_LIST_DEADLINE_TENTHS / 10)).$((PR_LIST_DEADLINE_TENTHS % 10))s — skipped; run: gh pr list)"
    fi
    rm -f "$prs_file"
  fi
fi

exit 0
