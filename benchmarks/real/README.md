# Historical real-bug benchmark corpus

This directory contains source-backed before/after cases from public project history.
Every case records the upstream repository, pull request, exact commits, full changed
source-file snapshots, expected finding IDs, and a deterministic state invariant.

Current corpus:

| Project | Language | Upstream fix | Category |
| --- | --- | --- | --- |
| FastAPI | Python | [#5465](https://github.com/fastapi/fastapi/pull/5465) | resource lifecycle |
| Prometheus | Go | [#1070](https://github.com/prometheus/prometheus/pull/1070) | resource lifecycle |
| Kubernetes | Go | [#29495](https://github.com/kubernetes/kubernetes/pull/29495) | resource lifecycle |
| Gin | Go | [#4422](https://github.com/gin-gonic/gin/pull/4422) | resource lifecycle |
| Terraform | Go | [#38585](https://github.com/hashicorp/terraform/pull/38585) | resource lifecycle |

Run the complete corpus with:

```bash
smartbench benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

Passing means that every historical buggy snapshot produces exactly one expected
finding and every fixed snapshot produces none. It demonstrates deterministic
expression of these known defects; it does not yet establish general precision or recall.
