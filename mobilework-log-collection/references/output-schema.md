# Output schema 2.0

```text
<output>/
├─ 版本_项目编号.md
├─ metrics字段说明.md
├─ 日志整理说明.md
└─ mw-<version>/
   ├─ manifest.json
   ├─ metrics.csv
   └─ <dimension>/<task>/
      ├─ summary.md
      ├─ run.json
      ├─ trace.jsonl
      └─ evidence/
         ├─ README.md
         ├─ session.json
         ├─ sessions.jsonl
         ├─ session_graph.json
         ├─ messages.jsonl
         ├─ parts.jsonl
         ├─ tool_calls.jsonl
         ├─ todos.jsonl
         ├─ session_inputs.jsonl
         ├─ control_events.jsonl
         ├─ artifacts.json
         └─ evidence_manifest.json
```

## Roles

- `版本_项目编号.md`: human-readable task-to-project path mapping.
- `metrics字段说明.md`: generated definition and provenance for every CSV field.
- `日志整理说明.md`: generated description of scope, formats, omissions, and totals.
- `manifest.json`: version identity and task directory/session mapping. Its `schema_version` is `2.0`.
- `metrics.csv`: one task per row with a stable, common header across versions.
- `summary.md`: derived human-readable task overview.
- `run.json`: complete task summary, metrics, recording scope, and notes.
- `trace.jsonl`: one lifecycle event per line in merged order.
- `evidence/*`: normalized source records, session graph, artifact inventory, and evidence-file hashes.

JSON is UTF-8 with stable indentation; JSONL is UTF-8 with one complete JSON object per line; CSV is UTF-8 with BOM for spreadsheet compatibility. Times use ISO 8601 with local UTC offset, and durations use milliseconds.

Do not add validation reports to this tree by default. A file or field meaning change requires a schema-version decision and corresponding reference update.
