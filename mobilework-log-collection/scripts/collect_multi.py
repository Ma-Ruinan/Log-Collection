"""Multi-dataset adapter for the existing per-session collector.

--check reads the source only. --collect writes a new collection. --verify reads
the collection and source, checking every attempt without altering either.
"""

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--check", action="store_true")
mode.add_argument("--collect", action="store_true")
mode.add_argument("--verify", action="store_true")
args = parser.parse_args()
config_path = Path(args.config).resolve()
cfg = json.loads(config_path.read_text(encoding="utf-8"))
source = Path(__file__).with_name("collect.py").read_text(encoding="utf-8")
prefix = source.split("\nconn = sqlite3.connect(DB_URI, uri=True)\n", 1)[0]
original_argv = sys.argv
sys.argv = [str(Path(__file__).with_name("collect.py")), "--config", str(config_path)]
namespace = {"__name__": "_mobilework_collection_core_"}
try:
    exec(compile(prefix, str(Path(__file__).with_name("collect.py")), "exec"), namespace)
finally:
    sys.argv = original_argv

database = Path(cfg["database"]).resolve()
source_root = Path(cfg["dataset_root"]).resolve()
target_root = Path(cfg["output"]).resolve()
assert database.is_file() and source_root.is_dir()
assert target_root != source_root


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def task_id(dataset, dimension, task):
    local = task.name.split("_", 1)[0]
    if dataset["mapping"] == "regular":
        number = {"W": 1, "O": 2, "C": 3, "F": 4, "S": 5, "M": 6}
        match = re.fullmatch(r"([WOCFSM])([1-9][0-9]*)", local)
        if not match:
            raise ValueError(f"无法识别常规任务编号：{task}")
        return f"{number[match.group(1)]}.{int(match.group(2))}"
    match = re.match(r"^维度([一二三四五六七八九十]+)", dimension.name)
    numerals = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if not match or match.group(1) not in numerals or not local.isdigit():
        raise ValueError(f"无法识别数字任务编号：{dimension}/{task}")
    return f"{numerals[match.group(1)]}.{int(local)}"


def discover(conn):
    output = []
    root_rows = conn.execute("SELECT * FROM session WHERE parent_id IS NULL").fetchall()
    for dataset in cfg["datasets"]:
        path = source_root / dataset["directory"]
        if not path.is_dir():
            raise ValueError(f"数据集目录不存在：{path}")
        tasks = []
        for dimension in sorted(path.iterdir()):
            if not dimension.is_dir() or dimension.name not in dataset["dimensions"]:
                continue
            for task in sorted(dimension.iterdir()):
                if not task.is_dir() or not (task / "问题描述.txt").is_file():
                    continue
                if not (task / "rubric.md").is_file():
                    raise ValueError(f"rubric不存在：{task}")
                tid = task_id(dataset, dimension, task)
                prefix = f"{tid}-{dataset['session_suffix']}"
                matcher = re.compile(re.escape(prefix) + r"(?:-([1-9][0-9]*))?")
                attempts = defaultdict(list)
                for row in root_rows:
                    matched = matcher.fullmatch(row["title"])
                    if matched:
                        attempts[int(matched.group(1) or 1)].append(row)
                if not attempts:
                    raise ValueError(f"没有对应根会话：{task}")
                if any(len(rows) != 1 for rows in attempts.values()):
                    raise ValueError(f"重复测试次数：{task}")
                if any(not Path(rows[0]["directory"]).is_dir() for rows in attempts.values()):
                    raise ValueError(f"项目目录不存在：{task}")
                tasks.append({"id": tid, "dimension": dimension.name, "task": task.name,
                              "attempts": {n: rows[0] for n, rows in sorted(attempts.items())}})
        if len(tasks) != dataset["expected_task_count"]:
            raise ValueError(f"{dataset['directory']} 识别{len(tasks)}题，预期{dataset['expected_task_count']}题")
        ids = [item["id"] for item in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError(f"重复任务编号：{dataset['directory']}")
        output.append((dataset, tasks))
    return output


def read_jsonl(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
conn.execute("BEGIN")
try:
    found = discover(conn)
    count = sum(len(item["attempts"]) for _, tasks in found for item in tasks)
    for dataset, tasks in found:
        repeat = [(item["id"], sorted(item["attempts"])) for item in tasks if len(item["attempts"]) > 1]
        print(json.dumps({"dataset": dataset["directory"], "tasks": len(tasks),
                          "attempts": sum(len(item["attempts"]) for item in tasks),
                          "repeated": repeat}, ensure_ascii=False))
    if args.check:
        print(f"READY tasks={sum(len(tasks) for _, tasks in found)} attempts={count}")
    elif args.collect:
        if target_root.exists() and any(target_root.iterdir()):
            raise ValueError(f"输出目录非空：{target_root}")
        target_root.mkdir(parents=True, exist_ok=True)
        all_rows = []
        for dataset, tasks in found:
            dataset_out = target_root / dataset["directory"]
            entries = []
            rows = []
            for item in tasks:
                task_out = dataset_out / item["dimension"] / item["task"]
                attempt_rows = []
                for number, session in item["attempts"].items():
                    output_dir = task_out / f"第{number}次"
                    info = {"dimension_directory": item["dimension"],
                            "dimension_name": item["dimension"], "task_directory": item["task"],
                            "task_name": item["task"].split("_", 1)[-1]}
                    metrics, _ = namespace["collect_task"](conn, dataset["session_suffix"],
                                                               item["id"], info, session, output_dir)
                    rows.append(metrics)
                    all_rows.append(metrics)
                    attempt_rows.append({"attempt": number, "title": session["title"],
                                         "root_session_id": session["id"],
                                         "project_directory": session["directory"],
                                         "relative_path": str(output_dir.relative_to(dataset_out)).replace("\\", "/")})
                    print(f"COLLECT {dataset['session_suffix']} {item['id']} #{number}", flush=True)
                official = max(item["attempts"])
                index = {"task_id": item["id"], "dimension_directory": item["dimension"],
                         "task_directory": item["task"], "official_delivery_attempt": official,
                         "attempts": attempt_rows}
                namespace["write_json"](task_out / "任务会话索引.json", index)
                entries.append(index)
            dataset_out.mkdir(parents=True, exist_ok=True)
            namespace["write_json"](dataset_out / "manifest.json", {
                "schema_version": "3.0", "dataset": dataset["directory"],
                "task_count": len(tasks), "attempt_count": len(rows), "tasks": entries})
            namespace["write_csv"](dataset_out / "metrics.csv", rows, list(rows[0]))
        namespace["write_metrics_guide"](list(all_rows[0]))
        namespace["atomic_write_text"](target_root / "日志整理说明.md",
            "# 日志整理说明\n\n按数据集、原维度、原题目目录归档；独立尝试分别保存。"
            "任务问题、rubric、源素材和项目文件内容不复制；正式交付对应次数见任务会话索引。\n")
        print(f"DONE tasks={sum(len(tasks) for _, tasks in found)} attempts={count}")
    else:
        verified = 0
        for dataset, tasks in found:
            dataset_out = target_root / dataset["directory"]
            manifest = json.loads((dataset_out / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["schema_version"] == "3.0"
            assert manifest["task_count"] == len(tasks)
            assert manifest["attempt_count"] == sum(len(item["attempts"]) for item in tasks)
            with (dataset_out / "metrics.csv").open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
            assert len(csv_rows) == manifest["attempt_count"]
            for item in tasks:
                task_out = dataset_out / item["dimension"] / item["task"]
                index = json.loads((task_out / "任务会话索引.json").read_text(encoding="utf-8"))
                assert index["official_delivery_attempt"] == max(item["attempts"])
                assert len(index["attempts"]) == len(item["attempts"])
                for number, session in item["attempts"].items():
                    out = task_out / f"第{number}次"
                    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
                    assert run["metrics"]["root_session_id"] == session["id"]
                    assert run["metrics"]["selected_attempt"] == number
                    evidence = out / "evidence"
                    recorded = json.loads((evidence / "evidence_manifest.json").read_text(encoding="utf-8"))
                    for file in recorded["files"]:
                        path = evidence / file["name"]
                        assert path.stat().st_size == file["size_bytes"] and digest(path) == file["sha256"]
                    source_sessions = {session["id"]}
                    queue = [session["id"]]
                    while queue:
                        for child in conn.execute("SELECT id FROM session WHERE parent_id=?", (queue.pop(0),)):
                            if child["id"] not in source_sessions:
                                source_sessions.add(child["id"])
                                queue.append(child["id"])
                    saved_sessions = read_jsonl(evidence / "sessions.jsonl")
                    assert {row["id"] for row in saved_sessions} == source_sessions
                    saved_messages = read_jsonl(evidence / "messages.jsonl")
                    assert len(saved_messages) == run["metrics"]["all_sessions_assistant_message_count"]
                    saved_parts = read_jsonl(evidence / "parts.jsonl")
                    assert len(saved_parts) >= len(read_jsonl(evidence / "tool_calls.jsonl"))
                    trace = read_jsonl(out / "trace.jsonl")
                    assert [row["trace_sequence"] for row in trace] == list(range(1, len(trace) + 1))
                    assert len(trace) == run["metrics"]["lifecycle_event_count"]
                    source_messages = {}
                    source_parts = {}
                    source_events = set()
                    for sid in source_sessions:
                        for row in conn.execute("SELECT * FROM message WHERE session_id=?", (sid,)):
                            data = namespace["parse_json"](row["data"])
                            if isinstance(data, dict) and data.get("role") == "assistant":
                                source_messages[row["id"]] = data
                        for row in conn.execute("SELECT * FROM part WHERE session_id=?", (sid,)):
                            if row["message_id"] in source_messages:
                                source_parts[row["id"]] = namespace["parse_json"](row["data"])
                        for row in conn.execute(
                            "SELECT id FROM event WHERE aggregate_id=? AND (type='session.created.1' OR type LIKE 'session.next.%')", (sid,)
                        ):
                            source_events.add(row["id"])
                    assert {row["id"] for row in saved_messages} == set(source_messages)
                    for row in saved_messages:
                        assert row["data"] == namespace["compact_value"](source_messages[row["id"]])
                    assert {row["id"] for row in saved_parts} == set(source_parts)
                    for row in saved_parts:
                        assert row["data"] == namespace["compact_value"](source_parts[row["id"]])
                    assert {row["source_event_id"] for row in trace} == source_events
                    verified += 1
        print(f"VERIFIED attempts={verified}")
finally:
    conn.rollback()
    conn.close()
