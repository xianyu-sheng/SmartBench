# Sharded review

For large repositories, the normal quick path runs one debate over a single
project-level evidence pack. Sharded review splits that work into deterministic
directory shards, runs bounded parallel sub-debates, and merges the results.

**Default is off.** `smartbench quick --max-workers 1` keeps the existing
single-debate behavior; sharding only activates with `--max-workers > 1`.

## Pipeline

1. **Plan** (`plan.py`) — group files by top-level directory, cap each shard at
   40 files, cap the plan at 20 shards, merge the smallest groups into `misc`
   when needed. Files are sorted; ids are stable; a file belongs to one shard.
2. **Scope** (`merge.scope_evidence_pack`) — keep facts whose evidence falls
   inside the shard; hypotheses are kept only when they expose a file path.
3. **Run** (`runner.run_sharded_review`) — bounded `ThreadPoolExecutor`; each
   shard gets its own `DebateEngine` and a scoped prompt. Outcomes are sorted by
   shard id regardless of completion order. A shard error becomes a `partial`
   result, not a crash; a deadline marks undispatched shards `skipped_budget`.
4. **Merge** (`merge.merge_shard_results`) — deduplicate by
   `(rule_id, file, line)`, keep the stronger suggestion on conflicts
   (confidence, then evidence count), concatenate unreviewed suggestions and
   debate logs with a `shard` tag, and report `cross_shard_duplicates`.

## Guarantees and limits

- Deterministic ordering of shards, merged suggestions and statuses.
- Every finding still goes through the same evidence gate; the merge cannot
  promote an unevidenced claim.
- No cross-shard LLM synthesis in v1: the merge is mechanical. A finding that
  only appears when two modules are combined is out of scope.
- No unbounded parallelism: `--max-workers` is capped at 8 in the CLI.
- Budget handling is minimal: the runner accepts a deadline and marks
  undispatched shards; full token/time circuit breaking is #20.

## Usage

```bash
smartbench quick --project ./large-repo --max-workers 4
```
