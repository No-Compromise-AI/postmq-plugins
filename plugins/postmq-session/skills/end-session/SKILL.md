---
name: end-session
description: Close a PostMQ build session — append a decision-log entry for every material decision, file a backlog item for everything deferred, resolve what you finished, verify CI on every PR you touched, then end the session with a history entry the next session reads first. Run before you stop.
---

# end-session

Closes the session so the next one starts informed. The expensive half is steps 1 and 2 — a session
that ends without them leaves the work done and the reasoning lost, which is the exact failure this
kit exists to prevent.

## Steps

1. **Log every material decision.** `mcp__postmq__append_decision_log` — `title`, `body_markdown`,
   and where relevant `entry_date`, `has_public_contract_impact`, `files_touched_count` (a number,
   not a list of paths), `source_pr_url`.

   One entry per decision, not one per session. A decision is material if a reasonable reviewer
   would ask "why did you do it that way?" — including the ones where you chose *not* to do
   something, and why.

   The log is **append-only**. If you got something wrong in an earlier entry, do not edit it:
   `mcp__postmq__correct_decision_log` writes a new entry pointing at the one it supersedes, so the
   reasoning stays legible instead of being quietly rewritten.

   An omitted `entry_date` defaults to today's date in UTC. Set it (`yyyy-MM-dd`) when the decision
   was made on another day, or when your local date is not the UTC one; a past date marks the
   entry as backfilled.

2. **File everything you deferred.** `mcp__postmq__file_backlog_item` — `title`, `body_markdown`,
   `priority`, `category`. In the body: why it was deferred, what done looks like, and what should
   make someone pick it up.

   No "I'll get to it later" without a row. If you finished items this session, close them with
   `mcp__postmq__triage_backlog_item` — resolving needs closing notes or a closing PR, dismissing
   needs a reason. Terminal states are absorbing: a resolved item does not silently reopen.

3. **Pre-merge guard.** For each branch, merge the main branch in locally and resolve conflicts now
   — at session close, in seconds — rather than after CI has started and the queue is behind you.

3b. **Reconcile the PR list.** `mcp__postmq__record_build_session_prs` with every PR this session
   produced, in the `related_prs` shape step 5 describes.

   The primary instruction for this is in **start-session**, under "While the session runs": record
   each PR at the moment you open it, because a session that crashes or runs out of context never
   reaches this skill at all — and a close-time-only record loses exactly those sessions. This step
   is the reconciliation pass, not the first time you should be calling it.

   It REPLACES the list, so send them all; re-sending an identical list changes nothing.

4. **Verify CI on every PR you touched.** This blocks the close.

   For each PR, read its state, merge state and check rollup. Classify it: **merged** (and confirm
   the merge actually contains your latest commit — a push exiting 0 is not the same as shipped),
   **in flight with every check green**, or **failed and fixed in this session**.

   If any PR you touched has a failing check or a dirty merge state, do not close the session. Fix
   it. Moving on with a broken PR in flight is how the next session inherits a problem it cannot
   see.

   **Any failing check, not only the required ones.** A repository with no branch protection has no
   required checks at all, so a rule about required checks blocks nothing there. A check you have
   good reason to treat as unrelated to your change (a known-flaky job, an outage elsewhere) does not
   block, but only if you name it and give the reason in the history entry at step 5. A failing
   check nobody mentions is the one that turns out to have mattered.

5. **Close the session.** `mcp__postmq__end_build_session` with `build_session_id`, `related_prs`,
   and a `session_history_entry_markdown` that carries what you verified in step 4 plus a short
   summary.

   `related_prs` takes the PRs themselves, not just their links: each entry needs a `url`, and the
   provider, number and repository are parsed from it — `github.com` and `dev.azure.com`, and also
   GitHub Enterprise and Azure DevOps Server, which are recognised by url SHAPE rather than by
   host. Supply `provider`, `number` or `repository` only to override what the url says. Add
   `work_item` when the PR closes an issue or a work item.

   ```json
   {
     "build_session_id": "01K…",
     "session_history_entry_markdown": "…",
     "related_prs": [
       {
         "url": "https://github.com/acme/api/pull/412",
         "work_item": { "url": "https://github.com/acme/api/issues/398" }
       }
     ],
     "related_commit_shas": ["39b8680"]
   }
   ```

   **It REPLACES the list, and the session is closed afterwards.** Send every PR the session
   produced, not just the last one. If you already recorded PRs with the tool in step 3b, either
   send them all again here or omit `related_prs` entirely — omitting keeps what is stored, sending
   a partial list loses the rest, and after the close there is no correcting it.

   The older `related_pr_urls` still works and is still parsed into the same structured entries, so
   nothing that already calls it breaks. Send one or the other: supplying both is an error rather
   than one quietly winning.

   **Azure DevOps work items:** the url never says which TYPE an item is — `_workitems/edit/1234`
   is identical for a user story, a bug and a task — so pass `work_item.kind` yourself if you want
   it recorded. Whatever your process template calls it is fine; it is stored as you send it.

   Before that, `mcp__postmq__update_build_session_state` is worth one more pass: its
   `current_state_markdown` is the first thing the next session reads. Write it for someone who was
   not here — what shipped, what is half-done, what to pick up next, and any trap you hit.

6. **Report what the session consumed.** `mcp__postmq__record_build_session_usage`, if a hook is not
   already doing it for you. It is idempotent per transcript, so a re-report overwrites and never
   double-counts.

   **On Antigravity nothing is doing it for you** — the kit wires no hook there — so this step is the
   only thing that records usage, and skipping it loses the session's numbers entirely. If the usage
   reporter is installed (it is a separate component from the session-state kit; the file below
   will simply not exist otherwise), run it rather than calling the tool with figures you would have
   to guess: it reads the counts the client itself recorded, out of the conversation database.

   ```bash
   python3 .agents/tools/postmq-usage-reporter.py --antigravity --dry-run
   ```

   Run it from the **repository root** — the path is relative. Check the figures, then send —
   **naming the database the dry run printed**, not by re-running discovery:

   ```bash
   python3 .agents/tools/postmq-usage-reporter.py --antigravity <the "transcript" path it printed>
   ```

   The preview and the send are two independent lookups; between them the answer can change, and
   the figures you approved would not be the ones sent.

   `--antigravity` picks the most recently written conversation that **could** be this session —
   one recording a workspace that contains this directory, or recording none at all. A conversation
   on some other project is ignored. If the newest such conversation cannot be identified, or two
   were written in the same moment, it refuses and says why rather than guessing: a machine holds
   hundreds of these and reporting the wrong one puts another conversation's tokens on this build
   session. Name the database as an argument when it does:

   ```bash
   python3 .agents/tools/postmq-usage-reporter.py --antigravity ~/.gemini/antigravity/conversations/<id>.db
   ```

   (`~/.gemini/antigravity-cli/conversations/` for a CLI session.) It also refuses when two
   conversations on this workspace were written too close together to tell apart — a workspace is
   not one conversation — and naming the database is what settles that.

   If `/start-session` could not write the pointer, the reporter has good totals and no session to
   attribute them to. Give it one:

   ```bash
   python3 .agents/tools/postmq-usage-reporter.py --antigravity <path-to.db> --build-session <id>
   ```

   It refuses to send a report with no turns in it, or one it could only read in part, so a
   database it could not read will never overwrite what the session already recorded.

7. **Clean up.** Remove any worktree you created and delete the merged branch — **except the worktree
   this session is running in.** The usage reporter runs from the session's own project directory
   when the session ENDS, after this skill has finished. Remove that directory now and the hook
   finds no script to run, so the session records no usage, without any error. Leave that one
   worktree for the next session to clean up, or remove it yourself after you exit. (On Antigravity
   no hook runs at the end; step 6 has already reported, so this exception does not apply there.)

## The one that gets skipped

Step 1. It always feels like the work is done and the log is paperwork. It is the opposite: the code
is recoverable from the diff, and the reasoning is not recoverable from anywhere. A month later the
decision log is the only thing that answers "why is it like this?".
