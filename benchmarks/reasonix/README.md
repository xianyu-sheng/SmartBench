# Reasonix reasoning-only completion benchmark

This benchmark keeps repository-specific vocabulary outside SmartBench's IR,
frontends, and analyzers. The same declarative engine consumes the YAML rule as
an external specification.

Known snapshots in `DeepSeek-Reasonix`:

- bad: `f590a66e` (the parent of the fix);
- fixed: `d68676e0`.

Run it with:

```bash
smartbench unified run \
  --project /path/to/DeepSeek-Reasonix \
  --language go \
  --rule reasonix-reasoning-stop-before-empty-retry \
  --state-rules benchmarks/reasonix/reasoning_stop.yaml \
  --output reasonix-report.json
```

The bad snapshot should report the transition from the
`hasVisibleFinalAnswer` branch to the `emptyFinalBlocks` update. The fixed
snapshot inserts `reasoningOnlyFinishHonoured` before that update and should
produce no finding.

For automated comparisons, create a manifest beside this file with `bad` and
`fixed` snapshot paths and run `smartbench benchmark run --manifest
manifest.yaml`. The runner records finding counts, rule IDs, errors, and
pass/fail status for every snapshot without changing either checkout.
