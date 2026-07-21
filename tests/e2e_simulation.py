"""
End-to-end simulation of SmartBench with mock LLM.
Tests the full pipeline: Phase 1→4→3→5.
"""
import json
import os

from smartbench.detector import ProjectScanner
from smartbench.engine.debate import DebateEngine
from smartbench.graph import CodeGraphBuilder, GraphRetriever
from smartbench.prompts.factory import PromptFactory

print("=== 4. END-TO-END SIMULATION (MOCK LLM) ===\n")

dir_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Phase 1: Detection
fp = ProjectScanner(dir_path).scan()
print(f"[Phase 1] Detected: {fp.summary()}")

# Phase 4: Code Graph
builder = CodeGraphBuilder()
graph = builder.build(dir_path, fp.primary_language)
print(f"[Phase 4] Graph: {graph.summary()}")

# Build context (graph-enhanced)
retriever = GraphRetriever(graph, dir_path, max_tokens_estimate=4000)
code_context = retriever.retrieve("diagnosis pipeline CLI")
print(f"[Phase 4] Retrieved {len(code_context)} chars of code context")

# Phase 3+5: Mock LLM + Debate
factory = PromptFactory(fp)

call_count = [0]

def mock_llm(prompt: str) -> str:
    call_count[0] += 1
    call = call_count[0]

    if call == 1:  # Strategy selection
        return json.dumps({
            "selected_strategy": "architecture_review",
            "confidence": 0.85,
            "reasoning": f"This {fp.primary_language.value} {fp.project_type.value} project benefits most from architecture review and modularity analysis",
            "parameter_overrides": {"focus_areas": ["modularity", "error handling"]},
            "alternative_strategies": ["correctness_audit"],
            "estimated_duration_minutes": 3,
        })

    if call == 2:  # Proposer
        return json.dumps({
            "analysis": {
                "root_cause": "Large monolithic CLI module with mixed concerns",
                "impact_assessment": "Makes testing and extension difficult",
            },
            "proposals": [
                {
                    "title": "Split CLI into separate command modules",
                    "location": "smartbench/cli.py:108",
                    "problem": "Single 640-line CLI file handles wizard, quick mode, diagnose, and utilities",
                    "solution": "Extract display helpers, utility functions, and mode implementations into separate modules",
                    "implementation_steps": [
                        "Move _display_* functions to smartbench/cli/display.py",
                        "Move _call_llm, resolve_project_path to smartbench/cli/utils.py",
                        "Move run_*_mode functions to smartbench/cli/modes.py",
                    ],
                    "expected_improvement": "60% easier to test, clearer separation of concerns",
                    "priority": 4,
                    "risk_level": "low",
                },
                {
                    "title": "Add complete type annotations for all function signatures",
                    "location": "smartbench/cli.py:multiple",
                    "problem": "Several functions use Optional[Dict] without full type annotations",
                    "solution": "Add complete type hints including return types, use TypedDict for config dicts",
                    "implementation_steps": [
                        "Add -> None to void functions",
                        "Use TypedDict for configuration dictionaries",
                        "Run mypy to verify",
                    ],
                    "expected_improvement": "Better IDE support, catch type errors at dev time",
                    "priority": 2,
                    "risk_level": "low",
                },
            ],
        })

    if call == 3:  # Critique
        return json.dumps({
            "verdicts": [
                {
                    "proposal_title": "Split CLI into separate command modules",
                    "verdict": "accept",
                    "concerns": ["Ensure import paths are updated across the codebase"],
                    "suggested_modifications": "Keep cli.py as a thin facade that re-exports from submodules",
                },
                {
                    "proposal_title": "Add complete type annotations for all function signatures",
                    "verdict": "accept",
                    "concerns": ["May require python>=3.10 for some syntax"],
                    "suggested_modifications": "Use from __future__ import annotations for forward compatibility",
                },
            ],
            "overall_assessment": "Both proposals are reasonable and low-risk. The module split is particularly impactful.",
        })

    # call == 4: Judge
    return json.dumps({
        "decision": "accepted",
        "reasoning": "Both proposals are well-scoped improvements with clear implementation paths",
        "final_suggestions": [
            {
                "title": "Split CLI into separate command modules",
                "description": "The 640-line CLI module handles too many concerns. Extracting display, utility, and mode functions into separate submodules improves testability and maintainability.",
                "implementation": "Create smartbench/cli/ package with display.py, utils.py, and modes.py. Keep cli.py as entry point facade.",
                "location": "smartbench/cli.py",
                "priority": 4,
                "risk_level": "low",
                "consensus": "high",
            },
            {
                "title": "Add complete type annotations",
                "description": "Several functions use incomplete type hints. Full annotations enable mypy static checking and improve IDE support.",
                "implementation": "Add return types to all functions, use TypedDict for config dicts, add future annotations import.",
                "location": "smartbench/cli.py:multiple",
                "priority": 2,
                "risk_level": "low",
                "consensus": "high",
            },
        ],
        "risk_summary": "Both changes are internal refactors with no API changes. Very low risk.",
    })


# Phase 3: Strategy selection
strategy_prompt = factory.build_strategy_prompt(
    "general code quality review",
    [
        {"name": "performance_analysis", "description": "CPU, memory, I/O profiling", "tools": ["perf", "pprof"]},
        {"name": "architecture_review", "description": "Design patterns, coupling, cohesion", "tools": ["code_graph"]},
        {"name": "security_scan", "description": "Vulnerabilities, injection", "tools": ["static_analysis"]},
    ],
)
strategy_response = mock_llm(strategy_prompt)
strategy = json.loads(strategy_response)
print(f"\n[Phase 3] Strategy: {strategy['selected_strategy']} (confidence: {strategy['confidence']:.0%})")
print(f"          {strategy['reasoning']}")

# Build analysis context
analysis_context = factory.build_analysis_context(
    code_context=code_context,
    user_symptoms="general code quality review",
)
print(f"[Phase 3] Analysis context: {len(analysis_context)} chars")

# Phase 5: Debate
engine = DebateEngine(mock_llm, prompt_factory=factory)
result = engine.debate(analysis_context, target="General code quality review")

print("\n[Phase 5] Debate completed:")
print(f"          Rounds: {result.iterations}")
print(f"          Est. tokens: ~{result.total_tokens_used}")
print(f"          Duration: {result.duration_ms}ms")
print(f"          Consensus: {result.consensus_reached}")
print(f"          Findings: {len(result.final_suggestions)}")

for i, sug in enumerate(result.final_suggestions, 1):
    print(f"\n  --- Finding #{i} ---")
    print(f"  Title:       {sug['title']}")
    print(f"  Priority:    {sug['priority']}/5")
    print(f"  Risk:        {sug['risk_level']}")
    print(f"  Consensus:   {sug['consensus']}")
    print(f"  Location:    {sug['location']}")
    print(f"  Description: {sug['description'][:120]}...")
    print(f"  Fix:         {sug['implementation'][:120]}...")

# Debate trace
print("\n  --- Debate Trace ---")
for entry in result.debate_log:
    print(f"  [{entry['role']:9s}] prompt={len(entry['input']):4d} chars  response={len(entry['output']):4d} chars")

print(f"\n{'='*60}")
print("END-TO-END SIMULATION: ALL PHASES PASSED")
print(f"{'='*60}")
