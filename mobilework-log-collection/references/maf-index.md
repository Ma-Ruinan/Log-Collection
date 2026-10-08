# Optional MAF event index

The primary collection is the MobileWork SQLite session and project-metadata evidence. MAF logs are a separate optional local source. They may include input, assistant-output and process checks, but their lines do not necessarily contain a root-session ID. Do not present them as an authoritative per-task audit trail unless attribution is independently established.

After `collect_multi.py --verify` succeeds, run:

```text
python scripts/maf_index.py --config <本地配置.json> --log <maf-engine.log> --log <maf.log>
```

The script creates `MAF审查索引.jsonl` at the collection root and refuses to overwrite an existing index. Each indexed decision line records timestamp, source path and line number, SHA-256 of the source line, parsed `facet`/`role`/`scene`/`decision`/`hit` fields when present, and an optional attempt association. It deliberately omits `preview` text. A user-input line is associated only when its preview is a unique substring of a collected `session_input.prompt` within 120 seconds; all other lines remain unassociated. This is a conservative heuristic, not a proof that all MAF checks were captured. Log rotation, truncation, different text normalization, clock skew, absent previews and overlapping tasks can prevent matching. Review the cited source line locally when deeper analysis is authorized.

Do not interpret `decision=1/2/3` or numeric `hit` values as named policy outcomes without the relevant MAF implementation or authoritative mapping. Do not infer that an unassociated output line belongs to the nearest task. Because this index is created after the core collection verification, verify its source paths and hashes separately before citing it; the core `--verify` does not validate optional MAF logs.
