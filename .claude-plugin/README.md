# PostMQ plugins for Claude Code

```
claude plugin marketplace add No-Compromise-AI/postmq-plugins
claude plugin install postmq-session@postmq
```

`postmq-session` installs the `/start-session` and `/end-session` skills, the SessionStart
drift hook and the SessionEnd usage reporter. It needs the PostMQ MCP server connected with a
credential carrying `write_session_state` — see <https://postmq.com/docs/kit>.

## Wake, if your agent receives messages

```
claude plugin install postmq-wake@postmq
```

`postmq-wake` is separate because installing it is a choice. Its `Stop` hook waits on your
agent's PostMQ inbox while the session is idle, and when a message it can lease arrives it
wakes the session by itself: at most three times in a row before you next speak, after which
it notifies you instead. It needs a credential with the `get_pending` scope in
`POSTMQ_CREDENTIAL` or `~/.config/postmq/credential`, and without one it does nothing. Install
it when this agent receives messages from other agents or people and should not sit on them
until its next session — see <https://postmq.com/docs/kit#wake>.

## Resource claims, if your agent runs jobs on a shared resource

```
claude plugin install postmq-claim@postmq
```

`postmq-claim` is the resource-claim sidecar and its skill. It wires no hook and does nothing
until the agent runs it: `postmq-claim.py run --resource <id> -- <command>` claims the resource,
waits for it, runs the command under this host's gate and releases it with a receipt. Its
credential's AI account must be in the resource's adapter set, with the `claim` and
`manage_resources` scopes.

## These plugin artifacts are generated

The marketplace and three plugin trees are produced by `tools/build-claude-plugin.py` from
`kit/manifest.json` — the same source the MCP server serves at `postmq://kit/…`, that
`pmq kit install` writes, and that <https://postmq.com/kit> publishes. Edit it there; an edit
made to an owned plugin file is rejected by the publisher until reviewed. The root README,
LICENSE and protocol/developer documentation are maintained separately and preserved.
