# Audit trajectories

Versioned records of SmartBench audit runs, kept **inside this repository** (no
separate dataset repo). The schema and tooling live in
[`smartbench/trajectory/`](../smartbench/trajectory/).

```
trajectories/
  LICENSE              # CC BY 4.0 for the data (code stays MIT)
  v0/
    trajectories.jsonl # 64 redacted `smartbench.trajectory/v1` documents
                       # project.repository is the stable purge identity
    manifest.json      # counts, splits, SHA-256
    dataset_card.md    # provenance, limitations, license, citation
    consent.json       # explicit-consent ledger (pending/granted/declined)
```

## Consent first

No third-party audit trajectory is published without explicit written consent
from the repository owner. The nine curated cases are `pending` in
[`v0/consent.json`](v0/consent.json) and excluded from the JSONL. See
[CONSENT.md](CONSENT.md) for the policy, request template and removal process.
Granted contributors are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).

## What v0 contains

| Split | Documents | Source |
|---|---|---|
| `real_world` | 0 | Pending explicit consent — see [CONSENT.md](CONSENT.md) |
| `benchmark` | 50 | 25 before/after cases from `benchmarks/real/manifest.yaml` |
| `negative` | 14 | Clean look-alikes from `benchmarks/negative/manifest.yaml` |

## Quick start

```bash
# validate the committed dataset (schema, counts, hash, redaction)
python -m pytest tests/test_trajectory_dataset.py -q

# runs are recorded automatically (user-side, redacted)
smartbench quick --project ./repo
# opt out for one run: --no-trajectory  (or SMARTBENCH_TRAJECTORY=0)

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
