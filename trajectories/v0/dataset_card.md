# SmartBench Audit Trajectories v0

**73 redacted audit trajectories** (80 KB JSONL): 9 real-world outcomes,
50 benchmark snapshots and 14 negative fixtures. This is a dataset of
**verifiable audit trajectories** — hypothesis/evidence/verdict records — not a
leaderboard and not a claim of general bug-detection accuracy.

- Format: JSONL, one `smartbench.trajectory/v1` document per line
- Manifest: [`manifest.json`](manifest.json) (counts, splits, SHA-256)
- License: **CC BY 4.0** for the data; SmartBench code remains MIT
- Generation: [`tools/build_trajectories_v0.py`](../../tools/build_trajectories_v0.py)

## Splits

| Split | Documents | What it contains | LLM involved |
|---|---|---|---|
| `real_world` | 9 | Public issues/PRs with runtime verification and an outcome (`merged`, `closed-fixed`, `closed-declined`, `open`, `pr-open`) | Human-verified |
| `benchmark` | 50 | 25 before/after cases (public fixes + synthetic fixtures); deterministic findings only | No |
| `negative` | 14 | Clean look-alike fixtures that must produce zero findings | No |

The `benchmark` split carries `config.expected_rule_ids` and
`config.snapshot=before|after`. The `negative` split asserts zero findings at
the default confidence threshold.

## What one document contains

See [`smartbench/trajectory/README.md`](../../smartbench/trajectory/README.md)
for the full schema. The important fields for research use are:

- `project`: name, path-hash, languages (no absolute paths)
- `config`: split, case id, expected rules, confidence threshold, category
- `deterministic.findings`: `rule_id`, project-relative `file_path`,
  `line_start`, severity, confidence
- `findings` + `finding_signature`: the final normalized set
- `adjudication`: label + reason + source (real-world split)
- `verification` + `provenance`: how the finding was verified and its public
  outcome (real-world split)
- `usage`: empty for v0 (no provider calls)

## Loading

```python
import json
docs = [json.loads(line) for line in open("trajectories/v0/trajectories.jsonl", encoding="utf-8")]
real_world = [d for d in docs if d["config"]["split"] == "real_world"]
```

## Construction and validation

The benchmark/negative splits are produced by running the deterministic
analysis over the committed corpora; no model is called. The real-world split
is curated from public threads in `sources/real_world.yaml`. Timestamps are
fixed and documents are sorted, so regeneration is stable. The manifest stores
the SHA-256 of the JSONL over canonical LF bytes (CRLF-normalized), so Windows
checkouts verify too; CI validates every document, the split counts and the
hash (`tests/test_trajectory_dataset.py`).

Regenerate (requires the full parser set, including Go):

```bash
python tools/build_trajectories_v0.py --root . --out trajectories/v0
```

## Privacy and disclosure

- Only **public** issues/PRs/commits are included; no unreleased vulnerability
  details.
- Project paths are replaced with names/hashes; home paths and token-like
  strings are redacted; strings are bounded.
- Raw prompts and model responses are never included.
- Takedown/removal requests: open an issue in the SmartBench repository.

## Limitations

- Small and resource-lifecycle heavy; not a measure of unknown-bug recall.
- No LLM conclusions in v0, so it cannot be used to evaluate model reasoning.
- Real-world line numbers are approximate (`provenance.location_note`); the
  public reference is authoritative.
- No cross-repository or cross-service trajectories.

## Citation

```bibtex
@misc{smartbench_trajectories_v0,
  title  = {SmartBench Audit Trajectories v0},
  author = {Xianyu Sheng},
  year   = {2026},
  url    = {https://github.com/xianyu-sheng/SmartBench/tree/main/trajectories/v0},
  note   = {CC BY 4.0}
}
```
