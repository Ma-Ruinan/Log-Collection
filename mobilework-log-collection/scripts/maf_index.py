"""Optional, conservative MAF event index for an existing multi-dataset collection.

Only user-input events with a unique prompt-text AND time match are associated
with a collected attempt. Other events remain unassociated. Raw previews are
never exported.
"""

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def write_jsonl(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_attempts(root, datasets):
    attempts = []
    for dataset in datasets:
        manifest_path = root / dataset["directory"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for task in manifest["tasks"]:
            for attempt in task["attempts"]:
                attempts.append({
                    "dataset": dataset["directory"],
                    "task_id": task["task_id"],
                    "attempt": attempt["attempt"],
                    "root_session_id": attempt["root_session_id"],
                    "relative_path": f'{dataset["directory"]}/{attempt["relative_path"]}',
                })
    return attempts


def session_tree(conn, root_id):
    found = {root_id}
    queue = [root_id]
    while queue:
        for row in conn.execute("SELECT id FROM session WHERE parent_id=?", (queue.pop(0),)):
            if row[0] not in found:
                found.add(row[0])
                queue.append(row[0])
    return found


def prompt_candidates(conn, attempts):
    output = []
    for attempt in attempts:
        for sid in session_tree(conn, attempt["root_session_id"]):
            for row in conn.execute("SELECT prompt,time_created FROM session_input WHERE session_id=?", (sid,)):
                if row[0]:
                    output.append((attempt, str(row[0]), int(row[1])))
    return output


def parse_line(line):
    head = re.match(r"^(\S+) \[\w+\] (.*)$", line)
    if not head:
        return None
    body = head.group(2)
    if "decision=" not in body:
        return None
    try:
        timestamp_ms = int(datetime.fromisoformat(head.group(1).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None
    fields = {}
    for name in ("facet", "role", "scene", "decision", "degraded", "stream"):
        match = re.search(r"(?:^|\s)" + name + r"=([^\s]+)", body)
        if match:
            fields[name] = match.group(1)
    hit = re.search(r"\bhit=\[([^]]*)\]", body)
    if hit:
        fields["hit"] = [entry.strip() for entry in hit.group(1).split(",") if entry.strip()]
    preview = re.search(r'\bpreview="(.*?)"\s*(?:→|->)', body)
    return timestamp_ms, fields, preview.group(1) if preview else None


def main():
    parser = argparse.ArgumentParser(description="为已收集日志建立可核对的 MAF 事件索引")
    parser.add_argument("--config", required=True)
    parser.add_argument("--log", action="append", required=True, help="MAF 日志文件，可重复指定")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    root = Path(cfg["output"]).resolve()
    target = root / "MAF审查索引.jsonl"
    if target.exists():
        raise SystemExit(f"索引已存在，不覆盖：{target}")
    attempts = load_attempts(root, cfg["datasets"])
    db = Path(cfg["database"]).resolve()
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        prompts = prompt_candidates(conn, attempts)
    finally:
        conn.close()
    records = []
    matched = 0
    for log_name in args.log:
        log = Path(log_name).resolve()
        with log.open("rb") as stream:
            for line_number, raw_bytes in enumerate(stream, 1):
                parsed = parse_line(raw_bytes.decode("utf-8", errors="replace"))
                if parsed is None:
                    continue
                when, fields, preview = parsed
                candidates = []
                if fields.get("facet") == "input" and fields.get("role") == "user" and preview and len(preview) >= 12:
                    for attempt, prompt, prompt_ms in prompts:
                        if abs(when - prompt_ms) <= 120_000 and preview in prompt:
                            candidates.append(attempt)
                unique = {(a["root_session_id"], a["attempt"]): a for a in candidates}
                association = next(iter(unique.values())) if len(unique) == 1 else None
                if association:
                    matched += 1
                records.append({
                    "timestamp_utc": datetime.fromtimestamp(when / 1000, timezone.utc).isoformat(timespec="milliseconds"),
                    "source_file": str(log),
                    "source_line": line_number,
                    "source_line_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "fields": fields,
                    "association": association,
                    "association_basis": "唯一输入正文片段且时间差不超过120秒" if association else "未能唯一核实，不归属到题目",
                })
    write_jsonl(target, records)
    print(json.dumps({"output": str(target), "events": len(records), "uniquely_associated": matched,
                      "unassociated": len(records) - matched}, ensure_ascii=False))


if __name__ == "__main__":
    main()
