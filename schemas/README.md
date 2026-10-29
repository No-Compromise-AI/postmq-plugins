# Wire schema publication

No stable PmP wire schema is released here yet. The [proposed specification](../spec/README.md) defines
portable requirements; the [A2A profile](../interop/a2a/README.md) describes interoperability limits.
Neither implies new PostMQ endpoints exist today.

A release must declare status, version, dialect, compatibility and matching normative contract. Publish
self-contained references and positive/negative fixtures for requests, responses, events and conditional
branches. Bound strings, collections and durations; separate authority from observation and retain identities/epochs.

Use a real schema validator, prove relevant predicates reject broken behavior, and pair shapes with
semantic/concurrency evidence. Shapes cannot prove current authority, custody, cleanup or transaction
ordering. Do not copy private wire artifacts wholesale or publish a second authority model under a new name.
