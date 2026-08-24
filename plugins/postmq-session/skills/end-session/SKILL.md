---
name: end-session
description: Close a PostMQ build session — append a decision-log entry for every material decision, file a backlog item for everything deferred, resolve what you finished, verify CI on every PR you touched, then end the session with a history entry the next session reads first. Run before you stop.
---

# /end-session

Closes the session so the next one starts informed. The expensive half is steps 1 and 2 — a session
that ends without them leaves the work done and the reasoning lost, which is the exact failure this
kit exists to prevent.

## Steps

1. **Log every material decision.** `mcp__postmq__append_decision_log` — `title`, `body_markdown`,
   `entry_date`, and where relevant `has_public_contract_impact`, `files_touched`, `source_pr_url`.

   One entry per decision, not one per session. A decision is material if a reasonable reviewer
   would ask "why did you do it that way?" — including the ones where you chose *not* to do
   something, and why.

   The log is **append-only**. If you got something wrong in an earlier entry, do not edit it:
   `mcp__postmq__correct_decision_log` writes a new entry pointing at the one it supersedes, so the
   reasoning stays legible instead of being quietly rewritten.

   **Set `entry_date` explicitly.** An omitted date does not default to today.

2. **File everything you deferred.** `mcp__postmq__file_backlog_item` — `title`, `body_markdown`,
   `priority`, `category`. In the body: why it was deferred, what done looks like, and what should
   make someone pick it up.

   No "I'll get to it later" without a row. If you finished items this session, close them with
   `mcp__postmq__triage_backlog_item` — resolving needs closing notes or a closing PR, dismissing
   needs a reason. Terminal states are absorbing: a resolved item does not silently reopen.

3. **Pre-merge guard.** For each branch, merge the main branch in locally and resolve conflicts now
   — at session close, in seconds — rather than after CI has started and the queue is behind you.

4. **Verify CI on every PR you touched.** This blocks the close.

   For each PR, read its state, merge state and check rollup. Classify it: **merged** (and confirm
   the merge actually contains your latest commit — a push exiting 0 is not the same as shipped),
   **in flight with required checks green**, or **failed and fixed in this session**.

   If any PR you touched has a failing required check or a dirty merge state, do not close the
   session. Fix it. Moving on with a broken PR in flight is how the next session inherits a problem
   it cannot see.

5. **Close the session.** `mcp__postmq__end_build_session` with `build_session_id`,
   `related_pr_urls`, and a `session_history_entry_markdown` that carries what you verified in
   step 4 plus a short summary.

   Before that, `mcp__postmq__update_build_session_state` is worth one more pass: its
   `current_state_markdown` is the first thing the next session reads. Write it for someone who was
   not here — what shipped, what is half-done, what to pick up next, and any trap you hit.

6. **Report what the session consumed.** `mcp__postmq__record_build_session_usage`, if a hook is not
   already doing it for you. It is idempotent per transcript, so a re-report overwrites and never
   double-counts.

7. **Clean up.** Remove any worktree you created and delete the merged branch.

## The one that gets skipped

Step 1. It always feels like the work is done and the log is paperwork. It is the opposite: the code
is recoverable from the diff, and the reasoning is not recoverable from anywhere. A month later the
decision log is the only thing that answers "why is it like this?".
