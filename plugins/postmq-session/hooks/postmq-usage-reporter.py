#!/usr/bin/env python3
"""
postmq-usage-reporter.py — PostMQ build-session token-usage reporter (SITE-PLAN WP P.5).

An end-of-session hook for Claude Code and Codex, a per-turn `Stop` hook for ZCode, which
has no end-of-session event, and on Antigravity a command the `end-session` skill runs, because
no hook on that client could be made to fire (measured against its CLI; its IDE was not tested). When a session ends it parses the
just-finished transcript (and its out-of-process sub-agent transcripts), sums the authoritative
token usage — the counters the client itself recorded — and records it against
the session's PostMQ build session by calling the `record_build_session_usage`
MCP tool over Streamable HTTP (or, with --rest, `POST /v1/usage`). Tokens only;
no dollars — PostMQ records tokens, and per-model price tables are not something
the product owns.

WIRING
    `pmq kit install` (or the files and steps on https://postmq.com/kit) puts this file where your
    client runs hooks from and merges its settings block for you. If you wire it by hand, in a
    Claude Code project that is `.claude/hooks/postmq-usage-reporter.py` plus this entry in
    `.claude/settings.json`:
    "hooks": { "SessionEnd": [ { "hooks": [ { "type": "command",
        "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/postmq-usage-reporter.py\\"",
        "timeout": 30 } ] } ] }
    Keep the `timeout`: Claude Code gives SessionEnd hooks a SHARED 1.5-second budget unless a
    hook's own timeout raises it, and a long transcript takes longer than that just to read.
    On Windows swap `python3` for `py -3` (or `python`).

    On CODEX, wire `postmq-usage-reporter-detach.sh` instead of this file: a Codex SessionEnd
    hook is killed at 1 second by default, 3 seconds at most, which a transcript read plus a
    network call does not fit inside. The wrapper returns at once and runs this reporter
    detached. Codex asks you to review a project hook once before it will run it.

    On ZCODE, wire the same detach wrapper — but on `Stop`, not `SessionEnd`, because
    ZCode HAS no SessionEnd (see the ZCode block below for what that costs and why it is
    still the right wiring).

    (In the PostMQ repository these files, and the per-client snippets that `pmq kit install`
    merges, live under tools/hooks/postmq-usage-reporter/; an installed project has none of
    those paths, which is why nothing here asks you to look there.)

    On ANTIGRAVITY there is NOTHING to wire, and that is a measured result rather than an
    omission: its binary carries a hook system and ~/.gemini/config/hooks.json parses into
    "named hooks", but on 2.8.0 no hook could be made to fire across seven binding spellings
    and a project-scoped .agents/hooks.json was never loaded. So the reporter installs to
    `.agents/tools/` — not `.agents/hooks/`, because it is not a hook there — and the
    end-session skill runs it:
        python3 .agents/tools/postmq-usage-reporter.py --antigravity
    The cost is real and is stated in the kit's honest_limits: a session that never runs
    end-session records no usage at all, where the other three clients have an event to
    fall back on.

HOW IT FINDS THE BUILD SESSION
    `/start-session` writes the minted build-session id to
    `<repo>/.claude/state/active-build-session-<claude_session_id>` (keyed per
    client session so parallel worktree sessions never collide; anchored at the
    git common dir so it is shared from any worktree; `.codex/state/`,
    `.zcode/state/` and `.agents/state/` are the other directories searched). This hook reads it
    back by the `session_id` the hook payload hands it, looking under the PROJECT directory
    (`$CLAUDE_PROJECT_DIR`, `$ZCODE_PROJECT_DIR`) first and the payload's `cwd` second, because
    the cwd follows the agent's `cd`. Override with POSTMQ_BUILD_SESSION_ID (testing / non-git
    checkouts).

    Beside the pointer, `/start-session` appends `<UTC time> <build session id>` to
    `build-session-boundaries-<client session id>` each time it opens a build session. When a
    Claude Code transcript spans more than one, the report is SPLIT at those times, one report per
    build session; without the log the whole transcript goes to the pointer, as it always did.

ENV
    POSTMQ_CREDENTIAL        required to send (or POSTMQ_CREDENTIAL_FILE, default ~/.config/postmq/credential,
                             the same order the wake receiver uses) — a PostMQ credential with the
                             write_session_state scope (`pmq_…`; the
                             form=mcp_config issue flow hands you one).
                             Surrounding whitespace is trimmed; a value that
                             still holds a character a credential never
                             contains (a line break, a space) is refused
    POSTMQ_MCP_URL           MCP Streamable-HTTP endpoint; default https://mcp.postmq.com/
    POSTMQ_API_URL           REST base (e.g. https://api.postmq.com); used only with --rest.
                             Both must be https:// (plain http:// only to this machine), and
                             neither may redirect: a 3xx is refused rather than followed,
                             because following it re-sends the credential
    POSTMQ_BUILD_SESSION_ID  override the state-file lookup (and never split: one attribution)
    POSTMQ_REPORT_DEADLINE_SECONDS
                             the whole run's wall-clock limit, transcript read and every
                             request together (default 25, inside the snippet's 30 s timeout)
    POSTMQ_ZCODE_ROLLOUT_DIR override where ZCode rollouts are looked for
                             (default ~/.zcode/cli/rollout)
    POSTMQ_ANTIGRAVITY_CONVERSATION_DIR
                             override where Antigravity conversation databases are looked
                             for. Replaces BOTH defaults (~/.gemini/antigravity/conversations
                             and ~/.gemini/antigravity-cli/conversations) rather than adding
                             to them, so a test cannot mix real sessions into its numbers.

MODES
    (stdin = hook JSON)      normal operation
    --payload-file <path>    read the hook JSON from a file instead of stdin
                             (with --delete-payload, unlink it afterwards). How the
                             Codex SessionEnd wrapper hands the payload to the
                             detached child it starts.
    --dry-run [transcript]   print the tool arguments that WOULD be sent, no network
    --backfill <transcript> --build-session <id> [--dry-run] [--rest]
                             record a PAST session's usage against a specific
                             build session. The client_session_id is the id the LIVE
                             hook would key on — the transcript filename for Claude
                             Code, and the id embedded in the rollout for Codex and
                             ZCode, whose filenames are not their session ids — so the
                             write lands on the same (build_session, client_session)
                             row. Idempotent: a re-run, or a later live report for
                             that session, overwrites and never doubles.
    --antigravity [db] [--build-session <id>]
                             resolve the Antigravity conversation opened against the CURRENT
                             DIRECTORY and report it. The route for a client with no hook.
                             It picks the most recently written conversation that COULD be this
                             session — one whose recorded workspace contains this directory, or
                             one that records no workspace. A conversation on another project is
                             ignored. If the newest such conversation cannot be identified, or
                             two were written in the same moment, it REFUSES and says why rather
                             than guessing — pass the database path to settle it.
                             `--build-session` supplies the id when no /start-session pointer was
                             written, which on a hookless client is the case the recovery path
                             exists for.
    --rest                   POST the same payload to $POSTMQ_API_URL/v1/usage
                             instead of calling the MCP tool
    --strict                 a failure exits 1 instead of 0 (CI / self-tests)

FAIL-OPEN
    A hook must NEVER block session end. Every failure path — no transcript, no
    build-session pointer, no credential, network error, non-2xx, JSON-RPC or
    tool error, unexpected exception — logs ONE line to stderr and exits 0.
    --strict flips that to exit 1. The credential is never printed.
"""
# Defer annotation evaluation so `X | None` hints run on Python 3.9 (macOS ships
# 3.9 as /usr/bin/python3). Without this the module raises at import — before the
# fail-open guard in main() can catch it.
from __future__ import annotations

import calendar
import glob
import ipaddress
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPORTER_VERSION = "1.4.0"
AGENT = "claude-code"
AGENT_CODEX = "codex"
AGENT_ZCODE = "zcode"
AGENT_ANTIGRAVITY = "antigravity"
TOOL_NAME = "record_build_session_usage"
DEFAULT_MCP_URL = "https://mcp.postmq.com/"
REST_ROUTE = "/v1/usage"
STATE_PREFIX = "active-build-session-"
LOG_PREFIX = "[postmq-usage-reporter]"
MCP_PROTOCOL_VERSION = "2025-06-18"
# The per-request ceiling, named rather than inline so the one place it is set is findable.
HTTP_TIMEOUT_SECONDS = 30
# The END-TO-END ceiling for one run, transcript read and every request together (GitHub #1387
# item 9). Three sequential requests at 30 s each fit no hook budget, and urllib's timeout bounds
# each socket operation rather than a request, so only a wall clock bounds the whole. It sits
# INSIDE the hook timeout the Claude Code snippet sets (hooks.json: 30 s), so the report finishes or
# gives up cleanly before the client kills the process; POSTMQ_REPORT_DEADLINE_SECONDS overrides it.
REPORT_DEADLINE_SECONDS = 25

# The exact argument names of the `record_build_session_usage` MCP tool
# (src/PostMQ.Mcp/Tools/UsageTools.cs) — which are also the exact JSON body
# field names of `POST /v1/usage` (src/PostMQ.Api/SessionState/UsageWriteEndpoints.cs).
# ONE payload serves both transports; build_arguments() asserts it emits exactly
# these keys, and the Tooling guard asserts these are a subset of the tool's.
ARGUMENT_KEYS = (
    "build_session_id",
    # The transcript id. It was `claude_session_id` until the kit grew past Claude Code; the
    # server still accepts that alias, but three of the four clients this file serves do not
    # report a Claude session, so the payload sends the canonical name.
    "client_session_id",
    "input_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "cache_read_tokens",
    "output_tokens",
    "turns",
    "sidechain_turns",
    "models",
    "agent",
    "reporter_version",
    "window_start",
    "window_end",
)


class ReportError(Exception):
    """A reporting failure with a one-line, credential-free message."""


def _log(msg: str) -> None:
    print(f"{LOG_PREFIX} {_redact(msg)}", file=sys.stderr)


def _redact(text: str) -> str:
    """Every diagnostic passes through here, so an endpoint that reflects the bearer back (in an error body, a
    JSON-RPC message, a malformed status line) cannot get it printed. A real credential is `pmq_` + base64url,
    which repr() never escapes, so it appears verbatim in any str() or repr() that carries it."""
    # The credential the run actually USES, snapshotted when _credential() read it — never re-read here: a file that
    # changed or vanished mid-run would leave a reflected bearer unredacted, and every log line would do file I/O
    # (Codex). Before any credential has been read, only the variable can have been sent.
    for secret in (_SECRET, (os.environ.get("POSTMQ_CREDENTIAL") or "").strip()):
        if len(secret) >= 8:
            text = text.replace(secret, "<redacted>")
    return text


# ── transcript aggregation (pure; unit-testable) ──────────────────────────
def _collect_file(path: str, snapshots: dict, *, is_subagent: bool) -> bool:
    """Fold one transcript file's assistant-usage lines into `snapshots`, keyed
    by message id, LAST line wins. Returns False when the file could not be read
    (in whole or in part), so the caller can mark the aggregate unreadable rather
    than send a total that is quietly short (GitHub #1387 item 10).

    Transcripts replay a message's usage line several times (~3x in the main
    transcript, up to 6x in sub-agent transcripts). Measured 2026-08-18 over
    15,770 replayed messages on one machine: the input classes and the model are
    identical across every replay, but in SUB-AGENT transcripts 765 of 942
    replayed messages carry a preliminary `output_tokens` (the stream-start
    snapshot, e.g. 7) on the earlier lines and the final count (e.g. 537) only on
    the last, and the last is always the max. Dedupe-on-first-occurrence therefore
    undercounts sub-agent output; dedupe-on-last does not."""
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message") or {}
                u = msg.get("usage")
                if not u:
                    continue
                # Key is global across files so a message can't be double-counted;
                # a line with no id at all is its own (file, line) key.
                mid = msg.get("id") or o.get("requestId") or (path, i)
                snapshots[mid] = {
                    "usage": u,
                    "model": msg.get("model"),
                    "timestamp": o.get("timestamp"),
                    # A turn is "sub-agent work" if it came from a sub-agent file
                    # OR was an inline sidechain turn in the main transcript.
                    "sidechain": bool(is_subagent or o.get("isSidechain")),
                }
    except (OSError, UnicodeDecodeError) as e:
        _log(f"could not read {path}: {e}")
        return False
    return True


def _cache_writes(u: dict):
    """`(5-minute, 1-hour)` cache-write tokens for one usage object.

    The per-TTL breakdown under `cache_creation` is the authority when it is there. It is newer
    than the `cache_creation_input_tokens` total and not on every line (older transcripts, some
    gateways), and reading the breakdown alone reported ZERO cache writes for every such line
    (GitHub #1387 item 13). So the total is the floor: whatever it holds beyond the breakdown is
    recorded in the 5-minute class, which is the API's default TTL. A breakdown that sums to MORE
    than the total is believed as it stands; the total is never used to shrink it."""
    cc = u.get("cache_creation")
    cc = cc if isinstance(cc, dict) else {}
    written_5m = int(cc.get("ephemeral_5m_input_tokens", 0) or 0)
    written_1h = int(cc.get("ephemeral_1h_input_tokens", 0) or 0)
    total = int(u.get("cache_creation_input_tokens", 0) or 0)
    return written_5m + max(0, total - written_5m - written_1h), written_1h


def _fold(snapshots: dict, tot: dict) -> None:
    """Sum the deduped per-message snapshots into `tot`."""
    for snap in snapshots.values():
        u = snap["usage"]
        tot["turns"] += 1
        if snap["sidechain"]:
            tot["sidechain_turns"] += 1
        tot["input_tokens"] += int(u.get("input_tokens", 0) or 0)
        tot["output_tokens"] += int(u.get("output_tokens", 0) or 0)
        tot["cache_read_tokens"] += int(u.get("cache_read_input_tokens", 0) or 0)
        written_5m, written_1h = _cache_writes(u)
        tot["cache_write_5m_tokens"] += written_5m
        tot["cache_write_1h_tokens"] += written_1h
        if snap["model"]:
            tot["_models"].add(snap["model"])
        ts = snap["timestamp"]
        if ts:
            if tot["window_start"] is None or ts < tot["window_start"]:
                tot["window_start"] = ts
            if tot["window_end"] is None or ts > tot["window_end"]:
                tot["window_end"] = ts


# ── Codex rollout transcripts ─────────────────────────────────────────────
# A Codex session's transcript is a "rollout" JSONL under ~/.codex/sessions/…, and the hook
# payload hands us its path directly. Its usage shape is NOT Claude Code's, and the two
# differences below were measured over 22 rollouts on 2026-08-25 rather than assumed — each
# one would silently mis-report if guessed the other way.
#
#   * `token_count` events carry a CUMULATIVE `total_token_usage` WITHIN ONE CLI PROCESS, so
#     within a segment the LAST one is the total and summing them would multiply a session's
#     usage by its event count. But the counter RESTARTS when a thread is resumed in a later
#     process, which appends to the SAME rollout file. Measured over 120 rollouts on
#     2026-08-25: 2 of them show `total_tokens` decreasing mid-file (305,066,626 -> 114,818
#     and 100,878,899 -> 173,998). Keeping only the file's final snapshot would have reported
#     the last segment alone — a 73% underreport on the second of those — and, because the
#     report is an upsert on (build_session, session_id), it would have OVERWRITTEN the
#     larger earlier number rather than sitting beside it. So a drop is read as a segment
#     boundary and each segment's terminal snapshot is summed.
#   * `input_tokens` INCLUDES its detail lines. Verified structurally: `total_tokens` equalled
#     `input_tokens + output_tokens` in 22 of 22 rollouts, which it could not if the cached
#     figure were a separate addend. PostMQ stores UNCACHED input separately, so both detail
#     lines — `cached_input_tokens` and `cache_write_input_tokens` — are subtracted. Reporting
#     input raw would double-count cache reads, the dominant class, into the uncached bucket.
#     (`cache_write_input_tokens` is 0 in every rollout observed, so its inclusiveness is
#     inferred from the same invariant rather than measured directly; subtracting it is the
#     choice that keeps `input + output == total` true either way.)
#   * `reasoning_output_tokens` is a SUBSET of `output_tokens` (never greater, in 22 of 22),
#     so it is not added on top.
#   * Codex does not split cache writes by TTL the way PostMQ's 5m/1h classes do, so whatever
#     it reports is recorded in the 5-minute class.
CODEX_EVENT = "event_msg"


def _is_codex_transcript(path: str) -> bool:
    """Whether `path` is a Codex rollout rather than a Claude Code transcript. Decided on the
    session_meta/event_msg envelope, which Claude Code transcripts never carry."""
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(200):
                line = fh.readline()
                if not line:
                    return False
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") in ("session_meta", "turn_context", CODEX_EVENT):
                    return True
                if o.get("type") in ("assistant", "user"):
                    return False
    except OSError:
        return False
    return False


def aggregate_codex(transcript_path: str) -> dict:
    """Fold a Codex rollout into the same five token classes Claude Code transcripts fold into."""
    tot = {
        "turns": 0, "sidechain_turns": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "window_start": None, "window_end": None, "_models": set(),
    }
    # Terminal snapshot of each segment. A segment ends where the cumulative counter drops.
    segments = []
    last_total = None
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                ts = o.get("timestamp")
                if ts:
                    if tot["window_start"] is None or ts < tot["window_start"]:
                        tot["window_start"] = ts
                    if tot["window_end"] is None or ts > tot["window_end"]:
                        tot["window_end"] = ts
                kind = o.get("type")
                payload = o.get("payload") or {}
                if kind == "turn_context":
                    model = payload.get("model")
                    if model:
                        tot["_models"].add(model)
                elif kind == CODEX_EVENT:
                    ptype = payload.get("type")
                    if ptype == "token_count":
                        info = payload.get("info") or {}
                        total = info.get("total_token_usage")
                        if total:
                            # Cumulative WITHIN a segment: keep the last, never sum. A drop means the
                            # thread was resumed in a new process, so close the previous segment.
                            if last_total is not None and _codex_total(total) < _codex_total(last_total):
                                segments.append(last_total)
                            last_total = total
                    elif ptype == "task_complete":
                        tot["turns"] += 1
    except (OSError, UnicodeDecodeError) as e:
        _log(f"could not read {transcript_path}: {e}")
        tot["_unreadable"] = True
        return tot

    if last_total is not None:
        segments.append(last_total)

    for seg in segments:
        raw_input = int(seg.get("input_tokens", 0) or 0)
        cached = int(seg.get("cached_input_tokens", 0) or 0)
        written = int(seg.get("cache_write_input_tokens", 0) or 0)
        tot["cache_read_tokens"] += cached
        tot["cache_write_5m_tokens"] += written
        # Both detail lines sit INSIDE the inclusive input figure; uncached is what is left.
        tot["input_tokens"] += max(0, raw_input - cached - written)
        tot["output_tokens"] += int(seg.get("output_tokens", 0) or 0)
    return tot


def codex_session_id(transcript_path: str) -> str | None:
    """A Codex rollout's own session id, from its `session_meta` header.

    This exists so `--backfill` keys the SAME row the live hook writes. Codex's SessionEnd payload
    reports `session_id` as the bare id (e.g. `01a037d7-e4c5-…`), while the rollout's FILE NAME is
    `rollout-<timestamp>-<id>.jsonl`. Deriving the id from the filename — which is right for Claude
    Code, whose transcript is named for its session — produces a different string, so a backfill
    would insert a SECOND usage row beside the live one instead of overwriting it, and the
    idempotency this reporter advertises would be quietly untrue."""
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for _ in range(50):
                line = fh.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == "session_meta":
                    payload = o.get("payload") or {}
                    found = payload.get("session_id") or payload.get("id")
                    return str(found) if found else None
    except OSError:
        return None
    return None


def _codex_total(snapshot: dict) -> int:
    """A snapshot's cumulative size, for detecting a counter restart. Falls back to the parts when
    `total_tokens` is absent, so a shape change degrades rather than reading every snapshot as 0."""
    total = snapshot.get("total_tokens")
    if total is not None:
        return int(total or 0)
    return int(snapshot.get("input_tokens", 0) or 0) + int(snapshot.get("output_tokens", 0) or 0)


# ── ZCode rollouts ────────────────────────────────────────────────────────
# A ZCode session's transcript is a "rollout" JSONL at
# ~/.zcode/cli/rollout/model-io-<session id>.jsonl — one JSON object per model REQUEST. Every
# fact below was measured against ZCode 3.10.2 on 2026-09-02, over 120 live records and the
# shipped writer, rather than inferred from the shape:
#
#   * THE HOOK PAYLOAD'S `transcript_path` IS A DECOY, and this is the trap the whole ZCode path
#     exists to avoid. ZCode builds a Claude-Code-compatible hook stdin, and part of that
#     compatibility is a `transcript_path` — pointing at a file it mkdtemp's for the hook, holding
#     ONE synthetic line for Stop and UserPromptSubmit and NOTHING at all for the other five
#     events, and rm -rf'd the moment the hook returns. Parsing it the way this reporter correctly
#     parses Claude Code's and Codex's would find no usage lines and report ZEROS — and because
#     the report is an upsert on (build_session, session_id), those zeros would OVERWRITE a
#     correct earlier row. So on ZCode the payload's transcript is ignored and the real rollout is
#     resolved from `session_id`; when it cannot be found the reporter REFUSES rather than
#     reporting what it can see, because here what it can see is nothing.
#   * `inputTokens` INCLUDES its detail lines. Measured against the raw provider usage carried in
#     the same record (`response.providerMetadata.anthropic.usage`): `inputTokens` equalled
#     `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` in 117 of the 117
#     records that carry both, and never the uncached figure alone. PostMQ stores UNCACHED input
#     separately, so both detail lines are subtracted — reporting it raw would fold cache reads,
#     the dominant class, into the uncached bucket. `totalTokens == inputTokens + outputTokens`
#     held in 120 of 120, which is the same structural invariant the Codex block leans on.
#   * Usage is PER-REQUEST, not cumulative. `requestId` was unique across all 120 records, so the
#     classes SUM. This is the opposite of Codex, where a `token_count` snapshot is cumulative and
#     summing would multiply a session by its event count — the two clients want opposite handling
#     and reading either one the other way is a silent order-of-magnitude error.
#   * There is no usable sub-agent axis. `model.role` is absent on 76 of 120 records (and carries
#     `lite` for ZCode's own title-generation calls), so it cannot say which turns were sub-agent
#     work. `sidechain_turns` is therefore 0, the same honest limitation the Codex path documents.
#   * `cacheWriteTokens` was 0 in all 120, and ZCode does not split cache writes by TTL, so
#     whatever it does report is recorded in the 5-minute class — again as on Codex.
#
# TWO RETENTION LIMITS, because they bound what this can ever report. ZCode keeps only the THREE
# most recent rollout files and deletes the rest, and it TRUNCATES a rollout that passes 64 MB
# rather than rotating it (leaving a `modelIOReset` marker). A report is therefore of what the
# rollout still holds; both are ZCode's behaviour, not something this reporter can work around.
# Say the second one's consequence plainly, because the per-turn wiring sharpens it: after a
# truncation the rollout holds only post-reset records, so an unguarded report would replace the
# session's recorded figures with SMALLER ones and every turn after it would do the same. That is
# what the high-water mark below refuses where it can — the recorded figures stand and the reports are
# dropped, so a very long ZCode session freezes at its last true reading rather than ratcheting
# down. Either way it under-reports; only one of them also erases what was already known.
ZCODE_RECORD = "model_io"
# `querySource` says what a request was FOR. ZCode issues its own auxiliary calls on a session's
# behalf — naming the session, running a web search, digesting a fetched page — and those spend
# tokens without being assistant turns. Their tokens are still summed (they are real spend on this
# session); they simply do not increment `turns`, which on every client means a model reply in the
# conversation. Measured on 3.10.2: a 3-request session was reporting 3 turns for 2 real ones.
# An UNKNOWN source counts, deliberately: erring toward a turn keeps a new conversational source
# visible rather than silently uncounted. (Found by Codex.)
ZCODE_AUXILIARY_SOURCES = frozenset({"session_title", "web_search_tool", "web_fetch_processing"})
ZCODE_ROLLOUT_PREFIX = "model-io-"
ZCODE_ROLLOUT_SUFFIX = ".jsonl"
ZCODE_SESSION_ENV = "ZCODE_SESSION_ID"
ZCODE_ROLLOUT_DIR_ENV = "POSTMQ_ZCODE_ROLLOUT_DIR"


def _is_zcode_transcript(path: str) -> bool:
    """Whether `path` is a ZCode rollout. Decided on the `model_io` envelope, which neither a
    Claude Code transcript nor a Codex rollout carries."""
    try:
        with open(path, encoding="utf-8") as fh:
            for _ in range(200):
                line = fh.readline()
                if not line:
                    return False
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                kind = o.get("type")
                if kind == ZCODE_RECORD:
                    return True
                if kind in ("assistant", "user", "session_meta", "turn_context", CODEX_EVENT):
                    return False
    except OSError:
        return False
    return False


def aggregate_zcode(transcript_path: str) -> dict:
    """Fold a ZCode rollout into the same five token classes the other two clients fold into."""
    tot = {
        "turns": 0, "sidechain_turns": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "window_start": None, "window_end": None, "_models": set(),
    }
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != ZCODE_RECORD:
                    continue
                usage = (o.get("response") or {}).get("usage") or {}
                if not usage:
                    # A request that failed before the provider answered carries an `error` and no
                    # usage. It spent no tokens and is not a turn.
                    continue
                if o.get("querySource") not in ZCODE_AUXILIARY_SOURCES:
                    tot["turns"] += 1
                raw_input = int(usage.get("inputTokens", 0) or 0)
                cached = int(usage.get("cacheReadTokens", 0) or 0)
                written = int(usage.get("cacheWriteTokens", 0) or 0)
                tot["cache_read_tokens"] += cached
                tot["cache_write_5m_tokens"] += written
                # Both detail lines sit INSIDE the inclusive input figure; uncached is what is left.
                tot["input_tokens"] += max(0, raw_input - cached - written)
                tot["output_tokens"] += int(usage.get("outputTokens", 0) or 0)
                model = (o.get("model") or {}).get("modelId")
                if model:
                    tot["_models"].add(model)
                for ts in (o.get("startedAt"), o.get("completedAt")):
                    if not ts:
                        continue
                    if tot["window_start"] is None or ts < tot["window_start"]:
                        tot["window_start"] = ts
                    if tot["window_end"] is None or ts > tot["window_end"]:
                        tot["window_end"] = ts
    except (OSError, UnicodeDecodeError) as e:
        _log(f"could not read {transcript_path}: {e}")
        tot["_unreadable"] = True
    return tot


def zcode_session_id(transcript_path: str) -> str | None:
    """A ZCode rollout's own session id, from its first record.

    Exists for the same reason `codex_session_id` does: the rollout's FILE NAME is
    `model-io-<session id>.jsonl`, so the filename stem carries a `model-io-` prefix the live hook's
    `session_id` does not. Keying a backfill on the stem would insert a SECOND usage row beside the
    live one instead of overwriting it, and the idempotency this reporter advertises would be
    quietly untrue."""
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for _ in range(200):
                line = fh.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == ZCODE_RECORD:
                    found = o.get("sessionId")
                    return str(found) if found else None
    except OSError:
        return None
    return None


def _zcode_rollout_dir() -> str:
    override = os.environ.get(ZCODE_ROLLOUT_DIR_ENV)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".zcode", "cli", "rollout")


def zcode_rollout_for(session_id: str) -> str | None:
    """The rollout ZCode is writing for `session_id`, or None.

    Two steps, and the second is why this is not just a path join. ZCode names the file after the
    session id put through its own filename sanitiser, so the constructed path is right today —
    but a constructed path that misses produces no report at all, silently, which is the failure
    this whole module is written against. So when it misses, the directory is READ and each
    rollout asked for its own session id. ZCode keeps at most three, so that is a handful of
    `readline`s and it turns a guess into a measurement."""
    if not session_id:
        return None
    directory = _zcode_rollout_dir()

    candidates = [session_id]
    # A payload could hand over the bare uuid rather than ZCode's own `sess_`-prefixed form.
    if not session_id.startswith("sess_"):
        candidates.append("sess_" + session_id)
    for candidate in candidates:
        path = os.path.join(
            directory, ZCODE_ROLLOUT_PREFIX + _zcode_file_segment(candidate) + ZCODE_ROLLOUT_SUFFIX)
        if os.path.exists(path):
            return path

    wanted = set(candidates)
    try:
        listing = sorted(glob.glob(
            os.path.join(directory, ZCODE_ROLLOUT_PREFIX + "*" + ZCODE_ROLLOUT_SUFFIX)))
    except OSError:
        return None
    for path in listing:
        if zcode_session_id(path) in wanted:
            return path
    return None


def zcode_ids_match(one: str | None, other: str | None) -> bool:
    """Whether two spellings name the same ZCode session.

    ZCode's own id is `sess_<uuid>`; a payload may hand over either that or the bare uuid. This is
    the ONLY equivalence allowed — in particular a rollout must not be accepted merely because it
    matches the ZCODE_SESSION_ID in the environment, since that variable is inherited and a
    different client can be carrying it."""
    if not one or not other:
        return False
    if one == other:
        return True
    # EXACTLY ONE of them may carry the prefix. Allowing both to would make `sess_x` and
    # `sess_sess_x` — two distinct canonical-looking ids — compare equal, which would defeat both
    # the rollout binding and the inherited-environment check. (Found by Codex.)
    if one.startswith("sess_") and not other.startswith("sess_"):
        return one[len("sess_"):] == other
    if other.startswith("sess_") and not one.startswith("sess_"):
        return other[len("sess_"):] == one
    return False


def _zcode_file_segment(segment: str) -> str:
    """ZCode's own filename sanitiser, mirrored: every run of characters outside [A-Za-z0-9_-]
    becomes one `-`, leading and trailing `-` are dropped, and the result is cut to 80."""
    out = []
    previous_dash = False
    for ch in segment:
        if ch.isascii() and (ch.isalnum() or ch in "_-"):
            out.append(ch)
            previous_dash = False
        elif not previous_dash:
            out.append("-")
            previous_dash = True
    return "".join(out).strip("-")[:80]


# ── Antigravity ───────────────────────────────────────────────────────────
# Antigravity ("agy" — Google's Gemini-powered client, IDE + CLI) is the first client here whose
# session store is not JSONL. Each conversation is its OWN SQLite database at
# `~/.gemini/{antigravity,antigravity-cli}/conversations/<conversation-id>.db`, and the payload
# columns hold bare protobuf — no field names on the wire, so the tags below are the whole contract.
#
# HOW THE TAGS WERE ESTABLISHED (2026-09-03, Antigravity 2.8.0, 10,752 `gen_metadata` rows across
# every conversation on one machine). They are NOT read off a published schema — there isn't one —
# so each is pinned by a property that would break if the tag meant something else:
#
#   1.4.2   uncached (billable) input tokens. Jumps to a large value on exactly the turns where
#           1.4.5 disappears, which is the signature of a prefix-cache MISS re-reading the whole
#           prompt: measured at turn 20 of one conversation, 1.4.2 = 56457 with no 1.4.5 at all.
#   1.4.5   cache-READ tokens. Absent on turn 0 (nothing is cached yet) and on cache misses;
#           otherwise climbs in plateaus and holds flat for several turns — cache checkpoints.
#   1.4.3   TOTAL output tokens, and the identity `1.4.3 == 1.4.9 + 1.4.10` held on 10752 of 10752
#           rows. That identity is what makes the split below safe to rely on.
#   1.4.9   reasoning ("thoughts") tokens — absent on a turn that did not think.
#   1.4.10  visible output tokens.
#   1.19    the model id. Eight distinct values were observed on one machine, including Anthropic
#           models served through Vertex, so this is a real list and not a constant.
#
# TWO TRAPS, both of which produce a plausible wrong answer rather than an error:
#
#   * 1.4.3 is the TOTAL, not the visible half. Reading it as "candidates" and then ALSO adding
#     1.4.10 somewhere counts visible output twice — once as output and once as input, since
#     1.4.10 looks prompt-shaped next to 1.4.2. Sum 1.4.3 alone.
#   * `1.17.2` mirrors `1.4` and is tempting as a fallback. It agreed on only 10708 of 10749 rows,
#     so it is NOT interchangeable. Every row carries `1.4` (10752/10752 measured), so there is
#     nothing to fall back to and this reads `1.4` only.
#
# NOT RECORDED ANYWHERE: cache-WRITE tokens. No field under `1.4` holds them and none was found
# elsewhere, so `cache_write_5m_tokens` and `cache_write_1h_tokens` are structurally 0 for this
# client — not "not yet parsed". See `honest_limits` in kit/manifest.json.
AGY_USAGE = (1, 4)              # the usage submessage inside a gen_metadata row
AGY_INPUT_TAG = 2               # uncached / billable prompt tokens
AGY_OUTPUT_TOTAL_TAG = 3        # == reasoning + visible; the ONLY field summed as output
# The two SUMMANDS of tag 3. Never summed into the report — that is the double-count this parser
# exists to avoid — but checked against it per row, because the mapping rests on that identity and
# nothing else would notice if it stopped holding. See `_agy_usage_identity_holds`.
AGY_REASONING_TAG = 9           # "thoughts" tokens; absent on a turn that did not think
AGY_VISIBLE_TAG = 10            # the visible half of the output
AGY_CACHE_READ_TAG = 5          # cache-read tokens
AGY_MODEL_TAG = 19              # model id, directly under field 1
AGY_STEP_TIMESTAMP = (1, 1)     # steps.metadata → google.protobuf.Timestamp → seconds
AGY_CONVERSATION_DIR_ENV = "POSTMQ_ANTIGRAVITY_CONVERSATION_DIR"
# The IDE and the CLI are separate surfaces with separate stores and identical schemas. Both are
# read: a founder who runs both would otherwise have half their sessions silently report nothing.
AGY_STORES = (("antigravity", "conversations"), ("antigravity-cli", "conversations"))
AGY_REQUIRED_TABLES = ("gen_metadata", "trajectory_meta")
AGY_SQLITE_MAGIC = b"SQLite format 3\x00"
# How long a read will wait for a busy writer before falling back to the immutable (stale) read.
# Deliberately short: this runs inside a session-end path, not a batch job.
AGY_BUSY_TIMEOUT_SECONDS = 2.0
# Set by `_agy_connect` when it fell back to the immutable read AND there was a WAL it therefore
# skipped. Module state rather than a return value because `_agy_connect` has several callers that
# only want a connection; `aggregate_antigravity` reads it immediately after its own open.
_AGY_READ_STATE = {"degraded": False}


# How deeply groups may nest before a buffer is REFUSED. A policy limit, not a format rule, and
# worth stating plainly: a 101-deep group is legal on the wire and this reader will call it
# malformed. That is the trade — protobuf's own implementations cap recursion around this depth,
# real messages nest a handful deep, and without a ceiling one corrupt megabyte of single-byte
# start-group tags becomes hundreds of megabytes of stack entries. A row refused here is skipped
# and counted as damage, which is the safe direction. (Raised by Codex.)
PB_MAX_GROUP_DEPTH = 100


def _pb_uvarint(buf: bytes, i: int):
    """One base-128 varint at `i` → `(value, next_index)`, or None if it is not a legal one.

    ONE reader for the key, the value and the length. The first version inlined the loop three
    times and hardened only the first, so an over-wide VALUE was still accepted while the test for
    it passed against the key — a guard that looked applied and covered a third of the cases.

    `shift > 63` alone would accept a tenth byte carrying more than the single bit a 64-bit number
    has left, which no protobuf writer emits; refusing it keeps a malformed buffer on the
    "incomplete" path instead of yielding an impossible field. (Raised by Codex.)"""
    n = len(buf)
    value = 0
    shift = 0
    while True:
        if i >= n or shift > 63 or (shift == 63 and buf[i] > 1):
            return None
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _pb_read(buf: bytes, i: int):
    """One protobuf field from `buf` at `i` → (field_number, wire_type, value, next_index).

    Returns None at a malformed or exhausted buffer rather than raising: these blobs are written
    by a client whose format is not documented, so an unreadable one must degrade to "no usage
    here" and never to an exception that costs the whole report."""
    n = len(buf)
    got = _pb_uvarint(buf, i)
    if got is None:
        return None
    key, i = got
    field, wire = key >> 3, key & 7
    # protobuf field numbers are 1 .. 2^29-1. Anything outside that is not a key, whatever it
    # decodes to, and accepting it would let a malformed buffer read as a complete message.
    if field == 0 or field > 0x1FFFFFFF:
        return None
    if wire == 0:
        got = _pb_uvarint(buf, i)
        if got is None:
            return None
        value, i = got
        return field, wire, value, i
    if wire == 2:
        got = _pb_uvarint(buf, i)
        if got is None:
            return None
        length, i = got
        if i + length > n:
            return None
        return field, wire, buf[i:i + length], i + length
    if wire == 5:
        return (field, wire, buf[i:i + 4], i + 4) if i + 4 <= n else None
    if wire == 1:
        return (field, wire, buf[i:i + 8], i + 8) if i + 8 <= n else None
    if wire == 3:
        # START GROUP — the deprecated encoding, and still legal protobuf. Skipping it properly
        # matters more than it looks: the first version treated it as malformed, and because a row
        # is validated end to end, ONE unknown group anywhere in a row would have discarded a
        # perfectly good usage record and then refused the whole report. Measured: appending an
        # empty field-100 group to a healthy row turned 5-in/7-out into zero turns. That is the
        # false negative — losing real usage — which is worse here than the malformed-row case this
        # validation exists for. (Found by Codex, third round.)
        end = _pb_skip_group(buf, i, field)
        return (field, wire, buf[i:end[0]], end[1]) if end else None
    # wire 4 (END GROUP) never appears except as the close of a group `_pb_skip_group` consumed,
    # so reaching it here means the buffer is malformed.
    return None


def _pb_skip_group(buf: bytes, i: int, field: int):
    """The extent of a group opened at `i` with number `field` → `(payload_end, after_end_tag)`.

    Nested groups are counted, so a group containing a group closes on its own END tag rather than
    on the inner one. None if the group never closes, which is a truncation like any other."""
    # A STACK of open field numbers, not a depth counter. Counting depth alone accepts
    # `0b 13 1c 0c` — start 1, start 2, end 3, end 1 — where the inner group closes on a field
    # number that was never opened. That is illegal protobuf, and accepting it lets a corrupt row
    # read as healthy and be counted. (Found by Codex, fourth round.)
    # Bounded — see PB_MAX_GROUP_DEPTH for what that costs in strict conformance and why it is
    # worth it. (Found by Codex, fifth round.)
    open_fields = [field]
    while i < len(buf):
        payload_end = i
        got = _pb_uvarint(buf, i)
        if got is None:
            return None
        key, after = got
        inner_field, inner_wire = key >> 3, key & 7
        if inner_field == 0 or inner_field > 0x1FFFFFFF:
            return None
        if inner_wire == 4:
            if inner_field != open_fields[-1]:
                return None
            open_fields.pop()
            if not open_fields:
                return payload_end, after
            i = after
            continue
        if inner_wire == 3:
            if len(open_fields) >= PB_MAX_GROUP_DEPTH:
                return None
            open_fields.append(inner_field)
            i = after
            continue
        stepped = _pb_read(buf, i)
        if stepped is None:
            return None
        i = stepped[3]
    return None


def _pb_scan(buf: bytes):
    """Yield every (field_number, wire_type, value) in `buf`, stopping at the first malformed one.

    A real generator, not a view over `_pb_parse`'s list: it used to build the whole list first,
    so every caller paid for materialising one tuple per field whatever it wanted. Silent on
    truncation, which is fine for a caller that only wants what is there and NOT fine for one
    about to sum it — use `_pb_complete` when a partial read would be reported as a whole number."""
    i = 0
    while i < len(buf):
        got = _pb_read(buf, i)
        if got is None:
            return
        field, wire, value, i = got
        yield field, wire, value


def _pb_complete(buf: bytes) -> bool:
    """Whether `buf` parses to its END — walked, never accumulated.

    The property every hot-path caller actually wants, and the reason it is separate: `_pb_parse`
    holds a tuple per field, so asking it this question about a few megabytes of legal repeated
    fields costs hundreds of megabytes. Same exhaustion class the group-depth cap addresses, by a
    route the cap does not cover. (Found by Codex, sixth round.)"""
    i = 0
    while i < len(buf):
        got = _pb_read(buf, i)
        if got is None:
            return False
        i = got[3]
    return True


def _pb_parse(buf: bytes):
    """`(fields, complete)` — everything that parsed, and whether the buffer parsed to its END.

    Materialises the fields, so it is for callers that want them (tests, and anything inspecting a
    row by hand). Code that only needs the second half asks `_pb_complete`, which walks instead.

    The `complete` half exists because of a failure mode that produces a plausible number rather
    than an error. A scan that aborts partway hands back the fields it managed to read, and
    `_pb_varint` cannot distinguish "this field was absent" from "the scan never reached it": both
    are 0. So a usage submessage truncated after the input tag yields a turn with real input and
    zero output, which looks like a turn that produced nothing. Callers that sum must skip such a
    row rather than count it. (Found by Codex.)

    Wire types 3 and 4 — the deprecated group encoding — ARE implemented, deliberately. Treating
    them as malformed made one unknown group anywhere in a row discard its perfectly good usage
    record and refuse the whole report, which loses real usage. So `complete=False` now means the
    bytes are actually damaged, and nothing legal routes to the skip path."""
    out = []
    i = 0
    while i < len(buf):
        got = _pb_read(buf, i)
        if got is None:
            return out, False
        field, wire, value, i = got
        out.append((field, wire, value))
    return out, True


def _agy_usage_identity_holds(usage: bytes) -> bool:
    """Whether this usage row still satisfies `output_total == reasoning + visible`.

    **Why a decoded row needs checking at all.** Antigravity publishes no schema. These tag
    semantics were established by MEASUREMENT — 10,752 rows on one machine running Antigravity
    2.8.0 — and the identity held on 10752 of 10752. Nothing checks the client version, so if a
    later build renumbers or repurposes a field this reader will decode SOMETHING and report it: a
    plausible wrong number rather than an error, which is the one failure mode this component is
    written against everywhere else (backlog `01a066cb-0c9f`, found in Codex review of #987).

    The identity is the cheapest thing that can notice. It relates three fields that a renumbering
    would almost certainly break, and it costs two varint reads per row.

    **A row where BOTH summands are absent passes.** Tag 9 is absent on a turn that did not think,
    and tag 10 can be absent too; `_pb_varint` reports an absent field as 0, so an all-absent pair
    is indistinguishable from a genuine `0 + 0`. Refusing those would discard real turns on today's
    encoding to guard against tomorrow's, which is the wrong trade — the check therefore only
    fires when the summands are PRESENT enough to disagree, i.e. when their sum is non-zero.
    """
    reasoning = _pb_varint(usage, AGY_REASONING_TAG)
    visible = _pb_varint(usage, AGY_VISIBLE_TAG)
    if reasoning == 0 and visible == 0:
        return True

    return reasoning + visible == _pb_varint(usage, AGY_OUTPUT_TOTAL_TAG)


# Kept as the name the row-validation paths read, so their intent stays legible at the call site.
def _pb_row_is_whole(*parts) -> bool:
    """Every given buffer parses to its end. `None` parts are skipped, not failed."""
    return all(_pb_complete(part) for part in parts if part is not None)


def _pb_sub(buf, *tags):
    """Walk length-delimited submessages by tag. None if any hop is missing.

    Every occurrence of the tag is MERGED, which for an embedded message is protobuf's own rule:
    the parser merges repeated occurrences, and merging is exactly parsing their concatenation —
    a scalar inside then reads last-wins (`_pb_varint`) and a field present in only one occurrence
    survives. Taking the last occurrence alone, as this did first, is the rule for a singular
    SCALAR and drops every field the earlier occurrences carried (GitHub #1387 item 14). Measured
    on this store the question is moot — 0 of 10752 rows carry a duplicate field 1 or 1.4 — so
    this is about being right rather than about a case that has been seen.

    Each occurrence must parse to its end ON ITS OWN before it is merged. Two damaged fragments can
    concatenate into bytes that parse cleanly (`10` + `051807` is a valid `10051807`), which would
    turn a row the completeness check refuses into a plausible turn. So a damaged occurrence is
    returned as it stands, and the caller's `_pb_row_is_whole` refuses the row exactly as it did
    before merging existed. (Found by Codex.)"""
    cur = buf
    for tag in tags:
        if cur is None:
            return None
        found = [value for field, wire, value in _pb_scan(cur) if field == tag and wire == 2]
        damaged = next((value for value in found if not _pb_complete(value)), None)
        cur = damaged if damaged is not None else (b"".join(found) if found else None)
    return cur


def _pb_varint(buf, tag: int) -> int:
    """A varint field's value, or 0 when it is absent — which is the honest reading here: an
    absent 1.4.5 means nothing was read from cache, and an absent 1.4.9 means nothing was thought."""
    if buf is None:
        return 0
    found = 0
    for field, wire, value in _pb_scan(buf):
        if field == tag and wire == 0:
            found = int(value)   # last wins — protobuf's rule for a singular scalar
    return found


def _pb_str(buf, tag: int):
    if buf is None:
        return None
    found = None
    for field, wire, value in _pb_scan(buf):
        if field == tag and wire == 2:
            try:
                found = value.decode("utf-8")
            except UnicodeDecodeError:
                found = None
    return found


def _agy_connect(path: str):
    """Read-only connection to a conversation database — `mode=ro` first, `immutable=1` only as a
    fallback.

    The order matters and the first version had it backwards. These databases belong to a RUNNING
    client holding them in WAL mode, so a plain `mode=ro` open is what actually reads the CURRENT
    conversation: it replays the WAL, and the newest turns are the ones a session-end report is
    most about. `immutable=1` skips the WAL entirely, and the checkpoint it reads instead can be
    arbitrarily old rather than a turn or two behind — reporting stale totals that, on a client
    that reports once per session, are never corrected.

    `immutable=1` is still the fallback, because `mode=ro` genuinely fails on a database whose
    `-shm` is absent or unwritable, which is exactly the state a copied or archived conversation
    is in. Its contract is that the file does not change while it is open; that is a promise this
    process cannot make about a live database, so it is used only when the honest open failed.
    Which one served a report is not currently recorded — see the note in the README."""
    con = None
    _AGY_READ_STATE["degraded"] = False
    try:
        # A short busy timeout, not the five-second default: `immutable=1` never waited for anyone,
        # so this open must not turn a busy writer into a five-second stall in a hook. On BUSY it
        # falls through to the immutable read, which is stale but immediate. (Raised by Codex.)
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=AGY_BUSY_TIMEOUT_SECONDS)
        # `connect` is lazy: the file is not touched until a statement runs, so a broken open
        # surfaces here rather than at the first real query.
        con.execute("select count(*) from sqlite_master").fetchone()
        return con
    except sqlite3.Error:
        # Close the one whose probe failed before opening another, or every fallback leaks a
        # handle — which surfaces as `ResourceWarning: unclosed database` on stderr and, because
        # this module's fail-open contract is one stderr line per run, breaks that too.
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
        # DEGRADED only when there is a WAL to miss. With no `-wal` beside it — a copied or
        # archived conversation, which is the case this fallback exists for — the immutable read
        # is the whole database and nothing is lost. With one, the checkpoint being read can be
        # arbitrarily old, and reporting it would overwrite a newer row with smaller counts.
        # (Found by Codex, third round.)
        _AGY_READ_STATE["degraded"] = os.path.exists(path + "-wal")
        return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def _has_sqlite_magic(path: str) -> bool:
    """Whether `path` begins with SQLite's file header — 16 bytes, no parsing.

    Classification asks this rather than `_is_antigravity_transcript`, and the difference is a
    bypass rather than a nicety. A conversation database too damaged to list its tables answered
    False to the full check, fell through `classify` as the DEFAULT client, and was reported as a
    claude-code session of zero turns — around the Antigravity refusal entirely, because the
    refusal keys on the agent classification produced. Every route reached it: hook stdin,
    `--backfill`, `--rest`. A file with this header is not any client's JSONL transcript, so
    calling it Antigravity and letting the unreadable-store guard refuse it is both more honest
    and safe. (Found by Codex, third round.)"""
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == AGY_SQLITE_MAGIC
    except OSError:
        return False


def _is_antigravity_transcript(path: str) -> bool:
    """Whether `path` is an Antigravity conversation database.

    Checked on the SQLite magic FIRST so that pointing this at a JSONL transcript is a cheap
    16-byte read rather than a sqlite3 open, and then on the tables — because "is a SQLite file"
    is not the claim, "is an Antigravity conversation" is."""
    try:
        with open(path, "rb") as fh:
            if fh.read(16) != AGY_SQLITE_MAGIC:
                return False
    except OSError:
        return False
    try:
        con = _agy_connect(path)
        try:
            names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return all(t in names for t in AGY_REQUIRED_TABLES)


def aggregate_antigravity(transcript_path: str) -> dict:
    """Fold an Antigravity conversation database into the same classes the other clients fold into.

    A turn is one `gen_metadata` row that carries a usage submessage — that is one model request.
    `sidechain_turns` stays 0: Antigravity records sub-agent work in the same trajectory rather
    than in a separate transcript, so there is no second file to attribute and reporting a
    non-zero count here would be inventing one."""
    tot = {
        "turns": 0, "sidechain_turns": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "window_start": None, "window_end": None, "_models": set(),
        # Set when the store could not be read at all. Without it a failed open is indistinguishable
        # from a conversation that genuinely spent nothing, and the zero it produces would be UPSERT
        # over a correct row — the same shape as the ZCode decoy this reporter already refuses.
        "_unreadable": False, "_truncated_rows": 0, "_identity_violations": 0,
    }
    try:
        con = _agy_connect(transcript_path)
    except sqlite3.Error as e:
        _log(f"could not open {transcript_path}: {e}")
        tot["_unreadable"] = True
        return tot
    if _AGY_READ_STATE["degraded"]:
        # Read without the WAL while a WAL existed: this conversation's newest turns are missing
        # by an unknown amount. Treated exactly like a partial read, because that is what it is.
        _log(f"{transcript_path} could only be opened without its write-ahead log, so the newest "
             f"turns are missing from this read.")
        tot["_unreadable"] = True
    try:
        try:
            # Iterated, not fetched: a conversation database has no size bound, and materialising
            # every blob risks a MemoryError, which `main`'s `except Exception` does not catch.
            cursor = con.execute("select data from gen_metadata")
        except sqlite3.Error as e:
            _log(f"could not read {transcript_path}: {e}")
            tot["_unreadable"] = True
            return tot
        try:
            for (blob,) in cursor:
                if not blob:
                    continue
                # EVERY level, from the row DOWN, and before deciding anything about the row's
                # shape. Two rounds of review landed here. Validating only the innermost usage
                # message was not enough — `_pb_sub` walks with the lenient scan, so a row whose
                # OUTER message is truncated still yields an inner one that parses perfectly, and
                # the row was counted with whatever the truncated prefix held (repro:
                # `0a06220410051807 0e` counted as a turn of 5 in / 7 out). And checking
                # `usage is None` FIRST was not enough either: a row damaged so badly that the
                # usage message cannot be extracted at all was skipped as though it were simply
                # not a model request, silently, so the report looked whole (repro: `0a03220210`).
                # Damage is now decided before shape. (Found by Codex, rounds two and three.)
                outer = _pb_sub(blob, AGY_USAGE[0])
                if not _pb_row_is_whole(blob, outer):
                    tot["_truncated_rows"] += 1
                    continue
                usage = _pb_sub(blob, *AGY_USAGE)
                if usage is None:
                    # A well-formed row that is not a model request — Antigravity writes those, and
                    # they are correctly not turns.
                    continue
                if not _pb_complete(usage):
                    tot["_truncated_rows"] += 1
                    continue
                # The mapping's own invariant, checked per row. A schema change that renumbered
                # these fields would otherwise be reported rather than refused — see
                # `_agy_usage_identity_holds`. Routed into the SAME damage path as a truncated row,
                # because the consequence is identical: this row's numbers cannot be trusted, and a
                # skipped row is already reported as skipped rather than silently dropped.
                if not _agy_usage_identity_holds(usage):
                    tot["_truncated_rows"] += 1
                    tot["_identity_violations"] += 1
                    continue
                tot["turns"] += 1
                tot["input_tokens"] += _pb_varint(usage, AGY_INPUT_TAG)
                tot["cache_read_tokens"] += _pb_varint(usage, AGY_CACHE_READ_TAG)
                # The TOTAL, which already contains the reasoning half. Summing the two summands
                # separately as well is the double-count this parser exists to avoid.
                tot["output_tokens"] += _pb_varint(usage, AGY_OUTPUT_TOTAL_TAG)
                model = _pb_str(outer, AGY_MODEL_TAG)
                if model:
                    tot["_models"].add(model)
        except sqlite3.Error as e:
            _log(f"stopped reading {transcript_path} partway: {e}")
            tot["_unreadable"] = True
            return tot
        if tot["_truncated_rows"]:
            _log(f"{tot['_truncated_rows']} unreadable row(s) in {transcript_path} were skipped "
                 f"rather than counted as turns that produced nothing.")
        if tot["_identity_violations"]:
            # Said separately and loudly: a truncated row is damage in one file, but a row whose
            # output no longer equals reasoning + visible suggests the MAPPING is wrong — every
            # number this client reports would then be suspect, not just this row's.
            _log(f"{tot['_identity_violations']} row(s) in {transcript_path} broke "
                 f"`output == reasoning + visible`. The Antigravity field mapping was measured "
                 f"against 2.8.0 and may no longer match this client; the rows were skipped rather "
                 f"than reported.")
        _agy_window(con, tot)
    finally:
        con.close()
    return tot


def _agy_window(con, tot: dict) -> None:
    """The session's first and last step time, as ISO-8601 UTC.

    `gen_metadata` carries no timestamp of its own — the times live on `steps.metadata` as a
    `google.protobuf.Timestamp`. The window is therefore the span of the CONVERSATION rather than
    of the model requests inside it, which is a slightly wider bracket and the only one the store
    offers."""
    try:
        # Iterated for the same reason `gen_metadata` is: a long conversation's step table has no
        # size bound, and materialising every blob risks a MemoryError that `main`'s
        # `except Exception` does not catch. (Found by Codex, seventh round.)
        rows = con.execute("select metadata from steps where metadata is not null")
    except sqlite3.Error as e:
        # Marked, not swallowed. A report that sends no window replaces a correct stored one with
        # nulls, on a report that otherwise looks whole — and it now costs a second thing too: an
        # absent window_end is the server's "write unconditionally" escape hatch, so this report
        # would also be exempt from the ordering check rather than merely wrong about its window.
        # (Found by Codex, third round.)
        _log(f"could not read the step times: {e}")
        tot["_unreadable"] = True
        return
    lo = hi = None
    try:
        rows = iter(rows)
    except TypeError:
        return
    for (blob,) in rows:
        if not blob:
            continue
        # BOTH levels. The damage that matters here is inside the Timestamp submessage, where a
        # truncation leaves a plausible prefix — `0a0308640e` yields 100, which reads as 1970 and
        # drags the whole window with it. Validating only the outer blob missed exactly that, and
        # the test written for it caught my own fix being one level short.
        stamp = _pb_sub(blob, AGY_STEP_TIMESTAMP[0])
        if not _pb_row_is_whole(blob, stamp):
            tot["_truncated_rows"] += 1
            continue
        seconds = _pb_varint(stamp, AGY_STEP_TIMESTAMP[1])
        if not seconds:
            continue
        lo = seconds if lo is None else min(lo, seconds)
        hi = seconds if hi is None else max(hi, seconds)
    if lo is not None:
        tot["window_start"] = _agy_iso(lo)
        tot["window_end"] = _agy_iso(hi)


def _agy_iso(seconds: int) -> str:
    """Seconds-since-epoch → the same `...Z` shape the other clients' transcripts already carry."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def antigravity_session_id(transcript_path: str):
    """A conversation's own id, read from `trajectory_meta.cascade_id`.

    The filename stem happens to equal it today. Reading the stem anyway would repeat the ZCode
    mistake in a quieter form — a filename is a naming convention, the column is the record — and
    the cost of asking is one row. The stem IS the fallback when the table cannot be read, because
    a probably-right id still lands on the right usage row while `None` reports nothing at all."""
    stem = os.path.basename(transcript_path)
    if stem.endswith(".db"):
        stem = stem[: -len(".db")]
    try:
        con = _agy_connect(transcript_path)
        try:
            row = con.execute("select cascade_id from trajectory_meta limit 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return stem or None
    if row and row[0]:
        return str(row[0])
    return stem or None


def _antigravity_conversation_dirs() -> list:
    """Where conversation databases live. The override wins and is used ALONE, so a test never
    silently mixes the running user's real sessions into a fixture's numbers."""
    override = os.environ.get(AGY_CONVERSATION_DIR_ENV)
    if override:
        return [override]
    home = os.path.expanduser("~")
    return [os.path.join(home, ".gemini", surface, leaf) for surface, leaf in AGY_STORES]


def antigravity_conversation_for(session_id: str):
    """The conversation database for `session_id`, across both surfaces, or None.

    Tries the constructed path first and then READS candidates for their own `cascade_id`, for the
    reason `zcode_rollout_for` does: a constructed path that misses produces no report at all,
    silently, which is the failure this module is written against. Newest first, so the scan stops
    early on the session that just ended."""
    if not session_id:
        return None
    dirs = _antigravity_conversation_dirs()
    for d in dirs:
        direct = os.path.join(d, f"{session_id}.db")
        if os.path.exists(direct) and _is_antigravity_transcript(direct):
            return direct
    candidates = []
    for d in dirs:
        candidates.extend(glob.glob(os.path.join(d, "*.db")))
    for path in sorted(candidates, key=_agy_mtime, reverse=True):
        if not _is_antigravity_transcript(path):
            continue
        if antigravity_session_id(path) == session_id:
            return path
    return None


def _agy_now() -> float:
    """Wall clock, named so a test can move it and so the one place it is read is findable."""
    return time.time()


def _agy_mtime(path: str) -> float:
    """The newest of the database and its `-wal`.

    A live conversation's most recent commits land in the WAL, and the main file's mtime can sit
    unchanged for the whole session — so ranking on it alone can make the conversation being
    written RIGHT NOW look older than a finished one. (Raised by Codex.)"""
    newest = 0.0
    for candidate in (path, path + "-wal"):
        try:
            newest = max(newest, os.path.getmtime(candidate))
        except OSError:
            continue
    return newest


# The workspace a conversation was opened against, as a `file://` URI on
# `trajectory_metadata_blob`. Present on 304 of 580 conversations measured, so it is a filter and
# never a requirement — a conversation without one simply cannot be matched by workspace.
AGY_WORKSPACE_TAG = 7
# How much newer the live conversation must be than any rival on the same workspace before
# discovery will pick it. SECONDS, not minutes: the live conversation is being written
# continuously while the agent works, so two conversations that are genuinely both open sit within
# seconds of each other at any moment, while a sequence — one finished, the next started a minute
# later — does not. The first version used 120s and refused ordinary sequential work: measured on
# this machine, 117 adjacent same-workspace pairs fall under two minutes and 14 workspaces would
# have been refused outright. Over-refusing loses the session's usage just as surely as
# misattributing it. (Found by Codex, eighth round.)
AGY_AMBIGUITY_SECONDS = 5.0
# How stale the chosen conversation may be. Discovery is run by `end-session` while the session is
# still open, so the live conversation was written moments ago; a workspace match that has not been
# touched for many minutes is some earlier conversation, not this one. Without this, a live
# conversation that records NO workspace (276 of 580 measured) is excluded from matching and an
# OLD one wins by default — the misattribution this whole path exists to prevent, arrived at from
# the other side. (Found by Codex, eighth round.)
AGY_FRESHNESS_SECONDS = 300.0


def antigravity_workspace(transcript_path: str):
    """The filesystem path a conversation was opened against, or None."""
    try:
        con = _agy_connect(transcript_path)
        try:
            row = con.execute("select data from trajectory_metadata_blob limit 1").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    uri = _pb_str(row[0], AGY_WORKSPACE_TAG)
    if not uri or not uri.startswith("file://"):
        return None
    # realpath, not normpath: the client records the path the user opened, while a process reports
    # the RESOLVED one from `getcwd`. On macOS that alone breaks the match for anything under
    # /tmp or /var (both symlinks into /private), so the two must be compared after resolution or
    # a real workspace silently never matches. Both sides of the comparison get the same treatment.
    return os.path.realpath(urllib.parse.unquote(uri[len("file://"):])) or None


def _dir_identity(path: str):
    """`(device, inode)` for a directory, or its resolved path if it cannot be stat'd.

    Directories are compared by IDENTITY, not by the spelling of their path. `realpath` resolves
    symlinks and leaves CASE alone, so on a case-insensitive volume — macOS's default — a
    conversation that recorded `/users/me/repo` and a session running in `/Users/me/repo` are the
    same directory with different strings, and a containment test on the strings drops the live
    conversation from the candidates entirely. Lowercasing would be wrong on a case-sensitive
    volume; the filesystem's own answer is right on both. (Found by Codex, eleventh round.)"""
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return os.path.realpath(path)


def _dir_identities(path: str) -> set:
    """The identity of `path` and of every directory containing it."""
    out = set()
    current = os.path.realpath(path)
    while True:
        out.add(_dir_identity(current))
        parent = os.path.dirname(current)
        if parent == current:
            return out
        current = parent


def antigravity_latest_conversation(cwd: str):
    """`(path, None)` for the conversation `cwd` is running in, or `(None, why not)`.

    ONE RULE, and it took ten review rounds to get to something this short: the live conversation
    is the most recently written thing that COULD be this session, and if that thing cannot be
    identified, or something else was written at the same moment, there is no answer.

    "Could be this session" — PLAUSIBLE below — is a conversation that either records a workspace
    containing `cwd`, or records no workspace at all. A conversation recording some other project's
    workspace is identifiably not this one and is ignored.

    Everything that came before was a threshold, and thresholds cannot decide identity. Three
    successive versions each closed one misattribution and left another: specificity-before-recency
    picked a thirty-second-old exact match over the LIVE conversation on a monorepo root; a
    freshness floor and a tie window both accepted a one-minute-old predecessor whenever the live
    conversation happened to record no workspace, which is 276 of 580 conversations here. The
    invariant replaces all of it — `--antigravity` is run BY the session it reports, so that
    session's database is the one being written as the command runs.

    Specificity survives only as a tie-break, and a narrow one: a tie between two conversations
    that both MATCH is genuinely ambiguous and refuses, so what it actually separates is a match
    from an unidentifiable conversation stamped at the same moment. The two remaining thresholds
    are secondary: a tie window, because two conversations genuinely both open sit within seconds
    of each other and nothing in the store separates them, and a staleness floor, because if
    nothing plausible has been touched in minutes then none of it is a live session. Refusing costs a report the operator can produce by naming the database; guessing
    costs another conversation's tokens on this build session, silently.

    Returns the REASON rather than logging it, so the caller emits one line carrying both the
    diagnosis and the remedy — this module's contract is one stderr line per failure."""
    if not cwd:
        return None, "no working directory to match a conversation against"
    target = os.path.realpath(cwd)
    ancestors = _dir_identities(target)

    files = []
    for d in _antigravity_conversation_dirs():
        files.extend(glob.glob(os.path.join(d, "*.db")))

    # (mtime, specificity, path, matched). `specificity` is the matched workspace's length and is
    # -1 for an unidentifiable conversation, so a tie between the two prefers the one we can name.
    plausible = []
    for path in files:
        if not _is_antigravity_transcript(path):
            continue
        workspace = antigravity_workspace(path)
        if not workspace:
            plausible.append((_agy_mtime(path), -1, path, False))
        elif _dir_identity(workspace) in ancestors:
            plausible.append((_agy_mtime(path), len(workspace), path, True))

    if not any(entry[3] for entry in plausible):
        return None, f"no Antigravity conversation records a workspace containing {target}"

    # RE-SAMPLED INTO ONE SNAPSHOT, and every decision below reads that snapshot. Each mtime
    # above was taken at a different moment, interleaved with two SQLite opens per file, and the
    # whole scan of a real 580-database store takes about half a second — during which the
    # conversation this command is reporting is being WRITTEN. So the live one can be sampled
    # early, overtaken on paper by a predecessor sampled later, and lose.
    #
    # The first version of this fix re-sampled only the WINNER, and left the rivals on their scan
    # timestamps. That is worse than it sounds: with A at 100 and B at 94 the rivals look six
    # seconds apart, but a fresh read of A=101 and B=100 puts them one second apart and therefore
    # ambiguous — and the check, comparing the new A against the stale B, saw seven seconds and
    # returned A. A whole snapshot or none. (Found by Codex, eleventh and twelfth rounds.)
    def _resample(entries):
        return [(_agy_mtime(e[2]), e[1], e[2], e[3]) for e in entries]

    first = _resample(plausible)
    plausible = _resample(first)
    if max(first)[2] != max(plausible)[2]:
        return None, ("the conversation store changed while it was being read, so which "
                      "conversation is the most recent could not be settled")
    best = max(plausible)

    if not best[3]:
        return None, (
            "the most recently written Antigravity conversation that could be this session "
            f"({os.path.basename(best[2])}) records no workspace, so it cannot be told apart from "
            f"the ones that do match {target}")

    rivals = [e for e in plausible
              if e[2] != best[2] and best[0] - e[0] <= AGY_AMBIGUITY_SECONDS]
    if rivals:
        names = ", ".join(sorted(os.path.basename(e[2]) for e in [best, *rivals]))
        return None, (
            f"{len(rivals) + 1} Antigravity conversations that could be this session were written "
            f"within {AGY_AMBIGUITY_SECONDS:.0f}s of each other, so which one it is cannot be told "
            f"from the store: {names}")

    age = _agy_now() - best[0]
    if age < -AGY_AMBIGUITY_SECONDS:
        # A clock correction or a preserved timestamp would otherwise win every comparison for as
        # long as it stayed in the future. (Raised by Codex.)
        return None, (
            f"the newest Antigravity conversation for {target} is stamped {-age:.0f}s in the "
            "FUTURE, so the store's timestamps cannot be used to tell which is this session")
    if age > AGY_FRESHNESS_SECONDS:
        return None, (
            f"the newest Antigravity conversation that could be this session was last written "
            f"{age:.0f}s ago, which is too stale — a live one is written as the agent works")

    return best[2], None


def _subagent_files(transcript_path: str) -> list:
    """Out-of-process sub-agent transcripts live at
    `<dir>/<session-id>/subagents/agent-*.jsonl` — the parent session is the
    directory name, so association needs no field-scraping."""
    d = os.path.dirname(transcript_path)
    sid = os.path.basename(transcript_path)
    if sid.endswith(".jsonl"):
        sid = sid[: -len(".jsonl")]
    return sorted(glob.glob(os.path.join(d, sid, "subagents", "*.jsonl")))


def classify(transcript_path: str) -> str:
    """Which client wrote this transcript — read ONCE, per report.

    The probe reads the FILE, and these files are live: a ZCode rollout is appended to on every
    request and truncated at 64 MB. Probing separately to pick the parser and to pick the reported
    `agent` can therefore disagree — aggregating a rollout as ZCode and labelling it `claude-code`.
    That is not cosmetic: both ZCode guards (the zero-turn refusal and the high-water mark) key on
    the label, so a disagreement bypasses both. One classification, passed to everything that needs
    it. (Found by Codex.)"""
    # First because it is the cheapest and the most certain: a 16-byte magic number, where the
    # other two probes read up to 200 JSON lines of what would be a binary file. Note it asks only
    # for the HEADER — see `_has_sqlite_magic` for why the stricter check belongs elsewhere.
    if _has_sqlite_magic(transcript_path):
        return AGENT_ANTIGRAVITY
    if _is_zcode_transcript(transcript_path):
        return AGENT_ZCODE
    if _is_codex_transcript(transcript_path):
        return AGENT_CODEX
    return AGENT


def aggregate(transcript_path: str, agent: str | None = None) -> dict:
    """Sum the main transcript + its sub-agent transcripts into a usage dict.
    Cache-read dominates by ~1000x, so the four billable input classes are
    kept separate — exactly the five classes PostMQ stores.

    `agent` is the classification when the caller already made it; omitting it re-reads the file,
    which is fine for a one-shot caller and wrong inside a report — see `classify`."""
    agent = agent or classify(transcript_path)
    tot = {
        "turns": 0, "sidechain_turns": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "window_start": None, "window_end": None, "_models": set(),
    }
    if agent == AGENT_ANTIGRAVITY:
        return aggregate_antigravity(transcript_path)
    if agent == AGENT_ZCODE:
        return aggregate_zcode(transcript_path)
    if agent == AGENT_CODEX:
        return aggregate_codex(transcript_path)
    snapshots, readable = _claude_snapshots(transcript_path)
    if not readable:
        tot["_unreadable"] = True
    _fold(snapshots, tot)
    return tot


def _empty_total() -> dict:
    return {
        "turns": 0, "sidechain_turns": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "window_start": None, "window_end": None, "_models": set(),
    }


def _claude_snapshots(transcript_path: str):
    """`(snapshots, readable)` for a Claude Code transcript and its sub-agent transcripts."""
    snapshots: dict = {}
    readable = _collect_file(transcript_path, snapshots, is_subagent=False)
    for sub in _subagent_files(transcript_path):
        readable = _collect_file(sub, snapshots, is_subagent=True) and readable
    return snapshots, readable


def aggregate_by_build_session(transcript_path: str, boundaries: list) -> dict:
    """A Claude Code transcript's totals SPLIT at build-session boundaries: `{build session id: aggregate}`,
    in the order the build sessions first appear.

    `boundaries` is `[(epoch seconds, build session id), ...]` as `read_boundaries` returns it. A turn
    belongs to the build session whose boundary is the latest at or before the turn's own timestamp;
    a turn before the first boundary belongs to the FIRST build session, which is exactly what a
    one-session transcript always reported; a turn with no readable timestamp belongs to the LATEST,
    which is what the pointer says. One read of the transcript serves every build session, so the
    parts always add up to the whole. (GitHub #1387 item 7.)"""
    snapshots, readable = _claude_snapshots(transcript_path)
    order = list(dict.fromkeys(bsid for _t, bsid in boundaries))
    groups = {bsid: {} for bsid in order}
    for key, snap in snapshots.items():
        groups[_boundary_owner(snap.get("timestamp"), boundaries)][key] = snap
    out = {}
    for bsid in order:
        tot = _empty_total()
        if not readable:
            tot["_unreadable"] = True
        _fold(groups[bsid], tot)
        out[bsid] = tot
    return out


def read_was_partial(tot: dict) -> bool:
    """Whether the aggregate it is given came from LESS than the whole store.

    `turns == 0` catches a read that failed before it started. It does not catch one that failed
    PARTWAY: a cursor error after some rows, rows skipped as unreadable, or a WAL that could not be
    read all produce a plausible non-zero total quietly short of the truth — and the upsert
    replaces every count, so sending it shrinks a correct row rather than erasing it.

    It takes the AGGREGATE, not a path, and that is the whole point of the signature. The first
    version re-read the database, so the payload and the guard were two different snapshots of a
    file being written: a partial first read followed by a healthy second one SENT the short
    payload, and the reverse order refused a healthy one. (Found by Codex, rounds two and three.)"""
    return bool(tot.get("_unreadable")) or bool(tot.get("_truncated_rows"))


def build_arguments(transcript_path: str, client_session_id: str, build_session_id: str | None,
                    tot: dict | None = None, agent: str | None = None) -> dict:
    """The ONE payload: the `record_build_session_usage` tool arguments, which
    are byte-for-byte the `POST /v1/usage` body. Keys are exactly ARGUMENT_KEYS."""
    # The reporting agent is derived from the TRANSCRIPT, never from a flag a caller could get
    # wrong — and derived ONCE, so the shape that was parsed and the shape that is reported cannot
    # disagree on a file that is still being written.
    # `agent` and `tot` travel TOGETHER from the caller, or neither does — ENFORCED, because the
    # docstring said it and the signature did not. Passing totals without the label they were read
    # under is how ZCode numbers end up reported as claude-code, which bypasses both ZCode guards.
    # (Found by Codex, fourth and fifth rounds.)
    # FALSINESS, not `is None`: `agent=""` slipped through the None-pairing test and was then
    # relabelled by `agent or classify(...)`, which is the exact mislabelling the pairing exists to
    # stop — ZCode totals reported as claude-code, reproduced. (Found by Codex, sixth round.)
    if bool(tot is None) != (not agent):
        raise ReportError("internal: build_arguments needs the aggregate and its agent together")
    agent = agent or classify(transcript_path)
    tot = tot if tot is not None else aggregate(transcript_path, agent)
    args = {
        "build_session_id": build_session_id,
        "client_session_id": client_session_id,
        "input_tokens": tot["input_tokens"],
        "cache_write_5m_tokens": tot["cache_write_5m_tokens"],
        "cache_write_1h_tokens": tot["cache_write_1h_tokens"],
        "cache_read_tokens": tot["cache_read_tokens"],
        "output_tokens": tot["output_tokens"],
        "turns": tot["turns"],
        "sidechain_turns": tot["sidechain_turns"],
        "models": sorted(tot["_models"]),
        "agent": agent,
        "reporter_version": REPORTER_VERSION,
        "window_start": tot["window_start"],
        "window_end": tot["window_end"],
    }
    if tuple(args) != ARGUMENT_KEYS:
        raise ReportError("internal: payload keys drifted from ARGUMENT_KEYS")
    return args


# ── build-session id resolution ───────────────────────────────────────────
# Where /start-session leaves the build-session pointer. One directory per client the kit installs
# into; `.claude` stays FIRST so an existing Claude Code install resolves exactly as it did before
# this became a list.
STATE_DIRS = ((".claude", "state"), (".codex", "state"), (".zcode", "state"),
              (".agents", "state"))


def _state_root(cwd: str) -> str:
    """The git common dir's parent, so a file written from any worktree is found from any worktree.
    Falls back to <cwd> outside a repository."""
    root = None
    try:
        common = subprocess.run(
            ["git", "-C", cwd or ".", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if common.returncode == 0 and common.stdout.strip():
            root = os.path.dirname(common.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    if root is None:
        root = cwd or "."
    return root


def _state_dirs(cwd: str, root: str | None = None) -> list:
    """The candidate directories a build-session pointer may live in, one per client.

    The root is resolved ONCE and shared. Calling `_state_root` per entry would shell out to git
    once per client directory, and a transient failure part-way through would yield a MIXED list — `.claude`
    under the git common root, `.codex` under the cwd fallback — so the pointer could be missed or
    the wrong one chosen. (Found by Codex.)"""
    root = root or _state_root(cwd)
    return [os.path.join(root, *parts) for parts in STATE_DIRS]


# A per-session HIGH-WATER MARK against ONE failure: ZCode TRUNCATES a rollout past 64 MB rather
# than rotating it, so every report after that reads only the post-reset suffix and the row would
# ratchet DOWN for the rest of the session — the first such report being the one that destroys the
# true total. Freezing at the last true figure is the better of the two available wrongs: it is an
# under-report either way, and this one does not also erase what was already known. It is logged,
# because a report silently declining to report is the failure this whole module is written
# against.
#
# WHAT IT DOES NOT DO, stated because a guard believed to be stronger than it is, is worse than no
# guard. It does NOT order two concurrent reports, and it never could have. An earlier draft tried:
# a mkdir mutex around read-send-write. Codex took it apart, and the decisive objection is not a
# bug in the lock but a fact about the system — no local protocol can establish monotonicity at
# all, because if a send commits and its response is lost, the mark is never written and the next
# report regresses the row whatever the local lock did; and the lock's own lease could not be given
# a sound duration, because urlopen's timeout is per-socket rather than end-to-end, so a legitimate
# hold has no finite upper bound. Four P1 races bought nothing.
#
# ORDERING NOW LIVES ON THE SERVER, which is the only place it could. The upsert is conditional on
# window_end: a report carrying one older than the stored row's is discarded rather than applied,
# and the response says so with `applied` false. That is what `send` returns and why the mark below
# is armed only on a write the server took. The mark stays because it is a different guard for a
# different failure — a rollout ZCode truncated mid-session, which is not an ordering problem.
#
# So the mark is deliberately BEST-EFFORT and deliberately simple. It catches the deterministic,
# sequential case — a truncated rollout reporting smaller for the rest of a session — and it is
# honest about the concurrent one, which belongs in a conditional upsert on the server. Filed.
# SCOPE, because a guard that refuses reports is itself a way to lose them. It applies to the
# ZCODE path only: the truncation it guards is ZCode's, and Claude Code and Codex report once at
# session end, so the guard could only ever refuse one of their legitimate re-reports. And it is keyed by BUILD SESSION as well as client session — the same pair the
# report's own idempotency key uses — so backfilling a past client session against a different
# build session is compared against that pair's own history rather than against another's.
HIGH_WATER_PREFIX = "usage-high-water-"


def _high_water_path(build_session_id: str, claude_session_id: str, cwd: str,
                     root: str | None = None) -> str | None:
    """Always `.zcode/state`, never "the first state directory that happens to exist".

    Resolving once inside a report guarantees that ONE report reads and writes the same file.
    Across reports, a rule keyed on existence is not stable: report 1 writes under `.zcode/state`,
    something creates `.claude/state`, and report 2 reads a mark that was never there — then sends
    the post-truncation figures the mark existed to refuse. The guard is ZCode-only, so its file
    belongs in ZCode's own directory unconditionally, created if absent. (Found by Codex.)"""
    if not build_session_id or not claude_session_id:
        return None
    key = _safe_file_segment(f"{build_session_id}-{claude_session_id}")
    return os.path.join(root or _state_root(cwd), ".zcode", "state", HIGH_WATER_PREFIX + key)


def _safe_file_segment(segment: str) -> str:
    """A filename-safe rendering of our OWN key.

    Deliberately not `_zcode_file_segment`, which mirrors ZCode's sanitiser so that ZCode's
    filenames can be reconstructed. Borrowing it here would couple our mark's name to another
    product's rule: the day that rule changes and the mirror follows, every existing mark is
    orphaned under its old name and the guard silently restarts from zero."""
    return "".join(c if (c.isascii() and (c.isalnum() or c in "_-")) else "-" for c in segment)[:120]


def read_high_water(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            found = json.load(fh)
        # Anything that is not the per-field object is a mark this version does not understand —
        # an older reporter wrote a bare integer here. Reading it as "nothing recorded" lets the
        # report through, which is the right way round: the mark exists to stop a regression, and a
        # mark we cannot interpret is not evidence of one. (Found by Codex.)
        return {k: int(found.get(k, 0) or 0) for k in MONOTONIC_FIELDS} if isinstance(found, dict) else {}
    except (OSError, ValueError, TypeError, OverflowError):
        # A half-written or absent mark reads as "nothing recorded", which lets the report
        # through. That is the right way round: the mark exists to stop a regression, and a
        # mark we cannot read is not evidence of one.
        return {}


def write_high_water(path: str | None, counts: dict) -> None:
    if not path:
        return
    # Write-then-rename: a reader must never see a half-written mark, and os.replace is atomic on
    # every platform this runs on.
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(counts, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# Comparing a SUM would let a report through that is larger overall and smaller in a class the
# server overwrites individually — a truncated suffix totalling 101 against a stored 100 can carry
# fewer cache reads and fewer turns, and the row would lose both. So every field the guard can
# meaningfully order is compared on its own. (Found by Codex.)
MONOTONIC_FIELDS = (
    "input_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens",
    "cache_read_tokens", "output_tokens", "turns", "sidechain_turns",
)


def reported_counts(arguments: dict) -> dict:
    """The per-field figures the high-water mark orders reports by."""
    return {k: int(arguments.get(k, 0) or 0) for k in MONOTONIC_FIELDS}


def regressed_fields(counts: dict, recorded: dict) -> list:
    """Which fields this report would REDUCE against the LOCAL mark. Empty means nothing known
    here objects to sending — not that the server row cannot still regress, which no local check
    can promise."""
    return sorted(k for k in MONOTONIC_FIELDS if counts.get(k, 0) < recorded.get(k, 0))


def _locate_pointer(names, directories):
    """`(build session id, the state directory it was found in)`, or `(None, None)`.

    The NAME is the outer loop, deliberately. With directories outermost, an alias spelling sitting
    in an earlier state directory would beat the canonical pointer in a later one and attribute this
    session's usage to a different, and perfectly valid, build session. (Found by Codex.)"""
    for name in names:
        for state_dir in directories:
            try:
                with open(os.path.join(state_dir, STATE_PREFIX + name), encoding="utf-8") as fh:
                    found = fh.read().strip()
                    if found:
                        return found, state_dir
            except OSError:
                continue
    return None, None


# THE BOUNDARY LOG, beside the pointer: `build-session-boundaries-<client session id>`, one line per
# build session start-session opened in this client session, `<UTC ISO-8601 time> <build session id>`,
# appended and never rewritten. The pointer alone cannot say WHEN it changed, so a second build
# session in one conversation used to take the whole transcript (GitHub #1387 item 7). The pointer
# keeps its old one-id shape on purpose: a reporter that predates the log still reads the pointer
# and behaves as it always did, and a skill that predates it writes no log, which reads as one build
# session. Neither half breaks the other while they are upgraded separately.
BOUNDARY_PREFIX = "build-session-boundaries-"

_ISO_UTC = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def _epoch(ts) -> float | None:
    """Seconds since the epoch for an ISO-8601 time, or None. Parsed rather than string-compared:
    `...:02.406Z` sorts BEFORE `...:02Z` as text and after it as a time, and the transcript and the
    log write different precisions."""
    if not isinstance(ts, str):
        return None
    m = _ISO_UTC.match(ts.strip())
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) for g in m.groups()[:6])
    fraction = float("0." + m.group(7)) if m.group(7) else 0.0
    offset = 0
    zone = m.group(8)
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 3600 + int(digits[2:]) * 60)
    try:
        return calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)) + fraction - offset
    except (ValueError, OverflowError):
        return None


def read_boundaries(state_dir: str | None, names) -> list:
    """The boundary log's entries, `[(epoch seconds, build session id), ...]` in time order, read from
    the directory the POINTER was found in, under the first name that has a log. Malformed lines are
    skipped. Empty when there is no log."""
    if not state_dir:
        return []
    for name in names:
        try:
            with open(os.path.join(state_dir, BOUNDARY_PREFIX + name), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        entries = []
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                continue
            when = _epoch(parts[0])
            if when is not None and parts[1]:
                # A time written to the SECOND (`date -u` cannot portably do better) is read as the
                # END of that second. The start-session call that wrote it is a turn logged before
                # the command ran, and the agent's next turn needs a model round trip, so a turn in
                # the same second is the old build session's. Read as the start of the second,
                # 10:00:01Z took a 10:00:01.900 turn away from the session that made it. (Codex.)
                if "." not in parts[0]:
                    when += 1.0
                entries.append((when, parts[1]))
        # Stable: two entries written in the same second keep the order they were written in.
        return sorted(entries, key=lambda e: e[0])
    return []


def _reconcile_pointer(boundaries: list, pointer_id: str, pointer_dir: str | None, names) -> list:
    """The boundaries with the pointer's build session appended, starting when the pointer file was
    last written. If that time is not after the log's last entry, which no writer produces in order
    and only a clock change or a hand edit could, the pointer's build session takes over from the
    log's last entry instead, so every turn still has exactly one owner."""
    written = None
    for name in names:
        try:
            written = os.path.getmtime(os.path.join(pointer_dir or "", STATE_PREFIX + name))
            break
        except (OSError, TypeError):
            continue
    last_start = boundaries[-1][0]
    if written is None or written <= last_start:
        _log(f"the build-session boundary log ends at {boundaries[-1][1]} but the pointer names "
             f"{pointer_id}, and the pointer is not newer than the log; {pointer_id} is credited "
             "from the log's last entry.")
        return boundaries[:-1] + [(last_start, pointer_id)]
    return boundaries + [(written, pointer_id)]


def _boundary_owner(ts, boundaries: list) -> str:
    when = _epoch(ts)
    if when is None:
        return boundaries[-1][1]
    owner = boundaries[0][1]
    for start, bsid in boundaries:
        if start <= when:
            owner = bsid
        else:
            break
    return owner


def resolve_build_session_id(claude_session_id: str, cwd: str, *, also: str = "",
                             root: str | None = None) -> str | None:
    """`also` is a second spelling of the same session to look the pointer up under.

    It exists because the ZCode path CANONICALISES the reported id to the one inside the rollout,
    while `/start-session` wrote the pointer under whatever id the agent was shown. Those agree in
    the normal flow — both are ZCode's `sess_`-prefixed id — but if a payload ever hands over the
    bare uuid, canonicalising and then looking up only the canonical name would miss a pointer
    that is sitting right there, and the usage would be read and then dropped. (Found by Codex.)"""
    env = os.environ.get("POSTMQ_BUILD_SESSION_ID")
    if env:
        return env.strip()
    seen = dict.fromkeys(n for n in (claude_session_id, also) if n)
    # Resolved once for BOTH spellings. Re-deriving per name invokes git twice, and a transient
    # failure between them gives the two names different roots — so an alias can be selected while
    # a canonical pointer sits under the other one. (Found by Codex.)
    return _locate_pointer(seen, _state_dirs(cwd, root))[0]


# ── transports ────────────────────────────────────────────────────────────
class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow no redirect. urllib's default handler re-sends every non-content header to the
    `Location` of a redirect it follows, Authorization included, to any host and from https down to
    http. For this POST it follows 301, 302 and 303, so a redirect from the endpoint (or anything in
    front of it) would hand the bearer to another address. Each 30x hook declines before the
    default one would parse `Location`, which is the endpoint's to write and can make urllib raise,
    so the 3xx falls through to the default error handler and is raised as an HTTPError for `_http`
    to report."""

    def http_error_302(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


# build_opener drops its default redirect handler when given a subclass of it.
_OPENER = urllib.request.build_opener(_RefuseRedirects)
# Plain http:// is only ever this machine (_require_encrypted refuses anything else), and an ambient HTTP_PROXY would
# carry it OFF this machine in cleartext, bearer and all (Codex). https:// keeps the default proxy handling: a proxy
# sees only a CONNECT tunnel.
_OPENER_DIRECT = urllib.request.build_opener(_RefuseRedirects, urllib.request.ProxyHandler({}))
_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _urlopen(req, timeout):
    """The one place a request leaves this process. Tests stand in for it here."""
    opener = _OPENER_DIRECT if req.type == "http" else _OPENER
    return opener.open(req, timeout=timeout)


_SECRET = ""  # the credential _credential() accepted, so _redact() redacts what was actually sent


def _credential_value() -> tuple[str, str]:
    """The credential and where it came from, unchecked: POSTMQ_CREDENTIAL, else the file named by
    POSTMQ_CREDENTIAL_FILE, else ~/.config/postmq/credential. The same order as the wake receiver, so one
    credential file serves both scripts; a hook run from an unexported subshell at session close found
    nothing before (issue #1385, field test 2026-10-26)."""
    value = (os.environ.get("POSTMQ_CREDENTIAL") or "").strip()
    if value:
        return value, "POSTMQ_CREDENTIAL"
    # A fixed LABEL, never the path itself: the path is the operator's to write, and printed into a log it could carry
    # a line break that forges a second log line (Codex).
    named = os.environ.get("POSTMQ_CREDENTIAL_FILE")
    label = "the file named by POSTMQ_CREDENTIAL_FILE" if named else "~/.config/postmq/credential"
    path = named or os.path.expanduser("~/.config/postmq/credential")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip(), label
    except (OSError, UnicodeDecodeError):
        return "", label


def _credential() -> str:
    """The credential, checked before it goes near a header. http.client refuses a header value
    holding a line break and raises an error whose message is the WHOLE value, so an unchecked
    pasted line ending would put the credential in the log. The check is a credential-FORMAT rule,
    stricter than what a header allows: a credential is `pmq_` + base64url, so anything outside
    printable ASCII (a space, a tab, non-ASCII) is a paste mistake rather than a credential."""
    global _SECRET
    value, source = _credential_value()
    if not value:
        raise ReportError("no PostMQ credential — set POSTMQ_CREDENTIAL or put one in ~/.config/postmq/credential "
                          "(it needs the write_session_state scope). Nothing was sent.")
    if not all("!" <= ch <= "~" for ch in value):
        raise ReportError(f"the credential in {source} holds a character a PostMQ credential never contains (a line "
                          "break, a space, a control character or non-ASCII text, usually a second line or a pasted "
                          "line ending). Nothing was sent, and the value is not shown.")
    _SECRET = value
    return value


# When this run must be finished by, on time.monotonic(). THREAD-LOCAL, set only on the worker
# thread main() starts: a worker abandoned at the deadline keeps its own expired deadline, so its next
# request refuses instead of starting, and code that calls a transport directly (tests) sees none.
_RUN = threading.local()


def _request_timeout(ceiling: float) -> float:
    """A request's socket timeout: its own ceiling, cut to what is left of the run's deadline. A
    run with nothing left raises rather than starting a request it cannot finish."""
    deadline = getattr(_RUN, "deadline", None)
    if deadline is None:
        return ceiling
    left = deadline - time.monotonic()
    if left <= 0:
        raise ReportError("the report's deadline passed before the next request; nothing more was sent")
    return min(ceiling, left)


def _http(url: str, body: dict, headers: dict, timeout: int = HTTP_TIMEOUT_SECONDS):
    """POST JSON; return (status, response_headers, body_text). Raises ReportError
    on any transport failure, non-2xx or redirect, with a message that carries
    no header this process sent. Text the endpoint wrote (an error body) is
    redacted BEFORE it is truncated, so an echoed credential cannot survive as a
    fragment cut short of the whole value."""
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode("utf-8"), headers=headers)
    timeout = _request_timeout(timeout)
    try:
        with _urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            detail = _redact(e.read().decode("utf-8", "replace"))[:200]
        except (OSError, ValueError):
            detail = ""
        finally:
            e.close()
        if e.code in _REDIRECT_CODES:
            # The status only. The Location is the endpoint's to write, so echoing any of it would let the
            # endpoint put text of its choosing in the log, and parsing it can itself raise.
            raise ReportError(f"POST {url} -> HTTP {e.code} redirect refused: the credential is never sent on to "
                              "another address, so point the URL at the final one") from None
        raise ReportError(f"POST {url} -> HTTP {e.code} {detail!r}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ReportError(f"POST {url} failed: {e}") from None
    except ValueError as e:
        # http.client raises ValueError for a header it will not send, and the message is the whole
        # value. _credential() refuses such a credential before it gets here; this keeps any other
        # header from reaching the log the same way.
        raise ReportError(f"POST {url} could not be sent: a header or the URL was refused before sending "
                          f"({type(e).__name__}; detail withheld because it can carry the credential)") from None


_SSE_FIELDS = ("data:", "event:", "id:", "retry:", ":")


def _sse_messages(text: str) -> list:
    """Every JSON-RPC message an SSE stream carries, in order.

    An event is the lines up to a blank line; its data is every `data:` line, joined with newlines
    (one optional space after the colon is not part of the value). `id:`, `event:`, `retry:` and
    comment lines carry no message. An event whose data is not JSON is skipped rather than fatal,
    since only the reply the caller is waiting for has to parse."""
    messages = []
    data = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") + [""]:
        if line == "":
            if data:
                try:
                    parsed = json.loads("\n".join(data))
                except ValueError:
                    parsed = None
                if isinstance(parsed, list):
                    messages.extend(parsed)
                elif parsed is not None:
                    messages.append(parsed)
            data = []
        elif line.startswith("data:"):
            value = line[len("data:"):]
            data.append(value[1:] if value.startswith(" ") else value)
    return messages


def _parse_rpc(text: str, request_id=None) -> dict:
    """The `result` of the reply to `request_id`, from a bare JSON-RPC response or an SSE stream.

    The stream case is why `request_id` exists (GitHub #1387 item 11). Streamable HTTP lets a server
    send notifications and requests on the stream BEFORE the reply, and an event may open with an
    `id:` line; the first version took the first `data:` line, so a notification ahead of the
    reply was read as the reply and its result lost, and a stream opening with `id:` did not parse.
    The reply is the message carrying `result` or `error` whose `id` is the request's."""
    stripped = text.strip()
    if stripped.startswith(_SSE_FIELDS):
        messages = _sse_messages(text)
    else:
        try:
            parsed = json.loads(stripped)
        except ValueError as e:
            raise ReportError(f"unparseable JSON-RPC response: {e}") from None
        messages = parsed if isinstance(parsed, list) else [parsed]
    replies = [m for m in messages if isinstance(m, dict) and ("result" in m or "error" in m)]
    if request_id is not None:
        replies = [m for m in replies if m.get("id") == request_id]
    if not replies:
        raise ReportError("the response carried no JSON-RPC reply"
                          + (f" to request {request_id}" if request_id is not None else ""))
    reply = replies[0]
    if "error" in reply:
        err = reply["error"] or {}
        raise ReportError(f"JSON-RPC error {err.get('code')}: {err.get('message', err)}")
    return reply.get("result") or {}


def _tool_payload(result: dict) -> dict:
    """Unwrap the MCP content envelope to the tool's own JSON; a tool-level
    {error:{code,message}} (PostMQ's uniform failure envelope) is a failure."""
    payload = None
    if "structuredContent" in result:
        payload = result["structuredContent"]
    else:
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    payload = json.loads(block["text"])
                except ValueError:
                    payload = {"_text": block["text"]}
                break
    if result.get("isError"):
        raise ReportError(f"tool reported isError: {_redact(json.dumps(payload))[:200]}")
    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"] or {}
        raise ReportError(f"tool error {err.get('code')}: {err.get('message')} (request_id {err.get('request_id')})")
    if payload is None:
        raise ReportError("tool returned no usable content")
    return payload


class McpClient:
    """Minimal MCP Streamable-HTTP client — initialize, notifications/initialized,
    tools/call. Stdlib only. Written for this hook rather than vendored from
    tools/generate-launch-plan.py because that client exits the process on
    failure and this one must raise (fail-open is decided by the caller)."""

    def __init__(self, url: str, credential: str) -> None:
        self._url = url
        self._auth = f"Bearer {credential}"
        self._session: str | None = None
        # The version initialize NEGOTIATED, sent on every later request as MCP-Protocol-Version,
        # which the 2025-06-18 transport makes a MUST (GitHub #1387 item 12). None until then: the
        # initialize request carries its version in the body and sends no header.
        self._protocol_version: str | None = None
        self._id = 0

    def _headers(self) -> dict:
        h = {
            "Authorization": self._auth,
            "Content-Type": "application/json",
            # The server may answer either shape; _parse_rpc handles both.
            "Accept": "application/json, text/event-stream",
        }
        if self._session:
            h["Mcp-Session-Id"] = self._session
        if self._protocol_version:
            h["MCP-Protocol-Version"] = self._protocol_version
        return h

    def _next(self) -> int:
        self._id += 1
        return self._id

    def call_tool(self, name: str, arguments: dict) -> dict:
        init_id = self._next()
        _status, headers, text = _http(self._url, {
            "jsonrpc": "2.0", "id": init_id, "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "postmq-usage-reporter", "version": REPORTER_VERSION}},
        }, self._headers())
        sid = {k.lower(): v for k, v in headers.items()}.get("mcp-session-id")
        if sid:
            self._session = sid
        init = _parse_rpc(text, init_id)  # a JSON-RPC error on initialize is a failure
        negotiated = init.get("protocolVersion") if isinstance(init, dict) else None
        self._protocol_version = negotiated if isinstance(negotiated, str) and negotiated else MCP_PROTOCOL_VERSION
        _http(self._url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, self._headers())
        call_id = self._next()
        _status, _headers, text = _http(self._url, {
            "jsonrpc": "2.0", "id": call_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, self._headers())
        return _tool_payload(_parse_rpc(text, call_id))


def _require_encrypted(url: str, variable: str) -> str:
    """Refuse an endpoint the credential would reach unencrypted: https://, or plain http:// to this machine only
    (local development). The URL is not echoed: it is the operator's to write and can carry userinfo. Backlog
    01a176da-288d."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is not None and parts.scheme == "https" and host:
        return url
    if parts is not None and parts.scheme == "http" and _is_this_machine(host):
        return url
    raise ReportError(f"{variable} must be an https:// address (plain http:// is allowed only to localhost or a "
                      "loopback address), because the credential would otherwise cross the network unencrypted. "
                      "Nothing was sent.")


def _is_this_machine(host: str) -> bool:
    """A loopback ADDRESS, or `localhost` / a `.localhost` name (RFC 6761). Never a prefix test on the text: a
    hostname such as `127.0.0.1.example.com` is somebody else's machine."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def send_mcp(arguments: dict) -> dict:
    url = _require_encrypted(os.environ.get("POSTMQ_MCP_URL") or DEFAULT_MCP_URL, "POSTMQ_MCP_URL")
    credential = _credential()
    return McpClient(url, credential).call_tool(TOOL_NAME, arguments)


def send_rest(arguments: dict) -> dict:
    base = os.environ.get("POSTMQ_API_URL")
    if not base:
        raise ReportError("--rest needs POSTMQ_API_URL (e.g. https://api.postmq.com).")
    url = _require_encrypted(base.rstrip("/"), "POSTMQ_API_URL") + REST_ROUTE
    credential = _credential()
    _status, _headers, text = _http(url, arguments, {
        "Authorization": f"Bearer {credential}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        return json.loads(text) if text.strip() else {}
    except ValueError as e:
        raise ReportError(f"REST response was not JSON: {e}") from None


def _target(rest: bool) -> dict:
    if rest:
        base = os.environ.get("POSTMQ_API_URL") or ""
        return {"transport": "rest", "url": (base.rstrip("/") + REST_ROUTE) if base else None, "route": f"POST {REST_ROUTE}"}
    return {"transport": "mcp", "url": os.environ.get("POSTMQ_MCP_URL") or DEFAULT_MCP_URL, "tool": TOOL_NAME}


def send(arguments: dict, *, rest: bool) -> bool:
    """Send via the chosen transport and log the outcome. Raises ReportError.

    Returns whether the server APPLIED the write. `applied: false` means the row already holds a
    report with a later `window_end`, so this one was discarded — a success with nothing to retry,
    and the one outcome the caller must not treat as "the server now has my numbers". A server that
    predates the flag omits it, and an absent flag is read as applied, so the older behaviour is
    unchanged."""
    result = send_rest(arguments) if rest else send_mcp(arguments)
    created = result.get("created") if isinstance(result, dict) else None
    applied = result.get("applied") if isinstance(result, dict) else None
    if applied is False:
        verb = "discarded as out of order"
    else:
        verb = "recorded" if created else ("overwrote" if created is False else "sent")
    _log(f"{verb} usage for build session {arguments['build_session_id']} "
         f"(transcript {arguments['client_session_id']}: {arguments['cache_read_tokens']} cache-read, "
         f"{arguments['output_tokens']} output, {arguments['turns']} turns) via {'rest' if rest else 'mcp'}")
    return applied is not False


# ── the hook path ─────────────────────────────────────────────────────────
def run(hook: dict, *, dry_run: bool = False, rest: bool = False,
        explicit_transcript: bool = False, agent: str | None = None) -> int:
    """Return 0 on success, 1 on any failure (main() maps that through --strict).

    `explicit_transcript` says a person named this file (`--dry-run <path>`, `--backfill`), so it
    is parsed as given. On the hook path the transcript comes from the CLIENT, and on ZCode that
    is the synthetic decoy the ZCode block describes — the one case where the payload has to be
    overruled.

    `agent` is the classification a CALLER already made. Every entry point probes the file once to
    work out its session id, and re-probing here made that two reads of a file that is still being
    written: a rollout that truncates in between yields ZCode's totals under the `claude-code`
    label, which bypasses both ZCode guards because they key on the label. Pass what you learned.
    (Found by Codex, fifth round.)

    Its scope is narrow and worth stating: it is trusted for the file the caller NAMED, which every
    route derives from one probe of exactly that file. It is discarded when the file actually read
    is a different one — the ZCode decoy swap below — because a label about the decoy says nothing
    about the rollout. It is not, and cannot be, a check that the caller classified correctly."""
    transcript = hook.get("transcript_path")
    # Set only where this function establishes the client itself, from evidence it verified.
    proven_agent = None
    claude_session_id = hook.get("session_id") or ""
    payload_session_id = claude_session_id   # kept for the pointer lookup; see resolve_build_session_id
    cwd = hook.get("cwd") or os.getcwd()

    # ZCode sets ZCODE_SESSION_ID for its hooks and nothing else does, so this both detects the
    # client and carries the fallback id. See the ZCode block for why the payload's transcript is
    # replaced rather than read, and why a miss REFUSES instead of reporting zeros: the report is
    # an upsert, so a zero row does not merely add nothing — it destroys a correct one.
    zcode_session = (os.environ.get(ZCODE_SESSION_ENV) or "").strip()
    if zcode_session and not explicit_transcript:
        # Decide WHOSE session this is before resolving anything. The variable says a ZCode hook is
        # running, but environment variables are INHERITED: a Claude Code or Codex session launched
        # from a ZCode terminal carries it too, and would otherwise abandon its own perfectly good
        # transcript to report the ZCode session's usage under its own id — wrong in both
        # directions at once, and reproduced by Codex against the first version of this branch.
        # A payload that names a session is the authority; the variable is only the fallback for
        # one that does not.
        if claude_session_id and not zcode_ids_match(claude_session_id, zcode_session):
            _log(f"session {claude_session_id} is not the ZCode session {zcode_session} that "
                 f"{ZCODE_SESSION_ENV} names — this client inherited the variable, so reading the "
                 "transcript it actually gave us.")
        else:
            # See the ZCode block for why the payload's transcript is replaced rather than read,
            # and why a miss REFUSES: the report is an upsert, so a zero row does not merely add
            # nothing, it destroys a correct one.
            wanted = claude_session_id or zcode_session
            rollout = zcode_rollout_for(wanted)
            internal = zcode_session_id(rollout) if rollout else None
            if not rollout or not zcode_ids_match(internal, wanted):
                _log(f"zcode session {wanted!r}: no rollout in {_zcode_rollout_dir()} whose own id "
                     "matches it. The hook payload's transcript is ZCode's synthetic one and "
                     "carries no usage, so reporting it would overwrite this session's row with "
                     "zeros; skipping instead.")
                return 1
            transcript = rollout
            # This branch has already PROVEN the client: it found a ZCode rollout whose own
            # internal session id matches. Letting `classify` re-derive it below would be a second
            # probe of a file ZCode is still appending to and truncating — and if that probe lost,
            # the rollout's totals would be reported as `claude-code`, which bypasses both ZCode
            # guards because they key on the label. (Found by Codex, fifth round.)
            proven_agent = AGENT_ZCODE
            # Report under the id ZCode wrote INSIDE the rollout, never the payload's spelling of
            # it: a payload may carry the bare uuid where ZCode's own is `sess_`-prefixed, and the
            # two would key different rows. Keep whichever spelling actually named the rollout as
            # the pointer alias — when the payload carried no session at all, that is the
            # environment's, and without this the pointer under it would be missed. (Found by Codex.)
            payload_session_id = payload_session_id or wanted
            claude_session_id = internal

    if not transcript or not os.path.exists(transcript):
        _log(f"no transcript ({transcript!r}); nothing to report.")
        return 1
    if not claude_session_id:
        claude_session_id = os.path.splitext(os.path.basename(transcript))[0]

    # ONE root for this whole report. It was derived independently by the pointer lookup and by
    # the high-water path, so a git call that succeeded for the first and transiently failed for
    # the second would find the pointer under the repository root while the mark fell back under
    # cwd — the existing mark missed, and a regressed report sent. (Found by Codex.)
    #
    # The PROJECT directory's root, when the client names one, and the payload's cwd only after it
    # (GitHub #1387 item 16). The payload's cwd follows the agent: a session that ends after a `cd`
    # into another repository, or out of any repository, looked for its pointer in the wrong place
    # and dropped the usage. The cwd stays as the fallback because a pointer is written wherever the
    # agent ran start-session, and the file is named for THIS client session, so a second place to
    # look cannot pick up another session's pointer. The fallback costs a git call only when the
    # project root did not have it.
    project_dir = (os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("ZCODE_PROJECT_DIR") or "").strip()
    state_root = _state_root(project_dir or cwd)
    pointer_names = list(dict.fromkeys(n for n in (claude_session_id, payload_session_id) if n))
    override = (os.environ.get("POSTMQ_BUILD_SESSION_ID") or "").strip()
    pointer_dir = None
    # Every root this report has a reason to look in for a ZCode high-water mark: the one the
    # pointer is in, and the cwd's when it differs, since a reporter before this change keyed the
    # mark on the cwd alone. Reading a mark from a root it was never written under costs nothing;
    # missing one that exists sends the regressed report it was there to stop. (Found by Codex.)
    mark_roots = [state_root]
    if override:
        build_session_id = override
    else:
        build_session_id, pointer_dir = _locate_pointer(pointer_names, _state_dirs(cwd, state_root))
        if build_session_id is None and project_dir and not _same_directory(project_dir, cwd):
            cwd_root = _state_root(cwd)
            build_session_id, pointer_dir = _locate_pointer(pointer_names, _state_dirs(cwd, cwd_root))
            if build_session_id is not None:
                # The pointer lives under the cwd's root, so that is this report's root from here on.
                state_root = cwd_root
            mark_roots = list(dict.fromkeys([state_root, cwd_root]))
    # ONE read of the store and ONE classification, used to build the payload AND to judge it.
    # Two reads of a file that is still being written are two different snapshots, and two
    # classifications of it can disagree.
    # Provenance, in this order: what THIS function proved, then what the caller says about the
    # file the caller was talking about, then a fresh probe. `proven_agent` is a separate variable
    # rather than an overwrite of `agent`, because letting a ZCode label skip the path check made
    # the parameter itself unsafe — any caller could then label an unrelated transcript ZCode and
    # be believed. No CLI route did, and a contract that only holds because nobody uses it wrongly
    # is not one. (Found by Codex, sixth round.)
    if proven_agent:
        reading_agent = proven_agent
    elif agent and transcript == hook.get("transcript_path"):
        reading_agent = agent
    else:
        reading_agent = classify(transcript)
    # Build-session BOUNDARIES (GitHub #1387 item 7). Without them a second build session opened in
    # the same conversation took the whole transcript. Only a Claude Code transcript can be split:
    # it records each turn's own time and its counts are per turn. Codex counts cumulatively and
    # ZCode and Antigravity are read through other shapes, so for them the whole transcript still
    # goes to the pointer's build session — and the log says so rather than splitting by a guess.
    # An explicit build session (POSTMQ_BUILD_SESSION_ID, --backfill, --build-session) is an
    # operator naming ONE attribution, so it is never split.
    boundaries = [] if override else read_boundaries(pointer_dir, pointer_names)
    if boundaries and build_session_id and boundaries[-1][1] != build_session_id:
        # The log and the pointer disagree about who is current: something wrote the pointer
        # without logging, which is what a skill that predates the log does. The pointer is right
        # about WHO, and its own modification time says WHEN. Attributing the whole transcript to
        # it instead would count every turn already reported to the logged build sessions a second
        # time — rows A=4, B=2 and then C=6 for a six-turn transcript. (Found by Codex.)
        boundaries = _reconcile_pointer(boundaries, build_session_id, pointer_dir, pointer_names)
    if len(dict.fromkeys(bsid for _t, bsid in boundaries)) < 2:
        boundaries = []
    elif reading_agent != AGENT:
        _log(f"session {claude_session_id} opened {len(dict.fromkeys(b for _t, b in boundaries))} build "
             f"sessions, but a {reading_agent} transcript cannot be split by turn; attributing all of it "
             f"to {build_session_id}.")
        boundaries = []

    if boundaries:
        split = aggregate_by_build_session(transcript, boundaries)
        totals = list(split.values())
        reports = [build_arguments(transcript, claude_session_id, bsid, tot, reading_agent)
                   for bsid, tot in split.items()]
        arguments = next(r for r in reports if r["build_session_id"] == build_session_id)
    else:
        reading = aggregate(transcript, reading_agent)
        totals = [reading]
        arguments = build_arguments(transcript, claude_session_id, build_session_id,
                                    reading, reading_agent)
        reports = [arguments]

    # A ZCode `Stop` hook fires AFTER a turn, so a report with no turns means the rollout was
    # empty, truncated to nothing, or not the one this session is writing. Sending it would put
    # zeros over a correct row — and, because an empty file answers no transcript probe, it would
    # do so labelled as the wrong client. (Found by Codex.)
    if arguments["agent"] == AGENT_ZCODE and arguments["turns"] == 0:
        _log(f"zcode session {claude_session_id}: the rollout at {transcript} carries no usable "
             "usage record, and a Stop hook fires after a turn — refusing to report zeros over "
             "whatever this session has already recorded.")
        return 1

    if dry_run:
        out = _target(rest)
        # The FILE this preview was read from. Discovery runs again on the real send, and between
        # the two the winner can change — so a preview that does not name its source cannot be
        # turned into a send of the same thing. Pass this path back on the send. (Found by Codex,
        # twelfth round.)
        out["transcript"] = transcript
        out["arguments"] = arguments
        if len(reports) > 1:
            # Everything a send would make, one report per build session, in the order they opened.
            out["reports"] = reports
        print(json.dumps(out, indent=2))
        return 0

    # The Antigravity refusal, and note it sits AFTER the dry-run print where ZCode's sits before.
    # That is deliberate: `--dry-run` is the documented way for the start-session skill to LEARN
    # this conversation's id, and refusing before printing would leave a conversation whose first
    # request is not yet committed with no id to name its pointer file — the guard defeating the
    # recovery it shares a client with. Nothing is sent in dry-run mode, so a send guard has no
    # business running there. (Found by Codex, second round.)
    #
    # Two conditions, because zero is only the loudest failure. Antigravity's store is SQLite, so a
    # transient open or query failure yields a COMPLETE, VALID aggregate rather than an error —
    # all-zero if it failed at the start, plausibly short if it failed partway. The upsert replaces
    # every count, so the first erases a correct row and the second silently shrinks it.
    if arguments["agent"] == AGENT_ANTIGRAVITY:
        if arguments["turns"] == 0:
            _log(f"antigravity conversation {claude_session_id}: {transcript} yielded no usable "
                 "usage record — refusing to report zeros over whatever this session has already "
                 "recorded.")
            return 1
        if read_was_partial(totals[0]):
            _log(f"antigravity conversation {claude_session_id}: {transcript} could only be read "
                 "in part, so these totals are short of the truth — refusing to report them over "
                 "whatever this session has already recorded.")
            return 1

    # The same two refusals for the clients that had neither (GitHub #1387 item 10). A Claude Code
    # or Codex transcript that could not be read, in whole or in part, used to be sent anyway: zero
    # over a correct row, or a total quietly short of it. "Nothing at all" is no turns AND no
    # tokens, not merely no turns, because a Codex session interrupted before any task completed
    # can still have spent tokens worth recording.
    if arguments["agent"] in (AGENT, AGENT_CODEX):
        if any(read_was_partial(t) for t in totals):
            _log(f"session {claude_session_id}: {transcript} (or one of its sub-agent transcripts) "
                 "could not be read in full, so these totals are short of the truth — refusing to "
                 "report them over whatever this session has already recorded.")
            return 1
        empty = [r for r in reports if _carries_nothing(r)]
        if len(empty) == len(reports):
            _log(f"session {claude_session_id}: {transcript} carries no usage record — refusing to "
                 "report zeros over whatever this session has already recorded.")
            return 1
        for r in empty:
            _log(f"session {claude_session_id}: build session {r['build_session_id']} had no turns of "
                 "its own in this transcript; nothing is sent for it.")
        reports = [r for r in reports if r not in empty]

    # The high-water guard sits AFTER the dry-run print, so `--dry-run` always shows what the
    # report would say, and before the send, which is the only thing it stops. ZCode only: see the
    # scope note on HIGH_WATER_PREFIX.
    if not build_session_id:
        # Name the directories actually searched, derived rather than spelled: this message used to
        # name `.claude/state/` alone, which sent a Codex or ZCode user to look somewhere the
        # pointer was never going to be.
        looked = ", ".join(
            f"{'/'.join(parts)}/{STATE_PREFIX}{name}"
            for name in dict.fromkeys(n for n in (claude_session_id, payload_session_id) if n)
            for parts in STATE_DIRS)
        _log(f"no active build session for client session {claude_session_id} "
             f"(looked for {looked}; and POSTMQ_BUILD_SESSION_ID is unset) — not attributed; skipping.")
        return 1

    # ZCode only — see the scope note on HIGH_WATER_PREFIX.
    if arguments["agent"] != AGENT_ZCODE:
        if len(reports) == 1:
            send(reports[0], rest=rest)
            return 0
        # One report per build session. A failure on one does not stop the others: each is its own
        # row, and an earlier session's figures are no less true because a later send failed.
        failed = 0
        for r in reports:
            try:
                send(r, rest=rest)
            except ReportError as e:
                _log(f"{e} (non-fatal; build session {r['build_session_id']})")
                failed += 1
        return 1 if failed else 0

    # Resolved once and reused, so the read and the write cannot target different files. The mark
    # itself always lives in `.zcode/state`; see _high_water_path.
    mark = _high_water_path(build_session_id, claude_session_id, cwd, state_root)
    if project_dir and len(mark_roots) == 1 and not _same_directory(project_dir, cwd):
        mark_roots = list(dict.fromkeys([state_root, _state_root(cwd)]))
    # The mark this report WRITES is resolved once, above; any other root is only READ, field-wise
    # maximum, so the read and the write still target the same file.
    recorded = read_high_water(mark)
    for root in mark_roots:
        if root == state_root:
            continue
        for k, v in read_high_water(_high_water_path(build_session_id, claude_session_id, cwd, root)).items():
            recorded[k] = max(recorded.get(k, 0), v)
    counts = reported_counts(arguments)
    regressed = regressed_fields(counts, recorded)
    if regressed:
        _log(f"session {claude_session_id}: this report would REDUCE "
             f"{', '.join(regressed)} against what is already recorded, which is what a rollout "
             "ZCode truncated mid-session looks like — so the recorded figures are kept and this "
             "report is dropped.")
        return 1

    # Only raise the LOCAL mark when the server says it took the write. The mark means "the row
    # holds at least this", so recording it after a report the server discarded would leave the
    # local guard refusing to re-send figures that never landed.
    if send(arguments, rest=rest):
        write_high_water(mark, counts)
    return 0


def _carries_nothing(arguments: dict) -> bool:
    return arguments["turns"] == 0 and not any(
        arguments[k] for k in ("input_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens",
                               "cache_read_tokens", "output_tokens"))


def _same_directory(one: str, other: str) -> bool:
    try:
        return os.path.realpath(one) == os.path.realpath(other)
    except (OSError, ValueError):
        return False


# ── backfill (manual: record a past / closed session) ─────────────────────
def _positionals(argv: list, valued: set) -> list:
    """The arguments that are neither flags nor the VALUE of one.

    Shared, because the alternative was two scans that disagreed: the Antigravity branch had its
    own, did not know `--build-session` takes a value, and so read the build-session id as a
    database path. (Found by Codex, eighth round.)"""
    return [a for i, a in enumerate(argv[1:], start=1)
            if not a.startswith("--") and argv[i - 1] not in valued]


def _arg_after(argv: list, flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def session_id_and_agent_for(transcript_path: str):
    """`(session id, agent)` for a transcript named by hand — ONE probe, both answers.

    Every entry point needs the id, and `run` needs the classification. Working them out
    separately means probing a live file twice, and the two probes can disagree: a rollout that
    truncates in between yields one client's totals under another's label, which bypasses the
    guards that key on the label. (Found by Codex, fifth round.)"""
    agent = classify(transcript_path)
    if agent == AGENT_ANTIGRAVITY:
        found = antigravity_session_id(transcript_path)
    elif agent == AGENT_ZCODE:
        found = zcode_session_id(transcript_path)
    elif agent == AGENT_CODEX:
        found = codex_session_id(transcript_path)
    else:
        found = None
    return found or os.path.splitext(os.path.basename(transcript_path))[0], agent


def session_id_for(transcript_path: str) -> str:
    """The session id the LIVE hook would key a report on, for a transcript named by hand.

    For Claude Code the transcript file is named for its session, so the filename stem IS the id.
    A Codex or ZCode rollout is named differently — `rollout-<timestamp>-<id>` and
    `model-io-<id>` — so the stem is a DIFFERENT string, and keying on it would write a second row
    beside the live one instead of overwriting it. Both carry their own id inside, so read it.

    An Antigravity conversation database is NAMED for its conversation id, but the id is also a
    column, and reading the column is what makes that a fact rather than a convention that could
    change under us.

    Kept as the id-only spelling for callers that do not need the classification; both go through
    `session_id_and_agent_for`, so the two cannot answer differently."""
    return session_id_and_agent_for(transcript_path)[0]


def run_backfill(argv: list) -> int:
    """`--backfill <transcript> --build-session <id> [--dry-run] [--rest]`.
    Unlike the hook path this returns non-zero on bad input — it is a deliberate
    operator command, not the never-block-session-end hook."""
    transcript = _arg_after(argv, "--backfill")
    build_session_id = _arg_after(argv, "--build-session")
    if not transcript:
        _log("usage: --backfill <transcript.jsonl> --build-session <id> [--dry-run] [--rest]")
        return 2
    if not os.path.exists(transcript):
        _log(f"transcript not found: {transcript}")
        return 2
    if not build_session_id:
        _log("--backfill requires --build-session <build-session-id>")
        return 2
    claude_session_id, transcript_agent = session_id_and_agent_for(transcript)
    os.environ["POSTMQ_BUILD_SESSION_ID"] = build_session_id
    return run({"transcript_path": transcript, "session_id": claude_session_id, "cwd": os.getcwd()},
               dry_run="--dry-run" in argv, rest="--rest" in argv, explicit_transcript=True,
               agent=transcript_agent)


# ── entrypoint ────────────────────────────────────────────────────────────
# ── argv validation ───────────────────────────────────────────────────────
# Every flag this CLI understands. BOOLEAN take no value; VALUED consume the next argument.
_BOOLEAN_FLAGS = frozenset({"--dry-run", "--rest", "--strict", "--antigravity", "--delete-payload"})
_VALUED_FLAGS = frozenset({"--backfill", "--build-session", "--payload-file"})
_KNOWN_FLAGS = _BOOLEAN_FLAGS | _VALUED_FLAGS

# `--dry-run [transcript]` and `--antigravity [database]` each take one optional positional, and
# never both at once — so one is the ceiling.
_MAX_POSITIONALS = 1


def validate_argv(argv: list) -> str | None:
    """The reason `argv` is unusable, or None.

    **Why this exists at all: a typo in `--dry-run` used to be a SEND.** Parsing was membership
    tests (`"--dry-run" in argv`) with no notion of a flag set, so `--dry-rn` simply did not match
    and the run proceeded as a real report — somebody previewing what would be recorded got it
    recorded instead. On a client with no hook that report is the only one the session will ever
    have, so it is not even recoverable by re-running (backlog `01a066ca-fd73`, found in Codex
    review of #987).

    Three failures, all of which used to pass silently:

    * an unknown flag — the typo case above;
    * a valued flag whose value is the next FLAG, which `_arg_after` would happily return, so
      `--build-session --dry-run` read the id as "--dry-run";
    * a surplus positional, which meant a mistyped flag landing as a transcript path.

    Repeats are rejected too: `--build-session A --build-session B` silently took A, and which one
    a reader expects is genuinely ambiguous — better to refuse than to pick.

    And a positional is accepted only by a mode that READS one: `--dry-run [transcript]` and
    `--antigravity [database]`. Anywhere else it was silently ignored, so `postmq-usage-reporter.py
    dry-run`, with the dashes forgotten and hook JSON on stdin, passed this function and SENT —
    the typo-becomes-a-report failure above, reached by a missing prefix rather than a misspelling
    (GitHub #1387 item 17). `--backfill` names its transcript as its value, and a payload file is
    the hook path, so neither takes one either.
    """
    seen = set()
    positionals = 0
    first_positional = None
    i = 1
    while i < len(argv):
        token = argv[i]
        if token.startswith("-"):
            if token not in _KNOWN_FLAGS:
                return f"unknown option {token}"
            if token in seen:
                return f"{token} given more than once"
            seen.add(token)
            if token in _VALUED_FLAGS:
                if i + 1 >= len(argv):
                    return f"{token} needs a value"
                if argv[i + 1].startswith("-"):
                    return f"{token} needs a value, not the next option ({argv[i + 1]})"
                i += 2
                continue
        else:
            positionals += 1
            if positionals > _MAX_POSITIONALS:
                return f"unexpected extra argument {token}"
            first_positional = token
        i += 1
    if positionals:
        reads_one = "--antigravity" in seen or (
            "--dry-run" in seen and "--backfill" not in seen and "--payload-file" not in seen)
        if not reads_one:
            return (f"unexpected argument {first_positional} — only --dry-run [transcript] and "
                    "--antigravity [database] take one; options need their leading dashes")
    return None


def _deadline_seconds() -> float:
    raw = (os.environ.get("POSTMQ_REPORT_DEADLINE_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else REPORT_DEADLINE_SECONDS
    except ValueError:
        value = REPORT_DEADLINE_SECONDS
    # `inf`, `nan` and `1e100` all parse, and the first two and the last make the thread join raise
    # OverflowError outside every handler, which exits non-zero without --strict. (Found by Codex.)
    if not math.isfinite(value) or value <= 0:
        return REPORT_DEADLINE_SECONDS
    return min(value, threading.TIMEOUT_MAX)


def main(argv: list) -> int:
    """Run the report under ONE wall-clock deadline (GitHub #1387 item 9).

    The work runs on a daemon thread the caller waits for with the deadline as its limit, because
    nothing else bounds it end to end: urllib's timeout applies to each socket operation, so an
    endpoint that answers a byte at a time holds a request open indefinitely, and the transcript read
    is unbounded too. Each request's own timeout is also cut to what is left (`_request_timeout`),
    so the common case ends with a clean error rather than an abandoned thread. A run that overruns
    is reported as one line and the process exits, which ends the thread with it; a request already
    on the wire may still land, and the report is an idempotent upsert, so that is harmless."""
    strict = "--strict" in argv
    budget = _deadline_seconds()
    deadline = time.monotonic() + budget
    outcome = {}

    def work():
        _RUN.deadline = deadline
        try:
            outcome["code"] = _main(argv, strict)
        except BaseException as e:  # noqa: BLE001 — surfaced on the calling thread below
            outcome["error"] = e

    worker = threading.Thread(target=work, name="postmq-usage-report", daemon=True)
    worker.start()
    worker.join(budget)
    if worker.is_alive():
        _log(f"gave up after {budget:g}s without finishing (POSTMQ_REPORT_DEADLINE_SECONDS); "
             "nothing more will be sent. A request already sent may still land, harmlessly: "
             "the report is an idempotent upsert.")
        return 1 if strict else 0
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise error
        _log(f"unexpected error (non-fatal): {type(error).__name__}: {error}")
        return 1 if strict else 0
    return outcome.get("code", 1 if strict else 0)


def _main(argv: list, strict: bool) -> int:
    """The run itself; main() bounds it."""
    # BEFORE anything else, and before any send. A malformed command line is refused rather than
    # interpreted — see validate_argv for why the previous behaviour turned a typo into a report.
    # Fail-OPEN is preserved: this is a session-end hook and must not block session end, so a bad
    # argv logs one line and exits 0 unless --strict. Nothing is sent either way, which is the
    # property that matters.
    problem = validate_argv(argv)
    if problem is not None:
        _log(f"{problem}. Nothing was reported. Run with no arguments for usage.")
        return 1 if strict else 0

    rest = "--rest" in argv
    try:
        if "--backfill" in argv:
            code = run_backfill(argv)
            return code if code == 2 else (code if strict else 0)

        dry_run = "--dry-run" in argv
        # Flags that CONSUME the next argument. One list, used by every positional scan below —
        # the Antigravity branch had its own scan that did not know about it, so
        # `--antigravity --build-session <id>` read the id as a database path and refused with
        # "no Antigravity conversation database at 01J0…". (Found by Codex, eighth round.)
        _valued = {"--payload-file", "--build-session"}
        # `--antigravity` exists because that client fires no hook, so there is no payload naming a
        # transcript and no session id in the environment. It resolves the conversation opened
        # against THIS directory — never the newest overall; see antigravity_latest_conversation.
        if "--antigravity" in argv and not _arg_after(argv, "--payload-file"):
            cwd = os.getcwd()
            # An explicit database path WINS over discovery. The end-session skill tells anyone
            # discovery fails for to pass one, so a flag that ignored it would make the documented
            # recovery a silent no-op — the failure this module is written against, in the very
            # instruction written to avoid it. (Found by Codex.)
            named = _positionals(argv, _valued)
            if named:
                found, why = named[0], None
            else:
                found, why = antigravity_latest_conversation(cwd)
            if found is None:
                _log(f"no Antigravity conversation resolved: {why}. Pass the database path as an "
                     "argument — ~/.gemini/antigravity/conversations/<id>.db for the IDE, "
                     "~/.gemini/antigravity-cli/conversations/<id>.db for the CLI — rather than "
                     "reporting a conversation that may not be this one.")
                return 1 if strict else 0
            if not os.path.exists(found):
                _log(f"no Antigravity conversation database at {found}.")
                return 1 if strict else 0
            # Existence is not enough. A file that is not a conversation database fails `classify`,
            # falls through as the DEFAULT client, and is then reported as a claude-code session of
            # zero turns — past the Antigravity guard entirely, because the guard keys on the agent.
            # `--antigravity /dev/null` reached a mocked send exactly that way. (Found by Codex.)
            if not _is_antigravity_transcript(found):
                _log(f"{found} is not an Antigravity conversation database — refusing to report it "
                     "rather than reporting it as some other client's session.")
                return 1 if strict else 0
            session_id, found_agent = session_id_and_agent_for(found)
            # `--build-session <id>` is accepted here for the same reason `--backfill` takes it:
            # the pointer is written by /start-session, and on a client with no hooks the operator
            # may be reporting a session whose pointer was never written — in which case the
            # documented recovery ("pass the database path") would read perfectly good totals and
            # then refuse for want of an id. (Found by Codex, seventh round.)
            named_build_session = _arg_after(argv, "--build-session")
            if named_build_session:
                os.environ["POSTMQ_BUILD_SESSION_ID"] = named_build_session
            hook = {"transcript_path": found,
                    "session_id": session_id,
                    "cwd": cwd}
            code = run(hook, dry_run=dry_run, rest=rest, explicit_transcript=True,
                       agent=found_agent)
            return code if strict else 0
        positional = _positionals(argv, _valued)
        explicit_transcript = bool(dry_run and positional)
        if explicit_transcript:
            transcript = positional[0]
            # One probe for the id AND the classification; see session_id_and_agent_for.
            if os.path.exists(transcript):
                named_session_id, named_agent = session_id_and_agent_for(transcript)
            else:
                named_session_id = os.path.splitext(os.path.basename(transcript))[0]
                named_agent = None
            hook = {"transcript_path": transcript,
                    "session_id": named_session_id,
                    "cwd": os.getcwd()}
        else:
            # --payload-file is how the Codex SessionEnd wrapper hands the payload over: the
            # wrapper must return inside Codex's 1-second hook timeout, so the reporter runs
            # DETACHED and cannot inherit a stdin that closed when the wrapper exited.
            payload_file = _arg_after(argv, "--payload-file")
            if payload_file:
                try:
                    with open(payload_file, encoding="utf-8") as fh:
                        raw = fh.read()
                except OSError as e:
                    _log(f"could not read --payload-file {payload_file}: {e}")
                    raw = ""
                finally:
                    if "--delete-payload" in argv:
                        try:
                            os.unlink(payload_file)
                        except OSError:
                            pass
            else:
                raw = "" if sys.stdin.isatty() else sys.stdin.read()
            hook = {}
            if raw.strip():
                try:
                    hook = json.loads(raw)
                except ValueError:
                    _log("stdin was not valid JSON; nothing to report.")
                    return 1 if strict else 0
        code = run(hook, dry_run=dry_run, rest=rest, explicit_transcript=explicit_transcript,
                   agent=named_agent if explicit_transcript else None)
    except ReportError as e:
        _log(f"{e} (non-fatal)")
        code = 1
    except Exception as e:  # noqa: BLE001 — a hook must never block session end
        _log(f"unexpected error (non-fatal): {type(e).__name__}: {e}")
        code = 1
    return code if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
