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
finding. The manual online experiment below replaces the reference learner with
real model proposals while keeping the same validator and analyzer.

## Online ProjectReader extraction

The manual online runner replaces the deterministic reference learner with a
configured LLM while preserving the same inventory, validator, analyzer, and
historical acceptance criteria:

```bash
python -m smartbench.experiments.project_reader_online \
  --manifest benchmarks/real/manifest.yaml \
  --negative-path benchmarks/experiments/project_reader_resource/negative \
  --output project-reader-online.json
```

It reads provider credentials through SmartBench's existing environment loader.
Supported variables are `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GLM_API_KEY`, `DOUBAO_API_KEY`, `MOONSHOT_API_KEY`, and
`DASHSCOPE_API_KEY`; the optional `SMARTBENCH_<PROVIDER>_MODEL` variable selects
a non-default model. Keys remain in memory and are never included in the JSON
report. The report also excludes raw prompts and raw model responses.

This runner is intentionally not a required CI job: it consumes an external,
nondeterministic service and credentials. The offline reference-assisted
experiment remains the deterministic CI gate. If no supported provider is
configured, the online runner writes `status: unavailable` and exits with code
2 instead of treating an absent model as an abstention or successful run.

The model still reads a fixed reference inventory and is evaluated by applying
its validated mappings to historical buggy snapshots. Consequently, a passing
online report demonstrates protocol extraction through the Agent boundary, not
autonomous discovery of unknown bugs.

For the stricter target-excluded experiment, use
`smartbench.experiments.project_reader_blind_online` with the blind manifest as
documented in `../project_reader_blind/README.md`.
