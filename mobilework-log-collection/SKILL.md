---
name: mobilework-log-collection
description: Locate, collect, normalize, and verify MobileWork AI Agent session logs for versioned or multi-dataset benchmark runs. Use for project-title discovery, task mapping, repeated attempts, JSON/JSONL/CSV evidence, optional MAF event indexing, or source verification. Do not use for scoring or Agent-quality comparison.
---

# MobileWork Log Collection

Create a reproducible, neutral record of observable MobileWork execution data. Use deterministic scripts for extraction and validation; do not ask a model to summarize a bulk raw-log dump into the canonical records.

This skill is portable across Agent tools that can read a local Skill folder and run Python scripts. Do not assume that the user knows the SQLite path, title suffix, task count, or output location. Ask for what is missing, inspect available read-only sources, and present the mapping before any export. Never invent missing values.

## Multi-dataset benchmark mode

For a run that must preserve each dataset's dimension/task directory names, read [references/multi-dataset-layout.md](references/multi-dataset-layout.md) first. This mode maps dataset task directories to root-session titles, retains all distinct attempts, and places logs under a separate output root. Use `scripts/collect_multi.py` for read-only discovery, collection and verification; the other scripts implement only the legacy title-equals-directory-ID layout. Do not run legacy scripts against this layout merely by changing the regex.

Use the title convention supplied by the tester, such as `W1-236b-0928` and `W1-236b-0928-2`; the suffix is a configuration value, not a fixed product version. Preserve the original dataset, dimension and task directory names. Keep first and second independent attempts even when only the second is used for delivery; a `继续` message within one root session is not another attempt. Treat the official delivery attempt as a separate decision, not proof that an attempt succeeded.

## Workflow

1. Read [references/configuration.md](references/configuration.md) and prepare a JSON config for this collection. Keep version identifiers and paths in config, not in the scripts.
2. If the MobileWork release or database structure is new, run `scripts/inspect_schema.py --database <path>` and compare the result with [references/source-schema.md](references/source-schema.md). Stop before extraction when a required table or column is absent.
3. Run `scripts/discover.py --config <config.json>` without writing the collection for the legacy layout. Review the selected root session for every task. The legacy selection rule is the highest numeric attempt suffix; no suffix means attempt 1. Stop on duplicate highest attempts, missing tasks, or unexpected counts. In multi-dataset mode use the mapping and attempt policy in [references/multi-dataset-layout.md](references/multi-dataset-layout.md).
4. Confirm that collection is authorized and that the configured output directory is new or empty. Run `scripts/collect.py --config <config.json>`.
5. Run `scripts/validate.py --config <config.json>` for structural and internal consistency checks.
6. Run `scripts/verify_source.py --config <config.json>` to independently compare saved records with the read-only SQLite source. Add `--report <path>` only when the user wants the check record saved; console-only is the default.
7. Report the output location, task count, included versions, and validation result. Do not add evaluation findings to the collection.

For multi-dataset mode, the equivalent order is `inspect_schema.py`, `collect_multi.py --check`, user review of mapping, `--collect`, then `--verify`. Run `scripts/maf_index.py` only if the user supplies accessible MAF logs and wants that optional evidence; read [references/maf-index.md](references/maf-index.md) first. Report the absolute final output path and any unassociated MAF events. A MAF decision code or hit ID alone is not a verified policy name or cause.

Use the bundled workspace Python when the system `python` command is only a Windows app alias.

## Recording invariants

- Include the selected root session and all descendant sessions.
- Preserve each source event sequence and add a deterministic merged trace sequence.
- Keep canonical machine-readable records in JSON/JSONL/CSV. Markdown summaries are derived views.
- Do not save the task question, user prompt text, fixed source-material contents, or rubric contents unless the user explicitly changes the scope.
- Save observable reasoning parts or reasoning lifecycle events only when they exist in the source. Never infer hidden reasoning.
- For large text, save the configured head and tail plus full character count and SHA-256.
- Avoid duplicating large tool results and assistant text inside `trace.jsonl`; retain references to their canonical evidence records.
- Preserve source status values. Use neutral states such as `未记录`, `未包含`, and `未保存` for absent or intentionally omitted information.
- Inventory project files by metadata and SHA-256 without copying their contents. Exclude `.git`, `node_modules`, `.venv`, and `__pycache__` by default.
- Do not create validation artifacts in the collection unless requested.
- Treat the database as read-only and hold one read transaction during collection or source verification.

Read [references/collection-rules.md](references/collection-rules.md) when changing inclusion, excerpt, deduplication, or attempt-selection behavior. Read [references/output-schema.md](references/output-schema.md) before changing files or fields. Read [references/metrics-fields.md](references/metrics-fields.md) before changing metrics.

## Versioning and changes

The legacy output schema is `2.0`; the multi-dataset directory contract is `3.0` and retains the legacy per-attempt evidence fields. Keep existing meanings stable within a schema version. When changing a field meaning, file role, ordering rule, or omission rule, update the schema version and the corresponding reference. Extend database adapters after inspecting actual schema; do not invent mappings for missing fields.
