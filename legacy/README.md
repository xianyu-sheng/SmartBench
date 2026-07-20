# Legacy Code (v0.1 - v0.5)

This directory contains code from the original Raft KV benchmarking pipeline, removed during the v0.6 refactoring when SmartBench pivoted from a distributed system benchmark tool to a universal code diagnostic platform.

## What's in here

| Directory | Description |
|---|---|
| `smartbench/agents/` | Old multi-agent pipeline: OrchestratorAgent, BenchmarkAgent, ObserverAgent, AnalysisAgent, VerificationAgent |
| `smartbench/plugins/systems/` | System plugins for benchmarking: RaftKVPlugin, MySQLPlugin, RedisPlugin |
| `smartbench/plugins/models/` | Old model provider plugins: AnthropicPlugin, OpenAICompatiblePlugin |
| `smartbench/engine/` | Old engine modules: diagnostic, aggregator, generator, flamegraph, weight, etc. |
| `smartbench/core/` | Old type system and config loader (replaced by PromptFactory + CLI-based config) |
| `start.py` | Old entry point for Raft KV benchmarking pipeline |
| `tests/` | Tests for the old modules |

## Why removed

The old code was designed for a completely different purpose (benchmarking and optimizing distributed systems like Raft KV stores). When SmartBench pivoted to become a universal code diagnostic tool with multi-agent debate + evidence verification, this code became dead weight.

The new architecture follows a clean 5-phase pipeline:
1. **fingerprint** → deterministic project detection
2. **graph** → AST-based code graph construction
3. **RAG** → semantic code chunking + vector indexing
4. **debate** → Proposer → Critique → Judge multi-agent debate
5. **verifier** → zero-LLM evidence verification against disk

## Can I restore this?

Yes. Move the files back to their original locations. Note that the old code may need updates to work with any API changes in the remaining modules.
