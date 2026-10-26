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

## This directory is generated

Every file here is produced by `tools/build-claude-plugin.py` in the PostMQ repository, from
`kit/manifest.json` — the same source the MCP server serves at `postmq://kit/…`, that
`pmq kit install` writes, and that <https://postmq.com/kit> publishes. Edit it there; an edit
made here is overwritten by the next build and drifts from the other three surfaces meanwhile.
