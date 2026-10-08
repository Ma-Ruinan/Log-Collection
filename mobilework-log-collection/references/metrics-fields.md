# Metrics field maintenance

`collect.py` is the single generation source for the complete metric field list and field descriptions. Every collection writes that list to `metrics字段说明.md`; every version must use the same ordered CSV header.

The schema groups metrics into:

- task/version and selected-session identity
- root timing and completion timing
- root and all-session message/part counts
- tool counts, statuses, errors, retries, and durations
- lifecycle-event counts and compact sequences
- reasoning records actually present in the source
- token, cache-token, and source cost fields
- declared result paths and project/inbox inventory counts

When adding a metric:

1. Derive it from a named saved record or an explicit calculation.
2. Preserve numeric zero; use a neutral text state only when the source value is absent.
3. Add its definition and source to `write_metrics_guide` in `collect.py`.
4. Add independent source verification when the field can be recomputed.
5. Confirm `validate.py` still matches the generated documentation and equal headers across versions.

Changing an existing field's meaning is a schema change. Renaming only for style is discouraged because it breaks longitudinal use.
