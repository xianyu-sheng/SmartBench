# Human feedback (`smartbench.feedback/v1`)

Phase 1 of [#45](https://github.com/xianyu-sheng/SmartBench/issues/45): capture
human decisions, rank offline, and **never suppress a finding**.

## Event schema

One event per human decision:

- `schema_version`, `event_id` (deterministic hash), `recorded_at`
- `label`: `actionable`, `design_trade_off`, `intentional`, `out_of_scope`,
  `theoretical`
- `rule_id`, `language`, project-relative `file_path`, `line_start`
- optional `confidence`, `reason`, `source`
- optional `trajectory_id`, `finding_signature` (link to a stored run)

Identical events collapse on append (same label/rule/file/line/reason/source),
so double-clicking does not distort the priors.

## Storage

Default: `~/.smartbench/feedback/events.jsonl` (override with `--file` or
`SMARTBENCH_FEEDBACK_FILE`). Append-only JSONL; corrupt lines are skipped.
User-side only — nothing is written into the analyzed repository.

## Ranking policy

Priors are aggregated per `(rule_id, language)` with `*` fallbacks and Laplace
smoothing: `(actionable + 1) / (support + 2)`.

- `smartbench feedback rank` sorts findings by the prior (then confidence,
  then original order) and annotates each with `actionable_ratio` / `support` /
  `label_counts`.
- **No finding is dropped.** The policy block reports `suppressed: 0`.
- `smartbench feedback policy` suggests an advisory `min_confidence` for
  priors dominated by rejections (support ≥ 3, ratio < 0.5). Suggestions are
  printed, never applied automatically.

Phase 2+ (applying the policy inside quick/preflight, learned policies) is
tracked in #45 and is intentionally out of scope here.

## CLI

```bash
smartbench feedback add \
  --label actionable --rule-id resource_leak --language python \
  --file-path src/main.py --line 10 --confidence 0.6 \
  --reason "confirmed by the maintainer"

smartbench feedback list [--rule-id ...] [--language ...] [--label ...] [--json]
smartbench feedback policy [--json]
smartbench feedback rank --input report.json --output ranked.json [--json]
```
