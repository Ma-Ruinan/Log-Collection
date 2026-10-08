# Collection rules

## Task and attempt selection

Discover task IDs from benchmark directories named `<task-id>_<task-name>`. Match root sessions named `<task-id>-<version>` or `<task-id>-<version>-<attempt>`. No suffix is attempt 1. Select the highest numeric attempt. Treat more than one root session at the highest attempt as an ambiguity and stop.

Require the configured task count in both the dataset and every version. Do not silently omit unmatched tasks or select by filesystem modification time.

## Session scope and ordering

Start with the selected root session and traverse `session.parent_id` breadth-first to include every descendant once. For merged trace ordering, sort by event time, session ID, and source event sequence. Preserve `source_seq` and assign a one-based continuous `trace_sequence`.

## Inclusion and omission

Include observable assistant messages, assistant parts, tool calls, tool input/status/timing/output, lifecycle events, todos, prompt-delivery metadata, control events, declared result paths, and project-file metadata.

By default:

- user prompt and task question: `未包含`
- fixed source-material contents and rubric contents: `未保存`
- source field not present: `未记录`
- project file contents: not copied
- unrelated global sessions: `未包含`

Keep actual database status strings such as `completed`, `error`, and `stop`. Do not replace them with conclusions.

## Large text and deduplication

Strings at or below the configured threshold are saved completely. Longer strings are represented by recording state, complete character count, SHA-256 of UTF-8 text, configured head, and configured tail.

`messages.jsonl`, `parts.jsonl`, and `tool_calls.jsonl` hold final canonical content. In `trace.jsonl`, large text/tool lifecycle payloads are replaced by length, hash, and a reference to the canonical record. Streaming `message.updated`, `message.part.updated`, and `session.updated` events are omitted when final states are already saved.

## Project inventory

Record relative path, size, modification time, and SHA-256. Do not traverse `.git`, `node_modules`, `.venv`, or `__pycache__`. Keep inbox references distinct from ordinary project files.

## Validation

Structural validation checks required files, JSON/JSONL parsing, counts, trace continuity, session graphs, CSV/run consistency, omission rules, and evidence hashes. Source verification independently re-reads SQLite and checks saved messages, parts, tools, events, metrics, excerpts, and project-file hashes.

Validation reports are console-only unless a report path is explicitly requested.
