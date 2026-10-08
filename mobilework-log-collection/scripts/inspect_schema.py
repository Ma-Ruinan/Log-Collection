import argparse
import json
import sqlite3
from pathlib import Path


REQUIRED = {
    "session": {
        "id", "parent_id", "directory", "title", "version", "time_created",
        "time_updated", "agent", "model", "cost", "tokens_input",
        "tokens_output", "tokens_reasoning", "tokens_cache_read",
        "tokens_cache_write", "metadata",
    },
    "message": {"id", "session_id", "time_created", "time_updated", "data"},
    "part": {"id", "message_id", "session_id", "time_created", "time_updated", "data"},
    "event": {"id", "aggregate_id", "seq", "type", "data"},
    "todo": {"session_id", "content", "status", "priority", "position", "time_created", "time_updated"},
    "session_input": {"id", "session_id", "prompt", "delivery", "admitted_seq", "promoted_seq", "time_created"},
    "session_message": {"id", "session_id", "type", "time_created", "time_updated", "data", "seq"},
}


parser = argparse.ArgumentParser(description="检查 MobileWork SQLite 日志结构")
parser.add_argument("--database", required=True)
args = parser.parse_args()
database = Path(args.database).expanduser().resolve()
if not database.is_file():
    raise SystemExit(f"数据库文件未包含：{database}")

connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
tables = {
    row[0]
    for row in connection.execute("select name from sqlite_master where type='table'")
}
details = {}
missing_tables = []
missing_columns = {}
for table, required_columns in REQUIRED.items():
    if table not in tables:
        missing_tables.append(table)
        continue
    columns = [row[1] for row in connection.execute(f'pragma table_info("{table}")')]
    missing = sorted(required_columns - set(columns))
    details[table] = {
        "columns": columns,
        "row_count": connection.execute(f'select count(1) from "{table}"').fetchone()[0],
    }
    if missing:
        missing_columns[table] = missing
connection.close()

result = {
    "state": "已匹配" if not missing_tables and not missing_columns else "结构有变化",
    "database": str(database),
    "missing_tables": sorted(missing_tables),
    "missing_columns": missing_columns,
    "tables": details,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(1 if missing_tables or missing_columns else 0)
