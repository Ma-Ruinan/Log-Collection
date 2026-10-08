import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path


parser = argparse.ArgumentParser(description="将 MobileWork 日志整理结果与源数据库交叉复核")
parser.add_argument("--config", required=True, help="与收集脚本相同的 UTF-8 JSON 配置")
parser.add_argument("--report", help="可选：把机器可读结果写入指定路径")
args = parser.parse_args()
CONFIG = json.loads(Path(args.config).read_text(encoding="utf-8"))
DB_PATH = Path(CONFIG["database"]).expanduser().resolve()
DB_URI = DB_PATH.as_uri() + "?mode=ro"
ROOT = Path(CONFIG["output"]).expanduser().resolve()
VERSIONS = tuple(str(value) for value in CONFIG["versions"])
EXPECTED_TASK_COUNT = int(CONFIG.get("expected_task_count", 30))
LIMIT = int(CONFIG.get("large_text_limit", 20_000))
HEAD = int(CONFIG.get("excerpt_head", 5_000))
TAIL = int(CONFIG.get("excerpt_tail", 1_000))
TZ = datetime.now().astimezone().tzinfo
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}


def iso(ms):
    if ms is None:
        return "未记录"
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(TZ).isoformat(
        timespec="milliseconds"
    )


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse(value):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_matches(saved, source):
    if isinstance(source, str) and len(source) > LIMIT:
        return (
            isinstance(saved, dict)
            and saved.get("recording") == "节选保存"
            and saved.get("character_count") == len(source)
            and saved.get("sha256") == sha_text(source)
            and saved.get("head") == source[:HEAD]
            and saved.get("tail") == source[-TAIL:]
        )
    if isinstance(source, dict):
        return (
            isinstance(saved, dict)
            and set(saved) == set(source)
            and all(compact_matches(saved[key], value) for key, value in source.items())
        )
    if isinstance(source, list):
        return (
            isinstance(saved, list)
            and len(saved) == len(source)
            and all(compact_matches(a, b) for a, b in zip(saved, source))
        )
    return saved == source


def descendants(conn, root_id):
    rows = []
    seen = {root_id}
    queue = deque([root_id])
    while queue:
        parent = queue.popleft()
        for row in conn.execute(
            "select * from session where parent_id=? order by time_created,id", (parent,)
        ):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
            queue.append(row["id"])
    return rows


def normalize_csv_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def metric_values_equal(field, csv_value, expected):
    """Compare serialized metric values by meaning, not presentation details."""
    if field == "project_directory":
        return csv_value.replace("\\", "/").casefold() == str(expected).replace("\\", "/").casefold()
    if field.endswith("_json"):
        try:
            return json.loads(csv_value) == expected
        except (TypeError, json.JSONDecodeError):
            return False
    return normalize_csv_value(expected) == csv_value


conn = sqlite3.connect(DB_URI, uri=True)
conn.row_factory = sqlite3.Row
conn.execute("BEGIN")
discrepancies = []
checks = Counter()
task_summaries = []


def check(condition, code, detail):
    checks[code] += 1
    if not condition:
        discrepancies.append({"code": code, "detail": detail})


for version in VERSIONS:
    version_dir = ROOT / f"mw-{version}"
    manifest = read_json(version_dir / "manifest.json")
    manifest_map = {item["task_id"]: item for item in manifest["tasks"]}
    with (version_dir / "metrics.csv").open(encoding="utf-8-sig", newline="") as handle:
        metric_rows = list(csv.DictReader(handle))
    metric_map = {row["task_id"]: row for row in metric_rows}

    check(len(manifest_map) == EXPECTED_TASK_COUNT, "version_task_count", f"mw-{version} manifest")
    check(len(metric_map) == EXPECTED_TASK_COUNT, "version_metrics_count", f"mw-{version} metrics")

    for task_id, item in manifest_map.items():
        metrics_csv = metric_map[task_id]
        task_dir = version_dir / item["task_directory"]
        run = read_json(task_dir / "run.json")
        metrics = run["metrics"]
        root_id = item["root_session_id"]
        root_session = conn.execute("select * from session where id=?", (root_id,)).fetchone()
        check(root_session is not None, "root_session_exists", f"{version}/{task_id}")
        if root_session is None:
            continue

        expected_title = re.compile(rf"^{re.escape(task_id)}-{re.escape(version)}(?:-(\d+))?$")
        check(bool(expected_title.match(root_session["title"])), "root_title", f"{version}/{task_id}")
        candidate_attempts = []
        for candidate in conn.execute(
            "select title from session where parent_id is null and (title=? or title like ?)",
            (f"{task_id}-{version}", f"{task_id}-{version}-%"),
        ):
            match = expected_title.match(candidate["title"])
            if match:
                candidate_attempts.append(int(match.group(1) or 1))
        selected_match = expected_title.match(root_session["title"])
        selected_attempt = int(selected_match.group(1) or 1) if selected_match else -1
        check(
            bool(candidate_attempts) and selected_attempt == max(candidate_attempts),
            "highest_attempt_selection",
            f"{version}/{task_id}",
        )
        check(
            Path(root_session["directory"]).is_dir(),
            "project_directory_exists",
            f"{version}/{task_id}",
        )
        check(
            item["project_directory"].replace("\\", "/")
            == root_session["directory"].replace("\\", "/"),
            "manifest_project_directory",
            f"{version}/{task_id}",
        )

        child_rows = descendants(conn, root_id)
        session_rows = [root_session, *child_rows]
        session_ids = [row["id"] for row in session_rows]
        session_map = {row["id"]: row for row in session_rows}
        saved_sessions = read_jsonl(task_dir / "evidence" / "sessions.jsonl")
        saved_graph = read_json(task_dir / "evidence" / "session_graph.json")
        check(
            {row["id"] for row in session_rows}
            == {row["id"] for row in saved_sessions},
            "session_set",
            f"{version}/{task_id}",
        )
        check(
            saved_graph["session_count"] == len(session_rows),
            "session_graph_count",
            f"{version}/{task_id}",
        )
        check(
            {(node["session_id"], node["parent_session_id"]) for node in saved_graph["nodes"]}
            == {
                (row["id"], row["parent_id"] or "未记录") for row in session_rows
            },
            "session_graph_edges",
            f"{version}/{task_id}",
        )

        source_messages = []
        source_message_data = {}
        assistant_ids = set()
        for sid in session_ids:
            for row in conn.execute(
                "select * from message where session_id=? order by time_created,id", (sid,)
            ):
                data = parse(row["data"])
                source_message_data[row["id"]] = data
                if data.get("role") == "assistant":
                    assistant_ids.add(row["id"])
                    source_messages.append((row, data))
        saved_messages = read_jsonl(task_dir / "evidence" / "messages.jsonl")
        check(len(saved_messages) == len(source_messages), "assistant_message_count", f"{version}/{task_id}")
        source_message_map = {row["id"]: (row, data) for row, data in source_messages}
        check(
            set(source_message_map) == {row["id"] for row in saved_messages},
            "assistant_message_set",
            f"{version}/{task_id}",
        )
        for saved in saved_messages:
            source_row, source_data = source_message_map[saved["id"]]
            check(
                compact_matches(saved["data"], source_data),
                "message_content",
                f"{version}/{task_id}/{saved['id']}",
            )
            for field in ("session_id", "time_created", "time_updated"):
                check(
                    saved[field] == source_row[field],
                    "message_metadata",
                    f"{version}/{task_id}/{saved['id']}/{field}",
                )

        source_parts = []
        for sid in session_ids:
            for row in conn.execute(
                "select * from part where session_id=? order by time_created,id", (sid,)
            ):
                if row["message_id"] in assistant_ids:
                    source_parts.append((row, parse(row["data"])))
        saved_parts = read_jsonl(task_dir / "evidence" / "parts.jsonl")
        source_part_map = {row["id"]: (row, data) for row, data in source_parts}
        check(len(saved_parts) == len(source_parts), "part_count", f"{version}/{task_id}")
        check(
            set(source_part_map) == {row["id"] for row in saved_parts},
            "part_set",
            f"{version}/{task_id}",
        )
        for saved in saved_parts:
            source_row, source_data = source_part_map[saved["id"]]
            check(
                compact_matches(saved["data"], source_data),
                "part_content",
                f"{version}/{task_id}/{saved['id']}",
            )

        source_tools = [
            (row, data) for row, data in source_parts if data.get("type") == "tool"
        ]
        saved_tools = read_jsonl(task_dir / "evidence" / "tool_calls.jsonl")
        source_tool_map = {row["id"]: (row, data) for row, data in source_tools}
        check(len(saved_tools) == len(source_tools), "tool_count", f"{version}/{task_id}")
        check(
            set(source_tool_map) == {row["part_id"] for row in saved_tools},
            "tool_set",
            f"{version}/{task_id}",
        )
        for saved in saved_tools:
            source_row, data = source_tool_map[saved["part_id"]]
            state = data.get("state") or {}
            timing = state.get("time") or {}
            expected_duration = (
                timing["end"] - timing["start"]
                if timing.get("start") is not None and timing.get("end") is not None
                else "未记录"
            )
            check(saved["tool_name"] == data.get("tool", "未记录"), "tool_name", f"{version}/{task_id}/{saved['part_id']}")
            check(saved["tool_call_id"] == data.get("callID", "未记录"), "tool_call_id", f"{version}/{task_id}/{saved['part_id']}")
            check(saved["status"] == state.get("status", "未记录"), "tool_status", f"{version}/{task_id}/{saved['part_id']}")
            check(saved["duration_ms"] == expected_duration, "tool_duration", f"{version}/{task_id}/{saved['part_id']}")
            check(
                compact_matches(saved["input"], state.get("input", "未记录")),
                "tool_input",
                f"{version}/{task_id}/{saved['part_id']}",
            )
            if "output" in state:
                output = state["output"]
                serialized = output if isinstance(output, str) else json.dumps(
                    output, ensure_ascii=False, separators=(",", ":")
                )
                check(
                    saved["output_character_count"] == len(serialized),
                    "tool_output_length",
                    f"{version}/{task_id}/{saved['part_id']}",
                )
                check(
                    saved["output_sha256"] == sha_text(serialized),
                    "tool_output_hash",
                    f"{version}/{task_id}/{saved['part_id']}",
                )
                check(
                    compact_matches(saved["output"], output),
                    "tool_output_content",
                    f"{version}/{task_id}/{saved['part_id']}",
                )
            else:
                check(saved["output_recording"] == "未记录", "tool_output_absent", f"{version}/{task_id}/{saved['part_id']}")

        saved_trace = read_jsonl(task_dir / "trace.jsonl")
        source_events = []
        for sid in session_ids:
            source_events.extend(
                conn.execute(
                    "select id,aggregate_id,seq,type,data from event where aggregate_id=? "
                    "and (type='session.created.1' or type like 'session.next.%')",
                    (sid,),
                ).fetchall()
            )
        check(len(saved_trace) == len(source_events), "trace_event_count", f"{version}/{task_id}")
        check(
            {row["id"] for row in source_events}
            == {event["source_event_id"] for event in saved_trace},
            "trace_event_set",
            f"{version}/{task_id}",
        )
        check(
            [event["trace_sequence"] for event in saved_trace]
            == list(range(1, len(saved_trace) + 1)),
            "trace_sequence",
            f"{version}/{task_id}",
        )
        trace_map = {event["source_event_id"]: event for event in saved_trace}
        for source_event in source_events:
            saved = trace_map[source_event["id"]]
            check(saved["source_seq"] == source_event["seq"], "trace_source_seq", f"{version}/{task_id}/{source_event['id']}")
            check(saved["event_type"] == source_event["type"], "trace_event_type", f"{version}/{task_id}/{source_event['id']}")
            if source_event["type"] == "session.next.prompted.1":
                check(
                    saved["data"].get("prompt", {}).get("text") == "未包含",
                    "trace_prompt_omitted",
                    f"{version}/{task_id}/{source_event['id']}",
                )
            if source_event["type"] == "session.next.tool.success.1":
                check("content" not in saved["data"] and "result" not in saved["data"], "trace_tool_result_deduplicated", f"{version}/{task_id}/{source_event['id']}")
            if source_event["type"] == "session.next.text.ended.1":
                check("text" not in saved["data"], "trace_text_deduplicated", f"{version}/{task_id}/{source_event['id']}")

        # Direct metric recomputation.
        root_messages = [
            (row, data) for row, data in source_messages if row["session_id"] == root_id
        ]
        root_parts = [(row, data) for row, data in source_parts if row["session_id"] == root_id]
        root_tools = [(row, data) for row, data in source_tools if row["session_id"] == root_id]
        all_part_types = Counter(data.get("type") for _, data in source_parts)
        root_part_types = Counter(data.get("type") for _, data in root_parts)
        all_tool_names = Counter(data.get("tool") for _, data in source_tools)
        root_tool_names = Counter(data.get("tool") for _, data in root_tools)
        all_tool_statuses = Counter((data.get("state") or {}).get("status") for _, data in source_tools)
        root_tool_statuses = Counter((data.get("state") or {}).get("status") for _, data in root_tools)
        all_event_types = Counter(row["type"] for row in source_events)
        durations = []
        root_durations = []
        for row, data in source_tools:
            timing = (data.get("state") or {}).get("time") or {}
            if timing.get("start") is not None and timing.get("end") is not None:
                duration = timing["end"] - timing["start"]
                durations.append(duration)
                if row["session_id"] == root_id:
                    root_durations.append(duration)
        inputs = []
        controls = []
        for sid in session_ids:
            inputs.extend(conn.execute("select * from session_input where session_id=?", (sid,)).fetchall())
            controls.extend(conn.execute("select * from session_message where session_id=? and type not in ('user','assistant')", (sid,)).fetchall())
        root_inputs = [row for row in inputs if row["session_id"] == root_id]
        root_input_ms = min((row["time_created"] for row in root_inputs), default=None)
        final_completed = []
        final_finishes = []
        for row, data in root_messages:
            if data.get("finish") == "stop":
                final_finishes.append(data.get("finish"))
                completed = (data.get("time") or {}).get("completed")
                if completed is not None:
                    final_completed.append(completed)
        final_ms = max(final_completed, default=None)
        root_model = parse(root_session["model"]) or {}
        direct = {
            "selected_project_title": root_session["title"],
            "selected_attempt": selected_attempt,
            "project_directory": root_session["directory"],
            "root_session_id": root_id,
            "runtime_version": root_session["version"],
            "agent": root_session["agent"] or "未记录",
            "model_id": root_model.get("id", "未记录"),
            "provider_id": root_model.get("providerID", "未记录"),
            "root_session_created": iso(root_session["time_created"]),
            "root_session_updated": iso(root_session["time_updated"]),
            "root_session_elapsed_ms": root_session["time_updated"] - root_session["time_created"],
            "root_input_time": iso(root_input_ms),
            "root_final_answer_completed": iso(final_ms),
            "root_answer_elapsed_ms": final_ms - root_input_ms if final_ms is not None and root_input_ms is not None else "未记录",
            "recorded_span_start": iso(min(row["time_created"] for row in session_rows)),
            "recorded_span_end": iso(max(row["time_updated"] for row in session_rows)),
            "recorded_span_ms": max(row["time_updated"] for row in session_rows) - min(row["time_created"] for row in session_rows),
            "session_count": len(session_rows),
            "child_session_count": len(child_rows),
            "session_input_count": len(inputs),
            "control_event_count": len(controls),
            "root_assistant_message_count": len(root_messages),
            "all_sessions_assistant_message_count": len(source_messages),
            "root_visible_text_part_count": root_part_types.get("text", 0),
            "all_sessions_visible_text_part_count": all_part_types.get("text", 0),
            "root_reasoning_part_count": root_part_types.get("reasoning", 0),
            "all_sessions_reasoning_part_count": all_part_types.get("reasoning", 0),
            "root_step_start_part_count": root_part_types.get("step-start", 0),
            "root_step_finish_part_count": root_part_types.get("step-finish", 0),
            "all_sessions_step_start_part_count": all_part_types.get("step-start", 0),
            "all_sessions_step_finish_part_count": all_part_types.get("step-finish", 0),
            "lifecycle_event_count": len(source_events),
            "retry_event_count": all_event_types.get("session.next.retried.1", 0),
            "compaction_started_event_count": all_event_types.get("session.next.compaction.started.1", 0),
            "compaction_ended_event_count": all_event_types.get("session.next.compaction.ended.1", 0),
            "reasoning_started_event_count": all_event_types.get("session.next.reasoning.started.1", 0),
            "reasoning_ended_event_count": all_event_types.get("session.next.reasoning.ended.1", 0),
            "model_switched_event_count": all_event_types.get("session.next.model.switched.1", 0),
            "agent_switched_event_count": all_event_types.get("session.next.agent.switched.1", 0),
            "root_tool_call_count": len(root_tools),
            "all_sessions_tool_call_count": len(source_tools),
            "root_tool_completed_count": root_tool_statuses.get("completed", 0),
            "root_tool_error_count": root_tool_statuses.get("error", 0),
            "all_sessions_tool_completed_count": all_tool_statuses.get("completed", 0),
            "all_sessions_tool_error_count": all_tool_statuses.get("error", 0),
            "all_sessions_tool_output_recorded_count": sum("output" in (data.get("state") or {}) for _, data in source_tools),
            "all_sessions_tool_duration_recorded_count": len(durations),
            "all_sessions_tool_duration_total_ms": sum(durations),
            "all_sessions_tool_duration_max_ms": max(durations) if durations else "未记录",
            "root_tool_duration_total_ms": sum(root_durations),
            "root_tool_counts_json": dict(root_tool_names),
            "all_sessions_tool_counts_json": dict(all_tool_names),
            "all_sessions_tool_status_counts_json": dict(all_tool_statuses),
            "lifecycle_event_counts_json": dict(all_event_types),
            "root_tokens_input": root_session["tokens_input"],
            "root_tokens_output": root_session["tokens_output"],
            "root_tokens_reasoning": root_session["tokens_reasoning"],
            "root_tokens_cache_read": root_session["tokens_cache_read"],
            "root_tokens_cache_write": root_session["tokens_cache_write"],
            "root_cost": root_session["cost"],
            "all_sessions_tokens_input": sum(row["tokens_input"] for row in session_rows),
            "all_sessions_tokens_output": sum(row["tokens_output"] for row in session_rows),
            "all_sessions_tokens_reasoning": sum(row["tokens_reasoning"] for row in session_rows),
            "all_sessions_tokens_cache_read": sum(row["tokens_cache_read"] for row in session_rows),
            "all_sessions_tokens_cache_write": sum(row["tokens_cache_write"] for row in session_rows),
            "all_sessions_cost": sum(row["cost"] for row in session_rows),
            "result_file_call_count": all_tool_names.get("result_file", 0),
            "root_final_finish_reason": final_finishes[-1] if final_finishes else "未记录",
        }
        for field, expected in direct.items():
            check(
                metric_values_equal(field, metrics_csv[field], expected),
                "metric_vs_database",
                f"{version}/{task_id}/{field}: {metrics_csv[field]!r} != {expected!r}",
            )
            check(
                metric_values_equal(field, metrics_csv[field], metrics[field]),
                "metric_csv_vs_run",
                f"{version}/{task_id}/{field}",
            )

        saved_artifacts = read_json(task_dir / "evidence" / "artifacts.json")
        declared_count = sum(
            len((data.get("state") or {}).get("input", {}).get("output_paths") or [])
            if isinstance((data.get("state") or {}).get("input", {}).get("output_paths") or [], list)
            else 1
            for _, data in source_tools
            if data.get("tool") == "result_file"
        )
        check(saved_artifacts["declared_output_count"] == declared_count, "declared_output_count", f"{version}/{task_id}")
        for file_record in saved_artifacts["project_files_excluding_inbox"] + saved_artifacts["inbox_file_references"]:
            path = Path(root_session["directory"]) / file_record["relative_path"]
            check(path.is_file(), "inventory_file_exists", f"{version}/{task_id}/{path}")
            if path.is_file():
                check(path.stat().st_size == file_record["size_bytes"], "inventory_file_size", f"{version}/{task_id}/{path}")
                check(sha_file(path) == file_record["sha256"], "inventory_file_hash", f"{version}/{task_id}/{path}")

        evidence_manifest = read_json(task_dir / "evidence" / "evidence_manifest.json")
        for file_record in evidence_manifest["files"]:
            path = task_dir / "evidence" / file_record["name"]
            check(path.is_file(), "evidence_file_exists", f"{version}/{task_id}/{path}")
            if path.is_file():
                check(path.stat().st_size == file_record["size_bytes"], "evidence_file_size", f"{version}/{task_id}/{path}")
                check(sha_file(path) == file_record["sha256"], "evidence_file_hash", f"{version}/{task_id}/{path}")

        task_summaries.append(
            {
                "version": f"mw-{version}",
                "task_id": task_id,
                "root_session_id": root_id,
                "session_count": len(session_rows),
                "assistant_message_count": len(source_messages),
                "part_count": len(source_parts),
                "tool_call_count": len(source_tools),
                "lifecycle_event_count": len(source_events),
                "project_file_record_count": len(saved_artifacts["project_files_excluding_inbox"]),
                "inbox_file_reference_count": len(saved_artifacts["inbox_file_references"]),
            }
        )

report = {
    "review_state": "已完成" if not discrepancies else "发现差异",
    "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "task_count": len(task_summaries),
    "check_execution_count": sum(checks.values()),
    "check_type_counts": dict(checks),
    "difference_count": len(discrepancies),
    "differences": discrepancies,
    "task_summaries": task_summaries,
}
conn.rollback()
conn.close()
if args.report:
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
print(json.dumps({key: value for key, value in report.items() if key != "task_summaries"}, ensure_ascii=False, indent=2))
raise SystemExit(1 if discrepancies else 0)
