# Audit trajectories

Versioned records of SmartBench audit runs, kept **inside this repository** (no
separate dataset repo). The schema and tooling live in
[`smartbench/trajectory/`](../smartbench/trajectory/).

```
trajectories/
  LICENSE              # CC BY 4.0 for the data (code stays MIT)
  v0/
    trajectories.jsonl # 69 redacted `smartbench.trajectory/v1` documents
    manifest.json      # counts, splits, SHA-256
    dataset_card.md    # provenance, limitations, license, citation
    sources/
      real_world.yaml  # curated public cases (issue/PR links, outcomes)
```

## What v0 contains

| Split | Documents | Source |
|---|---|---|
| `real_world` | 5 | Public issues/PRs with runtime verification and outcomes |
| `benchmark` | 50 | 25 before/after cases from `benchmarks/real/manifest.yaml` |
| `negative` | 14 | Clean look-alikes from `benchmarks/negative/manifest.yaml` |

## Quick start

```bash
# validate the committed dataset (schema, counts, hash, redaction)
python -m pytest tests/test_trajectory_dataset.py -q

# record your own runs (user-side, opt-in)
smartbench quick --project ./repo --trajectory

# export them as a dataset
smartbench trajectory export my-dataset.jsonl
```

## License

Data: **CC BY 4.0** — see [LICENSE](LICENSE). SmartBench source code remains
MIT. Upstream repositories keep their own licenses; see each document's
`provenance.reference`.

## Provenance and removal

Real-world entries link to public issues/PRs and record the outcome. No
unreleased vulnerability details are published. To request removal of an entry,
open an issue in the SmartBench repository.
