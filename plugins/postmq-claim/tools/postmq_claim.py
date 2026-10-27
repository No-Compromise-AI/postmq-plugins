"""
postmq_claim — the PostMQ adapter SDK: hold a resource claim around one job, and speak for a resource
(resource coordination §8.3–§8.4, issues #1397 and #1398).

A shared resource — a GPU host, a laptop's unit-test gate, a licence seat — is registered with PostMQ by an AI account
that can speak for it, its ADAPTER. This module is everything an adapter needs except the part only the host can know:
it claims the resource, waits for its turn if someone else holds it, runs the §8.3 launch sequence under a host-local
gate and journal, keeps the claim alive while the job runs, releases it with a receipt, reconciles after a crash, and
registers the resource and reports its health. What the job runs IN, and what the host can honestly say about it
afterwards, is a PROVIDER's — an object this module drives and never sees inside.

    postmq-claim.py run --resource <resource_id> [--ttl S] [--wait S] [--provider F:fn] -- <command> [args…]
    postmq-claim.py reconcile --resource <resource_id> [--claim <claim_id>] [--provider F:fn]
    postmq-claim.py serve --kind K --display-name N --provider F:fn [--interval S]
    postmq-claim.py status --resource <resource_id>

`postmq-claim.py` is the command line; this file is the library it imports, installed beside it. A provider is any
object with the nine methods below, built by a no-argument factory named on the command line as
`--provider path/to/file.py:factory` — or imported and passed to `run()`, `reconcile_resource()` or `serve()`. With no
provider the GENERIC one is used: a POSIX process group, which is the reference implementation of the protocol.

THE PROVIDER PROTOCOL (the interface fixed with the GB10 adapter on MovieStudio #125; D-RC-12, D-RC-13, D-RC-14).
The SDK owns the gate, the journal, the claim, the wait, the renewals, the admission sequence, the release pipe and
EVERY call to PostMQ; a provider never calls PostMQ.
    declare()                                  → {receipt_provider, enforcement, capabilities, identity_kind,
                                                  identity_key, provider_host_key}
    preflight(claim)                           → {"result": "admitted"} or {"result": "refused", "reason",
                                                  "cleanup", "health"?}
    container(claim_id, fence, argv, env, release_fd)
                                               → a JSON-safe handle, returned only once the job's container exists
                                                  with a trampoline in it blocked reading `release_fd`. One byte on
                                                  that fd starts the job; EOF without a byte means it must never run.
                                                  The SDK closes `release_fd` once container() has returned, however
                                                  late; a provider must never close it itself.
    journal_ref(handle)                        → printable ASCII, no newline, ≤ 200 characters; a native provider's
                                                  has no whitespace (the generic one keeps kit 1.0.0's two tokens)
    stop(handle, grace_seconds)                → returns once the container is empty or the grace is spent;
                                                  `grace_seconds` is the POST-KILL drain window, and 0 means kill now
    inspect(handle_or_ref)                     → {"state": "gone" | "alive" | "unverifiable", "job_exit": int|None,
                                                  "job_signal": int|None, …counters}. Must accept a bare journal_ref
                                                  string: a reconcile has no live handle. job_exit / job_signal are
                                                  how the SDK learns the job ended, and stay None until it has.
    release_receipt(handle, execution)         → {"cleanup", "detail"?, "telemetry_tail"?}
    reconcile_verdict(entries, claim_id, fence)
                                               → (outcome, detail, tail) — §8.3's journal verdict and, for a native
                                                  provider, rev 6's device evidence and rev 7's job-leaf reading
    telemetry()                                → (telemetry_dict, health)
and, optionally, forward_signal(handle, signum): a provider whose job can take a terminal's signal (the generic one)
receives Ctrl-C/SIGTERM/SIGHUP at once, and the SDK then waits STOP_GRACE_SECONDS for the job before stop(handle, 0).

EVERY PROVIDER CALL HAS A HARD WALL-CLOCK BUDGET (D-RC-14). When one runs out the SDK stops waiting, never calls that
method again for the claim, ignores a late return, and takes the fallback — the same one a provider that RAISES gets:
    declare 10 s (exit 3) · telemetry 10 s (health unknown) · preflight 30 s (refused, pristine) · container 30 s (the
    release fd closed unwritten; released aborted/unknown) · journal_ref 20 s (the same) · stop grace + 30 s
    (released aborted/unknown; a reconcile reads the container later) · inspect 20 s (unverifiable) ·
    release_receipt 30 s (unknown) · reconcile_verdict 30 s (unknown).

THE CLAIM ROW IS THE AUTHORITY, NEVER A NOTICE. A grant also arrives in the agent's inbox as a `resource_notice`, and
this script listens for that wake (GET /v1/messages/events, the same stream postmq-wake.py holds) — but it never acts
on a notice's body and never leases one. On every wake, and on a 30-second timer, it re-reads the claim with get_claim
and acts on the row's state and fence. The timer is a correctness fallback, not a courtesy: the agent shares this
inbox and may lease the notice before this process sees the wake (design §7.7).

THE LAUNCH SEQUENCE (§8.3) — the order is the mechanism. Every sidecar and reconciler on this host for one resource
shares $POSTMQ_HOST_STATE/resources/<resource_id>/: a lock file (the GATE) and an append-only, fsynced JOURNAL.
    1. take the gate (60 s bound)
    2. re-read the claim under the gate: granted, our fence, at least 30 s before its deadline
    3. check the journal: our epoch retired, or a newer one recorded → this epoch is dead
    4. append `intent <claim_id> <fence> <pid>`; ask the provider's preflight (a refusal is reported and retires
       the epoch)
    5. report_admission(admitted) — the claim's heartbeat clock starts; from here only a release ends it
    6. the provider creates the job's container with a trampoline blocked on a pipe; append
       `launched <claim_id> <fence> <journal_ref>`; renew the claim — §7.5's re-read before launch, which must leave
       at least LAUNCH_MARGIN_SECONDS — and only then write the byte that lets the trampoline exec the command —
       without a shell — and release the gate. The claim is renewed from admission on, not only once the job runs,
       so a provider whose startup outlasts a short TTL does not lose the claim before the job begins.
A crash between exec and the journal can therefore never read as "never launched", and a retired epoch never execs.

WHILE IT RUNS: it renews at a third of the TTL, keeping the deadline from the server's clock, and retries a refused
renew after exactly Retry-After. If the claim is lost — expired (a laptop that slept through its TTL), revoked, or
fenced off — it stops the job as the resource's declared `revocation` says, waits, and releases late with the receipt:
the evidence still counts, the claim does not come back. A Ctrl-C or SIGTERM is forwarded to the job, which is then
released. While still QUEUED, an exit cancels the claim, so a sidecar that dies waiting leaves no grant nobody admits.

THE GENERIC PROVIDER. With no native receipt provider, receipts say `provider: generic` and report only what this
process can see: `verified_clean` when the job's process group is gone, `unknown` otherwise. They make a resource
available again only where the resource declared `receipt_provider: generic` — a `logical` resource such as a unit-
test gate. On a device registered with a native provider (such as linux-cgroup) the same sidecar still runs the gate
and still reports, and the resource stays `reconciling` until that provider has looked.

THE ACCOUNT. The credential's AI account must be in the resource's adapter set (the account that registered the
resource is; the owner adds others from the dashboard), because report_admission is an adapter's call. Its scopes:
`claim`, `manage_resources` (admission and reconcile), `discover_resources` (get_resource) and `get_pending` (the
wake stream; without it the sidecar falls back to the 30-second timer alone and says so).

NEVER RECORDED: the command, its arguments and the environment are never sent to PostMQ — not in the claim's `job`,
not in a receipt. `--job` sends only the JSON object you pass.

ENV
    POSTMQ_CREDENTIAL / POSTMQ_CREDENTIAL_FILE   the agent's credential, exactly as postmq-wake.py reads it
    POSTMQ_API_URL          REST base (default https://api.postmq.com); https:// only, plain http:// to this machine
                            only, and a redirect is refused rather than followed
    POSTMQ_HOST_STATE       the host-shared gate directory (default ~/.local/state/postmq-host)
    POSTMQ_HOST_KEY         the provider host identity a receipt names (default: this machine's id)

EXIT STATUS
    the command's own status when it ran and the claim held to the end (128 + N when it died of signal N)
    3  configuration       4  never granted (denied, timed out, withdrawn, cancelled)
    5  granted but not launched (the launch sequence stopped before exec)
    6  the claim was lost while the job ran (the job was stopped and released late)
    130/143  interrupted (SIGINT/SIGTERM) — cancelled while queued, or the job stopped and released

Standard library only, Python 3.8+. POSIX (Linux, macOS): the gate is flock(2) and the job's group is a POSIX session.
It does not run on Windows yet.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import math
import os
import queue
import random
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import ipaddress
import http.client

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

VERSION = "1.2.0"
DEFAULT_API_URL = "https://api.postmq.com"
PROVIDER = "generic"
ADVISORY = {"ownership": "advisory", "descendant_supervision": "advisory", "memory_accounting": "advisory",
            "revocation": "advisory"}
CLEANUP_VALUES = ("verified_clean", "residue", "cleanup_failed", "unknown")
HEALTH_VALUES = ("healthy", "degraded", "unhealthy", "unknown")
MAX_TEXT = 500          # the coordinator's limit on a reason, a detail and a telemetry summary (Appendix A)
MAX_TAIL = 64           # … and on a telemetry_tail's entries

LAUNCH_MARGIN_SECONDS = 30      # §7.5: never launch with less than this before the claim's deadline
GATE_TIMEOUT_SECONDS = 60       # §8.3 step 1
WAIT_TIMER_SECONDS = 30         # §7.7: the correctness fallback beside the wake
REQUEST_TIMEOUT_SECONDS = 30
STREAM_READ_TIMEOUT_SECONDS = 75  # the server keeps the event stream alive every 20 s
STOP_GRACE_SECONDS = 30         # after the first signal, before SIGKILL
DRAIN_SECONDS = 5               # after the job exits, for its group to empty
TRANSPORT_RETRY_SECONDS = 5     # a fixed retry, never an exponential back-off that can outrun a TTL
SERVE_INTERVAL_SECONDS = 15     # serve(): telemetry and health, and the reconcile check
INSPECT_POLL_SECONDS = 0.25     # supervise(): how often the provider is asked whether the job ended

# Every provider call's hard wall-clock budget (D-RC-14), in seconds. `stop` gets its grace plus BUDGET_STOP_EXTRA.
BUDGET_DECLARE = 10.0
BUDGET_TELEMETRY = 10.0
BUDGET_PREFLIGHT = 30.0
BUDGET_CONTAINER = 30.0
BUDGET_STOP_EXTRA = 30.0
BUDGET_INSPECT = 20.0
BUDGET_RELEASE_RECEIPT = 30.0
BUDGET_RECONCILE_VERDICT = 30.0

EXIT_CONFIG = 3
EXIT_NOT_GRANTED = 4
EXIT_NOT_LAUNCHED = 5
EXIT_LOST = 6

ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
LIVE_STATES = ("queued", "granted", "admitted")


class Fatal(Exception):
    """Retrying would only fail again: configuration, a refused credential, a redirect."""


class Refused(Exception):
    """A well-formed refusal from the coordinator: `{error: {code, message, details}}`."""

    def __init__(self, status, code, message, details, retry_after):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message, self.details, self.retry_after = status, code, message, details, retry_after


class ProviderUnavailable(Exception):
    """A provider call ran past its budget or raised. The SDK takes that call's fallback and never calls the method
    again for this claim; the message names the method and why, never the provider's own error text (which could quote
    anything)."""


class Transient(Exception):
    """A transport failure, a 5xx or a rate limit: worth retrying, after Retry-After when there is one."""

    def __init__(self, text, retry_after=None):
        super().__init__(text)
        self.retry_after = retry_after


# --------------------------------------------------------------------------------------------------- configuration

_SECRET = None


def credential() -> str:
    """The credential, checked before it goes near a header — postmq-wake.py's rule, for the same reason: http.client
    refuses a header value holding a line break and its error message is the whole value."""
    global _SECRET
    source = "POSTMQ_CREDENTIAL"
    value = os.environ.get("POSTMQ_CREDENTIAL", "").strip()
    if not value:
        source = os.environ.get("POSTMQ_CREDENTIAL_FILE") or os.path.expanduser("~/.config/postmq/credential")
        try:
            with open(source, "r", encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            value = ""
    if not value:
        raise Fatal(
            "No PostMQ credential. Set POSTMQ_CREDENTIAL (or put one in ~/.config/postmq/credential): a credential "
            "issued to an AI account in the resource's adapter set, with the claim and manage_resources scopes.")
    if not all("!" <= ch <= "~" for ch in value):
        raise Fatal(
            f"The credential in {source} holds a character a PostMQ credential never contains (a line break, a "
            "space, a control character or non-ASCII text). Nothing was sent, and the value is not shown.")
    _SECRET = value
    return value


def api_url() -> str:
    """https://, or plain http:// to this machine only, so the credential never crosses the network in clear."""
    url = (os.environ.get("POSTMQ_API_URL") or DEFAULT_API_URL).strip().rstrip("/")
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is not None and parts.scheme == "https" and host:
        return url
    if parts is not None and parts.scheme == "http" and _is_this_machine(host):
        return url
    raise Fatal(
        "POSTMQ_API_URL must be an https:// address (plain http:// is allowed only to localhost or a loopback "
        "address), because the credential would otherwise cross the network unencrypted. Nothing was sent.")


def _is_this_machine(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_state_root() -> str:
    return os.environ.get("POSTMQ_HOST_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "postmq-host")


def host_key() -> str:
    """The identity of the host a receipt was produced on (`provider_host_key`). For the generic provider on a
    `logical` resource it is informational; on a device it must equal the resource's identity key to count, which is
    what POSTMQ_HOST_KEY is for."""
    explicit = os.environ.get("POSTMQ_HOST_KEY", "").strip()
    if explicit:
        return explicit[:512]
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, "r", encoding="ascii") as handle:
                value = handle.read().strip()
            if value:
                return "machine-id:" + value
        except (OSError, UnicodeDecodeError):
            pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True,
                                 timeout=5).stdout
            match = re.search(r'"IOPlatformUUID" = "([0-9A-Fa-f-]{36})"', out)
            if match:
                return match.group(1).upper()
        except (OSError, subprocess.SubprocessError):
            pass
    return "hostname:" + socket.gethostname()


def _redact(text: str) -> str:
    if _SECRET and len(_SECRET) >= 8:
        return text.replace(_SECRET, "<redacted>")
    return text


def log(message: str) -> None:
    # Every diagnostic goes to stderr, redacted. stdout belongs to the job.
    sys.stderr.write("postmq-claim: " + _redact(message) + "\n")
    sys.stderr.flush()


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{value.microsecond // 1000:03d}Z"


def utc_now() -> str:
    return _iso(datetime.datetime.now(datetime.timezone.utc))


def parse_time(value):
    """An Appendix A timestamp (`2026-10-26T18:00:00.000Z`) as an aware datetime, or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def telemetry_tail(entries):
    """A provider's telemetry tail as the coordinator accepts it — the last 64 `{observed_at, summary ≤ 500}` objects
    — or None. An entry of any other shape is dropped rather than coerced: one malformed entry makes the coordinator
    refuse the WHOLE receipt, which would cost a finished job its release."""
    if not isinstance(entries, list):
        return None
    kept = []
    for entry in entries:
        if isinstance(entry, dict) and parse_time(entry.get("observed_at")) is not None \
                and isinstance(entry.get("summary"), str):
            kept.append({"observed_at": entry["observed_at"], "summary": entry["summary"][:MAX_TEXT]})
    return kept[-MAX_TAIL:] or None


def seconds_left(row: dict, field: str, now: str):
    """`row[field] − now`, both from the server's clock, so a skewed host clock never moves a deadline."""
    deadline, server_now = parse_time(row.get(field)), parse_time(now)
    if deadline is None or server_now is None:
        return None
    return (deadline - server_now).total_seconds()


def _retry_after(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


# --------------------------------------------------------------------------------------------------- the API

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow no redirect: urllib re-sends the Authorization header to a redirect's Location, to any host."""

    def http_error_302(self, req, fp, code, msg, headers):
        return None

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


_OPENER = urllib.request.build_opener(_RefuseRedirects)
_OPENER_DIRECT = urllib.request.build_opener(_RefuseRedirects, urllib.request.ProxyHandler({}))


def _open(request, timeout):
    opener = _OPENER_DIRECT if request.type == "http" else _OPENER
    return opener.open(request, timeout=timeout)


class Api:
    """The fifteen coordination routes the sidecar uses, with the wake kit's transport rules."""

    def __init__(self):
        self.base = api_url()
        self.token = credential()

    def request(self, method, path, body=None, idempotency_key=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": "postmq-claim/" + VERSION,
        })
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if idempotency_key:
            request.add_header("Idempotency-Key", idempotency_key)
        try:
            with _open(request, REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
            headers = error.headers
            if status in (301, 302, 303, 307, 308):
                raise Fatal(f"{self.base} answered HTTP {status} redirect, which the sidecar refuses: following it "
                            "re-sends the credential. Point POSTMQ_API_URL at the final address.")
            retry_after = _retry_after(headers.get("Retry-After") if headers else None)
            parsed = _json_or_none(raw)
            error_body = parsed.get("error") if isinstance(parsed, dict) else None
            code = error_body.get("code") if isinstance(error_body, dict) else None
            if status == 401:
                raise Fatal("the server refused this credential (401). Check that it is current and issued to an "
                            "AI account in this workspace.")
            if status == 429 and code != "POSTMQ_CLAIM_QUEUE_FULL":
                raise Transient(f"HTTP 429 {code or ''}".strip(), retry_after)
            if status >= 500:
                raise Transient(f"HTTP {status} {code or ''}".strip(), retry_after)
            if not isinstance(error_body, dict) or not isinstance(code, str):
                # Not the coordinator answering — a proxy, or POSTMQ_API_URL pointing somewhere else.
                raise Fatal(f"{self.base} answered HTTP {status} without a PostMQ error body; check POSTMQ_API_URL")
            details = error_body.get("details") if isinstance(error_body.get("details"), dict) else {}
            message = error_body.get("message") if isinstance(error_body.get("message"), str) else ""
            raise Refused(status, code, _redact(message)[:300], details, retry_after) from None
        except ValueError:
            raise Fatal("the request could not be built: a header was refused before sending (detail withheld "
                        "because it can carry the credential)") from None
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            raise Transient(f"{type(error).__name__}") from None
        parsed = _json_or_none(raw)
        if not isinstance(parsed, dict):
            raise Transient(f"HTTP {status} with a body that is not a JSON object")
        return status, parsed

    def call(self, method, path, body=None, idempotency_key=None, budget=120.0):
        """`request`, retrying a Transient for up to `budget` seconds — after exactly Retry-After when the server sent
        one, otherwise after a fixed few seconds. Only safe for calls whose replay is harmless; each caller says why."""
        deadline = time.monotonic() + budget
        while True:
            try:
                return self.request(method, path, body, idempotency_key)
            except Transient as error:
                wait = error.retry_after if error.retry_after is not None else TRANSPORT_RETRY_SECONDS
                if time.monotonic() + wait > deadline:
                    raise
                log(f"{method} {path.split('?')[0]} did not complete ({error}); retrying in {wait:.0f}s")
                time.sleep(wait)

    # A get is always safe to repeat.
    def get_claim(self, claim_id, budget=120.0):
        return self.call("GET", f"/v1/claims/{claim_id}", budget=budget)[1]

    def get_resource(self, resource_id, budget=120.0):
        return self.call("GET", f"/v1/resources/{resource_id}", budget=budget)[1]


def _json_or_none(raw):
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------------------------------- the wake

class WakePump:
    """Reads GET /v1/messages/events on its own thread and puts one "wake" per inbox event on a queue, so the waiter
    can block WITH a timeout. It never reads a payload and never leases: a wake only ever means "re-read the claim"."""

    def __init__(self, api: Api):
        self.api = api
        self.events = queue.Queue()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.done.set()

    def _run(self):
        failures = 0
        cursor = None
        while not self.done.is_set():
            try:
                for event, event_id in self._frames(cursor):
                    failures = 0
                    if self.done.is_set():
                        return
                    if event == "inbox":
                        cursor = event_id or cursor
                        self.events.put(("wake", None))
                    elif event == "closed":
                        raise Fatal("the server closed the wake stream")
                    elif event == "reconnect":
                        break
            except Fatal as error:
                self.events.put(("fatal", str(error)))
                return
            except Exception as error:  # noqa: BLE001 - a dropped stream; reconnect like the wake kit does
                failures += 1
                delay = error.retry_after if isinstance(error, Transient) and error.retry_after is not None \
                    else min(60, 2 ** min(failures, 6))
                self.done.wait(delay * (0.5 + random.random() / 2))
                continue
            self.done.wait(0.5 + random.random())

    def _frames(self, cursor):
        request = urllib.request.Request(self.api.base + "/v1/messages/events", headers={
            "Authorization": "Bearer " + self.api.token,
            "Accept": "text/event-stream",
            "User-Agent": "postmq-claim/" + VERSION,
        })
        if cursor:
            request.add_header("Last-Event-ID", cursor)
        try:
            response = _open(request, STREAM_READ_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise Fatal(f"the wake stream refused this credential ({error.code}; it needs the get_pending scope)")
            if error.code in (301, 302, 303, 307, 308):
                raise Fatal(f"the wake stream answered HTTP {error.code} redirect")
            raise Transient(f"HTTP {error.code}",
                            _retry_after(error.headers.get("Retry-After") if error.headers else None)) from None
        except ValueError:
            raise Fatal("the wake request could not be built") from None
        event, event_id = None, None
        with response:
            while not self.done.is_set():
                raw = response.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if event is not None:
                        yield event, event_id
                    event, event_id = None, None
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    event = value
                elif field == "id":
                    event_id = value


# --------------------------------------------------------------------------------------------------- the gate + journal

class Entry:
    """One journal record. `ref` is the provider's journal_ref for a `launched` entry: what a reconcile hands back to
    the provider's inspect. A generic entry also carries its process group and start token, parsed out of the ref."""
    __slots__ = ("kind", "claim", "fence", "pid", "group", "start", "ref")

    def __init__(self, kind, claim=None, fence=None, pid=None, group=None, start=None, ref=None):
        self.kind, self.claim, self.fence, self.pid = kind, claim, fence, pid
        self.group, self.start = group, start
        self.ref = ref if ref is not None else (f"pgid:{group} {start}" if group is not None else None)


MAX_JOURNAL_REF = 200


def valid_journal_ref(ref) -> bool:
    """Printable ASCII with no newline, ≤ 200 characters — and, unless it is the generic provider's two-token form,
    no whitespace, so one record is always one line of at most five fields."""
    if not isinstance(ref, str) or not ref or len(ref) > MAX_JOURNAL_REF:
        return False
    if any(not (" " <= ch <= "~") for ch in ref):
        return False
    parts = ref.split(" ")
    if len(parts) == 1:
        return True
    return len(parts) == 2 and parts[0].startswith("pgid:") and parts[0][5:].isdigit() and bool(parts[1])


def parse_entry(line: str):
    """One journal line, or None when it is not one this version wrote. Never raises: the journal is shared by every
    sidecar on the host, and an unreadable line must cost that line, not the reader. Two `launched` shapes:
    kit 1.0.0's generic `launched <claim> <fence> pgid:<n> <start>`, which this version still writes for the generic
    provider so an older sidecar on the same host reads it; and `launched <claim> <fence> <ref>` from a native provider,
    whose ref has no whitespace."""
    parts = line.split()
    try:
        if len(parts) == 4 and parts[0] == "intent" and ULID.match(parts[1]):
            return Entry("intent", claim=parts[1], fence=int(parts[2]), pid=int(parts[3]))
        if len(parts) == 5 and parts[0] == "launched" and ULID.match(parts[1]) and parts[3].startswith("pgid:"):
            return Entry("launched", claim=parts[1], fence=int(parts[2]), group=int(parts[3][5:]), start=parts[4])
        if len(parts) == 4 and parts[0] == "launched" and ULID.match(parts[1]):
            return Entry("launched", claim=parts[1], fence=int(parts[2]), ref=parts[3])
        if len(parts) == 2 and parts[0] == "retired":
            return Entry("retired", fence=int(parts[1]))
    except ValueError:
        return None
    return None


class HostGate:
    """$POSTMQ_HOST_STATE/resources/<resource_id>/{lock, journal}: one per resource per host, shared by every sidecar
    and reconciler on it (§8.3). The journal is append-only, fsynced per entry and read newest-first; a torn last line
    (no newline — a writer that died mid-append) is ignored."""

    def __init__(self, resource_id: str):
        if not ULID.match(resource_id):
            raise Fatal("the resource id is not a PostMQ id (26 characters, Crockford base32)")
        self.dir = os.path.join(host_state_root(), "resources", resource_id)
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.lock_path = os.path.join(self.dir, "lock")
        self.journal_path = os.path.join(self.dir, "journal")
        self._fd = None

    def acquire(self, timeout: float, stop=None) -> bool:
        """The gate, waiting at most `timeout` — and no longer than `stop()` stays false, so an interrupt received
        while another launcher holds the gate is answered at once rather than after the bound."""
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline or (stop is not None and stop()):
                    os.close(fd)
                    return False
                time.sleep(0.2)

    def release(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def append(self, line: str):
        created = not os.path.exists(self.journal_path)
        fd = os.open(self.journal_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            # The WHOLE record, or an error: a short write (a full volume) that reported success would let the job
            # start with only part of `launched` on disk — a torn line, which reads as "never launched".
            data = (line + "\n").encode("ascii")
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("the journal accepted no bytes")
                data = data[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            # The new directory entry is durable only once its directory is.
            dir_fd = os.open(self.dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

    def entries(self):
        """Every well-formed entry, newest first."""
        try:
            with open(self.journal_path, "r", encoding="ascii", errors="replace") as handle:
                text = handle.read()
        except FileNotFoundError:
            return []
        lines = text.split("\n")
        complete = lines[:-1]  # the last element is "" after a final newline, or a torn line without one
        parsed = [parse_entry(line) for line in complete]
        return [entry for entry in reversed(parsed) if entry is not None]


def epoch_dead(entries, fence: int) -> bool:
    """§8.3 step 3: this host retired `fence`, or recorded an intent or a launch for a newer one."""
    for entry in entries:
        if entry.kind == "retired" and entry.fence == fence:
            return True
        if entry.kind in ("intent", "launched") and entry.fence > fence:
            return True
    return False


# --------------------------------------------------------------------------------------------------- processes

def process_start(pid: int):
    """A token for when `pid` started, so a reused pid is never mistaken for the job; None when it does not exist."""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="ascii", errors="replace") as handle:
            stat = handle.read()
        # The command name is parenthesised and can hold spaces; the fields after its closing paren are fixed.
        fields = stat[stat.rindex(")") + 2:].split()
        return "proc:" + fields[19]
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    text = out.stdout.strip()
    return "ps:" + "_".join(text.split()) if out.returncode == 0 and text else None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


GROUP_GONE, GROUP_ALIVE, GROUP_UNVERIFIABLE = "gone", "alive", "unverifiable"
LIVE_PHASES = ("admitted", "running")  # the phases in which the claim is kept alive: startup counts


def group_state(pgid: int, start) -> str:
    """Whether the job's process group still has a member: gone, alive, or unverifiable. A group whose leader pid now
    belongs to a process that started at another time is not this job's — the pgid was reused — and that start-time
    evidence is the ONLY thing that turns a group we cannot inspect into "gone". A permission refusal on its own
    proves nothing: a job that changed its OS identity is still running, so it is unverifiable, never clean."""
    try:
        os.killpg(pgid, 0)
        refused = False
    except ProcessLookupError:
        return GROUP_GONE
    except PermissionError:
        refused = True
    leader = process_start(pgid)
    if leader is not None and start is not None and leader != start:
        return GROUP_GONE
    return GROUP_UNVERIFIABLE if refused else GROUP_ALIVE


def group_alive(pgid: int, start) -> bool:
    """Whether the group may still hold the resource — alive, or unverifiable, which must never read as clean."""
    return group_state(pgid, start) != GROUP_GONE


def signal_group(pgid: int, signum: int):
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


# The child that becomes the job: it is already its own session leader (start_new_session), so its pid is the
# process group the journal records. It blocks until the parent has written `launched`, then execs the command with
# no shell. EOF on the pipe — the sidecar died before the journal entry — means it must never exec.
TRAMPOLINE = (
    "import os,sys\n"
    "fd=int(sys.argv[1])\n"
    "go=os.read(fd,1)\n"
    "os.close(fd)\n"
    "if go!=b'x':\n"
    "    os._exit(125)\n"
    "try:\n"
    "    os.execvp(sys.argv[2],sys.argv[2:])\n"
    "except OSError as e:\n"
    "    sys.stderr.write('postmq-claim: could not start %s: %s\\n'%(sys.argv[2],e.strerror))\n"
    "    os._exit(127)\n"
)


# --------------------------------------------------------------------------------------------------- providers

class ReleaseWriter:
    """The write end of a launch's release pipe: one byte releases the trampoline, closing it unwritten makes the
    trampoline exit without exec. Closing is idempotent, so every path — including an exception — can close it."""

    def __init__(self, fd):
        self.fd = fd

    def release(self):
        fd, self.fd = self.fd, None
        try:
            os.write(fd, b"x")
        finally:
            os.close(fd)

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


class Bounded:
    """Every provider call under a hard wall-clock budget (D-RC-14). A call that runs past its budget, or raises, is
    reported as ProviderUnavailable; the method is then never called again through this object, and a late return is
    discarded by the daemon thread it ran on. One Bounded per claim (or per serve loop)."""

    def __init__(self, provider):
        self.provider = provider
        self.spent = set()

    def call(self, name, budget, *args, after=None):
        """`after`, when given, runs on the provider's own thread once the method has returned or raised — however
        long after the budget ran out. It is how a resource the provider was handed (the release pipe's read end) is
        released by the one party that knows the provider has finished with it: closing it on the caller's thread at
        the budget would free the descriptor NUMBER while a late provider still holds it, and the next launch in this
        process could be handed the same number."""
        if name in self.spent:
            if after is not None:
                after()
            raise ProviderUnavailable(f"{name} already ran out of its budget or failed for this claim")
        method = getattr(self.provider, name, None)
        if method is None:
            self.spent.add(name)
            if after is not None:
                after()
            raise ProviderUnavailable(f"the provider has no {name}()")
        box = {}
        done = threading.Event()

        def work():
            try:
                box["value"] = method(*args)
            except BaseException as error:  # noqa: BLE001 — a provider's failure is a fallback, never a crash
                box["error"] = error
            finally:
                if after is not None:
                    try:
                        after()
                    except Exception:  # noqa: BLE001
                        pass
                done.set()

        threading.Thread(target=work, name=f"provider-{name}", daemon=True).start()
        if not done.wait(budget):
            self.spent.add(name)
            raise ProviderUnavailable(f"{name} ran past its {budget:.0f}s budget")
        if "error" in box:
            self.spent.add(name)
            raise ProviderUnavailable(f"{name} raised {type(box['error']).__name__}")
        return box.get("value")


def _job_end(popen):
    """(job_exit, job_signal) of a finished Popen, or (None, None) while it runs."""
    code = popen.poll()
    if code is None:
        return None, None
    return (None, -code) if code < 0 else (code, None)


class GenericProvider:
    """The reference provider: the job's container is a POSIX process group (a child that setsid()s, blocks on the
    release pipe, then execs the command with no shell). It can see whether that group is gone, and nothing else, so
    every control it offers is advisory and its receipts say `verified_clean` only when the group is gone. Its
    journal_ref is kit 1.0.0's `pgid:<n> <start>`, byte for byte, so an older sidecar on the same host still reads a
    launch this one recorded."""

    def __init__(self, provider_host_key=None, identity_kind="logical", identity_key=None, capabilities=None):
        self.host_key = provider_host_key or host_key()
        self.identity_kind = identity_kind
        self.identity_key = identity_key
        self.capabilities = dict(capabilities or {})
        self._children = {}   # claim_id -> Popen; a handle stays JSON-safe and names the claim

    # ---- the protocol

    def declare(self):
        return {"receipt_provider": PROVIDER, "enforcement": dict(ADVISORY), "capabilities": dict(self.capabilities),
                "identity_kind": self.identity_kind, "identity_key": self.identity_key,
                "provider_host_key": self.host_key}

    def preflight(self, claim):
        return {"result": "admitted"}

    def container(self, claim_id, fence, argv, env, release_fd):
        child = subprocess.Popen([sys.executable, "-I", "-c", TRAMPOLINE, str(release_fd)] + list(argv),
                                 pass_fds=(release_fd,), start_new_session=True, env=env)
        start = None
        for _ in range(20):
            start = process_start(child.pid)
            if start:
                break
            time.sleep(0.05)
        self._children[claim_id] = child
        return {"kind": "pgid", "claim_id": claim_id, "fence": fence, "pgid": child.pid, "start": start or "unknown"}

    def journal_ref(self, handle):
        return f"pgid:{handle['pgid']} {handle['start']}"

    def forward_signal(self, handle, signum):
        signal_group(handle["pgid"], signum)

    def stop(self, handle, grace_seconds):
        """SIGTERM the WHOLE group, wait up to the grace for it to empty — a leader that exits on SIGTERM can leave a
        child that ignores it — then SIGKILL what is left. A grace of 0 kills at once."""
        pgid, start = handle["pgid"], self._start(handle)
        child = self._children.get(handle["claim_id"])
        if grace_seconds > 0:
            signal_group(pgid, signal.SIGTERM)
            if self._wait_group(child, pgid, start, grace_seconds):
                return
            log(f"the job did not stop within {grace_seconds:.0f}s; killing its process group")
        signal_group(pgid, signal.SIGKILL)
        self._wait_group(child, pgid, start, 10)

    def inspect(self, handle_or_ref):
        if isinstance(handle_or_ref, str):
            pgid, start = self._parse_ref(handle_or_ref)
            if pgid is None:
                return {"state": GROUP_UNVERIFIABLE, "job_exit": None, "job_signal": None}
            return {"state": group_state(pgid, start), "job_exit": None, "job_signal": None}
        handle = handle_or_ref
        child = self._children.get(handle["claim_id"])
        job_exit, job_signal = _job_end(child) if child is not None else (None, None)
        return {"state": group_state(handle["pgid"], self._start(handle)), "job_exit": job_exit,
                "job_signal": job_signal}

    def release_receipt(self, handle, execution):
        pgid, start = handle["pgid"], self._start(handle)
        child = self._children.get(handle["claim_id"])

        def alive():
            # Reap our own leader first: an exited child nobody has waited for is a zombie, and a zombie leader keeps
            # its process group present — a trampoline that saw EOF and exited would otherwise read as residue.
            if child is not None:
                child.poll()
            return group_alive(pgid, start)

        deadline = time.monotonic() + DRAIN_SECONDS
        while alive() and time.monotonic() < deadline:
            time.sleep(0.2)
        if not alive():
            return {"cleanup": "verified_clean"}
        return {"cleanup": "unknown", "detail": "the job's process group still has members after it exited"}

    def reconcile_verdict(self, entries, claim_id, fence):
        outcome, detail = journal_verdict(entries, claim_id, fence)
        return outcome, detail, None

    def telemetry(self):
        return {"host": socket.gethostname()}, "healthy"

    # ---- helpers

    @staticmethod
    def _start(handle):
        return None if handle.get("start") in (None, "unknown") else handle["start"]

    @staticmethod
    def _parse_ref(ref):
        parts = ref.split(" ")
        if len(parts) != 2 or not parts[0].startswith("pgid:") or not parts[0][5:].isdigit():
            return None, None
        return int(parts[0][5:]), (None if parts[1] == "unknown" else parts[1])

    @staticmethod
    def _wait_group(child, pgid, start, seconds) -> bool:
        """Wait up to `seconds` for the leader to exit AND the group to empty; True when both happened."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if (child is None or child.poll() is not None) and not group_alive(pgid, start):
                return True
            time.sleep(0.1)
        return (child is None or child.poll() is not None) and not group_alive(pgid, start)


def load_provider(spec: str):
    """`path/to/file.py:factory` → factory(). The file is loaded by path, not imported from sys.path, so a provider
    beside the project cannot be shadowed by a same-named module elsewhere."""
    path, sep, factory = spec.rpartition(":")
    if not sep or not path or not factory.isidentifier():
        raise Fatal("--provider must be path/to/file.py:factory")
    if not os.path.isfile(path):
        raise Fatal(f"--provider: {path} is not a file")
    # A name of its own per file, registered BEFORE the code runs: `dataclasses`, `typing.get_type_hints` and pickle
    # look a class's module up in sys.modules while the module is still executing.
    name = "postmq_claim_provider_" + hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:12]
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise Fatal(f"--provider: {path} cannot be loaded as Python")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    try:
        module_spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001
        sys.modules.pop(name, None)
        raise Fatal(f"--provider: loading {os.path.basename(path)} raised {type(error).__name__}") from None
    make = getattr(module, factory, None)
    if not callable(make):
        raise Fatal(f"--provider: {os.path.basename(path)} has no callable {factory}")
    try:
        return make()
    except Exception as error:  # noqa: BLE001
        raise Fatal(f"--provider: {factory}() raised {type(error).__name__}") from None


def declared(bounded: Bounded) -> dict:
    """The provider's declaration, checked: the receipt provider and host key every receipt names must be there."""
    try:
        decl = bounded.call("declare", BUDGET_DECLARE)
    except ProviderUnavailable as error:
        raise Fatal(f"the provider could not declare itself: {error}") from None
    if not isinstance(decl, dict) or not isinstance(decl.get("receipt_provider"), str) \
            or not isinstance(decl.get("provider_host_key"), str) or not decl["provider_host_key"]:
        raise Fatal("the provider's declare() must return receipt_provider and provider_host_key")
    return decl


# --------------------------------------------------------------------------------------------------- the holder

class Holder:
    """The sidecar's own state machine. `phase` decides what an exit does, so every way out — a return, an exception,
    a signal — leaves the claim the way §8.3 says. Everything about the job's container is the provider's; the holder
    reaches it only through `self.bounded`, so a provider that hangs or raises costs a fallback, never the claim."""

    def __init__(self, api: Api, resource_id: str, provider):
        self.api = api
        self.resource_id = resource_id
        self.provider = provider
        self.bounded = Bounded(provider)
        decl = declared(self.bounded)
        self.receipt_provider = decl["receipt_provider"]
        self.host_key = decl["provider_host_key"]
        self.gate = HostGate(resource_id)
        self.claim_id = None
        self.fence = None
        self.phase = "new"         # new | queued | granted | admitted | running | done | abandoned
        self.revocation = "advisory"
        self.interrupted = None    # the signal number that asked us to stop
        self.lost = threading.Event()
        self.lost_reason = None
        self.heartbeat_started = False
        self.handle = None         # the provider's handle on the job's container, once it exists
        self.expires_mono = None   # the claim's deadline on this host's monotonic clock

    # ---- signals

    def install_signals(self):
        forward = getattr(self.provider, "forward_signal", None)

        def handler(signum, _frame):
            if self.interrupted is None:
                self.interrupted = signum
                log(f"received {signal.Signals(signum).name}; stopping")
            if self.handle is not None and forward is not None:
                # Forward to the job: it is in its own session and does not see the terminal's signal. A provider
                # that cannot take a signal (a native container) is stopped by supervise() instead.
                try:
                    forward(self.handle, signum)
                except Exception:  # noqa: BLE001 — a signal handler must never raise
                    pass

        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), handler)

    # ---- claim

    def claim(self, ttl, wait, job, build_session):
        body = {"resource_id": self.resource_id, "mode": "exclusive",
                "holder_instance": f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"[:128]}
        if ttl is not None:
            body["ttl_seconds"] = ttl
        if wait is not None:
            body["max_wait_seconds"] = wait
        if job is not None:
            body["job"] = job
        if build_session:
            body["build_session_id"] = build_session
        # One key for every retry of this request: a lost reply is answered with the claim as it now stands (§7.1),
        # so a retry can never queue or grant twice.
        key = "postmq-claim-" + secrets.token_hex(16)
        deadline = time.monotonic() + 300
        while True:
            try:
                status, result = self.api.call("POST", "/v1/claims", body, idempotency_key=key, budget=120)
                break
            except Refused as refusal:
                if refusal.code == "POSTMQ_CLAIM_QUEUE_FULL" and refusal.retry_after is not None \
                        and time.monotonic() + refusal.retry_after < deadline and wait:
                    log(f"the queue is full; retrying in {refusal.retry_after:.0f}s")
                    time.sleep(refusal.retry_after)
                    continue
                raise
        claim = result.get("claim") or {}
        self.claim_id = claim.get("claim_id")
        if not isinstance(self.claim_id, str) or not ULID.match(self.claim_id):
            raise Transient("the claim response carried no claim id")
        self.fence = claim.get("fence")
        self.phase = "granted" if claim.get("state") == "granted" else "queued"
        return claim, result.get("now")

    def wait_for_grant(self, claim, now):
        """Queued: re-read the claim on every wake and every 30 s, until it is granted or ends."""
        position = claim.get("position")
        log(f"queued on {self.resource_id} at position {position}; waiting for a grant (wake + {WAIT_TIMER_SECONDS}s timer)")
        pump = WakePump(self.api).start()
        started = time.monotonic()
        next_timer = started + WAIT_TIMER_SECONDS
        stream_down = False
        try:
            while True:
                if self.interrupted is not None:
                    return None
                trigger = None
                try:
                    kind, detail = pump.events.get(timeout=max(0.05, min(1.0, next_timer - time.monotonic())))
                    if kind == "wake":
                        trigger = "wake"
                        # A burst of inbox events is one re-read.
                        while not pump.events.empty():
                            pump.events.get_nowait()
                    elif kind == "fatal" and not stream_down:
                        stream_down = True
                        log(f"{detail}; waiting on the {WAIT_TIMER_SECONDS}s timer alone")
                except queue.Empty:
                    if time.monotonic() >= next_timer:
                        trigger = "timer"
                if trigger is None:
                    continue
                next_timer = time.monotonic() + WAIT_TIMER_SECONDS
                result = self.api.get_claim(self.claim_id)
                row = result.get("claim") or {}
                state = row.get("state")
                if state == "granted":
                    self.phase = "granted"
                    self.fence = row.get("fence")
                    log(f"granted with fence {self.fence} — noticed by the {trigger} after "
                        f"{time.monotonic() - started:.1f}s")
                    return row
                if state == "queued":
                    if row.get("position") != position:
                        position = row.get("position")
                        log(f"now at position {position}")
                    continue
                self.phase = "done"
                log(f"the claim ended while queued: {state} ({row.get('end_reason')})")
                return None
        finally:
            pump.stop()

    # ---- launch sequence (§8.3)

    def launch(self, argv, env):
        fence = self.fence
        # 1. the gate, bounded — and given up at once on an interrupt, which a granted claim answers by cancelling.
        stop = lambda: self.interrupted is not None  # noqa: E731
        if not self.gate.acquire(GATE_TIMEOUT_SECONDS, stop=stop):
            if stop():
                self._cancel()
                return 128 + self.interrupted
            # Rev 6, C5: a gate held this long leaves the grant to expire at its admission deadline into
            # `reconciling` (§10, "granted, never admitted") — in front of the owner, never handed to the next
            # claimant on a host whose gate is stuck. A.2 refuses release_claim on a granted claim.
            self.phase = "abandoned"
            log(f"the host gate for {self.resource_id} has been held for {GATE_TIMEOUT_SECONDS}s by another launcher "
                "or reconciler; not launching. The grant is left to expire into reconciling, for the owner to see.")
            return EXIT_NOT_LAUNCHED
        try:
            # 2. re-read under the gate.
            result = self.api.get_claim(self.claim_id)
            row, now = result.get("claim") or {}, result.get("now")
            margin = seconds_left(row, "expires_at", now)
            if row.get("state") != "granted" or row.get("fence") != fence or margin is None \
                    or margin < LAUNCH_MARGIN_SECONDS:
                self.gate.release()
                if row.get("state") == "granted":
                    self._cancel()
                else:
                    self.phase = "done"
                log(f"not launching: under the gate the claim reads {row.get('state')} with fence {row.get('fence')} "
                    f"and {margin if margin is None else round(margin)}s left")
                return EXIT_NOT_LAUNCHED
            if stop():
                self.gate.release()
                self._cancel()
                return 128 + self.interrupted
            # 3. the journal.
            if epoch_dead(self.gate.entries(), fence):
                self.gate.release()
                # Rev 6, C6: a retired epoch reports `refused, pristine` — §7.8's "available at once, attempt
                # consumed", with the receipt that says nothing ran. A.2 refuses a release while granted.
                try:
                    self._report_admission("refused", reason="this host's journal has retired this epoch")
                    self.phase = "done"
                except Refused as refusal:
                    log(f"the refusal report was not accepted ({refusal.code})")
                log(f"not launching: this host's journal retired fence {fence} or recorded a newer one")
                return EXIT_NOT_LAUNCHED
            # 4. intent, then the provider's preflight.
            self.gate.append(f"intent {self.claim_id} {fence} {os.getpid()}")
            verdict = self._preflight(row)
            if verdict is not None:
                self.gate.append(f"retired {fence}")
                self.gate.release()
                try:
                    self._report_admission("refused", reason=verdict["reason"], cleanup=verdict["cleanup"],
                                           health=verdict.get("health"))
                except Refused as refusal:
                    log(f"the refusal report was not accepted ({refusal.code})")
                self.phase = "done"
                log(f"not launching: the provider refused admission ({verdict['reason']})")
                return EXIT_NOT_LAUNCHED
            # 5. admission.
            admitted = self._admit()
            if admitted is None:
                self.gate.append(f"retired {fence}")
                self.gate.release()
                self.phase = "done"
                return EXIT_NOT_LAUNCHED
            self.phase = "admitted"
            self._set_deadline(admitted.get("claim") or {}, admitted.get("now"))
            self._start_heartbeat()
            if stop():
                # Interrupted after admission and before anything started: retire the epoch under the gate, then
                # release with the receipt that says nothing ran — a cancel is refused once a claim is admitted.
                self._release_unlaunched("interrupted before the job started")
                return 128 + self.interrupted
            # 6. the container, recorded, then the byte that lets it exec.
            read_fd, write_fd = os.pipe()
            writer = ReleaseWriter(write_fd)
            try:
                return self._create_and_release(argv, env, fence, read_fd, writer, stop)
            except BaseException:
                # Anything that escapes before the byte — a Fatal from the pre-launch renew (a revoked credential
                # answers 401), a provider failure nobody anticipated — must still give the trampoline its EOF: a
                # writer left open in a long-lived SDK caller is a container blocked forever, waiting to be told.
                writer.close()
                raise
        finally:
            if self.gate.held:
                self.gate.release()

    def _create_and_release(self, argv, env, fence, read_fd, writer, stop):
        """§8.3 step 6, under the gate: the container, its journal entry, the pre-launch renew, then the byte. None
        when the command was released; an exit status when it was not (the writer is then closed unwritten)."""
        try:
            # The read end belongs to the provider's thread from here: it is closed there, once container() has
            # returned, never here at the budget (see Bounded.call). The write end stays ours.
            self.handle = self.bounded.call("container", BUDGET_CONTAINER, self.claim_id, fence, list(argv),
                                            env, read_fd, after=lambda: os.close(read_fd))
            ref = self.bounded.call("journal_ref", BUDGET_INSPECT, self.handle)
            if not valid_journal_ref(ref):
                raise ProviderUnavailable("journal_ref returned something that is not a journal reference")
        except ProviderUnavailable as error:
            # Nothing may run: the release pipe is closed unwritten. Whatever was created is the provider's to
            # inspect; with no handle there is nothing to look at, so the receipt cannot say clean.
            writer.close()
            log(f"the job's container could not be created: {error}")
            self._release_unlaunched(f"the provider could not create the container: {error}",
                                     launched=self.handle is not None, known_clean=False)
            return EXIT_NOT_LAUNCHED
        self.gate.append(f"launched {self.claim_id} {fence} {ref}")
        # §7.5 before launch: the provider's startup may have taken most of the claim's TTL, so renew it — the
        # ownership predicate is in the renew's UPDATE, which makes it the authoritative re-read — and start the
        # command only with at least the launch margin left. Recorded first, so a reconcile can find the
        # container whichever way this goes.
        not_live = self._renew_for_launch()
        if not_live is not None and not stop():
            writer.close()
            log(f"not launching: {not_live}")
            self._wait_gone(10)
            self._release_unlaunched(f"not launched: {not_live}", launched=True)
            return EXIT_NOT_LAUNCHED
        if stop():
            # The last instant an interrupt can stop the command from running at all: the trampoline is blocked
            # on the pipe, and closing it unwritten makes it exit without exec. Recorded, then released.
            writer.close()
            self._wait_gone(10)
            self._release_unlaunched("interrupted before the job started", launched=True)
            return 128 + self.interrupted
        writer.release()
        self.phase = "running"
        self.gate.release()
        log(f"launched under fence {fence} ({ref})")
        return None

    def _preflight(self, claim_row):
        """The provider's preflight: None to admit, or {reason, cleanup} to refuse. A preflight that runs past its
        budget or fails refuses admission with `pristine` — nothing was loaded (D-RC-14)."""
        try:
            answer = self.bounded.call("preflight", BUDGET_PREFLIGHT, claim_row)
        except ProviderUnavailable as error:
            return {"reason": f"preflight unavailable: {error}"[:MAX_TEXT], "cleanup": "pristine"}
        if not isinstance(answer, dict) or answer.get("result") not in ("admitted", "refused"):
            return {"reason": "preflight returned neither admitted nor refused", "cleanup": "pristine"}
        if answer["result"] == "admitted":
            return None
        cleanup = answer.get("cleanup") if answer.get("cleanup") in ("pristine", "residue") else "pristine"
        reason = answer.get("reason") if isinstance(answer.get("reason"), str) and answer["reason"] else "refused"
        verdict = {"reason": reason[:MAX_TEXT], "cleanup": cleanup}
        if answer.get("health") in HEALTH_VALUES:
            # The coordinator applies a refusal's health before it dispatches the next claimant: a provider that
            # refuses because the device is unhealthy must not have the resource handed straight to someone else.
            verdict["health"] = answer["health"]
        return verdict

    def _admit(self):
        """report_admission(admitted). A refusal is re-read before it is believed: a retry after a lost reply meets
        CLAIM_NOT_GRANTED for the admission that DID land, and treating that as a refusal would strand an admitted
        claim with no job and no heartbeat."""
        try:
            return self._report_admission("admitted")
        except Refused as refusal:
            if refusal.code == "POSTMQ_SCOPE_INSUFFICIENT":
                log("report_admission was refused: this credential's AI account is not in the resource's adapter "
                    "set (the owner adds it from the dashboard), or it lacks manage_resources")
            result = self.api.get_claim(self.claim_id)
            row = result.get("claim") or {}
            if row.get("state") == "admitted" and row.get("fence") == self.fence:
                return result
            log(f"admission refused ({refusal.code}); the claim reads {row.get('state')} — not launching")
            return None

    def _report_admission(self, result, reason=None, cleanup="pristine", health=None):
        body = {"fence": self.fence, "result": result, "cleanup": cleanup, "provider": self.receipt_provider,
                "provider_host_key": self.host_key, "observed_at": utc_now()}
        if reason:
            body["reason"] = reason[:MAX_TEXT]
        if health:
            body["health"] = health
        # Safe to retry: a repeated report is either refused (and re-read by _admit) or a no-op on an ended claim.
        return self.api.call("POST", f"/v1/claims/{self.claim_id}/admission", body)[1]

    def _set_deadline(self, row, now):
        left = seconds_left(row, "expires_at", now)
        if left is not None:
            self.expires_mono = time.monotonic() + left

    def _start_heartbeat(self):
        """Start renewing at admission, not at launch: the provider's container() and journal_ref() may together take
        longer than a short TTL, and an admitted claim nobody renews expires under a job that has not started yet."""
        if not self.heartbeat_started:
            self.heartbeat_started = True
            threading.Thread(target=self._guarded, args=(self.heartbeat,), daemon=True).start()

    def _renew_for_launch(self):
        """§7.5's re-read before launch, as a renew: its ownership predicate (admitted, this fence, not expired) is in
        the UPDATE, so a success is authoritative, and it returns the new deadline from the server's clock. None to
        launch; otherwise why not. A 429 is waited out for exactly Retry-After while the deadline allows; nothing else
        is retried — a launch is the one moment a stale answer must not be acted on."""
        while True:
            if self.interrupted is not None:
                return "interrupted"  # launch() then takes its interrupted-before-launch path
            if self.lost.is_set():
                return f"the claim was lost during startup ({self.lost_reason})"
            try:
                _, result = self.api.request("POST", f"/v1/claims/{self.claim_id}/renew", {"fence": self.fence})
            except Refused as refusal:
                if refusal.code != "POSTMQ_CLAIM_RATE_LIMITED":
                    return f"the claim could not be renewed before launch ({refusal.code})"
                wait = refusal.retry_after if refusal.retry_after is not None else TRANSPORT_RETRY_SECONDS
            except Transient as error:
                wait = error.retry_after if error.retry_after is not None else TRANSPORT_RETRY_SECONDS
            else:
                self._set_deadline(result, result.get("now"))
                margin = seconds_left(result, "expires_at", result.get("now"))
                if margin is None or margin < LAUNCH_MARGIN_SECONDS:
                    return (f"the renewed claim has {margin if margin is None else round(margin)}s left, under the "
                            f"{LAUNCH_MARGIN_SECONDS}s launch margin")
                if self.lost.is_set():
                    # The startup heartbeat saw the claim end while this renew's answer was in flight: a loss is
                    # sticky, and a claim already known lost is never launched on a late success.
                    return f"the claim was lost during startup ({self.lost_reason})"
                return None
            # A renew that lands resets the deadline to now + TTL, so the only question is whether the claim is still
            # alive when the retry would be sent.
            left = None if self.expires_mono is None else self.expires_mono - time.monotonic()
            if left is None or wait >= left:
                return "the claim's deadline passes before a renew could be retried"
            if self._pause(wait):
                return "interrupted" if self.interrupted is not None else \
                    f"the claim was lost during startup ({self.lost_reason})"

    def _pause(self, seconds):
        """Wait up to `seconds`, returning True as soon as the claim is lost or a signal asks us to stop: a launch
        waiting out a Retry-After must not hold the gate and an admitted claim past an interrupt."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.interrupted is not None or self.lost.wait(min(0.1, max(0.0, deadline - time.monotonic()))):
                return True
        return self.interrupted is not None or self.lost.is_set()

    # ---- while it runs

    def heartbeat(self):
        """Renew at a third of the remaining TTL; after a refusal wait exactly Retry-After; give up — and treat the
        claim as lost — only when the server says so or the deadline passes on this host's monotonic clock."""
        while self.phase in LIVE_PHASES and not self.lost.is_set():
            left = (self.expires_mono - time.monotonic()) if self.expires_mono else 30
            if left <= 0:
                self._lose("the claim's deadline passed without a successful renew")
                return
            if self.lost.wait(max(1.0, left / 3)):
                return
            if self.phase not in LIVE_PHASES:
                return
            while self.phase in LIVE_PHASES and not self.lost.is_set():
                try:
                    _, result = self.api.request("POST", f"/v1/claims/{self.claim_id}/renew", {"fence": self.fence})
                    self._set_deadline(result, result.get("now"))
                    break
                except Refused as refusal:
                    if refusal.code in ("POSTMQ_CLAIM_EXPIRED", "POSTMQ_CLAIM_FENCE_STALE", "POSTMQ_CLAIM_NOT_HELD",
                                        "POSTMQ_CLAIM_NOT_FOUND", "POSTMQ_CLAIM_NOT_ADMITTED"):
                        self._lose(f"renew refused: {refusal.code}")
                        return
                    wait = refusal.retry_after if refusal.retry_after is not None else TRANSPORT_RETRY_SECONDS
                except Transient as error:
                    wait = error.retry_after if error.retry_after is not None else TRANSPORT_RETRY_SECONDS
                left = None if self.expires_mono is None else self.expires_mono - time.monotonic()
                if left is not None and wait >= left:
                    # Retry-After outlasts the claim: never retry early, and never let the wait carry the job past
                    # its deadline either. Wait to the deadline, then the claim is lost.
                    if self.lost.wait(max(0.0, left)):
                        return
                    self._lose("the claim's deadline passed while a renew waited out its Retry-After")
                    return
                if self.lost.wait(wait):
                    return

    def watch_for_revocation(self, pump: WakePump):
        """On every wake while the job runs, re-read the claim: a revocation reaches the inbox before the next renew."""
        while self.phase == "running" and not self.lost.is_set():
            try:
                kind, _ = pump.events.get(timeout=1.0)
            except queue.Empty:
                continue
            if kind != "wake" or self.phase != "running":
                continue
            try:
                row = self.api.get_claim(self.claim_id, budget=10).get("claim") or {}
            except (Transient, Refused):
                continue
            if row.get("state") != "admitted" or row.get("fence") != self.fence:
                self._lose(f"the claim now reads {row.get('state')} ({row.get('end_reason')})")
                return

    def _guarded(self, target, *args):
        """A thread's uncaught exception would print a traceback that can quote response data; name its type only,
        and treat the claim as lost, since a dead heartbeat is a claim nobody is keeping alive."""
        try:
            target(*args)
        except Exception as error:  # noqa: BLE001
            log(f"the {target.__name__} thread stopped on an unexpected {type(error).__name__}")
            self._lose(f"the {target.__name__} thread stopped")

    def _lose(self, reason):
        if not self.lost.is_set():
            self.lost_reason = reason
            self.lost.set()

    def _inspect(self):
        """The provider's view of the job: (state, returncode or None). A provider that cannot answer is
        `unverifiable` with no exit — never read as gone."""
        try:
            seen = self.bounded.call("inspect", BUDGET_INSPECT, self.handle)
        except ProviderUnavailable:
            return GROUP_UNVERIFIABLE, None
        if not isinstance(seen, dict):
            return GROUP_UNVERIFIABLE, None
        state = seen.get("state") if seen.get("state") in (GROUP_GONE, GROUP_ALIVE, GROUP_UNVERIFIABLE) \
            else GROUP_UNVERIFIABLE
        if isinstance(seen.get("job_exit"), int):
            return state, seen["job_exit"]
        if isinstance(seen.get("job_signal"), int):
            return state, -seen["job_signal"]
        return state, None

    def _wait_gone(self, seconds):
        """Wait up to `seconds` for the job to have ended AND its container to be empty; True when both happened."""
        deadline = time.monotonic() + seconds
        while True:
            state, code = self._inspect()
            if code is not None and state == GROUP_GONE:
                return True
            if time.monotonic() >= deadline or "inspect" in self.bounded.spent:
                return False
            time.sleep(INSPECT_POLL_SECONDS)

    def supervise(self):
        """Run the job to its end, a lost claim, or an interruption; release with a receipt; return the exit code."""
        pump = WakePump(self.api).start()
        self._start_heartbeat()  # a no-op when admission already started it, which it always does on this path
        threading.Thread(target=self._guarded, args=(self.watch_for_revocation, pump), daemon=True).start()
        try:
            # Why the run ended (the exit status) and whether WE stopped the job (the receipt's `execution`) are two
            # facts: a job left to finish under `revocation: unavailable` completed, though its claim was lost.
            stopped_by, aborted, returncode, unstoppable = None, False, None, False
            while True:
                _, returncode = self._inspect()
                if returncode is not None:
                    break
                if "inspect" in self.bounded.spent:
                    # The provider can no longer say whether the job ended: stop it, and release what we know.
                    stopped_by, aborted = "lost", True
                    self._lose("the provider stopped answering inspect")
                    unstoppable = not self._stop(STOP_GRACE_SECONDS)
                    break
                if self.lost.is_set() and stopped_by is None:
                    stopped_by = "lost"
                    log(f"the claim was lost while the job ran: {self.lost_reason}")
                    if self.revocation == "unavailable":
                        log("the resource declares revocation: unavailable, so the job runs to its own end")
                    else:
                        aborted = True
                        unstoppable = not self._stop(STOP_GRACE_SECONDS)
                        _, returncode = self._inspect()
                        break
                if self.interrupted is not None and stopped_by is None:
                    break
                # The deadline, watched here as well as by the heartbeat: a renew request can block for its whole
                # timeout, and the job must not outlive its claim by that long.
                if stopped_by is None and self.expires_mono is not None and time.monotonic() >= self.expires_mono:
                    self._lose("the claim's deadline passed without a successful renew")
                time.sleep(INSPECT_POLL_SECONDS)
            if self.interrupted is not None and stopped_by is None:
                # A provider that takes signals already got it from the handler, and the job may have died of it
                # before this loop looked — but a child that ignores it can still be in the container, so the job
                # is given the grace and then stopped either way.
                stopped_by, aborted = "interrupted", True
                if getattr(self.provider, "forward_signal", None) is not None and self._wait_gone(STOP_GRACE_SECONDS):
                    pass
                else:
                    unstoppable = not self._stop(0 if getattr(self.provider, "forward_signal", None) is not None
                                                 else STOP_GRACE_SECONDS)
                _, returncode = self._inspect()
            self.phase = "done" if self.phase == "running" else self.phase
            return self._release(returncode, stopped_by, aborted, unstoppable)
        finally:
            self.lost.set()  # stops the heartbeat and the watcher
            pump.stop()

    def _stop(self, grace) -> bool:
        """The provider's stop, under its budget (grace + BUDGET_STOP_EXTRA). False when it could not be stopped in
        time — the release then says `unknown`, and a reconcile reads the container from its journal_ref later."""
        try:
            self.bounded.call("stop", grace + BUDGET_STOP_EXTRA, self.handle, grace)
            return True
        except ProviderUnavailable as error:
            log(f"the provider could not stop the job: {error}")
            return False

    def _release(self, returncode, stopped_by, aborted, unstoppable=False):
        execution = "aborted" if aborted else ("completed" if returncode == 0 else "failed")
        receipt = {"cleanup": "unknown", "execution": execution, "provider": self.receipt_provider,
                   "provider_host_key": self.host_key, "observed_at": utc_now()}
        if unstoppable:
            receipt["detail"] = "provider stop exceeded its budget"
        else:
            try:
                answer = self.bounded.call("release_receipt", BUDGET_RELEASE_RECEIPT, self.handle, execution)
                if isinstance(answer, dict) and answer.get("cleanup") in CLEANUP_VALUES:
                    receipt["cleanup"] = answer["cleanup"]
                    if isinstance(answer.get("detail"), str) and answer["detail"]:
                        receipt["detail"] = answer["detail"][:MAX_TEXT]
                    tail = telemetry_tail(answer.get("telemetry_tail"))
                    if tail:
                        receipt["telemetry_tail"] = tail
                else:
                    receipt["detail"] = "release_receipt returned no cleanup verdict"
            except ProviderUnavailable as error:
                receipt["detail"] = f"release_receipt unavailable: {error}"
        try:
            # Safe to retry: an exact replay of a completed release answers 200 (Appendix A.2).
            _, result = self.api.call("POST", f"/v1/claims/{self.claim_id}/release",
                                      {"fence": self.fence, "receipt": receipt})
            resource = result.get("resource") or {}
            log(f"released with a {receipt['cleanup']} receipt; the resource is {resource.get('availability')}")
            late = False
        except Refused as refusal:
            late = refusal.code == "POSTMQ_CLAIM_EXPIRED"
            log(f"the release was refused ({refusal.code})"
                + ("; the receipt was recorded as a late release" if late else ""))
        self.phase = "done"
        if stopped_by == "lost" or late:
            return EXIT_LOST
        if stopped_by == "interrupted":
            return 128 + (self.interrupted or signal.SIGTERM)
        if returncode is None:
            return EXIT_LOST
        return returncode if returncode >= 0 else 128 - returncode

    # ---- exits that never launched

    def _cancel(self):
        try:
            # Safe to retry: a repeated cancel of a cancelled claim is refused, and the claim is gone either way.
            self.api.call("POST", f"/v1/claims/{self.claim_id}/cancel", {}, budget=30)
            log("cancelled the claim")
        except (Refused, Transient) as error:
            log(f"could not cancel the claim ({error})")
        self.phase = "done"

    def _release_unlaunched(self, detail, launched=False, known_clean=True):
        """Admitted but the command never ran: retire the epoch (so no launcher can exec it later) and release with a
        receipt saying so. A container that was created and told to exit is inspected rather than assumed gone."""
        try:
            self.gate.append(f"retired {self.fence}")
        except OSError:
            pass
        if self.gate.held:
            self.gate.release()
        receipt = {"cleanup": "unknown", "execution": "aborted", "provider": self.receipt_provider,
                   "provider_host_key": self.host_key, "observed_at": utc_now()}
        if not launched:
            # Nothing was created for this claim, so the SDK itself knows what it left: nothing — unless the caller
            # says it cannot know (a container() that ran out of time may yet create one).
            if known_clean:
                receipt["cleanup"] = "verified_clean"
        elif known_clean:
            # A container exists and the command never ran in it. Whether the resource is as it was is the
            # PROVIDER's to say — an empty process group is not a native provider's device baseline — so ask it,
            # and fall back to unknown.
            try:
                answer = self.bounded.call("release_receipt", BUDGET_RELEASE_RECEIPT, self.handle, "aborted")
                if isinstance(answer, dict) and answer.get("cleanup") in CLEANUP_VALUES:
                    receipt["cleanup"] = answer["cleanup"]
                    if isinstance(answer.get("detail"), str) and answer["detail"]:
                        detail = f"{detail}; {answer['detail']}"
                    tail = telemetry_tail(answer.get("telemetry_tail"))
                    if tail:
                        receipt["telemetry_tail"] = tail
            except ProviderUnavailable as error:
                detail = f"{detail}; release_receipt unavailable: {error}"
        receipt["detail"] = detail[:MAX_TEXT]
        try:
            self.api.call("POST", f"/v1/claims/{self.claim_id}/release", {"fence": self.fence, "receipt": receipt},
                          budget=30)
            log(f"released without running the job ({detail})")
        except (Refused, Transient) as error:
            log(f"could not release the claim ({error})")
        self.phase = "done"

    def abandon_on_exit(self):
        """The `finally` of every run: whatever phase we died in, leave the claim as §8.3 says."""
        if self.phase in ("queued", "granted"):
            self._cancel()
        elif self.phase == "admitted":
            # Admitted, never launched (we died between steps 5 and 6): retire the epoch and release — nothing ran.
            self._release_unlaunched("admitted but never launched", launched=self.handle is not None)
        elif self.phase == "running" and self.handle is not None:
            unstoppable = not self._stop(STOP_GRACE_SECONDS)
            _, code = self._inspect()
            self._release(code, "interrupted", True, unstoppable)


# --------------------------------------------------------------------------------------------------- reconcile

def journal_verdict(entries, claim_id: str, fence: int):
    """§8.3's reconcile verdict for one epoch, from this host's journal and the processes it names. The journal says
    what was launched HERE through the gate; it is evidence only where it has an entry, so no entry is `unknown`,
    never `verified_clean`."""
    mine = [e for e in entries if e.fence == fence and e.kind in ("intent", "launched") and e.claim == claim_id]
    launched = next((e for e in mine if e.kind == "launched"), None)
    if launched is not None and launched.group is None:
        return "unknown", "this host's journal names a container another provider created; it cannot be read here"
    if launched is not None:
        state = group_state(launched.group, None if launched.start == "unknown" else launched.start)
        if state == GROUP_ALIVE:
            return "cleanup_failed", "the job's process group is still running"
        if state == GROUP_UNVERIFIABLE:
            return "unknown", "the job's process group exists but this account may not inspect it"
        return "verified_clean", "the job's process group is gone"
    intent = next((e for e in mine if e.kind == "intent"), None)
    if intent is not None:
        if pid_alive(intent.pid):
            return "unknown", "a launch intent is recorded and a process with its launcher's pid still exists"
        return "verified_clean", "a launch intent is recorded, nothing was launched, and its launcher is gone"
    return "unknown", "this host's journal has no launch for this claim"


def reconcile_target(api: Api, resource_id: str):
    """The claim the coordinator is waiting on before `resource_id` can be lent again (`reconciling_for_claim_id`,
    rev 9), or None. It is the only claim a reconcile can change anything for, and the only way to learn it when this
    host's journal never recorded the claim — a gate that timed out, a sidecar killed while queued, a launch from
    another host, an owner's revoke. Shown to adapters only; a server that predates rev 9 sends nothing, and the
    journal is then the fallback."""
    try:
        row = api.get_resource(resource_id, budget=30).get("resource") or {}
    except (Transient, Refused):
        return None
    target = row.get("reconciling_for_claim_id")
    return target if isinstance(target, str) and ULID.match(target) else None


def reconcile(api: Api, resource_id: str, claim_id, provider, quiet=False, bounded=None):
    """Reconcile `resource_id` under the gate, with the provider's verdict, for `claim_id` — or, when that is None, for
    the claim the coordinator is waiting on, else the newest one this host's journal names. Returns the outcome
    reported, or None when there was no claim to name."""
    bounded = bounded or Bounded(provider)
    decl = declared(bounded)
    gate = HostGate(resource_id)
    if claim_id is None:
        claim_id = reconcile_target(api, resource_id)
    if claim_id is None:
        newest = next((e for e in gate.entries() if e.kind in ("intent", "launched")), None)
        if newest is None:
            if not quiet:
                log("the resource is not waiting on a claim this account can see, and this host's journal names none; "
                    "pass --claim to report on one")
            return None
        claim_id = newest.claim
    row = api.get_claim(claim_id).get("claim") or {}
    fence = row.get("fence")
    if row.get("state") in LIVE_STATES:
        if not quiet:
            log(f"claim {claim_id} is {row.get('state')}; there is nothing to reconcile")
        return None
    if not isinstance(fence, int):
        log("get_claim did not show this account the claim's fence; reconcile is an adapter's call")
        return None
    tail = None
    if gate.acquire(GATE_TIMEOUT_SECONDS):
        try:
            try:
                answer = bounded.call("reconcile_verdict", BUDGET_RECONCILE_VERDICT, gate.entries(), claim_id, fence)
                outcome, detail, tail = answer
                if outcome not in ("verified_clean", "residue", "cleanup_failed", "unknown"):
                    outcome, detail, tail = "unknown", "reconcile_verdict returned no outcome", None
            except (ProviderUnavailable, TypeError, ValueError) as error:
                outcome, detail, tail = "unknown", f"reconcile_verdict unavailable: {error}", None
            # Retired BEFORE the gate is released, so no launcher holding this epoch can exec after we looked.
            gate.append(f"retired {fence}")
        finally:
            gate.release()
    else:
        outcome, detail = "unknown", f"the host gate was held for {GATE_TIMEOUT_SECONDS}s by a launcher or reconciler"
    body = {"claim_id": claim_id, "fence": fence, "outcome": outcome, "provider": decl["receipt_provider"],
            "provider_host_key": decl["provider_host_key"], "observed_at": utc_now(), "detail": str(detail)[:MAX_TEXT]}
    tail = telemetry_tail(tail)
    if tail:
        body["telemetry_tail"] = tail
    try:
        _, result = api.call("POST", f"/v1/resources/{resource_id}/reconcile", body)
        log(f"reconciled claim {claim_id}: {outcome} ({detail}); the resource is "
            f"{(result.get('resource') or {}).get('availability')}")
    except Refused as refusal:
        log(f"reconcile recorded but not applied ({refusal.code}): {outcome} — {detail}")
    return outcome


# --------------------------------------------------------------------------------------------------- library entry points

def run(provider, resource_id, argv, *, wait=0, job=None, ttl=None, build_session=None, api=None,
        install_signals=True) -> int:
    """Claim `resource_id` (queueing up to `wait` seconds), run `argv` in the provider's container under the §8.3
    launch sequence, keep the claim alive, and release it with the provider's receipt. Returns the exit status the
    CLI would: the command's own when it ran with the claim held to the end, else 4/5/6/128+N."""
    argv = list(argv)
    if not argv:
        raise Fatal("give the command to run")
    if job is not None and not isinstance(job, dict):
        raise Fatal("job must be a JSON object")
    api = api or Api()
    holder = Holder(api, resource_id, provider)
    resource = (api.get_resource(resource_id).get("resource") or {})
    holder.revocation = ((resource.get("enforcement") or {}).get("revocation")) or "advisory"
    if resource.get("availability") == "reconciling":
        # §7.4: in practice the next claimant's sidecar on the host reconciles; a report about the wrong claim is
        # recorded as late and changes nothing, so trying is always safe.
        try:
            reconcile(api, resource_id, None, provider, quiet=True, bounded=holder.bounded)
        except (Refused, Transient) as error:
            log(f"could not reconcile before claiming ({error})")
    if install_signals:
        holder.install_signals()
    try:
        try:
            claim, now = holder.claim(ttl, wait, job, build_session)
        except Refused as refusal:
            if refusal.code == "POSTMQ_CLAIM_DENIED":
                reason = refusal.details.get("reason") if isinstance(refusal.details.get("reason"), dict) else {}
                hint = " (pass --wait SECONDS to queue)" if reason.get("code") == "held_by_peer" and not wait else ""
                log(f"denied: {reason.get('code')}{hint}")
                return EXIT_NOT_GRANTED
            if refusal.code in ("POSTMQ_CLAIM_QUEUE_FULL", "POSTMQ_CLAIM_ALREADY_HELD"):
                log(f"not claimed: {refusal.code}")
                return EXIT_NOT_GRANTED
            raise
        log(f"claim {holder.claim_id}: {claim.get('state')}")
        if holder.phase == "queued":
            claim = holder.wait_for_grant(claim, now)
            if claim is None:
                if holder.interrupted is not None:
                    return 128 + holder.interrupted
                return EXIT_NOT_GRANTED
        if holder.interrupted is not None:
            return 128 + holder.interrupted
        env = dict(os.environ)
        env.update({"POSTMQ_CLAIM_ID": holder.claim_id, "POSTMQ_CLAIM_FENCE": str(holder.fence),
                    "POSTMQ_RESOURCE_ID": resource_id})
        stopped = holder.launch(argv, env)
        if stopped is not None:
            return stopped
        return holder.supervise()
    finally:
        holder.abandon_on_exit()


def reconcile_resource(provider, resource_id, claim_id=None, *, api=None):
    """Report what this host's journal and the provider say about the resource's last claim (or `claim_id`).
    Returns the outcome reported, or None when this host could name no claim."""
    return reconcile(api or Api(), resource_id, claim_id, provider)


def serve(provider, registration, *, interval=SERVE_INTERVAL_SECONDS, api=None, stop=None, on_registered=None):
    """Speak for a resource: register it from the provider's declaration and `registration` (`kind` and
    `display_name` at least; also `project_id`, `aliases`, `parent_resource_id`, `provider`, `identity_kind`,
    `identity_key`), then every `interval` seconds report the provider's telemetry and health and reconcile it while it
    is reconciling or quarantined. Runs until `stop()` is true (or forever); returns the resource id.

    Registering at every start is safe: a re-registration of the same identity by the same account updates in place
    and, under rev 6, does not undo a withdrawal the owner made."""
    api = api or Api()
    bounded = Bounded(provider)
    decl = declared(bounded)
    body = {"kind": registration.get("kind"), "display_name": registration.get("display_name"),
            "identity_kind": registration.get("identity_kind") or decl.get("identity_kind"),
            "identity_key": registration.get("identity_key") or decl.get("identity_key"),
            "capabilities": decl.get("capabilities") or {}, "enforcement": decl.get("enforcement") or dict(ADVISORY),
            "receipt_provider": decl["receipt_provider"]}
    for optional in ("project_id", "aliases", "parent_resource_id", "provider"):
        if registration.get(optional) is not None:
            body[optional] = registration[optional]
    for required in ("kind", "display_name", "identity_kind", "identity_key"):
        if not isinstance(body.get(required), str) or not body[required]:
            raise Fatal(f"serve needs {required} (from the registration or the provider's declaration)")
    key = "postmq-serve-" + hashlib.sha256(f"{body['identity_kind']}:{body['identity_key']}".encode()).hexdigest()[:40]
    _, result = api.call("POST", "/v1/resources", body, idempotency_key=key)
    resource = result.get("resource") or {}
    resource_id = resource.get("resource_id")
    if not isinstance(resource_id, str) or not ULID.match(resource_id):
        raise Transient("the registration response carried no resource id")
    log(f"registered {body['display_name']} as {resource_id}; reporting every {interval:.0f}s")
    if on_registered is not None:
        on_registered(resource_id)
    stop = stop or (lambda: False)
    while not stop():
        try:
            telemetry, health = bounded.call("telemetry", BUDGET_TELEMETRY)
            if not isinstance(telemetry, dict) or health not in ("healthy", "degraded", "unhealthy", "unknown"):
                telemetry, health = {}, "unknown"
        except (ProviderUnavailable, TypeError, ValueError):
            telemetry, health = {}, "unknown"
            # A telemetry() that ran over is never called again in this Bounded; give the next pass a fresh one so a
            # single slow read does not silence the adapter for good, but keep declare's verdict.
            bounded = Bounded(provider)
            bounded.spent.add("declare")
        try:
            _, updated = api.call("PATCH", f"/v1/resources/{resource_id}", {"telemetry": telemetry, "health": health},
                                  budget=30)
            availability = (updated.get("resource") or {}).get("availability")
        except (Refused, Transient) as error:
            log(f"could not report telemetry ({error})")
            availability = None
        if availability in ("reconciling", "quarantined"):
            try:
                reconcile(api, resource_id, None, provider, quiet=True, bounded=Bounded(provider))
            except (Refused, Transient, Fatal) as error:
                log(f"could not reconcile ({error})")
        deadline = time.monotonic() + interval
        while not stop() and time.monotonic() < deadline:
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    return resource_id


# --------------------------------------------------------------------------------------------------- commands

def _provider_from(args):
    spec = getattr(args, "provider", None)
    return load_provider(spec) if spec else GenericProvider(host_key())


def cmd_run(args) -> int:
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not argv:
        raise Fatal("give the command to run after --, e.g. postmq-claim.py run --resource <id> -- make test")
    if shutil.which(argv[0]) is None and not os.path.isfile(argv[0]):
        # Before claiming: a resource held for a command that cannot start helps nobody.
        raise Fatal(f"{argv[0]!r} was not found on PATH; nothing was claimed")
    job = None
    if args.job is not None:
        try:
            job = json.loads(args.job)
        except ValueError:
            raise Fatal("--job must be a JSON object") from None
        if not isinstance(job, dict):
            raise Fatal("--job must be a JSON object")
    return run(_provider_from(args), args.resource, argv, wait=args.wait, job=job, ttl=args.ttl,
               build_session=args.build_session)


def cmd_reconcile(args) -> int:
    if args.claim is not None and not ULID.match(args.claim):
        raise Fatal("--claim is not a PostMQ id")
    outcome = reconcile(Api(), args.resource, args.claim, _provider_from(args))
    return 0 if outcome is not None else 1


def cmd_serve(args) -> int:
    registration = {"kind": args.kind, "display_name": args.display_name, "project_id": args.project,
                    "identity_kind": args.identity_kind, "identity_key": args.identity_key}
    stopping = threading.Event()

    def handler(signum, _frame):
        log(f"received {signal.Signals(signum).name}; stopping")
        stopping.set()

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), handler)
    serve(_provider_from(args), registration, interval=args.interval or SERVE_INTERVAL_SECONDS,
          stop=stopping.is_set)
    return 0


def cmd_status(args) -> int:
    api = Api()
    result = api.get_resource(args.resource)
    resource = result.get("resource") or {}
    gate = HostGate(args.resource)
    newest = gate.entries()[:5]
    print(json.dumps({
        "resource_id": resource.get("resource_id"),
        "availability": resource.get("availability"),
        "health": resource.get("health"),
        "current_claim": resource.get("current_claim"),
        "queue_depth": resource.get("queue_depth"),
        "journal_newest": [{k: getattr(e, k) for k in Entry.__slots__ if getattr(e, k) is not None} for e in newest],
        "now": result.get("now"),
    }, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="postmq-claim", description="Hold a PostMQ resource claim around one job.")
    parser.add_argument("--version", action="version", version="postmq-claim " + VERSION)
    sub = parser.add_subparsers(dest="mode", required=True)
    provider_help = "a provider as path/to/file.py:factory (default: the generic POSIX process-group provider)"

    p = sub.add_parser("run", help="claim, wait, launch the command under the host gate, release with a receipt")
    p.add_argument("--resource", required=True, help="the resource id")
    p.add_argument("--ttl", type=int, help="heartbeat TTL in seconds (30-3600; the server's default is 300)")
    p.add_argument("--wait", type=int, help="queue for up to this many seconds if the resource is busy (default 0)")
    p.add_argument("--job", help="a JSON object describing the job, sent as the claim's job (never the command)")
    p.add_argument("--build-session", help="a SpecStep/PostMQ build session id to attribute the claim to")
    p.add_argument("--provider", help=provider_help)
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- the command and its arguments")
    # The three waits the design fixes, shortened only by the test suite (tools/tests/postmq-claim) so it can drive
    # the timer fallback, a held gate and a lost claim in seconds rather than minutes.
    p.add_argument("--timer", type=float, help=argparse.SUPPRESS)
    p.add_argument("--gate-timeout", type=float, help=argparse.SUPPRESS)
    p.add_argument("--stop-grace", type=float, help=argparse.SUPPRESS)
    p.add_argument("--launch-margin", type=float, help=argparse.SUPPRESS)
    p.add_argument("--budget-scale", type=float, help=argparse.SUPPRESS)

    p = sub.add_parser("reconcile", help="report what this host's journal says about the resource's last claim")
    p.add_argument("--resource", required=True)
    p.add_argument("--claim", help="the claim to report on (default: the one the resource is waiting on, else the newest "
                                   "one this host's journal names)")
    p.add_argument("--provider", help=provider_help)
    p.add_argument("--gate-timeout", type=float, help=argparse.SUPPRESS)
    p.add_argument("--budget-scale", type=float, help=argparse.SUPPRESS)

    p = sub.add_parser("serve", help="register the resource, then report its health and reconcile it, until stopped")
    p.add_argument("--kind", required=True, help="the resource's kind, e.g. gpu_host or unit_gate")
    p.add_argument("--display-name", required=True)
    p.add_argument("--project", help="the project id to register it under")
    p.add_argument("--identity-kind", help="override the provider's identity kind")
    p.add_argument("--identity-key", help="override the provider's identity key")
    p.add_argument("--interval", type=float, help=f"seconds between reports (default {SERVE_INTERVAL_SECONDS})")
    p.add_argument("--provider", help=provider_help)
    p.add_argument("--budget-scale", type=float, help=argparse.SUPPRESS)

    p = sub.add_parser("status", help="the resource as the server sees it, and this host's newest journal entries")
    p.add_argument("--resource", required=True)

    args = parser.parse_args(argv)
    global WAIT_TIMER_SECONDS, GATE_TIMEOUT_SECONDS, STOP_GRACE_SECONDS, LAUNCH_MARGIN_SECONDS
    WAIT_TIMER_SECONDS = getattr(args, "timer", None) or WAIT_TIMER_SECONDS
    GATE_TIMEOUT_SECONDS = getattr(args, "gate_timeout", None) or GATE_TIMEOUT_SECONDS
    STOP_GRACE_SECONDS = getattr(args, "stop_grace", None) or STOP_GRACE_SECONDS
    if getattr(args, "launch_margin", None) is not None:
        # The test suite's knob for the fake's seconds-long TTLs; the real coordinator's TTL floor is the margin.
        LAUNCH_MARGIN_SECONDS = args.launch_margin
    scale = getattr(args, "budget_scale", None)
    if scale:
        # The test suite's knob: every provider budget scaled together, so a fallback is exercised in under a second.
        for name in ("BUDGET_DECLARE", "BUDGET_TELEMETRY", "BUDGET_PREFLIGHT", "BUDGET_CONTAINER", "BUDGET_STOP_EXTRA",
                     "BUDGET_INSPECT", "BUDGET_RELEASE_RECEIPT", "BUDGET_RECONCILE_VERDICT"):
            globals()[name] = globals()[name] * scale
    if fcntl is None or not hasattr(os, "setsid"):
        log("the sidecar needs POSIX (flock and sessions); it does not run on Windows yet")
        return EXIT_CONFIG
    if args.mode != "serve" and not ULID.match(getattr(args, "resource", "") or ""):
        log("--resource is not a PostMQ id (26 characters, Crockford base32)")
        return EXIT_CONFIG
    try:
        return {"run": cmd_run, "reconcile": cmd_reconcile, "serve": cmd_serve, "status": cmd_status}[args.mode](args)
    except Fatal as error:
        log(str(error))
        return EXIT_CONFIG
    except (Refused, Transient) as error:
        log(f"stopped: {error}")
        return EXIT_CONFIG
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
