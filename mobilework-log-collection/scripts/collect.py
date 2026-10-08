import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


parser = argparse.ArgumentParser(description="按统一格式收集 MobileWork 日志")
parser.add_argument("--config", required=True, help="UTF-8 JSON 配置文件")
args = parser.parse_args()
CONFIG = json.loads(Path(args.config).read_text(encoding="utf-8"))

DB_PATH = Path(CONFIG["database"]).expanduser().resolve()
DB_URI = DB_PATH.as_uri() + "?mode=ro"
DATASET = Path(CONFIG["dataset"]).expanduser().resolve()
TARGET = Path(CONFIG["output"]).expanduser().resolve()
VERSIONS = tuple(str(value) for value in CONFIG["versions"])
EXPECTED_TASK_COUNT = int(CONFIG.get("expected_task_count", 30))
TASK_ID_PATTERN = str(CONFIG.get("task_id_pattern", r"[WOCFSM][1-5]"))
LARGE_TEXT_LIMIT = int(CONFIG.get("large_text_limit", 20_000))
EXCERPT_HEAD = int(CONFIG.get("excerpt_head", 5_000))
EXCERPT_TAIL = int(CONFIG.get("excerpt_tail", 1_000))
if not VERSIONS:
    raise SystemExit("versions 至少需要一个版本号")
if not DB_PATH.is_file():
    raise SystemExit(f"数据库文件未包含：{DB_PATH}")
if not DATASET.is_dir():
    raise SystemExit(f"测试数据集目录未包含：{DATASET}")
if LARGE_TEXT_LIMIT <= 0 or EXCERPT_HEAD < 0 or EXCERPT_TAIL < 0:
    raise SystemExit("文本节选参数需要为非负值，large_text_limit 需要大于 0")
if EXCERPT_HEAD + EXCERPT_TAIL > LARGE_TEXT_LIMIT:
    raise SystemExit("excerpt_head + excerpt_tail 不应超过 large_text_limit")
TZ = datetime.now().astimezone().tzinfo
COLLECTED_AT = datetime.now(TZ).isoformat(timespec="seconds")


def iso(ms):
    if ms is None:
        return "未记录"
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(TZ).isoformat(
        timespec="milliseconds"
    )


def parse_json(value):
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def atomic_write_text(path, content, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(content, encoding=encoding, newline="\n")
    os.replace(temp, path)


def write_json(path, value):
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path, values):
    atomic_write_text(
        path,
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
    )


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_text(value):
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compact_value(value):
    if isinstance(value, str):
        if len(value) <= LARGE_TEXT_LIMIT:
            return value
        return {
            "recording": "节选保存",
            "character_count": len(value),
            "sha256": sha256_text(value),
            "head": value[:EXCERPT_HEAD],
            "tail": value[-EXCERPT_TAIL:],
        }
    if isinstance(value, list):
        return [compact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: compact_value(item) for key, item in value.items()}
    return value


def contains_large_text(value):
    if isinstance(value, str):
        return len(value) > LARGE_TEXT_LIMIT
    if isinstance(value, list):
        return any(contains_large_text(item) for item in value)
    if isinstance(value, dict):
        return any(contains_large_text(item) for item in value.values())
    return False


def safe_json_string(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def compress_sequence(names):
    if not names:
        return "未记录"
    groups = []
    current = names[0]
    count = 1
    for name in names[1:]:
        if name == current:
            count += 1
        else:
            groups.append(current if count == 1 else f"{current}×{count}")
            current = name
            count = 1
    groups.append(current if count == 1 else f"{current}×{count}")
    return " → ".join(groups)


def sanitize_lifecycle_payload(event_type, payload):
    data = deepcopy(payload)
    if event_type == "session.next.prompted.1":
        prompt = data.get("prompt")
        if isinstance(prompt, dict):
            prompt["text"] = "未包含"
            prompt["text_recording"] = "未包含"
    elif event_type == "session.next.tool.input.ended.1":
        text = data.pop("text", None)
        data["input_text_recording"] = {
            "state": "未重复保存",
            "character_count": len(text) if isinstance(text, str) else "未记录",
            "sha256": sha256_text(text) if isinstance(text, str) else "未记录",
            "reference": "evidence/tool_calls.jsonl",
        }
    elif event_type == "session.next.tool.success.1":
        content = data.pop("content", None)
        result = data.pop("result", None)
        serialized = safe_json_string({"content": content, "result": result})
        data["result_recording"] = {
            "state": "未重复保存",
            "character_count": len(serialized),
            "sha256": sha256_text(serialized),
            "reference": "evidence/parts.jsonl and evidence/tool_calls.jsonl",
        }
    elif event_type == "session.next.text.ended.1":
        text = data.pop("text", None)
        data["text_recording"] = {
            "state": "未重复保存",
            "character_count": len(text) if isinstance(text, str) else "未记录",
            "sha256": sha256_text(text) if isinstance(text, str) else "未记录",
            "reference": "evidence/parts.jsonl",
        }
    return compact_value(data)


def collect_descendants(conn, root_session_id):
    result = []
    queue = deque([root_session_id])
    seen = {root_session_id}
    while queue:
        parent = queue.popleft()
        children = conn.execute(
            "select * from session where parent_id=? order by time_created,id", (parent,)
        ).fetchall()
        for child in children:
            if child["id"] in seen:
                continue
            seen.add(child["id"])
            result.append(child)
            queue.append(child["id"])
    return result


def row_with_parsed_json(row, compact=False):
    record = dict(row)
    for key in ("data", "model", "permission", "metadata", "summary_diffs", "revert"):
        if key in record:
            record[key] = parse_json(record[key])
    return compact_value(record) if compact else record


def find_dataset_tasks():
    tasks = {}
    dimension_rows = []
    for dimension in sorted(DATASET.iterdir(), key=lambda path: path.name):
        if not dimension.is_dir():
            continue
        task_rows = []
        for task in sorted(dimension.iterdir(), key=lambda path: path.name):
            if not task.is_dir():
                continue
            match = re.match(rf"^({TASK_ID_PATTERN})_", task.name)
            if not match:
                continue
            task_id = match.group(1)
            if task_id in tasks:
                raise RuntimeError(f"固定任务目录中任务编号重复：{task_id}")
            task_name = task.name.split("_", 1)[1].strip()
            dimension_name = dimension.name.split("_", 1)[1] if "_" in dimension.name else dimension.name
            tasks[task_id] = {
                "dimension_directory": dimension.name,
                "dimension_name": dimension_name,
                "task_directory": task.name,
                "task_name": task_name,
            }
            task_rows.append(task_id)
        if task_rows:
            dimension_rows.append((dimension.name, task_rows))
    if len(tasks) != EXPECTED_TASK_COUNT:
        raise RuntimeError(
            f"固定任务目录识别到 {len(tasks)} 项，与配置的 {EXPECTED_TASK_COUNT} 项不一致"
        )
    return tasks, dimension_rows


def select_root_sessions(conn):
    selected = {version: {} for version in VERSIONS}
    candidates = {version: {} for version in VERSIONS}
    versions_pattern = "|".join(re.escape(version) for version in VERSIONS)
    pattern = re.compile(rf"^({TASK_ID_PATTERN})-({versions_pattern})(?:-(\d+))?$")
    for row in conn.execute("select * from session"):
        if row["parent_id"] is not None:
            continue
        match = pattern.match(row["title"])
        if not match:
            continue
        task_id, version = match.group(1), match.group(2)
        attempt = int(match.group(3) or 1)
        candidate = dict(row)
        candidate["_attempt"] = attempt
        candidates[version].setdefault(task_id, []).append(candidate)
    for version, version_candidates in candidates.items():
        for task_id, rows in version_candidates.items():
            highest = max(row["_attempt"] for row in rows)
            winners = [row for row in rows if row["_attempt"] == highest]
            if len(winners) != 1:
                titles = ", ".join(row["title"] for row in winners)
                raise RuntimeError(f"{task_id}-{version} 最高测试次数存在重复记录：{titles}")
            selected_row = dict(winners[0])
            selected_row.pop("_attempt", None)
            selected[version][task_id] = selected_row
    for version in VERSIONS:
        if len(selected[version]) != EXPECTED_TASK_COUNT:
            raise RuntimeError(
                f"mw-{version} 选定 {len(selected[version])} 项，与配置的 "
                f"{EXPECTED_TASK_COUNT} 项不一致"
            )
    return selected, candidates


def collect_task(conn, version, task_id, task_info, root_session, output_dir=None):
    version_label = f"mw-{version}"
    task_dir = output_dir or (
        TARGET
        / version_label
        / task_info["dimension_directory"]
        / task_info["task_directory"]
    )
    evidence_dir = task_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    descendants = collect_descendants(conn, root_session["id"])
    sessions = [root_session, *descendants]
    session_ids = [row["id"] for row in sessions]
    session_map = {row["id"]: row for row in sessions}

    all_messages = []
    message_data = {}
    assistant_message_ids = set()
    all_parts_original = []
    all_parts_saved = []
    all_todos = []
    all_inputs = []
    all_controls = []
    lifecycle_events = []

    for session_row in sessions:
        sid = session_row["id"]
        messages = conn.execute(
            "select * from message where session_id=? order by time_created,id", (sid,)
        ).fetchall()
        for row in messages:
            data = parse_json(row["data"])
            message_data[row["id"]] = data
            if isinstance(data, dict) and data.get("role") == "assistant":
                assistant_message_ids.add(row["id"])
                record = dict(row)
                record["data"] = compact_value(data)
                all_messages.append(record)

        parts = conn.execute(
            "select * from part where session_id=? order by time_created,id", (sid,)
        ).fetchall()
        for row in parts:
            if row["message_id"] not in assistant_message_ids:
                continue
            original_data = parse_json(row["data"])
            original = dict(row)
            original["data"] = original_data
            all_parts_original.append(original)
            saved = dict(row)
            saved["data"] = compact_value(original_data)
            all_parts_saved.append(saved)

        for row in conn.execute(
            "select * from todo where session_id=? order by position", (sid,)
        ):
            all_todos.append(dict(row))

        for row in conn.execute(
            "select id,session_id,delivery,admitted_seq,promoted_seq,time_created "
            "from session_input where session_id=? order by admitted_seq,id",
            (sid,),
        ):
            record = dict(row)
            record["time_created_iso"] = iso(record["time_created"])
            record["prompt_recording"] = "未包含"
            all_inputs.append(record)

        for row in conn.execute(
            "select * from session_message where session_id=? "
            "and type not in ('user','assistant') order by seq,id",
            (sid,),
        ):
            record = dict(row)
            record["data"] = compact_value(parse_json(record["data"]))
            record["time_created_iso"] = iso(record["time_created"])
            all_controls.append(record)

        for row in conn.execute(
            "select id,aggregate_id,seq,type,data from event "
            "where aggregate_id=? and (type='session.created.1' or type like 'session.next.%') "
            "order by seq,id",
            (sid,),
        ):
            payload = parse_json(row["data"])
            lifecycle_events.append(
                {
                    "source_event_id": row["id"],
                    "session_id": sid,
                    "parent_session_id": session_row["parent_id"],
                    "source_seq": row["seq"],
                    "event_type": row["type"],
                    "timestamp_ms": payload.get("timestamp") if isinstance(payload, dict) else None,
                    "timestamp": iso(payload.get("timestamp"))
                    if isinstance(payload, dict) and payload.get("timestamp") is not None
                    else iso(session_row["time_created"]),
                    "data": sanitize_lifecycle_payload(row["type"], payload),
                }
            )

    lifecycle_events.sort(
        key=lambda event: (
            event["timestamp_ms"]
            if event["timestamp_ms"] is not None
            else session_map[event["session_id"]]["time_created"],
            event["session_id"],
            event["source_seq"],
        )
    )
    for index, event in enumerate(lifecycle_events, start=1):
        event["trace_sequence"] = index

    final_message_ids = {
        message_id
        for message_id, data in message_data.items()
        if message_id in assistant_message_ids
        and isinstance(data, dict)
        and data.get("finish") == "stop"
    }
    root_final_message_ids = {
        message_id
        for message_id in final_message_ids
        if any(
            message["id"] == message_id and message["session_id"] == root_session["id"]
            for message in all_messages
        )
    }

    tool_calls = []
    for part in all_parts_original:
        data = part["data"]
        if not isinstance(data, dict) or data.get("type") != "tool":
            continue
        state = data.get("state") or {}
        timing = state.get("time") or {}
        output = state.get("output")
        output_recording = "未记录"
        output_value = None
        if "output" in state:
            output_recording = "节选保存" if contains_large_text(output) else "完整保存"
            output_value = compact_value(output)
        output_serialized = (
            output if isinstance(output, str) else safe_json_string(output)
        ) if "output" in state else None
        markers = []
        if isinstance(output, str):
            if "404" in output[:500]:
                markers.append("输出前500字符包含404")
            if "403" in output[:500]:
                markers.append("输出前500字符包含403")
        tool_calls.append(
            {
                "session_id": part["session_id"],
                "parent_session_id": session_map[part["session_id"]]["parent_id"],
                "message_id": part["message_id"],
                "part_id": part["id"],
                "time_created": part["time_created"],
                "time_created_iso": iso(part["time_created"]),
                "tool_name": data.get("tool", "未记录"),
                "tool_call_id": data.get("callID", "未记录"),
                "status": state.get("status", "未记录"),
                "input": compact_value(state.get("input", "未记录")),
                "title": state.get("title", "未记录"),
                "error": compact_value(state.get("error", "未记录")),
                "time_start": iso(timing.get("start")) if timing.get("start") is not None else "未记录",
                "time_end": iso(timing.get("end")) if timing.get("end") is not None else "未记录",
                "duration_ms": timing.get("end") - timing.get("start")
                if timing.get("start") is not None and timing.get("end") is not None
                else "未记录",
                "output_recording": output_recording,
                "output_character_count": len(output_serialized)
                if output_serialized is not None
                else "未记录",
                "output_sha256": sha256_text(output_serialized)
                if output_serialized is not None
                else "未记录",
                "output": output_value if output_recording != "未记录" else "未记录",
                "output_markers": markers,
                "metadata": compact_value(state.get("metadata", "未记录")),
                "attachments": compact_value(state.get("attachments", "未记录")),
            }
        )

    # result_file 中明确登记的交付路径。
    declared_outputs = []
    for tool in tool_calls:
        if tool["tool_name"] != "result_file" or not isinstance(tool["input"], dict):
            continue
        paths = tool["input"].get("output_paths") or []
        if isinstance(paths, str):
            paths = [paths]
        for value in paths:
            session_directory = Path(session_map[tool["session_id"]]["directory"])
            path = Path(value)
            if not path.is_absolute():
                path = session_directory / path
            exists = path.is_file()
            declared_outputs.append(
                {
                    "declared_path": value,
                    "resolved_path": str(path),
                    "exists_at_collection": exists,
                    "size_bytes": path.stat().st_size if exists else "未记录",
                    "sha256": sha256_file(path) if exists else "未记录",
                    "session_id": tool["session_id"],
                    "tool_call_id": tool["tool_call_id"],
                }
            )

    # 项目目录只保存文件清单，不复制文件；inbox 单独计数。
    project_directory = Path(root_session["directory"])
    project_files = []
    inbox_files = []
    skipped_directory_names = {".git", "node_modules", ".venv", "__pycache__"}
    if project_directory.is_dir():
        for current_root, directory_names, file_names in os.walk(project_directory):
            directory_names[:] = [
                name for name in directory_names if name not in skipped_directory_names
            ]
            current = Path(current_root)
            for file_name in file_names:
                path = current / file_name
                relative = path.relative_to(project_directory)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                record = {
                    "relative_path": str(relative),
                    "size_bytes": stat.st_size,
                    "last_modified": datetime.fromtimestamp(stat.st_mtime, TZ).isoformat(
                        timespec="milliseconds"
                    ),
                    "sha256": sha256_file(path),
                }
                if len(relative.parts) >= 2 and relative.parts[0] == ".mobilework" and relative.parts[1] == "inbox":
                    inbox_files.append(record)
                else:
                    project_files.append(record)

    artifacts = {
        "inventory_captured_at": COLLECTED_AT,
        "project_directory": str(project_directory),
        "declared_output_count": len(declared_outputs),
        "declared_outputs": declared_outputs,
        "project_file_count_excluding_inbox": len(project_files),
        "project_files_excluding_inbox": project_files,
        "inbox_file_reference_count": len(inbox_files),
        "inbox_file_references": inbox_files,
        "file_content_recording": "未保存",
        "skipped_directory_names": sorted(skipped_directory_names),
    }

    root_messages = [m for m in all_messages if m["session_id"] == root_session["id"]]
    root_parts = [p for p in all_parts_original if p["session_id"] == root_session["id"]]
    root_tools = [t for t in tool_calls if t["session_id"] == root_session["id"]]
    all_tool_names = Counter(tool["tool_name"] for tool in tool_calls)
    root_tool_names = Counter(tool["tool_name"] for tool in root_tools)
    all_tool_statuses = Counter(tool["status"] for tool in tool_calls)
    root_tool_statuses = Counter(tool["status"] for tool in root_tools)
    all_part_types = Counter(part["data"].get("type") for part in all_parts_original)
    root_part_types = Counter(part["data"].get("type") for part in root_parts)
    event_types = Counter(event["event_type"] for event in lifecycle_events)
    root_event_types = Counter(
        event["event_type"]
        for event in lifecycle_events
        if event["session_id"] == root_session["id"]
    )

    durations = [tool["duration_ms"] for tool in tool_calls if isinstance(tool["duration_ms"], int)]
    root_durations = [
        tool["duration_ms"] for tool in root_tools if isinstance(tool["duration_ms"], int)
    ]
    first_input_ms = min((item["time_created"] for item in all_inputs), default=None)
    root_input_ms = min(
        (
            item["time_created"]
            for item in all_inputs
            if item["session_id"] == root_session["id"]
        ),
        default=None,
    )
    root_final_completed_values = []
    for message in root_messages:
        if message["id"] not in root_final_message_ids:
            continue
        data = message_data[message["id"]]
        completed = (data.get("time") or {}).get("completed")
        if completed is not None:
            root_final_completed_values.append(completed)
    root_final_completed_ms = max(root_final_completed_values, default=None)
    recorded_start_ms = min(row["time_created"] for row in sessions)
    recorded_end_ms = max(row["time_updated"] for row in sessions)

    root_model = parse_json(root_session["model"]) or {}
    root_agent = root_session["agent"] or "未记录"
    all_tokens = {
        "input": sum(row["tokens_input"] for row in sessions),
        "output": sum(row["tokens_output"] for row in sessions),
        "reasoning": sum(row["tokens_reasoning"] for row in sessions),
        "cache_read": sum(row["tokens_cache_read"] for row in sessions),
        "cache_write": sum(row["tokens_cache_write"] for row in sessions),
        "cost": sum(row["cost"] for row in sessions),
    }

    metrics = {
        "version_label": version_label,
        "dimension_id": task_id[0],
        "dimension_name": task_info["dimension_name"],
        "task_id": task_id,
        "task_name": task_info["task_name"],
        "selected_project_title": root_session["title"],
        "selected_attempt": (
            1
            if root_session["title"].endswith(f"-{version}")
            else int(root_session["title"].rsplit("-", 1)[1])
        ),
        "project_directory": str(project_directory),
        "root_session_id": root_session["id"],
        "runtime_version": root_session["version"],
        "agent": root_agent,
        "model_id": root_model.get("id", "未记录"),
        "provider_id": root_model.get("providerID", "未记录"),
        "root_session_created": iso(root_session["time_created"]),
        "root_session_updated": iso(root_session["time_updated"]),
        "root_session_elapsed_ms": root_session["time_updated"] - root_session["time_created"],
        "root_input_time": iso(root_input_ms),
        "root_final_answer_completed": iso(root_final_completed_ms),
        "root_answer_elapsed_ms": root_final_completed_ms - root_input_ms
        if root_final_completed_ms is not None and root_input_ms is not None
        else "未记录",
        "recorded_span_start": iso(recorded_start_ms),
        "recorded_span_end": iso(recorded_end_ms),
        "recorded_span_ms": recorded_end_ms - recorded_start_ms,
        "session_count": len(sessions),
        "child_session_count": len(descendants),
        "session_input_count": len(all_inputs),
        "control_event_count": len(all_controls),
        "root_assistant_message_count": len(root_messages),
        "all_sessions_assistant_message_count": len(all_messages),
        "root_visible_text_part_count": root_part_types.get("text", 0),
        "all_sessions_visible_text_part_count": all_part_types.get("text", 0),
        "root_reasoning_part_count": root_part_types.get("reasoning", 0),
        "all_sessions_reasoning_part_count": all_part_types.get("reasoning", 0),
        "root_step_start_part_count": root_part_types.get("step-start", 0),
        "root_step_finish_part_count": root_part_types.get("step-finish", 0),
        "all_sessions_step_start_part_count": all_part_types.get("step-start", 0),
        "all_sessions_step_finish_part_count": all_part_types.get("step-finish", 0),
        "lifecycle_event_count": len(lifecycle_events),
        "retry_event_count": event_types.get("session.next.retried.1", 0),
        "compaction_started_event_count": event_types.get(
            "session.next.compaction.started.1", 0
        ),
        "compaction_ended_event_count": event_types.get(
            "session.next.compaction.ended.1", 0
        ),
        "reasoning_started_event_count": event_types.get(
            "session.next.reasoning.started.1", 0
        ),
        "reasoning_ended_event_count": event_types.get(
            "session.next.reasoning.ended.1", 0
        ),
        "model_switched_event_count": event_types.get(
            "session.next.model.switched.1", 0
        ),
        "agent_switched_event_count": event_types.get(
            "session.next.agent.switched.1", 0
        ),
        "root_tool_call_count": len(root_tools),
        "all_sessions_tool_call_count": len(tool_calls),
        "root_tool_completed_count": root_tool_statuses.get("completed", 0),
        "root_tool_error_count": root_tool_statuses.get("error", 0),
        "all_sessions_tool_completed_count": all_tool_statuses.get("completed", 0),
        "all_sessions_tool_error_count": all_tool_statuses.get("error", 0),
        "all_sessions_tool_output_recorded_count": sum(
            tool["output_recording"] != "未记录" for tool in tool_calls
        ),
        "all_sessions_tool_duration_recorded_count": len(durations),
        "all_sessions_tool_duration_total_ms": sum(durations),
        "all_sessions_tool_duration_max_ms": max(durations) if durations else "未记录",
        "root_tool_duration_total_ms": sum(root_durations),
        "root_tool_counts_json": dict(root_tool_names),
        "all_sessions_tool_counts_json": dict(all_tool_names),
        "all_sessions_tool_status_counts_json": dict(all_tool_statuses),
        "lifecycle_event_counts_json": dict(event_types),
        "root_tokens_input": root_session["tokens_input"],
        "root_tokens_output": root_session["tokens_output"],
        "root_tokens_reasoning": root_session["tokens_reasoning"],
        "root_tokens_cache_read": root_session["tokens_cache_read"],
        "root_tokens_cache_write": root_session["tokens_cache_write"],
        "root_cost": root_session["cost"],
        "all_sessions_tokens_input": all_tokens["input"],
        "all_sessions_tokens_output": all_tokens["output"],
        "all_sessions_tokens_reasoning": all_tokens["reasoning"],
        "all_sessions_tokens_cache_read": all_tokens["cache_read"],
        "all_sessions_tokens_cache_write": all_tokens["cache_write"],
        "all_sessions_cost": all_tokens["cost"],
        "result_file_call_count": all_tool_names.get("result_file", 0),
        "declared_output_path_count": len(declared_outputs),
        "declared_output_existing_count": sum(
            item["exists_at_collection"] for item in declared_outputs
        ),
        "project_file_count_excluding_inbox": len(project_files),
        "project_file_bytes_excluding_inbox": sum(
            item["size_bytes"] for item in project_files
        ),
        "inbox_file_reference_count": len(inbox_files),
        "root_final_finish_reason": (
            message_data[next(iter(root_final_message_ids))].get("finish")
            if root_final_message_ids
            else "未记录"
        ),
    }

    # 结构化记录。
    write_json(evidence_dir / "session.json", row_with_parsed_json(root_session))
    write_jsonl(
        evidence_dir / "sessions.jsonl", [row_with_parsed_json(row) for row in sessions]
    )
    write_json(
        evidence_dir / "session_graph.json",
        {
            "root_session_id": root_session["id"],
            "session_count": len(sessions),
            "nodes": [
                {
                    "session_id": row["id"],
                    "parent_session_id": row["parent_id"] or "未记录",
                    "title": row["title"],
                    "directory": row["directory"],
                    "time_created": iso(row["time_created"]),
                    "time_updated": iso(row["time_updated"]),
                }
                for row in sessions
            ],
        },
    )
    write_jsonl(evidence_dir / "messages.jsonl", all_messages)
    write_jsonl(evidence_dir / "parts.jsonl", all_parts_saved)
    write_jsonl(evidence_dir / "tool_calls.jsonl", tool_calls)
    write_jsonl(evidence_dir / "todos.jsonl", all_todos)
    write_jsonl(evidence_dir / "session_inputs.jsonl", all_inputs)
    write_jsonl(evidence_dir / "control_events.jsonl", all_controls)
    write_json(evidence_dir / "artifacts.json", artifacts)
    write_jsonl(task_dir / "trace.jsonl", lifecycle_events)

    run = {
        "schema_version": "2.0",
        "collection_state": "已整理",
        "collected_at": COLLECTED_AT,
        "metrics": metrics,
        "trace": {
            "path": "trace.jsonl",
            "event_count": len(lifecycle_events),
            "included_event_types": "session.created.1 和 session.next.*",
            "source_order": "每个 Session 保留 event.seq；合并顺序使用事件时间、Session ID、event.seq",
        },
        "recording_scope": {
            "source_database": str(DB_PATH),
            "source_database_size_bytes": DB_PATH.stat().st_size,
            "source_database_last_modified": datetime.fromtimestamp(
                DB_PATH.stat().st_mtime, TZ
            ).isoformat(timespec="milliseconds"),
            "included": [
                "根 Session 与子 Session",
                "Assistant 消息最终状态",
                "Assistant Part 最终状态",
                "工具输入、状态、时间、输出或输出节选",
                "Todo 当前记录",
                "输入投递元数据",
                "非重复控制事件",
                "Session 生命周期事件",
                "result_file 登记路径",
                "项目文件元数据与哈希",
            ],
            "not_included": [
                "问题正文",
                "用户消息中的系统提示",
                "固定任务源素材文件内容",
                "rubric 内容",
                "全局日志中的无关 Session",
                "message.updated、message.part.updated、session.updated 流式增量事件",
            ],
            "large_text_rule": {
                "threshold_characters": LARGE_TEXT_LIMIT,
                "recording": (
                    f"超过阈值时保存字符数、SHA-256、前{EXCERPT_HEAD}字符"
                    f"和后{EXCERPT_TAIL}字符"
                ),
            },
        },
        "record_notes": [
            f"独立 reasoning Part：{'已记录' if metrics['all_sessions_reasoning_part_count'] else '未记录'}。",
            f"reasoning 生命周期事件：{'已记录' if metrics['reasoning_started_event_count'] or metrics['reasoning_ended_event_count'] else '未记录'}。",
            f"子 Session：{metrics['child_session_count']} 个。",
            "文件内容：未保存；文件路径、大小、时间与哈希按记录范围保存。",
            "cost 使用数据库原始字段值，未换算为其他费用口径。",
        ],
    }
    write_json(task_dir / "run.json", run)

    tool_errors = [tool for tool in tool_calls if tool["status"] == "error"]
    error_lines = []
    for tool in tool_errors[:10]:
        error_value = tool["error"]
        if isinstance(error_value, str):
            error_text = error_value.replace("\n", " ")[:300]
        else:
            error_text = safe_json_string(error_value)[:300]
        error_lines.append(
            f"- `{tool['time_created_iso']}` `{tool['tool_name']}` "
            f"`{tool['tool_call_id']}`：{error_text}"
        )
    if not error_lines:
        error_lines = ["- 状态为 `error` 的工具记录：未记录。"]

    child_lines = [
        f"- `{row['id']}`：{row['title']}" for row in descendants
    ] or ["- 子 Session：未记录。"]
    declared_lines = [
        f"- `{item['declared_path']}`；整理时存在：{str(item['exists_at_collection']).lower()}"
        for item in declared_outputs
    ] or ["- `result_file` 登记路径：未记录。"]

    summary = f"""# {version_label} / {task_id} 运行记录摘要

## 基本信息

- 项目名称：`{root_session['title']}`
- 项目目录：`{root_session['directory']}`
- 根 Session ID：`{root_session['id']}`
- 运行时版本：`{root_session['version']}`
- 根 Session 时间：{metrics['root_session_created']} 至 {metrics['root_session_updated']}
- 输入记录时间：{metrics['root_input_time']}
- 最终文本完成时间：{metrics['root_final_answer_completed']}

## 记录数量

- Session：{metrics['session_count']} 个，其中子 Session {metrics['child_session_count']} 个
- Assistant 消息：根 Session {metrics['root_assistant_message_count']} 条；全部 Session {metrics['all_sessions_assistant_message_count']} 条
- 可见文本 Part：根 Session {metrics['root_visible_text_part_count']} 条；全部 Session {metrics['all_sessions_visible_text_part_count']} 条
- reasoning Part：根 Session {metrics['root_reasoning_part_count']} 条；全部 Session {metrics['all_sessions_reasoning_part_count']} 条
- 生命周期事件：{metrics['lifecycle_event_count']} 条；重试事件 {metrics['retry_event_count']} 条；压缩开始事件 {metrics['compaction_started_event_count']} 条
- 工具调用：根 Session {metrics['root_tool_call_count']} 次；全部 Session {metrics['all_sessions_tool_call_count']} 次
- 全部 Session 工具状态：{safe_json_string(dict(all_tool_statuses))}
- 全部 Session 工具记录耗时合计：{metrics['all_sessions_tool_duration_total_ms']} 毫秒；有耗时记录 {metrics['all_sessions_tool_duration_recorded_count']} 次

## 工具记录

- 工具名称分布：{safe_json_string(dict(all_tool_names))}
- 根 Session 工具顺序：{compress_sequence([tool['tool_name'] for tool in root_tools])}

## Token 与 cost 字段

- 根 Session：input {metrics['root_tokens_input']}，output {metrics['root_tokens_output']}，reasoning {metrics['root_tokens_reasoning']}，cache read {metrics['root_tokens_cache_read']}，cache write {metrics['root_tokens_cache_write']}，cost {metrics['root_cost']}
- 全部 Session：input {metrics['all_sessions_tokens_input']}，output {metrics['all_sessions_tokens_output']}，reasoning {metrics['all_sessions_tokens_reasoning']}，cache read {metrics['all_sessions_tokens_cache_read']}，cache write {metrics['all_sessions_tokens_cache_write']}，cost {metrics['all_sessions_cost']}

## 子 Session

{chr(10).join(child_lines)}

## 交付与项目文件记录

{chr(10).join(declared_lines)}

- 项目文件（不含 `.mobilework/inbox`）：{metrics['project_file_count_excluding_inbox']} 个
- `.mobilework/inbox` 文件引用：{metrics['inbox_file_reference_count']} 个
- 文件内容：未保存

## 状态为 error 的工具记录

{chr(10).join(error_lines)}

## 保存状态

- 问题正文：未包含
- 用户消息中的系统提示：未包含
- 固定任务源素材与 rubric：未保存
- 超过 {LARGE_TEXT_LIMIT} 字符的单项文本：节选保存，并记录字符数和 SHA-256
- Assistant 可见文本、工具参数、状态、时间和最终输出状态：按 `evidence` 目录说明保存
"""
    atomic_write_text(task_dir / "summary.md", summary)

    readme = f"""# {task_id} 记录文件说明

本目录对应 `{root_session['title']}`。根 Session 与其子 Session 使用 `session_id`、`parent_session_id`、`message_id`、`part_id` 和 `tool_call_id` 关联。

- `session.json`：根 Session 的最终记录。
- `sessions.jsonl`：根 Session 与所有子 Session，每行一条。
- `session_graph.json`：Session 父子关系及起止时间。
- `messages.jsonl`：Assistant 消息最终状态；用户消息正文未包含。
- `parts.jsonl`：Assistant Part 最终状态，包含可见文本、步骤和工具状态。
- `tool_calls.jsonl`：每次工具调用的名称、参数、状态、时间、输出保存状态和 Part 引用。
- `todos.jsonl`：数据库中保留的 Todo 当前记录；历史变化仍可从 `parts.jsonl` 的 `todowrite` 调用读取。
- `session_inputs.jsonl`：输入投递方式和序号；输入正文未包含。
- `control_events.jsonl`：不与消息正文重复的 Session 控制记录。
- `artifacts.json`：`result_file` 登记路径、整理时文件状态、项目文件元数据；文件内容未保存。
- `../trace.jsonl`：按时间合并的生命周期事件，保留每个 Session 的原始事件序号。

超过 {LARGE_TEXT_LIMIT} 字符的单项文本保存为节选，同时记录完整文本的字符数与 SHA-256。生命周期事件中的工具结果和可见文本不重复保存，使用 ID 指向 `parts.jsonl` 或 `tool_calls.jsonl`。
"""
    atomic_write_text(evidence_dir / "README.md", readme)

    manifest_path = evidence_dir / "evidence_manifest.json"
    evidence_files = sorted(
        path for path in evidence_dir.iterdir() if path.is_file() and path != manifest_path
    )
    write_json(
        manifest_path,
        {
            "collected_at": COLLECTED_AT,
            "root_session_id": root_session["id"],
            "files": [
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in evidence_files
            ],
        },
    )
    return metrics, task_dir


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            output = dict(row)
            for key, value in output.items():
                if isinstance(value, (dict, list)):
                    output[key] = safe_json_string(value)
            writer.writerow(output)
    os.replace(temp, path)


def write_metrics_guide(fields):
    descriptions = {
        "version_label": ("版本目录标识", "Session 标题映射"),
        "dimension_id": ("任务编号首字母", "固定任务目录"),
        "dimension_name": ("维度目录名称中的名称部分", "固定任务目录"),
        "task_id": ("任务编号", "固定任务目录与 Session 标题"),
        "task_name": ("任务目录名称中的任务名称", "固定任务目录"),
        "selected_project_title": ("实际采用的项目标题；存在次数后缀时采用最高次数", "session.title"),
        "selected_attempt": ("采用的运行次数；无次数后缀时为 1", "session.title"),
        "project_directory": ("项目目录绝对路径", "session.directory"),
        "root_session_id": ("该任务根 Session ID", "session.id"),
        "runtime_version": ("Session 记录的运行时版本", "session.version"),
        "agent": ("根 Session 的 Agent 标识", "session.agent"),
        "model_id": ("根 Session 的模型 ID", "session.model.id"),
        "provider_id": ("根 Session 的模型提供方 ID", "session.model.providerID"),
        "root_session_created": ("根 Session 创建时间，ISO 8601", "session.time_created"),
        "root_session_updated": ("根 Session 最后更新时间，ISO 8601", "session.time_updated"),
        "root_session_elapsed_ms": ("根 Session 最后更新时间减创建时间，毫秒", "session 时间字段计算"),
        "root_input_time": ("根 Session 首条输入记录时间", "session_input.time_created"),
        "root_final_answer_completed": ("根 Session 中 finish=stop 消息的完成时间", "message.data.time.completed"),
        "root_answer_elapsed_ms": ("最终文本完成时间减首条输入时间，毫秒", "对应时间字段计算"),
        "recorded_span_start": ("根与子 Session 中最早的创建时间", "sessions"),
        "recorded_span_end": ("根与子 Session 中最晚的更新时间", "sessions"),
        "recorded_span_ms": ("recorded_span_end 与 start 的差值，毫秒", "sessions 时间字段计算"),
        "session_count": ("根 Session 加全部后代 Session 数量", "session.parent_id"),
        "child_session_count": ("全部后代 Session 数量", "session.parent_id"),
        "session_input_count": ("全部 Session 的输入记录数", "session_input"),
        "control_event_count": ("非 user/assistant 的 session_message 记录数", "session_message"),
        "root_assistant_message_count": ("根 Session 的 Assistant 消息数", "message"),
        "all_sessions_assistant_message_count": ("根与子 Session 的 Assistant 消息总数", "message"),
        "root_visible_text_part_count": ("根 Session 中 type=text 的 Assistant Part 数", "part"),
        "all_sessions_visible_text_part_count": ("全部 Session 中 type=text 的 Assistant Part 数", "part"),
        "root_reasoning_part_count": ("根 Session 中 type=reasoning 的 Part 数", "part"),
        "all_sessions_reasoning_part_count": ("全部 Session 中 type=reasoning 的 Part 数", "part"),
        "root_step_start_part_count": ("根 Session 的 step-start Part 数", "part"),
        "root_step_finish_part_count": ("根 Session 的 step-finish Part 数", "part"),
        "all_sessions_step_start_part_count": ("全部 Session 的 step-start Part 数", "part"),
        "all_sessions_step_finish_part_count": ("全部 Session 的 step-finish Part 数", "part"),
        "lifecycle_event_count": ("保存到 trace.jsonl 的生命周期事件数", "event"),
        "retry_event_count": ("session.next.retried.1 事件数", "event"),
        "compaction_started_event_count": ("session.next.compaction.started.1 事件数", "event"),
        "compaction_ended_event_count": ("session.next.compaction.ended.1 事件数", "event"),
        "reasoning_started_event_count": ("session.next.reasoning.started.1 事件数", "event"),
        "reasoning_ended_event_count": ("session.next.reasoning.ended.1 事件数", "event"),
        "model_switched_event_count": ("session.next.model.switched.1 事件数", "event"),
        "agent_switched_event_count": ("session.next.agent.switched.1 事件数", "event"),
        "root_tool_call_count": ("根 Session 的工具 Part 数", "part"),
        "all_sessions_tool_call_count": ("全部 Session 的工具 Part 数", "part"),
        "root_tool_completed_count": ("根 Session 中 status=completed 的工具数", "part.data.state.status"),
        "root_tool_error_count": ("根 Session 中 status=error 的工具数", "part.data.state.status"),
        "all_sessions_tool_completed_count": ("全部 Session 中 status=completed 的工具数", "part.data.state.status"),
        "all_sessions_tool_error_count": ("全部 Session 中 status=error 的工具数", "part.data.state.status"),
        "all_sessions_tool_output_recorded_count": ("工具状态中带 output 字段的调用数", "part.data.state"),
        "all_sessions_tool_duration_recorded_count": ("同时记录 start/end 的工具调用数", "part.data.state.time"),
        "all_sessions_tool_duration_total_ms": ("有时间记录的工具耗时之和，毫秒；并行调用会分别累计", "工具时间字段计算"),
        "all_sessions_tool_duration_max_ms": ("单次工具调用的最大记录耗时，毫秒", "工具时间字段计算"),
        "root_tool_duration_total_ms": ("根 Session 有时间记录的工具耗时之和，毫秒", "工具时间字段计算"),
        "root_tool_counts_json": ("根 Session 各工具名称与次数的 JSON 对象", "part"),
        "all_sessions_tool_counts_json": ("全部 Session 各工具名称与次数的 JSON 对象", "part"),
        "all_sessions_tool_status_counts_json": ("全部 Session 各工具状态与次数的 JSON 对象", "part"),
        "lifecycle_event_counts_json": ("各生命周期事件类型与次数的 JSON 对象", "event"),
        "root_tokens_input": ("根 Session 累计 input Token", "session.tokens_input"),
        "root_tokens_output": ("根 Session 累计 output Token", "session.tokens_output"),
        "root_tokens_reasoning": ("根 Session 累计 reasoning Token", "session.tokens_reasoning"),
        "root_tokens_cache_read": ("根 Session 累计 cache read Token", "session.tokens_cache_read"),
        "root_tokens_cache_write": ("根 Session 累计 cache write Token", "session.tokens_cache_write"),
        "root_cost": ("根 Session 的原始 cost 字段，不另行换算", "session.cost"),
        "all_sessions_tokens_input": ("根与子 Session 的 input Token 合计", "sessions 汇总"),
        "all_sessions_tokens_output": ("根与子 Session 的 output Token 合计", "sessions 汇总"),
        "all_sessions_tokens_reasoning": ("根与子 Session 的 reasoning Token 合计", "sessions 汇总"),
        "all_sessions_tokens_cache_read": ("根与子 Session 的 cache read Token 合计", "sessions 汇总"),
        "all_sessions_tokens_cache_write": ("根与子 Session 的 cache write Token 合计", "sessions 汇总"),
        "all_sessions_cost": ("根与子 Session 的原始 cost 字段合计", "sessions 汇总"),
        "result_file_call_count": ("全部 Session 中 result_file 工具调用数", "part"),
        "declared_output_path_count": ("result_file.output_paths 中登记的路径数", "tool input"),
        "declared_output_existing_count": ("整理时仍存在的登记路径数", "文件系统检查"),
        "project_file_count_excluding_inbox": ("项目目录文件数，不含 .mobilework/inbox", "文件系统清单"),
        "project_file_bytes_excluding_inbox": ("上述文件大小合计，字节", "文件系统清单"),
        "inbox_file_reference_count": (".mobilework/inbox 下的文件引用数", "文件系统清单"),
        "root_final_finish_reason": ("根 Session 最终消息的 finish 字段", "message.data.finish"),
    }
    lines = [
        "# metrics.csv 字段说明",
        "",
        "各版本的 `metrics.csv` 使用相同列。每行对应一个选定任务，所有计数和数值均来自已保存记录或明确列出的计算。",
        "",
        "## 记录约定",
        "",
        "- 时间使用 ISO 8601，并保留本机时区偏移。耗时单位为毫秒。",
        "- `root_*` 只统计根 Session；`all_sessions_*` 统计根 Session 及全部后代 Session。",
        "- `*_json` 在 CSV 单元格中保存 JSON 对象，键是名称，值是出现次数。",
        "- `未记录` 表示源记录中没有对应值；`未包含` 表示整理范围主动不保存该内容。",
        "- 数值 0 是数据库或计数得到的实际值，不替换为 `未记录`。",
        "- 工具 `completed`、`error` 等状态按原始字段保存，不增加其他状态判断。",
        "",
        "## 字段表",
        "",
        "| 字段 | 含义与读取方式 | 来源 |",
        "|---|---|---|",
    ]
    for field in fields:
        meaning, source = descriptions[field]
        lines.append(f"| `{field}` | {meaning} | `{source}` |")
    atomic_write_text(TARGET / "metrics字段说明.md", "\n".join(lines) + "\n")


def write_collection_guide(total_metrics):
    total_sessions = sum(row["session_count"] for row in total_metrics)
    total_events = sum(row["lifecycle_event_count"] for row in total_metrics)
    total_tools = sum(row["all_sessions_tool_call_count"] for row in total_metrics)
    total_files = sum(row["project_file_count_excluding_inbox"] for row in total_metrics)
    lines = [
        "# Mobilework 日志整理说明",
        "",
        "## 整理范围",
        "",
        f"本次整理包含 {', '.join(f'`mw-{version}`' for version in VERSIONS)}，每个版本 {EXPECTED_TASK_COUNT} 个任务，共 {len(total_metrics)} 个根 Session。完成时间：{COLLECTED_AT}。",
        "",
        "同一任务存在多个测试次数后缀时，采用最高次数对应的根 Session。项目路径映射记录在 `版本_项目编号.md`。固定任务目录只用于取得维度和任务目录名称，没有向其中写入文件。",
        "",
        "## 目录结构",
        "",
        "```text",
        "version-log/",
        "├─ 版本_项目编号.md",
        "├─ metrics字段说明.md",
        "├─ 日志整理说明.md",
        "└─ mw-<版本>/（各版本结构相同）",
        "   ├─ manifest.json",
        "   ├─ metrics.csv",
        "   └─ <维度>/<任务>/",
        "      ├─ summary.md",
        "      ├─ run.json",
        "      ├─ trace.jsonl",
        "      └─ evidence/",
        "```",
        "",
        "## 每个任务的文件",
        "",
        "- `summary.md`：项目、时间、Session、消息、Part、工具、Token、交付路径和记录状态的可读摘要。",
        "- `run.json`：该任务的完整汇总字段、保存范围、文本节选规则和记录状态。",
        "- `trace.jsonl`：Session 生命周期事件，每行一条，包含合并序号、原始事件序号、Session 关系、时间和事件数据。",
        "- `evidence/session.json`：根 Session 最终记录。",
        "- `evidence/sessions.jsonl`：根 Session 与全部后代 Session。",
        "- `evidence/session_graph.json`：Session 父子关系。",
        "- `evidence/messages.jsonl`：Assistant 消息最终状态。",
        "- `evidence/parts.jsonl`：Assistant Part 最终状态。",
        "- `evidence/tool_calls.jsonl`：工具调用的统一记录。",
        "- `evidence/todos.jsonl`：Todo 当前记录。",
        "- `evidence/session_inputs.jsonl`：输入投递方式及序号，不含输入正文。",
        "- `evidence/control_events.jsonl`：非 user/assistant 的 Session 控制记录。",
        "- `evidence/artifacts.json`：登记的交付路径和项目文件元数据，不复制文件内容。",
        "- `evidence/evidence_manifest.json`：证据文件大小与 SHA-256。",
        "- `evidence/README.md`：以上文件在该任务目录中的对应说明。",
        "",
        "## Trace 记录方式",
        "",
        "`trace.jsonl` 从数据库 `event` 表按 Session ID 读取。保存 `session.created.1` 和全部 `session.next.*` 生命周期事件，包括输入、Step、文本、reasoning、工具、重试、压缩、Agent 切换和模型切换等实际存在的事件。",
        "",
        "高频的 `message.updated`、`message.part.updated` 和 `session.updated` 流式增量事件未重复保存；消息与 Part 的最终状态分别保存在 `messages.jsonl` 和 `parts.jsonl`。工具成功事件中的大段结果以及文本结束事件中的正文不在 Trace 内重复保存，而是通过 ID 指向 Part 和工具记录。",
        "",
        "多个 Session 合并时，`trace_sequence` 使用事件时间、Session ID、原始事件序号排序；`source_seq` 始终保留数据库中每个 Session 自己的原始序号。",
        "",
        "## 文本与文件保存方式",
        "",
        f"- 单项文本不超过 {LARGE_TEXT_LIMIT} 字符时完整保存。",
        f"- 超过 {LARGE_TEXT_LIMIT} 字符时保存完整字符数、SHA-256、前 {EXCERPT_HEAD} 字符和后 {EXCERPT_TAIL} 字符。",
        "- 问题正文、用户消息中的系统提示：未包含。",
        "- 固定任务源素材文件内容、rubric 内容：未保存。",
        "- 项目文件内容：未复制；保存路径、大小、时间和 SHA-256。",
        "- `.git`、`node_modules`、`.venv`、`__pycache__` 目录：文件清单未包含。",
        f"- 与 {len(total_metrics)} 个选定根 Session 无关的全局记录：未包含。",
        "",
        "## 空值与状态写法",
        "",
        "源记录没有对应字段时使用 `未记录`；主动不保存的内容使用 `未包含` 或 `未保存`。数据库中的原始状态值（如 `completed`、`error`、`stop`）保持原样。数值 0 保持为 0。",
        "",
        "## 版本级文件",
        "",
        f"- `manifest.json`：{EXPECTED_TASK_COUNT} 个任务的编号、目录、项目名称、采用次数、根 Session、Session 数量和整理状态。",
        f"- `metrics.csv`：{EXPECTED_TASK_COUNT} 个任务的统一数值与计数字段，字段含义见根目录 `metrics字段说明.md`。",
        "",
        "## 本次记录数量",
        "",
        f"- 根 Session：{len(total_metrics)} 个",
        f"- 根与子 Session 合计：{total_sessions} 个",
        f"- 生命周期事件：{total_events} 条",
        f"- 工具调用：{total_tools} 次",
        f"- 项目文件清单（不含 inbox）：{total_files} 项",
        "",
        "## 一致性检查",
        "",
        "整理完成后应运行结构校验和源数据库复核，检查任务数量、目录映射、Session 父子关系、JSON/JSONL、Trace 序号、CSV、登记路径和证据文件哈希。校验默认只输出到控制台。",
    ]
    atomic_write_text(TARGET / "日志整理说明.md", "\n".join(lines) + "\n")


conn = sqlite3.connect(DB_URI, uri=True)
conn.row_factory = sqlite3.Row
conn.execute("BEGIN")
tasks, dimension_rows = find_dataset_tasks()
selected, candidates = select_root_sessions(conn)
if TARGET.exists() and any(TARGET.iterdir()):
    raise SystemExit(
        f"输出目录不是空目录：{TARGET}。请使用新的输出目录，或在确认后另行处理旧目录。"
    )
TARGET.mkdir(parents=True, exist_ok=True)

# 路径索引。
index_lines = [
    "# Mobilework 版本与项目路径",
    "",
    "同一任务存在多个测试次数后缀时，采用最高次数对应的项目目录。",
    "",
]
for version in VERSIONS:
    index_lines.extend([f"## mw-{version}", "", "```text"])
    for dimension_name, task_ids in dimension_rows:
        for task_id in task_ids:
            row = selected[version][task_id]
            index_lines.append(f"{task_id}：{row['directory'].replace('/', '\\')}")
        index_lines.append("")
    if index_lines[-1] == "":
        index_lines.pop()
    index_lines.extend(["```", ""])
atomic_write_text(TARGET / "版本_项目编号.md", "\n".join(index_lines))

all_metrics = []
metric_fields = None
for version in VERSIONS:
    version_metrics = []
    manifest_tasks = []
    for dimension_name, task_ids in dimension_rows:
        for task_id in task_ids:
            metrics, output_dir = collect_task(
                conn, version, task_id, tasks[task_id], selected[version][task_id]
            )
            version_metrics.append(metrics)
            all_metrics.append(metrics)
            manifest_tasks.append(
                {
                    "task_id": task_id,
                    "task_directory": str(output_dir.relative_to(TARGET / f"mw-{version}")),
                    "selected_project_title": metrics["selected_project_title"],
                    "selected_attempt": metrics["selected_attempt"],
                    "project_directory": metrics["project_directory"],
                    "root_session_id": metrics["root_session_id"],
                    "session_count": metrics["session_count"],
                    "collection_state": "已整理",
                }
            )
            print(
                f"{version} {task_id} sessions={metrics['session_count']} "
                f"events={metrics['lifecycle_event_count']} tools={metrics['all_sessions_tool_call_count']}"
            )
    metric_fields = list(version_metrics[0].keys())
    write_csv(TARGET / f"mw-{version}" / "metrics.csv", version_metrics, metric_fields)
    write_json(
        TARGET / f"mw-{version}" / "manifest.json",
        {
            "schema_version": "2.0",
            "version": f"mw-{version}",
            "collection_state": "已整理",
            "collected_at": COLLECTED_AT,
            "task_count": len(manifest_tasks),
            "tasks": manifest_tasks,
        },
    )

write_metrics_guide(metric_fields)
write_collection_guide(all_metrics)
conn.rollback()
conn.close()
print(f"DONE tasks={len(all_metrics)}")
