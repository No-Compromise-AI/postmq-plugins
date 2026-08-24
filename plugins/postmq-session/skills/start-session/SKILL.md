---
name: start-session
description: Open a PostMQ build session — read the project's context files, recover where the last session stopped (active session, recent decisions, backlog counts), surface drift against the main branch, then resume or start a session. Run BEFORE any material work — a new feature, a behaviour-changing fix, a schema or migration change, a public-contract change, or a scope change.
---

# /start-session

Recovers where the last session stopped, then opens (or resumes) a build session so everything you
do next is attributed to it.

Session state lives in PostMQ, not in files. Nothing here writes a local ledger.

## When to run it

Before **any material work**: a new feature, a fix that changes behaviour, a schema or migration
change, a public API or wire-contract change, a security-posture change, a scope change.

Not before answering a question, reading code, or a trivial typo fix.

If you pivot mid-session to unrelated work, that is a new session — run this again.

## Steps

1. **Read the project's own rules.** Whatever this repository uses — `CLAUDE.md`, `AGENTS.md`,
   `CONTRIBUTING.md`, a `docs/` convention file. Re-read them; they change between sessions and a
   remembered version is the one that is wrong.

2. **Recover where the last session stopped.** Three calls, in this order:

   - `mcp__postmq__list_build_sessions` with `status="Active"` — the resume target.
   - `mcp__postmq__list_decision_log` with `limit=10` — what was decided recently, and why.
   - `mcp__postmq__count_backlog_items_by_status` — what is open.

   If an active session exists, read its `current_state_markdown` first. That is the lead the last
   session left for you, and it is the single highest-value thing on this list.

3. **Surface drift.** Compare the age of the main branch's last commit against the most recent
   decision-log entry and build-session close. If anything is more than a day stale and you are
   about to do material work, say so before starting — a stale picture is how two sessions do the
   same work twice.

   Also check what is already in flight: open pull requests, and any other working copies with
   uncommitted changes. Starting work a peer session has already shipped is the most common way
   this goes wrong.

4. **Pick a branch.** Material work belongs on its own branch off the main branch, not on main.

5. **Resume or start the session.**

   - If an active session matches the work you are about to do, resume it and
     `mcp__postmq__ping_build_session` to show it is alive.
   - Otherwise `mcp__postmq__start_build_session` with `computer`, `branch`, `worktree_path` and a
     specific `intent` — what you are about to do and why, not a restatement of the ticket title.

   `start_build_session` is **resume-or-create**: the same project, computer, branch and actor gets
   the same session back (200) rather than a second one (201). You do not have to check first.

6. **Ask which rules apply — before you touch a file.** `mcp__postmq__query_applicable_rules`,
   passing the file paths, operations, languages, code patterns and project attributes of the change
   you are about to make. Only the rules whose every populated trigger dimension matches come back,
   so the answer is short enough to actually read.

## What good looks like

You should be able to answer, out loud, before writing any code: what the last session decided, what
is still open, what is in flight elsewhere, and which rules apply to the change you are about to
make. If you cannot, you have not finished this skill.

## If the calls fail

Every `mcp__postmq__*` call is scoped by the credential. If they fail closed, the credential is
missing, expired, or scoped to a different workspace — fix that rather than proceeding without a
session. Writes need the `write_session_state` scope; reads need only an authenticated caller.

Skipping the protocol is itself a decision. Never skip it silently — say that you are skipping it,
and why.
