# MobileWork SQLite source schema

This adapter was verified against the schema used for the 0827 and 0904 collections. Inspect a new release before extraction.

## Required tables and relations

- `session`: root and child sessions. `id` is the session key; `parent_id` links descendants; `title` identifies task/version/attempt; `directory` identifies the project.
- `message`: message state keyed by `id` and linked with `session_id`; structured content is in `data`.
- `part`: message parts linked by `message_id` and `session_id`; structured content is in `data`.
- `event`: ordered lifecycle events. `aggregate_id` is the session, and `seq` is the source sequence.
- `todo`: todo state linked by `session_id`.
- `session_input`: prompt delivery metadata linked by `session_id`. The prompt field exists but its text is omitted from the collection.
- `session_message`: user, assistant, and control envelopes linked by `session_id`; only non-user/non-assistant control records are exported from this table.

## Required columns

```text
session: id, parent_id, directory, title, version, time_created, time_updated,
         agent, model, cost, tokens_input, tokens_output, tokens_reasoning,
         tokens_cache_read, tokens_cache_write, metadata
message: id, session_id, time_created, time_updated, data
part: id, message_id, session_id, time_created, time_updated, data
event: id, aggregate_id, seq, type, data
todo: session_id, content, status, priority, position, time_created, time_updated
session_input: id, session_id, prompt, delivery, admitted_seq, promoted_seq, time_created
session_message: id, session_id, type, time_created, time_updated, data, seq
```

Additional columns may be retained in raw session records. Missing required tables or columns require an adapter update based on observed data; record the field as absent only when the output schema permits it.

## Snapshot behavior

Collection and source verification open SQLite with `mode=ro` and begin one read transaction. This keeps reads within a run consistent while leaving MobileWork data unchanged.
