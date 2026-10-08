import argparse
import json
import re
import sqlite3
from pathlib import Path


parser = argparse.ArgumentParser(description="只读发现 MobileWork 版本测试会话")
parser.add_argument("--config", required=True, help="UTF-8 JSON 配置文件")
args = parser.parse_args()
config = json.loads(Path(args.config).read_text(encoding="utf-8"))
database = Path(config["database"]).expanduser().resolve()
dataset = Path(config["dataset"]).expanduser().resolve()
versions = tuple(str(value) for value in config["versions"])
expected = int(config.get("expected_task_count", 30))
task_pattern = str(config.get("task_id_pattern", r"[WOCFSM][1-5]"))

if not database.is_file():
    raise SystemExit(f"数据库文件未包含：{database}")
if not dataset.is_dir():
    raise SystemExit(f"测试数据集目录未包含：{dataset}")
if not versions:
    raise SystemExit("versions 至少需要一个版本号")

tasks = {}
errors = []
for dimension in sorted(dataset.iterdir(), key=lambda path: path.name):
    if not dimension.is_dir():
        continue
    for task_dir in sorted(dimension.iterdir(), key=lambda path: path.name):
        if not task_dir.is_dir():
            continue
        match = re.match(rf"^({task_pattern})_", task_dir.name)
        if not match:
            continue
        task_id = match.group(1)
        if task_id in tasks:
            errors.append(f"数据集任务编号重复：{task_id}")
            continue
        tasks[task_id] = {
            "dimension_directory": dimension.name,
            "task_directory": task_dir.name,
        }
if len(tasks) != expected:
    errors.append(f"数据集识别到 {len(tasks)} 项，与配置的 {expected} 项不一致")

version_pattern = "|".join(re.escape(version) for version in versions)
title_re = re.compile(rf"^({task_pattern})-({version_pattern})(?:-(\d+))?$")
connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
candidates = {version: {task_id: [] for task_id in tasks} for version in versions}
for row in connection.execute(
    "select id,title,directory,time_created,time_updated,parent_id from session where parent_id is null"
):
    match = title_re.match(row["title"])
    if not match:
        continue
    task_id, version = match.group(1), match.group(2)
    if task_id not in tasks:
        continue
    candidates[version][task_id].append(
        {
            "attempt": int(match.group(3) or 1),
            "title": row["title"],
            "session_id": row["id"],
            "project_directory": row["directory"],
            "time_created": row["time_created"],
            "time_updated": row["time_updated"],
        }
    )
connection.close()

selections = []
for version in versions:
    for task_id in sorted(tasks):
        rows = candidates[version][task_id]
        if not rows:
            errors.append(f"未记录匹配的根会话：{task_id}-{version}")
            continue
        highest = max(row["attempt"] for row in rows)
        winners = [row for row in rows if row["attempt"] == highest]
        if len(winners) != 1:
            errors.append(f"最高测试次数存在重复记录：{task_id}-{version}-{highest}")
            continue
        selections.append(
            {
                "version": version,
                "task_id": task_id,
                "selected": winners[0],
                "candidate_count": len(rows),
                "candidate_titles": [row["title"] for row in sorted(rows, key=lambda item: (item["attempt"], item["session_id"]))],
            }
        )

result = {
    "state": "已就绪" if not errors else "待核对",
    "dataset_task_count": len(tasks),
    "version_count": len(versions),
    "selected_session_count": len(selections),
    "expected_selected_session_count": len(versions) * expected,
    "errors": errors,
    "selections": selections,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(1 if errors else 0)
