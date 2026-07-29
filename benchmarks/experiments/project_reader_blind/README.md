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

The experiment evaluates three portable protocol modes:

1. `exact`: acquire symbols must be identical;
2. `typed_method`: an independently observed method call may transfer across
   receiver spellings only when both calls have the same uniquely proven,
   normalized receiver type and canonical method symbol, and their result
   position, resource member path, cleanup method, and target-side member use
   agree;
3. `method_shape`: the explicitly partial fallback may transfer across
   receiver names only when result position, resource member path, cleanup
   method, and target-side member use all agree. It cannot override available
   type evidence.

The Prometheus reference call `i.httpClient.Do` and historical target call
`httpClient.Do` independently resolve to `net/http.Client.Do`; the report
retains the reference and target type-evidence IDs. Kubernetes remains an exact
match. Gin and Terraform remain unsupported rather than receiving special-case
rules.

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

## Repeated live-model trials

The online blind runner replaces the deterministic protocol miner with a real
environment-configured ProjectReader while keeping the same pinned references,
target exclusions, deterministic evidence resolver, type/fact validator,
before/after analyzer, and
independent negative:

```bash
python -m smartbench.experiments.project_reader_blind_online \
  --benchmark-manifest benchmarks/real/manifest.yaml \
  --blind-manifest benchmarks/experiments/project_reader_blind/manifest.yaml \
  --negative-path benchmarks/experiments/project_reader_resource/negative \
  --trials 3 \
  --max-repairs 1 \
  --output project-reader-blind-online.json
```

Only the two cases with admissible references invoke the model. Repeated trials
are reported separately; a reference-backed case passes only if every trial
reproduces its expected pre-fix finding, remains clean after the fix, and emits
no independent-negative finding. The report persists provider/model names,
parsed validation decisions, and deterministic witnesses, but never API keys,
raw prompts, or raw responses. If no supported provider is configured, it
returns `status: unavailable` instead of silently substituting a mock.

The model supplies semantic selectors rather than opaque `fact-*` or `type-*`
IDs. The resolver requires a unique structural match and reports agent-cited,
resolved, unresolved, and ambiguous evidence separately. Older clients may
still submit IDs, but those values are retained only as audit input and cannot
override the resolver.

When resolution or validation rejects every proposed candidate, a trial may perform a bounded
evidence-feedback repair. The model receives only the same blind inventory, its
previous structured output, and deterministic rejection reasons. Supported
candidates are never created by the repair itself: the replacement document
must pass the unchanged resolver and validator. Reports expose initial
rejections, repair attempts, and recovered trials so repair cannot hide model
instability.

The 2026-07-29 DeepSeek A/B used three trials per admissible case with repair
disabled. Resolver-only mode passed 6/6 trials, compared with the earlier 3/6
no-repair citation baseline. All six accepted candidates reproduced the
historical before finding, clean after state, and zero negative findings. This
result covers two Go resource protocols only; it is not a general accuracy or
recall number.
