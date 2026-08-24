#!/usr/bin/env python3
"""
postmq-usage-reporter.py — PostMQ build-session token-usage reporter (SITE-PLAN WP P.5).

A Claude Code `SessionEnd` hook. When a session ends it parses the just-finished
transcript (and its out-of-process sub-agent transcripts), sums the authoritative
token usage — the same numbers the billing system sees — and records it against
the session's PostMQ build session by calling the `record_build_session_usage`
MCP tool over Streamable HTTP (or, with --rest, `POST /v1/usage`). Tokens only;
no dollars — PostMQ records tokens, and per-model price tables are not something
the product owns.

WIRING (one line + one settings entry)
    cp tools/hooks/postmq-usage-reporter/postmq-usage-reporter.py .claude/hooks/
    then add to `.claude/settings.json` (see hooks.json next to this file):
    "hooks": { "SessionEnd": [ { "hooks": [ { "type": "command",
        "command": "python3 \\"$CLAUDE_PROJECT_DIR/.claude/hooks/postmq-usage-reporter.py\\"" } ] } ] }
    On Windows swap `python3` for `py -3` (or `python`); see README.md.

HOW IT FINDS THE BUILD SESSION
    `/start-session` writes the minted build-session id to
    `<repo>/.claude/state/active-build-session-<claude_session_id>` (keyed per
    Claude session so parallel worktree sessions never collide; anchored at the
    git common dir so it is shared from any worktree). This hook reads it back
    by the `session_id` the SessionEnd payload hands it. Override with
    POSTMQ_BUILD_SESSION_ID (testing / non-git checkouts).

ENV
    POSTMQ_CREDENTIAL        required to send — a PostMQ credential with the
                             write_session_state scope (`pmq_…`; the
                             form=mcp_config issue flow hands you one)
    POSTMQ_MCP_URL           MCP Streamable-HTTP endpoint; default https://mcp.postmq.com/
    POSTMQ_API_URL           REST base (e.g. https://api.postmq.com); used only with --rest
    POSTMQ_BUILD_SESSION_ID  override the state-file lookup

MODES
    (stdin = hook JSON)      normal operation
    --dry-run [transcript]   print the tool arguments that WOULD be sent, no network
    --backfill <transcript> --build-session <id> [--dry-run] [--rest]
                             record a PAST session's usage against a specific
                             build session. The claude_session_id is the transcript
                             filename, so the write lands on the same
                             (build_session, claude_session) row the live hook
                             would use — idempotent: a re-run, or a later real
                             SessionEnd for that session, overwrites, never doubles.
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

import glob
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPORTER_VERSION = "1.0.0"
AGENT = "claude-code"
TOOL_NAME = "record_build_session_usage"
DEFAULT_MCP_URL = "https://mcp.postmq.com/"
REST_ROUTE = "/v1/usage"
STATE_PREFIX = "active-build-session-"
LOG_PREFIX = "[postmq-usage-reporter]"
MCP_PROTOCOL_VERSION = "2025-06-18"

# The exact argument names of the `record_build_session_usage` MCP tool
# (src/PostMQ.Mcp/Tools/UsageTools.cs) — which are also the exact JSON body
# field names of `POST /v1/usage` (src/PostMQ.Api/SessionState/UsageWriteEndpoints.cs).
# ONE payload serves both transports; build_arguments() asserts it emits exactly
# these keys, and the Tooling guard asserts these are a subset of the tool's.
ARGUMENT_KEYS = (
    "build_session_id",
    "claude_session_id",
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
    print(f"{LOG_PREFIX} {msg}", file=sys.stderr)


# ── transcript aggregation (pure; unit-testable) ──────────────────────────
def _collect_file(path: str, snapshots: dict, *, is_subagent: bool) -> None:
    """Fold one transcript file's assistant-usage lines into `snapshots`, keyed
    by message id, LAST line wins.

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
    except OSError as e:
        _log(f"could not read {path}: {e}")


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
        cc = u.get("cache_creation") or {}
        tot["cache_write_5m_tokens"] += int(cc.get("ephemeral_5m_input_tokens", 0) or 0)
        tot["cache_write_1h_tokens"] += int(cc.get("ephemeral_1h_input_tokens", 0) or 0)
        if snap["model"]:
            tot["_models"].add(snap["model"])
        ts = snap["timestamp"]
        if ts:
            if tot["window_start"] is None or ts < tot["window_start"]:
                tot["window_start"] = ts
            if tot["window_end"] is None or ts > tot["window_end"]:
                tot["window_end"] = ts


def _subagent_files(transcript_path: str) -> list:
    """Out-of-process sub-agent transcripts live at
    `<dir>/<session-id>/subagents/agent-*.jsonl` — the parent session is the
    directory name, so association needs no field-scraping."""
    d = os.path.dirname(transcript_path)
    sid = os.path.basename(transcript_path)
    if sid.endswith(".jsonl"):
        sid = sid[: -len(".jsonl")]
    return sorted(glob.glob(os.path.join(d, sid, "subagents", "*.jsonl")))


def aggregate(transcript_path: str) -> dict:
    """Sum the main transcript + its sub-agent transcripts into a usage dict.
    Cache-read dominates by ~1000x, so the four billable input classes are
    kept separate — exactly the five classes PostMQ stores."""
    tot = {
        "turns": 0, "sidechain_turns": 0,
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0,
        "window_start": None, "window_end": None, "_models": set(),
    }
    snapshots: dict = {}
    _collect_file(transcript_path, snapshots, is_subagent=False)
    for sub in _subagent_files(transcript_path):
        _collect_file(sub, snapshots, is_subagent=True)
    _fold(snapshots, tot)
    return tot


def build_arguments(transcript_path: str, claude_session_id: str, build_session_id: str | None) -> dict:
    """The ONE payload: the `record_build_session_usage` tool arguments, which
    are byte-for-byte the `POST /v1/usage` body. Keys are exactly ARGUMENT_KEYS."""
    tot = aggregate(transcript_path)
    args = {
        "build_session_id": build_session_id,
        "claude_session_id": claude_session_id,
        "input_tokens": tot["input_tokens"],
        "cache_write_5m_tokens": tot["cache_write_5m_tokens"],
        "cache_write_1h_tokens": tot["cache_write_1h_tokens"],
        "cache_read_tokens": tot["cache_read_tokens"],
        "output_tokens": tot["output_tokens"],
        "turns": tot["turns"],
        "sidechain_turns": tot["sidechain_turns"],
        "models": sorted(tot["_models"]),
        "agent": AGENT,
        "reporter_version": REPORTER_VERSION,
        "window_start": tot["window_start"],
        "window_end": tot["window_end"],
    }
    if tuple(args) != ARGUMENT_KEYS:
        raise ReportError("internal: payload keys drifted from ARGUMENT_KEYS")
    return args


# ── build-session id resolution ───────────────────────────────────────────
def _state_dir(cwd: str) -> str:
    """The shared .claude/state dir (git common dir's parent), so the marker
    written from any worktree is found from any worktree. Falls back to
    <cwd>/.claude/state."""
    try:
        common = subprocess.run(
            ["git", "-C", cwd or ".", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if common.returncode == 0 and common.stdout.strip():
            return os.path.join(os.path.dirname(common.stdout.strip()), ".claude", "state")
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.join(cwd or ".", ".claude", "state")


def resolve_build_session_id(claude_session_id: str, cwd: str) -> str | None:
    env = os.environ.get("POSTMQ_BUILD_SESSION_ID")
    if env:
        return env.strip()
    if not claude_session_id:
        return None
    path = os.path.join(_state_dir(cwd), STATE_PREFIX + claude_session_id)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


# ── transports ────────────────────────────────────────────────────────────
def _http(url: str, body: dict, headers: dict, timeout: int = 30):
    """POST JSON; return (status, response_headers, body_text). Raises ReportError
    on any transport failure or non-2xx, with a message that never carries a
    header (so never the credential)."""
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read()[:200].decode("utf-8", "replace")
        except (OSError, ValueError):
            detail = ""
        finally:
            e.close()
        raise ReportError(f"POST {url} -> HTTP {e.code} {detail!r}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ReportError(f"POST {url} failed: {e}") from None


def _parse_rpc(text: str) -> dict:
    """Accept a bare JSON-RPC response or an SSE frame carrying one."""
    text = text.strip()
    if text.startswith("event:") or text.startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[len("data:"):].strip()
                break
    try:
        parsed = json.loads(text)
    except ValueError as e:
        raise ReportError(f"unparseable JSON-RPC response: {e}") from None
    if "error" in parsed:
        err = parsed["error"] or {}
        raise ReportError(f"JSON-RPC error {err.get('code')}: {err.get('message', err)}")
    return parsed.get("result") or {}


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
        raise ReportError(f"tool reported isError: {json.dumps(payload)[:200]}")
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
        return h

    def _next(self) -> int:
        self._id += 1
        return self._id

    def call_tool(self, name: str, arguments: dict) -> dict:
        _status, headers, text = _http(self._url, {
            "jsonrpc": "2.0", "id": self._next(), "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "postmq-usage-reporter", "version": REPORTER_VERSION}},
        }, self._headers())
        sid = {k.lower(): v for k, v in headers.items()}.get("mcp-session-id")
        if sid:
            self._session = sid
        _parse_rpc(text)  # a JSON-RPC error on initialize is a failure
        _http(self._url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, self._headers())
        _status, _headers, text = _http(self._url, {
            "jsonrpc": "2.0", "id": self._next(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, self._headers())
        return _tool_payload(_parse_rpc(text))


def send_mcp(arguments: dict) -> dict:
    credential = os.environ.get("POSTMQ_CREDENTIAL")
    if not credential:
        raise ReportError("POSTMQ_CREDENTIAL not set — cannot record usage (needs the write_session_state scope).")
    url = os.environ.get("POSTMQ_MCP_URL") or DEFAULT_MCP_URL
    return McpClient(url, credential).call_tool(TOOL_NAME, arguments)


def send_rest(arguments: dict) -> dict:
    credential = os.environ.get("POSTMQ_CREDENTIAL")
    if not credential:
        raise ReportError("POSTMQ_CREDENTIAL not set — cannot record usage (needs the write_session_state scope).")
    base = os.environ.get("POSTMQ_API_URL")
    if not base:
        raise ReportError("--rest needs POSTMQ_API_URL (e.g. https://api.postmq.com).")
    url = base.rstrip("/") + REST_ROUTE
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


def send(arguments: dict, *, rest: bool) -> None:
    """Send via the chosen transport and log the outcome. Raises ReportError."""
    result = send_rest(arguments) if rest else send_mcp(arguments)
    created = result.get("created") if isinstance(result, dict) else None
    verb = "recorded" if created else ("overwrote" if created is False else "sent")
    _log(f"{verb} usage for build session {arguments['build_session_id']} "
         f"(transcript {arguments['claude_session_id']}: {arguments['cache_read_tokens']} cache-read, "
         f"{arguments['output_tokens']} output, {arguments['turns']} turns) via {'rest' if rest else 'mcp'}")


# ── the hook path ─────────────────────────────────────────────────────────
def run(hook: dict, *, dry_run: bool = False, rest: bool = False) -> int:
    """Return 0 on success, 1 on any failure (main() maps that through --strict)."""
    transcript = hook.get("transcript_path")
    claude_session_id = hook.get("session_id") or ""
    cwd = hook.get("cwd") or os.getcwd()

    if not transcript or not os.path.exists(transcript):
        _log(f"no transcript ({transcript!r}); nothing to report.")
        return 1
    if not claude_session_id:
        claude_session_id = os.path.splitext(os.path.basename(transcript))[0]

    build_session_id = resolve_build_session_id(claude_session_id, cwd)
    arguments = build_arguments(transcript, claude_session_id, build_session_id)

    if dry_run:
        out = _target(rest)
        out["arguments"] = arguments
        print(json.dumps(out, indent=2))
        return 0

    if not build_session_id:
        _log(f"no active build session for claude session {claude_session_id} "
             f"(no .claude/state/{STATE_PREFIX}<id> file and no POSTMQ_BUILD_SESSION_ID) — not attributed; skipping.")
        return 1

    send(arguments, rest=rest)
    return 0


# ── backfill (manual: record a past / closed session) ─────────────────────
def _arg_after(argv: list, flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


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
    claude_session_id = os.path.splitext(os.path.basename(transcript))[0]
    os.environ["POSTMQ_BUILD_SESSION_ID"] = build_session_id
    return run({"transcript_path": transcript, "session_id": claude_session_id, "cwd": os.getcwd()},
               dry_run="--dry-run" in argv, rest="--rest" in argv)


# ── entrypoint ────────────────────────────────────────────────────────────
def main(argv: list) -> int:
    strict = "--strict" in argv
    rest = "--rest" in argv
    try:
        if "--backfill" in argv:
            code = run_backfill(argv)
            return code if code == 2 else (code if strict else 0)

        dry_run = "--dry-run" in argv
        positional = [a for a in argv[1:] if not a.startswith("--")]
        if dry_run and positional:
            transcript = positional[0]
            hook = {"transcript_path": transcript,
                    "session_id": os.path.splitext(os.path.basename(transcript))[0],
                    "cwd": os.getcwd()}
        else:
            raw = "" if sys.stdin.isatty() else sys.stdin.read()
            hook = {}
            if raw.strip():
                try:
                    hook = json.loads(raw)
                except ValueError:
                    _log("stdin was not valid JSON; nothing to report.")
                    return 1 if strict else 0
        code = run(hook, dry_run=dry_run, rest=rest)
    except ReportError as e:
        _log(f"{e} (non-fatal)")
        code = 1
    except Exception as e:  # noqa: BLE001 — a hook must never block session end
        _log(f"unexpected error (non-fatal): {type(e).__name__}: {e}")
        code = 1
    return code if strict else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
