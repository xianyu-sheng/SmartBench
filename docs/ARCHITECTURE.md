# SmartBench Architecture

SmartBench is organized as a compiler-like analysis platform. Language
frontends lower source projects into a versioned Semantic IR; deterministic
analyzers and graph retrieval consume that IR; agents only interpret and
challenge source-backed evidence.

```text
source project
    -> language frontend
    -> SemanticIR (v1)
    -> structural code graph + capability matrix
    -> declarative rules + CFG analyzers / graph retrieval
    -> EvidencePack
    -> evidence-exclusive proposer / critic / judge / verifier
    -> Finding + SARIF / JSON report
```

## Boundaries

### Language frontend

`smartbench.core.adapters.LanguageAdapter` is the compatibility name for the
frontend interface. Every frontend provides:

- language and file extensions;
- structural parsing into `CodeGraph`;
- `semantic_capabilities`;
- `parse_semantic_file` and `parse_semantic_project`.

Adding a language should normally mean adding a frontend adapter. Core rules
must not import that language's parser.

### Semantic IR

`smartbench.ir.SemanticIR` is the stable analysis boundary. It contains:

- the compatibility structural graph;
- source-unit metadata and bounded project file access;
- detected languages;
- a capability matrix;
- versioned semantic facts;
- normalized operations and control-flow edges;
- a schema version (`semantic-ir/v1`).

Capability declarations are conservative. Unsupported information is reported
as missing/unknown instead of being treated as a clean result.

### Deterministic graph retrieval

`smartbench.graph.evidence.DeterministicGraphRAG` performs stable lexical seed
selection, bounded graph traversal, deterministic ranking, and source-location
collection. It returns an `EvidencePack` containing:

- graph facts;
- stable content-addressed fact IDs;
- source references and snippets;
- retrieval trace;
- graph snapshot hash.

The pack is the factual boundary for later model calls. It is not a free-form
LLM summary.

### Multi-agent verification

Production diagnosis uses `EvidencePolicy.EXCLUSIVE`: arbitrary repository
context is removed from proposer/critic/judge prompts, and the same pack is
used in every round. Proposed and final suggestions must cite valid `fact-*`
IDs; unsupported or unknown IDs are rejected by a deterministic evidence gate.
The optional policy remains available only for backwards-compatible library
use. Existing disk and graph verifiers remain compatible with SemanticIR
through its graph compatibility view.

## Current migration state

- Python and Go have semantic frontends that lower into the same normalized
  operation model. Existing JavaScript/TypeScript data-flow rules receive
  SemanticIR through the unified engine boundary but do not yet claim the new
  operation capabilities.
- Existing CodeGraph consumers remain compatible through delegated graph
  queries.
- Unified results carry bounded EvidencePacks by default; use
  `--no-evidence` or `--max-evidence-packs` to control output size.
- Go is recognized by the structural frontend but does not yet claim semantic
  type or interprocedural data-flow capabilities. Its first semantic frontend
  now lowers functions, parameters, assignments, calls, branches, loops,
  returns, goroutines, defer, channel send/receive and select into the common
  operation model. State/event and concurrency capabilities remain explicitly
  partial until interprocedural resolution is available.

### Declarative state-machine analysis

`smartbench.analysis.StateMachineAnalyzer` evaluates reusable invariants over
normalized operations. Its guard checks use intraprocedural CFG reachability,
dominance, and branch control dependence; source order alone is not treated as
proof. Invariants select an event, an action and an optional guard/exit
relation; the engine contains no project-specific identifiers. Repository-
specific expectations belong in benchmark specifications or rules, not in the
language frontend.

Rule files use the `smartbench.state-rules/v1` YAML schema and are loaded with
the repeatable `--state-rules` CLI option. A validated rule is adapted to the
normal `DiagnosticRule` interface, so its violations pass through confidence
filtering, JSON/SARIF output, and exact EvidencePack construction like built-in
rules. See `benchmarks/reasonix/reasoning_stop.yaml` for a real pre-fix/post-fix
specification.

`smartbench.benchmarks.BenchmarkRunner` executes declared repository snapshots
through the same engine and checks finding-count/rule-ID expectations. The
`smartbench benchmark run --manifest ...` command emits machine-readable
pass/fail results without mutating a repository or creating a worktree.

## Acceptance criteria for new frontends

Before a language is marked semantically supported, its adapter must provide:

1. deterministic source-location round trips;
2. a capability declaration with explicit partial/unknown semantics;
3. stable SemanticIR serialization for the same source snapshot;
4. at least one pre-fix/post-fix known-bug benchmark;
5. no changes to language-neutral analyzers for basic rule execution.
