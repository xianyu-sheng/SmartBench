# Blind project-protocol transfer experiment

This corpus measures how much of the historical Go resource benchmark remains
detectable when the ProjectReader reference inventory cannot see the target bug
file or its fix.

The reference files are pinned, unrelated files from the projects' current
default branches. Their upstream commit and SHA-256 are recorded in
`manifest.yaml`. The historical target files are explicitly excluded:

- Prometheus learns from `discovery/stackit/server.go`, not
  `retrieval/target.go`;
- Kubernetes learns from `pkg/util/tail/tail.go`, not
  `pkg/kubectl/resource_printer.go`;
- Gin and Terraform have no admissible same-project reference because current
  code search found their exact acquire symbol only in the excluded target
  file. They remain unsupported rather than receiving invented evidence.

The experiment evaluates two portable protocol modes:

1. `exact`: acquire symbols must be identical;
2. `method_shape`: an independently observed method call may transfer across
   receiver names only when result position, resource member path, cleanup
   method, and target-side member use all agree.

Run it with:

```bash
python -m smartbench.experiments.project_reader_blind \
  --benchmark-manifest benchmarks/real/manifest.yaml \
  --blind-manifest benchmarks/experiments/project_reader_blind/manifest.yaml \
  --negative-path benchmarks/experiments/project_reader_resource/negative \
  --output project-reader-blind.json
```

`passed` means the audited expectations, hashes, target exclusions, historical
before/after checks, and independent negative check remain reproducible. It does
not mean all cases were detected. Diagnostic coverage is reported separately.

No finding from this experiment authorizes an upstream Issue or pull request.
Detected cases are existing historical regressions and are verified by a
before/after contrast, a deterministic path witness, and the historical change
reference already recorded in the benchmark manifest.
