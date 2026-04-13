#!/usr/bin/env python3
"""
SmartBench 一键启动脚本

最简单的使用方式：直接运行此脚本，按照提示输入参数即可。
"""

import sys
import os
import time

# 确保可以导入 smartbench
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smartbench.agents.orchestrator import OrchestratorAgent
from smartbench.agents.benchmark import BenchmarkAgent
from smartbench.agents.analysis import AnalysisAgent
from smartbench.agents.verification import VerificationAgent
from smartbench.plugins.systems.raft_kv import RaftKVPlugin
from smartbench.core.config import ConfigLoader


def run_pipeline(
    system: str = "raft_kv",
    target_qps: float = 400.0,  # 你的系统实测 363 QPS，设为 400 有挑战性
    rounds: int = 1,
    analysis_rounds: int = 2,
    models: str = "",
    incremental: bool = False,  # 默认关闭增量压测，加速
    cross_validate: bool = True,
    config_path: str = "config/default.yaml",
):
    """
    运行完整的 Multi-Agent Pipeline。

    参数说明:
      target_qps      - 目标 QPS，默认 300
      rounds          - 压测轮次，默认 1
      analysis_rounds - 分析轮次，默认 2
      models          - 模型列表，逗号分隔
      system          - 目标系统
    """
    print("=" * 50)
    print("  SmartBench v0.2 - Multi-Agent 系统")
    print("=" * 50)
    print(f"  目标 QPS:        {target_qps}")
    print(f"  压测轮次:        {rounds}")
    print(f"  分析轮次:        {analysis_rounds}")
    print(f"  模型:           {models or 'deepseek (默认)'}")
    print("=" * 50)

    # Load config
    cfg = ConfigLoader.load(config_path)

    # Get system config
    system_config = cfg.get_system(system)
    if not system_config:
        print(f"[ERROR] 未找到系统配置: {system}")
        return

    # Determine models
    if models:
        model_names = [m.strip() for m in models.split(",")]
        enabled_models = [m for m in cfg.models if m.name in model_names and m.enabled]
    else:
        enabled_models = cfg.get_enabled_models()

    # Initialize plugin
    if system_config.system_type == "raft_kv":
        plugin = RaftKVPlugin(project_path=system_config.project_path)
    else:
        print(f"[ERROR] 不支持的系统类型: {system_config.system_type}")
        return

    print(f"\n[1/4] 启动压测...")
    start = time.time()

    # Create orchestrator
    orchestrator = OrchestratorAgent()

    context = {
        "system_plugin": plugin,
        "target_qps": target_qps,
        "benchmark_rounds": rounds,
        "analysis_rounds": analysis_rounds,
        "models": [m.name for m in enabled_models],
        "model_configs": enabled_models,
        "system_name": system,
        "system_type": system_config.system_type,
        "cross_validation": cross_validate,
        "incremental_analysis": incremental,
        "incremental": incremental,
    }

    result = orchestrator.execute(context)
    duration = time.time() - start

    # Display results
    if result.is_success():
        print(f"\n[OK] Pipeline 执行成功！耗时: {duration:.1f}s")

        # Metrics
        current_qps = result.data.get("current_qps", 0)
        gap = target_qps - current_qps
        gap_pct = (gap / target_qps * 100) if target_qps > 0 else 0
        print(f"\n  当前 QPS: {current_qps:.1f}")
        print(f"  目标 QPS: {target_qps}")
        print(f"  差距:     {gap_pct:.1f}%")

        # Suggestions
        suggestions = result.data.get("suggestions", [])
        if suggestions:
            print(f"\n  优化建议 (共 {len(suggestions)} 条):")
            for i, s in enumerate(suggestions[:5], 1):
                title = s.get("title", "")[:40]
                risk = s.get("risk_level", "medium")
                priority = s.get("priority", 3)
                conf = s.get("self_confidence", 0.5)
                print(f"    {i}. {title}")
                print(f"       风险: {risk} | 优先级: {'*' * priority} | 置信度: {conf:.0%}")
        else:
            print("\n  [WARN] 未生成优化建议")

        # Stages
        stages = result.data.get("stages", [])
        if stages:
            print(f"\n  执行阶段:")
            for stage in stages:
                icon = "OK" if stage.get("status") == "success" else "FAIL"
                print(f"    [{icon}] {stage['stage']}: {stage['duration']:.1f}s")
    else:
        print(f"\n[ERROR] Pipeline 执行失败: {result.error}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="SmartBench 一键启动")
    parser.add_argument("--target-qps", type=float, default=300.0, help="目标 QPS")
    parser.add_argument("--rounds", type=int, default=1, help="压测轮次")
    parser.add_argument("--analysis-rounds", type=int, default=2, help="分析轮次")
    parser.add_argument("--models", type=str, default="", help="模型")
    parser.add_argument("--system", type=str, default="raft_kv", help="目标系统")
    parser.add_argument("--config", type=str, default="config/default.yaml", help="配置文件")
    parser.add_argument("--incremental", action="store_true", help="增量压测")
    parser.add_argument("--no-cross-validate", action="store_true", help="禁用交叉验证")

    args = parser.parse_args()

    run_pipeline(
        system=args.system,
        target_qps=args.target_qps,
        rounds=args.rounds,
        analysis_rounds=args.analysis_rounds,
        models=args.models,
        incremental=args.incremental,
        cross_validate=not args.no_cross_validate,
        config_path=args.config,
    )


if __name__ == "__main__":
    main()
