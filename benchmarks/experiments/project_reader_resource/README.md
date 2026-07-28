# ProjectReader resource protocol experiment

This experiment tests the architectural seam between uncertain project
interpretation and deterministic verification. It does **not** claim that an
LLM autonomously discovered the protocols.

For each Go case in the public historical corpus it:

1. learns a portable `acquire symbol + result index + cleanup method` mapping
   from the fixed reference snapshot;
2. applies one language-neutral CFG/dominance analyzer to the buggy snapshot;
3. verifies that the same analyzer remains clean on the fixed snapshot;
4. checks an independent file containing four correct cleanup patterns;
5. records abstentions instead of converting missing uses into findings.

Run it with:

```bash
python -m smartbench.experiments.project_reader_resource \
  --manifest benchmarks/real/manifest.yaml \
  --negative-path benchmarks/experiments/project_reader_resource/negative \
  --output project-reader-experiment.json
```

The deterministic reference learner is a reproducible stand-in for the future
ProjectReader Agent. `smartbench.engine.project_reader` separately defines and
tests the strict Agent JSON contract and citation gate. Acquire calls, result
positions, and cleanup methods must all be grounded in reachable SemanticIR
operations; invented operations, missing facts, and invented cleanup methods
are rejected. Explicit ownership transfer produces an abstention rather than a
finding. A later online experiment must replace the reference learner with real
model proposals while keeping the same validator and analyzer.
