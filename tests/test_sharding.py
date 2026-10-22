"""Tests for deterministic sharded review (issue #48)."""

import re
import threading
import time

from typer.testing import CliRunner

from smartbench.cli.main import app
from smartbench.engine.debate import DebateResult
from smartbench.ir import EvidencePack, EvidenceRef, SemanticFact
from smartbench.sharding import (
    Shard,
    ShardOutcome,
    build_shard_plan,
    merge_shard_results,
    run_sharded_review,
    scope_evidence_pack,
)


def _result(suggestions):
    return DebateResult(final_suggestions=suggestions, review_status="completed")


def _suggestion(title, *, file_path="a.py", line=1, confidence=0.6):
    return {
        "rule_id": "resource_leak",
        "title": title,
        "location": {"file_path": file_path, "line_start": line},
        "confidence": confidence,
    }


def test_plan_is_deterministic_and_skips_vendor_dirs():
    files = [
        "b/x.py",
        "a/y.py",
        "a/z.py",
        "root.py",
        ".venv/skip.py",
        "node_modules/skip.js",
    ]

    plan = build_shard_plan(files, max_files_per_shard=1, max_shards=10)
    reversed_plan = build_shard_plan(reversed(files), max_files_per_shard=1, max_shards=10)

    assert plan == reversed_plan
    assert plan.total_files == 4
    assert sorted(path for shard in plan.shards for path in shard.files) == [
        "a/y.py",
        "a/z.py",
        "b/x.py",
        "root.py",
    ]
    assert all(len(shard.files) == 1 for shard in plan.shards)
    assert [shard.shard_id for shard in plan.shards] == ["a:1", "a:2", "b", "root"]


def test_scope_keeps_only_shard_evidence():
    fact_a = SemanticFact(
        "a.py",
        "reads",
        "resource",
        evidence=(EvidenceRef("a.py", 3),),
    )
    fact_b = SemanticFact(
        "b.py",
        "reads",
        "resource",
        evidence=(EvidenceRef("b.py", 4),),
    )
    pack = EvidencePack.from_facts("q", [fact_a, fact_b])

    scoped = scope_evidence_pack(pack, Shard("a", ("a.py",)))

    assert [fact.subject for fact in scoped.facts] == ["a.py"]
    assert scoped.retrieval_trace[-1] == "shard:a"


def test_merge_dedupes_and_prefers_stronger_suggestion():
    weak = _suggestion("weak", confidence=0.6)
    strong = _suggestion("strong", confidence=0.9)
    outcomes = [
        ShardOutcome("a", "completed", _result([weak])),
        ShardOutcome("b", "completed", _result([strong])),
    ]

    merged = merge_shard_results(outcomes)

    assert len(merged.final_suggestions) == 1
    assert merged.final_suggestions[0]["title"] == "strong"
    assert merged.review_status == "completed"
    assert merged.analysis_report["merge"]["cross_shard_duplicates"] == 1
    assert merged.analysis_report["merge"]["conflicts_resolved"] == 1


def test_merge_marks_partial_when_a_shard_fails():
    outcomes = [
        ShardOutcome("a", "completed", _result([_suggestion("ok")])),
        ShardOutcome("b", "error", error="boom"),
    ]

    merged = merge_shard_results(outcomes)

    assert merged.review_status == "partial"
    assert merged.consensus_reached is False
    assert merged.stage_statuses == {"a": "completed", "b": "error"}


def test_parallel_runner_is_bounded_and_sorted():
    plan = build_shard_plan(
        [f"g{index}/f.py" for index in range(4)],
        max_files_per_shard=1,
        max_shards=10,
    )
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def run_shard(shard):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return _result([_suggestion(shard.shard_id, file_path=f"{shard.shard_id}.py")])

    outcomes = run_sharded_review(plan, run_shard, max_workers=2)

    assert state["peak"] <= 2
    assert [outcome.shard_id for outcome in outcomes] == sorted(
        shard.shard_id for shard in plan.shards
    )
    assert all(outcome.status == "completed" for outcome in outcomes)


def test_parallel_matches_sequential_merge():
    plan = build_shard_plan(
        [f"g{index}/f.py" for index in range(3)],
        max_files_per_shard=1,
        max_shards=10,
    )

    def run_shard(shard):
        return _result(
            [_suggestion(f"finding-{shard.shard_id}", file_path=f"{shard.shard_id}.py")]
        )

    sequential = merge_shard_results(
        run_sharded_review(plan, run_shard, max_workers=1), plan=plan
    )
    parallel = merge_shard_results(
        run_sharded_review(plan, run_shard, max_workers=3), plan=plan
    )

    assert parallel.final_suggestions == sequential.final_suggestions
    assert parallel.stage_statuses == sequential.stage_statuses
    assert parallel.analysis_report["merge"] == sequential.analysis_report["merge"]


def test_deadline_skips_undispatched_shards():
    plan = build_shard_plan(["a/f.py", "b/f.py"], max_files_per_shard=1, max_shards=10)
    called = []

    outcomes = run_sharded_review(
        plan,
        lambda shard: called.append(shard.shard_id),
        max_workers=1,
        deadline=time.monotonic() - 1,
    )

    assert called == []
    assert [outcome.status for outcome in outcomes] == ["skipped_budget", "skipped_budget"]


def test_quick_help_documents_max_workers():
    result = CliRunner().invoke(app, ["quick", "--help"], env={"COLUMNS": "200"})
    plain = re.sub(r"\[[0-9;]*m", "", result.output)

    assert result.exit_code == 0
    assert "--max-workers" in plain
