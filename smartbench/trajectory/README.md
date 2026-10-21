# Audit trajectories (`smartbench.trajectory/v1`)

One audit run becomes one versioned JSON document plus one index line. The goal
is a replayable, trainable record of **hypothesis → evidence → verdict**, not a
log dump.

## What is stored

- `schema_version`, `trajectory_id`, `recorded_at`, `tool_version`
- `project`: name, `root_hash` (hash of the absolute path, not the path), languages
- `config`: concern, rules/languages, trials, temperature, seed
- `stages`: deterministic analysis plus the debate status/duration
- `deterministic`: rule findings, their signature, bounded errors
- `debate`: role, round, output hash and size — **not** raw prompts or responses
- `findings`: `rule_id`, project-relative `file_path`, `line_start`, title,
  severity, confidence, evidence IDs, status
- `adjudication`, `verification`, `human_feedback`, `usage`
- `finding_signature`: stable hash of the normalized finding set

## Privacy and redaction

- Raw prompts and model responses are never stored; the debate block keeps only
  a hash and a size.
- Absolute project paths are replaced with `<project>`; the home directory with `~`.
- API keys/tokens (OpenAI, GitHub, AWS, Slack, private-key blocks) are masked.
- Strings are bounded (2000 chars); nested depth is bounded.
- Redaction is **best-effort**, not a formal guarantee. Do not record secrets in
  the first place.

## Storage

Default: `~/.smartbench/trajectories`. Override with `--trajectory-dir` or
`SMARTBENCH_TRAJECTORY_DIR`.

```
<trajectory_id>.json   # one redacted document
index.jsonl            # append-only summaries
```

A failed write is logged and ignored: recording never breaks an audit.

## CLI

```bash
# record (opt-in)
smartbench quick --project ./repo --trajectory
smartbench unified run --project ./repo --trajectory --trajectory-dir ./.trajectories

# inspect
smartbench trajectory list --dir ./.trajectories
smartbench trajectory show <id> --dir ./.trajectories --json

# export a dataset (JSONL + <output>.manifest.json)
smartbench trajectory export dataset.jsonl --dir ./.trajectories

# replay: schema check; with --project also re-runs the deterministic stage
smartbench trajectory replay <id> --dir ./.trajectories --project ./repo
```

## Replay semantics

Replay is intentionally honest about what it can reproduce:

- **Structural**: the schema and the recorded signature are validated.
- **Deterministic** (`--project`): the deterministic analysis is re-run on the
  checkout and its `(rule_id, file_path, line_start)` set is compared with the
  recorded `deterministic.findings`.
- **LLM stages are skipped**, not simulated. A model conclusion cannot be
  reproduced without the model, so the report says so instead of pretending.

## Status

Schema, redaction, local store, `quick`/`unified` recording, list/show/export and
deterministic replay are implemented. Dataset publication (v0) and outcome
labelling are tracked separately.
