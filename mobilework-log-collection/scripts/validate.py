import argparse
import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


parser = argparse.ArgumentParser(description="校验 MobileWork 日志整理目录")
parser.add_argument("--config", required=True, help="与收集脚本相同的 UTF-8 JSON 配置")
args = parser.parse_args()
CONFIG = json.loads(Path(args.config).read_text(encoding="utf-8"))
ROOT = Path(CONFIG["output"]).expanduser().resolve()
VERSIONS = tuple(str(value) for value in CONFIG["versions"])
EXPECTED_TASK_COUNT = int(CONFIG.get("expected_task_count", 30))
TASK_ID_PATTERN = str(CONFIG.get("task_id_pattern", r"[WOCFSM][1-5]"))
REQUIRED_TASK_FILES = (
    "summary.md",
    "run.json",
    "trace.jsonl",
    "evidence/session.json",
    "evidence/sessions.jsonl",
    "evidence/session_graph.json",
    "evidence/messages.jsonl",
    "evidence/parts.jsonl",
    "evidence/tool_calls.jsonl",
    "evidence/todos.jsonl",
    "evidence/session_inputs.jsonl",
    "evidence/control_events.jsonl",
    "evidence/artifacts.json",
    "evidence/evidence_manifest.json",
    "evidence/README.md",
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def as_int(value):
    return int(value)


index_text = (ROOT / "版本_项目编号.md").read_text(encoding="utf-8")
mapping_rows = re.findall(rf"^({TASK_ID_PATTERN})：(.+)$", index_text, re.MULTILINE)
assert len(mapping_rows) == len(VERSIONS) * EXPECTED_TASK_COUNT
assert all(Path(path).is_dir() for _, path in mapping_rows)
assert (ROOT / "metrics字段说明.md").is_file()
assert (ROOT / "日志整理说明.md").is_file()

all_metric_rows = []
task_dirs = []
version_counts = {}
metric_headers = None

for version in VERSIONS:
    version_dir = ROOT / f"mw-{version}"
    manifest = read_json(version_dir / "manifest.json")
    assert manifest["task_count"] == EXPECTED_TASK_COUNT
    assert len(manifest["tasks"]) == EXPECTED_TASK_COUNT
    assert len({item["task_id"] for item in manifest["tasks"]}) == EXPECTED_TASK_COUNT

    with (version_dir / "metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = reader.fieldnames
    assert len(rows) == EXPECTED_TASK_COUNT
    assert len({row["task_id"] for row in rows}) == EXPECTED_TASK_COUNT
    if metric_headers is None:
        metric_headers = headers
    else:
        assert headers == metric_headers
    all_metric_rows.extend(rows)
    version_counts[version] = len(rows)

    manifest_by_task = {item["task_id"]: item for item in manifest["tasks"]}
    for row in rows:
        task_dir = version_dir / manifest_by_task[row["task_id"]]["task_directory"]
        task_dirs.append(task_dir)
        for relative in REQUIRED_TASK_FILES:
            assert (task_dir / relative).is_file(), task_dir / relative

        run = read_json(task_dir / "run.json")
        trace = read_jsonl(task_dir / "trace.jsonl")
        sessions = read_jsonl(task_dir / "evidence" / "sessions.jsonl")
        messages = read_jsonl(task_dir / "evidence" / "messages.jsonl")
        parts = read_jsonl(task_dir / "evidence" / "parts.jsonl")
        tools = read_jsonl(task_dir / "evidence" / "tool_calls.jsonl")
        graph = read_json(task_dir / "evidence" / "session_graph.json")
        artifacts = read_json(task_dir / "evidence" / "artifacts.json")
        inputs = read_jsonl(task_dir / "evidence" / "session_inputs.jsonl")

        assert len(trace) == as_int(row["lifecycle_event_count"])
        assert [item["trace_sequence"] for item in trace] == list(
            range(1, len(trace) + 1)
        )
        assert len(sessions) == as_int(row["session_count"])
        assert graph["session_count"] == len(sessions)
        assert graph["root_session_id"] == row["root_session_id"]
        assert len(messages) == as_int(row["all_sessions_assistant_message_count"])
        assert all(item["data"].get("role") == "assistant" for item in messages)
        assert len(tools) == as_int(row["all_sessions_tool_call_count"])
        assert sum(item["data"].get("type") == "tool" for item in parts) == len(tools)
        assert all(item.get("prompt_recording") == "未包含" for item in inputs)
        assert artifacts["declared_output_count"] == as_int(
            row["declared_output_path_count"]
        )

        evidence_manifest = read_json(task_dir / "evidence" / "evidence_manifest.json")
        for item in evidence_manifest["files"]:
            path = task_dir / "evidence" / item["name"]
            assert path.stat().st_size == item["size_bytes"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]

        assert run["metrics"]["task_id"] == row["task_id"]
        assert run["metrics"]["root_session_id"] == row["root_session_id"]
        assert run["metrics"]["lifecycle_event_count"] == len(trace)
        assert run["recording_scope"]["not_included"]

expected_total = len(VERSIONS) * EXPECTED_TASK_COUNT
assert len(task_dirs) == expected_total
assert len(set(task_dirs)) == expected_total
assert len(all_metric_rows) == expected_total

guide_text = (ROOT / "metrics字段说明.md").read_text(encoding="utf-8")
documented_fields = set(re.findall(r"^\| `([^`]+)` \|", guide_text, re.MULTILINE))
assert set(metric_headers) == documented_fields

authored_paths = [
    ROOT / "metrics字段说明.md",
    ROOT / "日志整理说明.md",
    *[task / "summary.md" for task in task_dirs],
    *[task / "run.json" for task in task_dirs],
    *[task / "evidence" / "README.md" for task in task_dirs],
]
for path in authored_paths:
    text = path.read_text(encoding="utf-8")
    assert "无法" not in text, path
    assert "不足" not in text, path

assert not list(ROOT.rglob("*.tmp"))
assert not list(ROOT.rglob("问题描述.txt"))
assert not list(ROOT.rglob("*_rubric.txt"))

total_files = [path for path in ROOT.rglob("*") if path.is_file()]
total_bytes = sum(path.stat().st_size for path in total_files)
total_sessions = sum(as_int(row["session_count"]) for row in all_metric_rows)
total_events = sum(as_int(row["lifecycle_event_count"]) for row in all_metric_rows)
total_tools = sum(as_int(row["all_sessions_tool_call_count"]) for row in all_metric_rows)
total_tool_errors = sum(
    as_int(row["all_sessions_tool_error_count"]) for row in all_metric_rows
)
total_retries = sum(as_int(row["retry_event_count"]) for row in all_metric_rows)
total_reasoning_parts = sum(
    as_int(row["all_sessions_reasoning_part_count"]) for row in all_metric_rows
)
total_reasoning_events = sum(
    as_int(row["reasoning_started_event_count"]) for row in all_metric_rows
)
tasks_with_children = [
    f"{row['version_label']}/{row['task_id']}"
    for row in all_metric_rows
    if as_int(row["child_session_count"]) > 0
]
tasks_without_tool_parts = [
    f"{row['version_label']}/{row['task_id']}"
    for row in all_metric_rows
    if as_int(row["all_sessions_tool_call_count"]) == 0
]

validation = {
    "check_state": "已检查",
    "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "version_task_counts": version_counts,
    "project_mapping_count": len(mapping_rows),
    "task_directory_count": len(task_dirs),
    "metrics_column_count": len(metric_headers),
    "record_file_count": len(total_files),
    "record_size_bytes": total_bytes,
    "session_count": total_sessions,
    "lifecycle_event_count": total_events,
    "tool_call_count": total_tools,
    "tool_error_status_count": total_tool_errors,
    "retry_event_count": total_retries,
    "reasoning_part_count": total_reasoning_parts,
    "reasoning_started_event_count": total_reasoning_events,
    "tasks_with_child_sessions": tasks_with_children,
    "tasks_with_no_tool_part_record": tasks_without_tool_parts,
    "checks": {
        "each_version_has_expected_tasks": "已检查",
        "project_directories_exist": "已检查",
        "json_and_jsonl_parse": "已检查",
        "trace_sequence_continuity": "已检查",
        "metrics_and_task_counts_match": "已检查",
        "session_graph_counts_match": "已检查",
        "evidence_hashes_match": "已检查",
        "problem_source_and_rubric_files": "未保存",
        "temporary_files": "未包含",
    },
}
print("VALID")
print(json.dumps(validation, ensure_ascii=False, indent=2))
