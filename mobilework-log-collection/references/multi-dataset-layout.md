# Multi-dataset log collection layout

This is an implementation contract for benchmark runs whose dataset task IDs differ from MobileWork root-session title IDs. Do not infer the mapping from display order alone; derive and verify it from the dimension/task directory names, title convention and execution records. This reference does not authorize export.

## Configurable inputs and mapping

Represent each dataset separately in configuration: benchmark directory, output subdirectory, expected included task IDs, session title suffix, and attempt policy. Preserve the dataset, dimension and task directory names exactly in the output. Never embed a product version, fixed task count or title suffix into reusable code.

The current adapter has two mapping modes. `regular` maps `W1…W5` to `1.1…1.5`, `O` to dimension 2, `C` to 3, `F` to 4, `S` to 5, and `M` to 6. `numeric` combines a Chinese-numbered dimension directory and a numeric task-directory prefix, for example `维度二/.../2_...` to `2.2`. If a benchmark uses another convention, inspect the title and task mapping first and extend the adapter; do not force a false match. Set the exact dataset and dimension directory names, session title suffix, and expected task count for each run in its local config. Unselected dimensions are not missing tasks.

Match full root-session titles, including optional numeric attempt suffix, and validate the source project's path, date and task association. No suffix denotes attempt 1. Show all candidates and detect missing, duplicate or ambiguous attempts before export. A `继续` message within the same root session remains part of that attempt; it does not create another attempt. Retain all distinct attempts as evidence; keep the official-delivery selection separate from that inventory. Do not infer that the highest suffix succeeded. If execution records indicate an additional attempt without a distinct matching root, investigate before asserting completeness.

## Planned output

```text
各测试任务日志/
├─ 常规能力测试数据集（原目录名）/
│  └─ 维度1_信息检索与浏览/
│     └─ W3_多跳信息追踪/
│        ├─ 任务会话索引.json
│        ├─ 第1次/
│        │  ├─ summary.md
│        │  ├─ run.json
│        │  ├─ trace.jsonl
│        │  └─ evidence/...
│        └─ 第2次/...
├─ alphadata测试数据集（原目录名）/
│  └─ 维度一-基础数据处理与统计计算_InfiAgent-DABench/
│     └─ 1_正态性检验/
│        ├─ 任务会话索引.json
│        └─ 第1次/...
└─ deepinsight测评数据集（原目录名）/
   └─ 维度四-智慧巡察/
      └─ 1_七家单位综合巡察报告/...
```

`（原目录名）` in this illustration means the exact existing dataset folder name, including its parenthetical description, not the abbreviated label. An index at each task root lists the task mapping, all matching root sessions and the official-delivery attempt. Each dataset root contains `manifest.json` and `metrics.csv`; the collection root contains `metrics字段说明.md` and `日志整理说明.md`. Keep source project paths as metadata and do not copy project files or original deliveries into the log output. This directory contract is schema/layout version `3.0`; each attempt retains the evidence files and omission rules of output schema 2.0. Do not create duplicate large assistant/tool text in `trace.jsonl`.

Use `scripts/collect_multi.py --config <config.json> --check` for a read-only discovery pass, then `--collect` to export into a missing or empty root, and `--verify` to check every attempt, source session tree, trace continuity and evidence hashes. The config uses `dataset_root`, `output`, and a `datasets` array of exact directory names, mapping mode (`regular` or `numeric`), session title suffix, included dimension names and expected task count. It also carries legacy `database`, `dataset`, and `versions` fields to reuse the existing per-attempt evidence extractor. Do not use the legacy `discover.py`, `collect.py`, `validate.py` or `verify_source.py` on this layout.

## Dry run and verification

Before any export, a read-only discovery pass should verify the database schema, all target task directories, exact title matches, distinct attempts, root/descendant relations and project directory existence. Report counts by dataset and all discrepancies. The planned output root must be checked for existing content; never overwrite an existing collection. After export, validate per-attempt files and index mappings, compare canonical records with the read-only database, and check that no task was silently skipped. Reporting is about log completeness and provenance, not task-quality scoring.
