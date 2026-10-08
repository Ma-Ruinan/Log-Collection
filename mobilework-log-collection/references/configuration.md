# Configuration

The scripts share one UTF-8 JSON configuration file.

```json
{
  "database": "C:/Users/<user>/.mobilework/xdg/data/opencode/opencode.db",
  "dataset": "D:/path/to/benchmark-dataset",
  "output": "D:/path/to/new-version-log",
  "versions": ["0827", "0904"],
  "expected_task_count": 30,
  "task_id_pattern": "[WOCFSM][1-5]",
  "large_text_limit": 20000,
  "excerpt_head": 5000,
  "excerpt_tail": 1000
}
```

## Fields

- `database`: Required MobileWork SQLite database. Scripts open it in read-only mode.
- `dataset`: Required benchmark root. Its immediate dimension directories contain task directories named `<task-id>_<task-name>`.
- `output`: Required collection root. `collect.py` accepts only a missing or empty directory.
- `versions`: Required non-empty list. A root-session title must be `<task-id>-<version>` or `<task-id>-<version>-<attempt>`.
- `expected_task_count`: Expected tasks per version; default `30`.
- `task_id_pattern`: Regular expression without anchors; default `[WOCFSM][1-5]`.
- `large_text_limit`: Text length above which excerpts replace the full string; default `20000`.
- `excerpt_head`: Leading characters saved for an excerpt; default `5000`.
- `excerpt_tail`: Trailing characters saved for an excerpt; default `1000`.

Use forward slashes in Windows JSON paths to avoid backslash escaping. Keep the config outside the final collection unless the user asks to preserve operational parameters there.

## Commands

```text
python scripts/inspect_schema.py --database <database>
python scripts/discover.py --config <config.json>
python scripts/collect.py --config <config.json>
python scripts/validate.py --config <config.json>
python scripts/verify_source.py --config <config.json>
```

`verify_source.py --report <path>` is optional. Without it, verification is console-only.
