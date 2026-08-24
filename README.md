# PostMQ plugins for Claude Code

```
claude plugin marketplace add No-Compromise-AI/postmq-plugins
claude plugin install postmq-session@postmq
```

`postmq-session` installs the `/start-session` and `/end-session` skills, the SessionStart
drift hook and the SessionEnd usage reporter. It needs the PostMQ MCP server connected with a
credential carrying `write_session_state` — see <https://postmq.com/api-docs/kit>.

## This directory is generated

Every file here is produced by `tools/build-claude-plugin.py` in the PostMQ repository, from
`kit/manifest.json` — the same source the MCP server serves at `postmq://kit/…`, that
`pmq kit install` writes, and that <https://postmq.com/kit> publishes. Edit it there; an edit
made here is overwritten by the next build and drifts from the other three surfaces meanwhile.
