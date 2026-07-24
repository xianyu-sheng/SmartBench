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

Interprocedural operations additionally conform to
`semantic-ir/contracts/v1`: functions expose typed signature metadata,
parameters expose positions and receiver status, assignments expose aligned
bindings, calls expose argument/result bindings and a host operation, and
returns expose value lists. Contract validation checks shape and alignment;
empty types remain explicit unknowns rather than being treated as proof.

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

### Interprocedural and concurrency linking

`smartbench.analysis.SemanticLinker` builds conservative operation-level call
edges after language IRs are merged. It resolves exact qualified symbols,
same-namespace simple calls, Python lexical `self`/`cls` calls, and globally
unique simple names. Python annotations, Go surface types, constructor syntax,
and resolved function return types can prove a receiver type; the linker then
iterates until no additional typed call can be resolved. Ambiguous names and
dotted receivers without unique type proof remain unresolved rather than
receiving invented edges. `CALL`, `SPAWN`, and `DEFER` share this contract.

The normalized interprocedural attribute contract is language-independent:
functions expose return types, parameters expose position and declared type,
assignments expose aligned bindings, and calls expose arguments, receiver and
result targets. A uniquely resolved call produces `DATA_DEPENDENCY` edges for
`argument_to_parameter` and `return_to_call`. `InterproceduralGraph` exposes
read-only callers, callees, bindings, returns and shortest call-path queries;
call-path depth is explicitly bounded and does not pretend to be whole-program
dominance.

`InterproceduralControlFlowGraph` adds a bounded, stack-consistent view over
the intraprocedural CFG. Embedded calls cannot be bypassed by a direct host to
continuation edge; their continuation is reintroduced only by a matching
`CALL_RETURN`. `InterproceduralStatePathQuery` exposes event-to-action paths
across function scopes as source-backed `STATE_TRANSITION` facts. It does not
turn path existence into an invariant violation: dominance and guard claims
remain the responsibility of a state analyzer with an explicit proof policy.

Go channel sends and receives expose a normalized `channel` attribute. The
linker emits `SYNCHRONIZES` edges only for matching channel expressions inside
the same function; interprocedural channel alias analysis remains explicitly
unsupported. Linked call, data-flow, and synchronization facts are included in
deterministic graph versions and are retrievable by EvidencePack queries.

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
- Go is recognized by the structural frontend and lowers functions,
  typed parameters/receivers/results, assignments, calls, branches, loops,
  returns, goroutines, defer, channel send/receive and select into the common
  operation model. Type, data-flow, state/event and concurrency capabilities
  remain explicitly partial because SmartBench does not yet run `go/types`,
  resolve aliases, or model runtime dispatch.
- The operation call graph is deliberately partial. Current SmartBench
  self-analysis resolves conservative call edges and reports unresolved and
  ambiguous counts in `ir.meta.semantic_linker`; these are coverage signals,
  not clean-result claims.
- The ICFG is deliberately bounded and synchronous. It does not yet model
  callbacks, recursion summaries, dynamic dispatch, exceptions, goroutine
  happens-before, or channel aliases; those cases remain unknown.

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

State invariants default to `scope: intraprocedural`, preserving the original
CFG proof policy. A rule may opt into `scope: interprocedural` with a bounded
`max_call_depth`; guard-before-action proofs then require a matching guard on
the ICFG witness path and a same-scope CFG dominance/branch-control proof for
the action. A present but unproven caller-side guard is reported as unknown,
not as a false violation.

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
