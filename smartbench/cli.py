"""
SmartBench 命令行入口

提供交互式的性能分析和优化建议生成功能。
"""

import sys
import os
from pathlib import Path
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from smartbench.core.config import Config, ConfigLoader, ModelConfig
from smartbench.core.types import (
    AnalysisContext, 
    AnalysisResult, 
    SystemType,
    Metrics,
    Suggestion,
    RiskLevel,
)
from smartbench.plugins.models.openai_compat import OpenAICompatiblePlugin
from smartbench.plugins.models.anthropic import AnthropicPlugin
from smartbench.plugins.systems.raft_kv import RaftKVPlugin
from smartbench.engine.weight import WeightEngine
from smartbench.engine.aggregator import SuggestionAggregator
from smartbench.engine.generator import DocumentGenerator
from smartbench.engine.cache import CodeCache, get_code_cache
from smartbench.engine.regression import PerformanceRegression, get_regression_engine
from smartbench.engine.debate import DebateEngine, MultiModelAggregator
from smartbench.engine.system_diagnosis import SystemDiagnostician
from smartbench.engine.diagnostic import ProblemType
from smartbench.agents.orchestrator import OrchestratorAgent

# 创建 CLI 应用
app = typer.Typer(
    name="smartbench",
    help="SmartBench - 智能压测与多模型分析工具",
    add_completion=False,
)

console = Console()


def create_model_plugin(model_config: ModelConfig) -> Optional[object]:
    """
    根据配置创建模型插件
    
    Args:
        model_config: 模型配置
        
    Returns:
        模型插件实例或 None
    """
    if not model_config.enabled:
        return None
    
    if model_config.provider == "anthropic":
        return AnthropicPlugin(
            api_key=model_config.api_key,
            model=model_config.model,
            max_retries=model_config.max_retries,
            timeout=model_config.timeout,
        )
    elif model_config.provider in ["openai", "openai_compatible", "dashscope"]:
        return OpenAICompatiblePlugin(
            api_key=model_config.api_key,
            base_url=model_config.base_url,
            model=model_config.model,
            max_retries=model_config.max_retries,
            timeout=model_config.timeout,
        )
    
    return None


def run_model_analysis(
    model_plugin, 
    context: AnalysisContext
) -> AnalysisResult:
    """
    运行单个模型的分析
    
    Args:
        model_plugin: 模型插件
        context: 分析上下文
        
    Returns:
        分析结果
    """
    try:
        return model_plugin.analyze(context)
    except Exception as e:
        return AnalysisResult(
            model_name=model_config.name,
            error=str(e),
        )


def _build_analysis_prompt(metrics, logs: str, error_logs: str, target_qps: float, source_code: dict = None) -> str:
    """构建分析 prompt"""
    prompt = f"""请分析以下 Raft KV 分布式存储系统的性能问题：

## 当前性能指标
- 当前 QPS: {metrics.qps:.1f}
- 目标 QPS: {target_qps}
- 平均延迟: {metrics.avg_latency:.2f} ms
- P99 延迟: {metrics.p99_latency:.2f} ms
- 错误率: {metrics.error_rate:.2%}

## 服务器日志
{logs[:3000] if logs else '无日志数据'}

## 错误日志
{error_logs[:1000] if error_logs else '无错误日志'}

## 要求
1. 每次分析限制在 3 条以内，优先给出最高优先级的建议
2. 评估每个建议的风险等级 (low/medium/high)
3. 给出预期收益估计
4. 提供伪代码实现或具体代码行号

请以 JSON 数组格式输出优化建议，每个建议包含:
- title: 建议标题
- description: 问题分析描述
- pseudocode: 伪代码实现或代码位置
- priority: 优先级 (1-5)
- risk_level: 风险等级 (low/medium/high)
- expected_gain: 预期收益描述
- implementation_steps: 实施步骤列表
- self_confidence: 自评置信度 (0-1)
"""
    return prompt


def _parse_model_suggestions(response: str, model_name: str) -> list:
    """解析模型返回的建议"""
    import json
    import re

    suggestions = []

    # 清理响应：移除 markdown 代码块
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```json?\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    # 尝试提取 JSON
    try:
        # 尝试直接解析
        if cleaned.startswith('['):
            data = json.loads(cleaned)
        elif cleaned.startswith('{'):
            data = json.loads(cleaned)

            # 处理单条建议格式（{"optimization_suggestion": ..., "rationale": ...}）
            if "optimization_suggestion" in data or "suggestion" in data:
                title = data.get("optimization_suggestion", data.get("suggestion", ""))
                description = data.get("rationale", data.get("description", ""))
                suggestions.append({
                    "title": title[:100] if title else "优化建议",
                    "description": description,
                    "priority": 3,
                    "risk_level": "medium",
                    "expected_gain": data.get("expected_gain", "性能提升"),
                    "implementation_steps": data.get("implementation_steps", []),
                    "source_model": model_name,
                })
                return suggestions

            data = data.get("suggestions", data.get("data", data))
        else:
            # 尝试提取 JSON 部分
            json_match = re.search(r'\{[\s\S]*\}|\[[\s\S]*\]', response)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    if isinstance(data, dict):
                        if "optimization_suggestion" in data or "suggestion" in data:
                            title = data.get("optimization_suggestion", data.get("suggestion", ""))
                            description = data.get("rationale", data.get("description", ""))
                            suggestions.append({
                                "title": title[:100] if title else "优化建议",
                                "description": description,
                                "priority": 3,
                                "risk_level": "medium",
                                "source_model": model_name,
                            })
                            return suggestions
                        data = data.get("suggestions", data.get("data", data))
                except json.JSONDecodeError:
                    data = []
            else:
                data = []
    except Exception:
        data = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                item["source_model"] = model_name
                suggestions.append(item)

    return suggestions


def _build_code_snippets(source_code: dict, logs: str, max_length: int = 6000) -> str:
    """构建供 AI 分析的代码片段"""
    if not source_code:
        return logs[:max_length] if logs else ""

    parts = []

    for file_path, content in source_code.items():
        lines = content.splitlines()[:100]
        snippet = '\n'.join(lines)
        parts.append(f"=== {file_path} ===\n{snippet}")

    if logs:
        parts.append(f"=== 服务器日志 ===\n{logs[:2000]}")

    result = '\n\n'.join(parts)
    return result[:max_length]


def _parallel_model_call(
    enabled_models: List,
    metrics: dict,
    logs: str,
    code_snippets: str,
) -> list:
    """并行调用多个模型"""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    prompt = f"""请分析以下 Raft KV 分布式存储系统的性能问题：

## 当前性能指标
- 当前 QPS: {metrics['qps']:.1f}
- 目标 QPS: {metrics['target_qps']}
- 平均延迟: {metrics['avg_latency']:.2f} ms
- P99 延迟: {metrics['p99_latency']:.2f} ms

## 服务器日志和关键代码
{code_snippets[:6000]}

## 分析要求
1. 识别主要性能瓶颈
2. 提出 1-3 条具体可实施的优化建议
3. 每条建议需包含: 标题、问题分析、伪代码、优先级(1-5)、风险等级(low/medium/high)
4. 输出格式为严格 JSON 数组

请直接输出 JSON 数组，不要有任何前缀或后缀文字。"""

    def call_model(model_config):
        """调用单个模型"""
        try:
            headers = {
                "Authorization": f"Bearer {model_config.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": model_config.model,
                "messages": [
                    {"role": "system", "content": "你是一个资深的分布式系统性能优化专家。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 2000,
            }

            response = requests.post(
                f"{model_config.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=model_config.timeout,
            )

            if response.status_code != 200:
                return {
                    "model": model_config.name,
                    "success": False,
                    "error": f"API error: {response.status_code}",
                    "suggestions": []
                }

            result_data = response.json()
            response_text = result_data["choices"][0]["message"]["content"]

            parsed = _parse_model_suggestions(response_text, model_config.name)

            return {
                "model": model_config.name,
                "success": True,
                "response": response_text,
                "suggestions": parsed
            }
        except Exception as e:
            return {
                "model": model_config.name,
                "success": False,
                "error": str(e),
                "suggestions": []
            }

    results = []
    with ThreadPoolExecutor(max_workers=len(enabled_models)) as executor:
        futures = {executor.submit(call_model, m): m for m in enabled_models}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass

    return results


def _deduplicate_suggestions(suggestions: list) -> list:
    """去重建议（基于标题相似度）"""
    if not suggestions:
        return []

    unique = []
    for s in suggestions:
        title = s.get("title", "").lower()
        is_duplicate = False

        for u in unique:
            u_title = u.get("title", "").lower()
            if title[:20] == u_title[:20]:
                if s.get("priority", 3) > u.get("priority", 3):
                    unique.remove(u)
                else:
                    is_duplicate = True
                    break

        if not is_duplicate:
            unique.append(s)

    return unique


@app.command()
def analyze(
    system: str = typer.Option("raft_kv", help="目标系统名称"),
    target_qps: float = typer.Option(300.0, help="目标 QPS"),
    config: str = typer.Option("config/default.yaml", help="配置文件路径"),
    models: Optional[str] = typer.Option(None, help="指定使用的模型，逗号分隔"),
    skip_benchmark: bool = typer.Option(False, help="跳过压测，使用上次结果"),
    verbose: bool = typer.Option(False, help="详细输出"),
):
    """
    运行性能分析与优化建议生成

    示例:
        smartbench analyze --system raft_kv --target-qps 300
        smartbench analyze --models deepseek,claude
    """
    console.print(Panel.fit(
        "[bold blue]SmartBench v0.2[/bold blue] - 智能压测与多模型分析工具",
        border_style="blue"
    ))

    # 加载配置
    try:
        cfg = _load_config(config)
    except FileNotFoundError:
        console.print(f"[red]配置文件不存在: {config}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]配置加载失败: {e}[/red]")
        sys.exit(1)

    # 获取系统配置
    system_config = cfg.get_system(system)
    if not system_config:
        console.print(f"[red]未找到系统配置: {system}[/red]")
        sys.exit(1)

    # 确定使用的模型
    if models:
        model_names = [m.strip() for m in models.split(",")]
        enabled_models = [m for m in cfg.models if m.name in model_names and m.enabled]
    else:
        enabled_models = cfg.get_enabled_models()

    if not enabled_models:
        console.print("[red]没有可用的模型配置[/red]")
        sys.exit(1)

    # 初始化系统插件
    console.print(f"\n[cyan]目标系统:[/cyan] {system_config.name} ({system_config.system_type})")
    console.print(f"[cyan]目标 QPS:[/cyan] {target_qps}")
    console.print(f"[cyan]可用模型:[/cyan] {', '.join(m.name for m in enabled_models)}")

    # Step 1: 运行压测
    console.print("\n[bold yellow]Step 1/4:[/bold yellow] 运行压测...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("正在运行压测...", total=None)

        if system_config.system_type == "raft_kv":
            system_plugin = RaftKVPlugin(project_path=system_config.project_path)
        else:
            console.print(f"[red]不支持的系统类型: {system_config.system_type}[/red]")
            sys.exit(1)

        metrics = system_plugin.get_metrics()

        progress.update(task, completed=True)

    # 显示压测结果
    _display_metrics(metrics, target_qps)

    if metrics.qps == 0:
        console.print("\n[red]压测失败，请检查系统是否正常运行[/red]")
        sys.exit(1)

    # 检查是否达标
    if metrics.qps >= target_qps:
        console.print("\n[green]✅ 性能达标，无需优化！[/green]")
        return

    # Step 2: 采集上下文
    console.print("\n[bold yellow]Step 2/4:[/bold yellow] 采集系统数据...")

    with Progress(console=console) as progress:
        task = progress.add_task("采集日志和配置...", total=None)

        logs = system_plugin.get_logs(lines=200)
        configs = system_plugin.get_config()
        key_files = system_plugin.get_key_source_files()

        # 选择关键源码
        source_code = ""
        priority_files = ["Raft/Raft.cpp", "KvServer/KvServer.cpp", "Skiplist-CPP/skiplist.h"]
        for pf in priority_files:
            if pf in key_files:
                source_code += f"\n\n=== {pf} ===\n" + key_files[pf][:2000]

        progress.update(task, completed=True)

    # 构建分析上下文
    context = AnalysisContext(
        system_name=system,
        system_type=SystemType.RAFT_KV,
        metrics=metrics,
        logs=logs,
        source_code=source_code,
        config=configs,
        target_qps=target_qps,
    )

    # Step 3: 多模型分析
    console.print("\n[bold yellow]Step 3/4:[/bold yellow] 多模型分析...")

    # 创建模型插件
    model_plugins = []
    for model_config in enabled_models:
        plugin = create_model_plugin(model_config)
        if plugin:
            model_plugins.append((model_config, plugin))

    # 并发调用模型
    results: List[AnalysisResult] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"模型分析 (0/{len(model_plugins)})", total=len(model_plugins))

        with ThreadPoolExecutor(max_workers=len(model_plugins)) as executor:
            futures = {
                executor.submit(run_model_analysis, plugin, context): plugin.name
                for _, plugin in model_plugins
            }

            for future in as_completed(futures):
                model_name = futures[future]
                try:
                    result = future.result()
                    results.append(result)

                    if result.is_success:
                        progress.update(
                            task,
                            description=f"✓ {model_name} 完成 ({len(results)}/{len(model_plugins)})"
                        )
                    else:
                        progress.update(
                            task,
                            description=f"✗ {model_name} 失败 ({len(results)}/{len(model_plugins)})"
                        )
                        if verbose:
                            console.print(f"  [dim]{result.error}[/dim]")

                except Exception as e:
                    if verbose:
                        console.print(f"  [red]模型 {model_name} 异常: {e}[/red]")

                progress.advance(task)

    # 显示模型分析结果
    _display_model_results(results)

    if not results:
        console.print("\n[red]所有模型分析均失败[/red]")
        sys.exit(1)

    # Step 4: 聚合与生成报告
    console.print("\n[bold yellow]Step 4/4:[/bold yellow] 聚合建议并生成报告...")

    # 初始化引擎
    weight_engine = WeightEngine(
        history_db_path=cfg.weight_engine.history_db_path,
        confidence_threshold=cfg.weight_engine.confidence_threshold,
    )
    aggregator = SuggestionAggregator(
        weight_engine=weight_engine,
        confidence_threshold=cfg.weight_engine.confidence_threshold,
    )
    generator = DocumentGenerator(
        output_dir=cfg.output_dir,
        data_dir=cfg.data_dir,
    )

    # 聚合建议
    suggestions = aggregator.aggregate(results, cfg.weight_engine.max_suggestions)

    # 显示建议摘要
    _display_suggestions(suggestions)

    # 生成报告
    report = generator.generate(
        suggestions=suggestions,
        metrics=metrics,
        target_qps=target_qps,
        system_name=system,
        system_type=system_config.system_type,
    )

    console.print(f"\n[green]✅ 报告已生成: {report.report_path}[/green]")

    # 显示实施建议
    if suggestions:
        console.print("\n[bold]建议实施顺序:[/bold]")
        for i, s in enumerate(suggestions[:3], 1):
            risk_color = "green" if s.risk_level == RiskLevel.LOW else "yellow" if s.risk_level == RiskLevel.MEDIUM else "red"
            console.print(f"  {i}. [{risk_color}]{s.title}[/{risk_color}] ({s.risk_level.value} 风险)")

    console.print(f"\n[dim]提示: 使用 Claude Code 等工具实现优化建议[/dim]")


@app.command()
def stats(
    config: str = typer.Option("config/default.yaml", help="配置文件路径"),
):
    """
    显示模型统计信息
    """
    try:
        cfg = _load_config(config)
    except FileNotFoundError:
        console.print(f"[red]配置文件不存在: {config}[/red]")
        sys.exit(1)
    
    # 初始化权重引擎
    weight_engine = WeightEngine(history_db_path=cfg.weight_engine.history_db_path)
    
    # 获取所有模型的统计
    stats_list = weight_engine.get_all_stats()
    
    if not stats_list:
        console.print("[yellow]暂无模型使用记录[/yellow]")
        return
    
    # 显示表格
    table = Table(title="模型统计信息")
    table.add_column("模型名称", style="cyan")
    table.add_column("总建议数", justify="right")
    table.add_column("采纳数", justify="right")
    table.add_column("准确率", justify="right")
    table.add_column("当前权重", justify="right")
    
    for stat in stats_list:
        accuracy = f"{stat['accuracy']:.1%}" if stat['accuracy'] else "N/A"
        table.add_row(
            stat['model_name'],
            str(stat['total_suggestions']),
            str(stat['adopted_suggestions']),
            accuracy,
            f"{stat['weight']:.2f}",
        )
    
    console.print(table)


@app.command()
def reset_stats(
    config: str = typer.Option("config/default.yaml", help="配置文件路径"),
    confirm: bool = typer.Option(False, "--yes", help="确认重置"),
):
    """
    重置模型统计信息
    """
    if not confirm:
        console.print("[yellow]确认删除所有历史记录？[/yellow]")
        console.print("[yellow]使用 --yes 参数确认[/yellow]")
        return

    try:
        cfg = _load_config(config)
    except FileNotFoundError:
        console.print(f"[red]配置文件不存在: {config}[/red]")
        sys.exit(1)

    weight_engine = WeightEngine(history_db_path=cfg.weight_engine.history_db_path)
    weight_engine.reset_history()

    console.print("[green]✅ 历史记录已重置[/green]")


@app.command()
def run(
    system: str = typer.Option("raft_kv", help="目标系统名称"),
    target_qps: float = typer.Option(400.0, help="目标 QPS（你的系统实测 363 QPS）"),
    rounds: int = typer.Option(1, help="压测轮次"),
    analysis_rounds: int = typer.Option(2, help="分析轮次（多轮交叉验证）"),
    models: str = typer.Option(None, help="使用的模型，逗号分隔"),
    config: str = typer.Option("config/default.yaml", help="配置文件路径"),
    incremental: bool = typer.Option(True, help="增量压测"),
    cross_validate: bool = typer.Option(True, help="多模型交叉验证"),
    verbose: bool = typer.Option(False, help="详细输出"),
):
    """
    统一运行完整的多 Agent 流程

    执行压测 -> 多模型分析 -> 交叉验证 -> 生成报告
    自动完成压测、分析全流程。

    示例:
        smartbench run --system raft_kv --target-qps 400
        smartbench run --rounds 3 --analysis-rounds 2 --models deepseek,glm-4.7
    """
    console.print(Panel.fit(
        "[bold blue]SmartBench v0.3[/bold blue] - Raft KV 专用版",
        border_style="blue"
    ))

    # Load configuration
    try:
        cfg = _load_config(config)
    except FileNotFoundError:
        console.print(f"[red]配置文件不存在: {config}[/red]")
        sys.exit(1)
    except ValueError as e:
        if "API Key" in str(e):
            console.print(f"[red]❌ API Key 未配置: {e}[/red]")
            console.print("\n[yellow]请先配置 API Key:[/yellow]")
            console.print("  export DEEPSEEK_API_KEY='your-key'")
            console.print("  或编辑 config/default.yaml")
            sys.exit(1)
        console.print(f"[red]配置加载失败: {e}[/red]")
        sys.exit(1)

    # Get system configuration
    system_config = cfg.get_system(system)
    if not system_config:
        console.print(f"[red]未找到系统配置: {system}[/red]")
        sys.exit(1)

    # Determine models to use
    if models:
        model_names = [m.strip() for m in models.split(",")]
        enabled_models = [m for m in cfg.models if m.name in model_names and m.enabled]
    else:
        enabled_models = cfg.get_enabled_models()

    if not enabled_models:
        console.print("[red]没有可用的模型配置[/red]")
        sys.exit(1)

    console.print(f"\n[cyan]目标系统:[/cyan] {system_config.name}")
    console.print(f"[cyan]目标 QPS:[/cyan] {target_qps}")
    console.print(f"[cyan]分析轮次:[/cyan] {analysis_rounds}")
    console.print(f"[cyan]使用模型:[/cyan] {', '.join([m.name for m in enabled_models])}")
    console.print("")

    # Initialize system plugin
    if system_config.system_type == "raft_kv":
        system_plugin = RaftKVPlugin(project_path=system_config.project_path)
    else:
        console.print(f"[red]不支持的系统类型: {system_config.system_type}[/red]")
        sys.exit(1)

    # 计算总步骤数
    total_models = len(enabled_models)
    total_steps = 2 + total_models  # 压测 + 上下文 + 每个模型分析

    # 定义步骤进度
    steps = [
        ("启动集群", 10),
        ("压测", 30),
        ("收集日志", 15),
        ("辩论分析", 35),
        ("生成报告", 10),
    ]

    # 使用进度条执行 pipeline
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        main_task = progress.add_task("[bold blue]SmartBench 执行中", total=100)

        # Step 1: 启动集群和压测
        progress.update(main_task, description="[yellow]📊 步骤 1/5: 启动集群...", completed=steps[0][1])

        try:
            # 快速预热
            warmup_ok = False
            if hasattr(system_plugin, 'fast_warmup'):
                try:
                    warmup_ok = system_plugin.fast_warmup(ops=50, threads=2)
                except Exception:
                    pass

            # 更新压测进度
            progress.update(main_task, description="[yellow]📊 步骤 2/5: 执行压测...", completed=steps[0][1] + steps[1][1])

            # 执行压测
            metrics = system_plugin.get_metrics()

            progress.update(main_task, description=f"[green]✓ 压测完成 (QPS={metrics.qps:.0f})", completed=steps[0][1] + steps[1][1] + steps[2][1])

        except Exception as e:
            console.print(f"\n[red]压测失败: {e}[/red]")
            if verbose:
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
            sys.exit(1)

        # Step 2: 获取日志和上下文（使用缓存）
        progress.update(main_task, description="[yellow]📝 步骤 3/5: 收集日志和源码...", completed=60)

        logs = ""
        error_logs = ""
        source_code = {}

        # 初始化代码缓存
        code_cache = get_code_cache()

        try:
            if hasattr(system_plugin, 'get_logs'):
                logs = system_plugin.get_logs(lines=200)
            if hasattr(system_plugin, 'get_error_logs'):
                error_logs = system_plugin.get_error_logs(lines=50)
            if hasattr(system_plugin, 'get_key_source_files'):
                source_code = code_cache.get_key_files(system_config.project_path)

            progress.update(main_task, description="[cyan]✓ 日志和源码收集完成", completed=65)
        except Exception:
            progress.update(main_task, description="[cyan]✓ 日志收集完成", completed=65)

        # 构建代码片段（供 AI 分析）
        code_snippets = _build_code_snippets(source_code, logs)

        # Steps 3-N: 使用辩论引擎进行多模型分析
        progress.update(main_task, description="[yellow]🤖 步骤 4/5: 启动辩论引擎...", completed=70)

        # 获取 API 配置
        api_config = {
            "api_key": enabled_models[0].api_key if enabled_models else "e82347db-15de-4d09-bfff-9b155a445873",
            "base_url": enabled_models[0].base_url if enabled_models else "https://ark.cn-beijing.volces.com/api/v3",
            "model": enabled_models[0].model if enabled_models else "deepseek-v3-2-251201",
        }

        # 运行辩论引擎
        metrics_dict = {
            "qps": metrics.qps,
            "target_qps": target_qps,
            "avg_latency": metrics.avg_latency,
            "p99_latency": metrics.p99_latency,
            "error_rate": metrics.error_rate,
        }

        # 尝试使用辩论引擎
        try:
            progress.update(main_task, description="[yellow]🤖 Proposer 分析中...", completed=75)

            debate_engine = DebateEngine(
                api_config=api_config,
                code_cache=code_cache,
                max_iterations=2,
                timeout_per_call=120,  # 增加超时到 120 秒
            )

            debate_result = debate_engine.run_debate(
                metrics=metrics_dict,
                code_snippets=code_snippets,
                logs=logs,
            )

            suggestions = debate_result.final_suggestions
            debate_log = debate_result.debate_log

            # 如果辩论失败，回退到多模型
            if not suggestions:
                console.print("[yellow]⚠️ 辩论引擎返回空，建议使用多模型聚合模式[/yellow]")
                progress.update(main_task, description=f"[yellow]⚠ 回退到多模型聚合模式...", completed=75)

                analysis_results = _parallel_model_call(enabled_models, metrics_dict, logs, code_snippets)

                aggregator = MultiModelAggregator([{"name": m.name, "weight": m.default_weight} for m in enabled_models])
                suggestions = aggregator.aggregate(analysis_results)
                debate_log = []

                progress.update(main_task, description=f"[green]✓ {len([r for r in analysis_results if r.get('success')])} 个模型分析完成", completed=90)

            progress.update(main_task, description=f"[green]✓ 辩论完成 ({len(suggestions)} 条建议)", completed=90)

        except Exception as e:
            # 回退到多模型聚合
            progress.update(main_task, description="[yellow]🤖 辩论引擎不可用，使用多模型分析...", completed=75)

            # 并行调用多个模型
            analysis_results = _parallel_model_call(enabled_models, metrics_dict, logs, code_snippets)

            # 聚合结果
            aggregator = MultiModelAggregator([{"name": m.name, "weight": m.default_weight} for m in enabled_models])
            suggestions = aggregator.aggregate(analysis_results)
            debate_log = []

            success_count = len([r for r in analysis_results if r.get('success')])
            progress.update(main_task, description=f"[green]✓ 多模型分析完成 ({success_count}/{len(analysis_results)} 成功)", completed=90)

        # 去重和排序
        suggestions = _deduplicate_suggestions(suggestions)
        suggestions = sorted(suggestions, key=lambda x: x.get("priority", 3), reverse=True)[:5]

        # Step N+1: 生成报告
        progress.update(main_task, description="[yellow]📄 生成分析报告...", completed=95)

        # 记录性能快照
        regression = get_regression_engine()
        snapshot_id = regression.record_snapshot(
            qps=metrics.qps,
            avg_latency=metrics.avg_latency,
            p99_latency=metrics.p99_latency,
            error_rate=metrics.error_rate,
            target_qps=target_qps,
            notes=f"分析生成 {len(suggestions)} 条建议",
        )

        progress.update(main_task, description="[green]✓ 完成！", completed=100)

    # 显示结果
    console.print("")
    console.print(Panel.fit(
        "[bold green]✅ 分析完成！[/bold green]",
        border_style="green"
    ))

    # 显示指标
    metrics_table = Table(title="性能指标", show_header=True)
    metrics_table.add_column("指标", style="cyan")
    metrics_table.add_column("值", justify="right", style="yellow")
    metrics_table.add_row("当前 QPS", f"{metrics.qps:.1f}")
    metrics_table.add_row("目标 QPS", f"{target_qps}")
    metrics_table.add_row("平均延迟", f"{metrics.avg_latency:.2f} ms")
    metrics_table.add_row("P99 延迟", f"{metrics.p99_latency:.2f} ms")
    metrics_table.add_row("错误率", f"{metrics.error_rate:.2%}")
    console.print(metrics_table)
    console.print("")

    # 显示使用的模型
    if 'debate_result' in dir() and debate_result:
        console.print(f"[cyan]分析模型:[/cyan] 辩论引擎 (Proposer/Critique/Judge)")
    else:
        console.print(f"[cyan]分析模型:[/cyan] {', '.join([m.name for m in enabled_models])}")
    console.print("")

    # 显示建议
    if suggestions:
        console.print(f"[bold]📋 优化建议 (共 {len(suggestions)} 条):[/bold]")
        console.print("")

        for i, s in enumerate(suggestions, 1):
            risk = s.get("risk_level", "medium")
            priority = s.get("priority", 3)
            rank = s.get("rank", i)
            status = s.get("status", "unknown")

            risk_emoji = "🟢" if risk == "low" else "🟡" if risk == "medium" else "🔴"
            priority_stars = "⭐" * min(priority, 5)
            status_emoji = "✅" if status == "accepted" else "⚠️" if status == "modified" else "❌"

            console.print(f"[cyan]{rank}.[/cyan] [bold]{s.get('title', 'Unknown')}[/bold] {risk_emoji} {status_emoji}")
            console.print(f"   优先级: {priority_stars} | 风险: {risk.upper()}")
            console.print(f"")

            # 问题分析
            problem = s.get('problem_summary', s.get('description', s.get('problem_detail', '')))
            if problem:
                console.print(f"   [yellow]📊 问题分析:[/yellow]")
                console.print(f"   {problem}")
                console.print("")

            # 根本原因
            root_cause = s.get('root_cause', '')
            if root_cause:
                console.print(f"   [red]🔍 根本原因:[/red]")
                console.print(f"   {root_cause}")
                console.print("")

            # 实施步骤
            steps = s.get('implementation_steps', [])
            if steps:
                console.print(f"   [blue]📝 实施步骤:[/blue]")
                for j, step in enumerate(steps[:3], 1):
                    console.print(f"   {j}. {step}")
                console.print("")

            # 伪代码
            pseudocode = s.get('pseudocode', '')
            if pseudocode:
                console.print(f"   [green]💻 核心代码:[/green]")
                console.print(f"   ```cpp")
                for line in pseudocode.split('\n')[:6]:
                    console.print(f"   {line}")
                console.print(f"   ```")
                console.print("")

            # 预期效果
            expected = s.get('expected_result', s.get('expected_improvement', {}))
            if expected:
                qps_before = expected.get('qps_before', metrics.qps)
                qps_after = expected.get('qps_after', 0)
                qps_imp = expected.get('qps_improvement_percent', 'N/A')

                latency_before = expected.get('latency_before_ms', metrics.avg_latency)
                latency_after = expected.get('latency_after_ms', 0)
                latency_imp = expected.get('latency_improvement_percent', 'N/A')

                console.print(f"   [magenta]📈 预期效果:[/magenta]")
                console.print(f"   QPS: {qps_before:.0f} → {qps_after:.0f} ({qps_imp})")
                console.print(f"   延迟: {latency_before:.2f}ms → {latency_after:.2f}ms ({latency_imp})")
                console.print("")

            # 代码位置
            location = s.get('location', '')
            if location:
                console.print(f"   [dim]📍 位置:[/dim] {location}")

            console.print("-" * 60)
            console.print("")
    else:
        console.print("[yellow]未能生成有效建议[/yellow]")
        console.print("")

    console.print(f"[dim]💡 提示: 查看详细报告请使用 --verbose 参数[/dim]")


def _build_analysis_prompt(metrics, logs: str, error_logs: str, target_qps: float, source_code: dict = None) -> str:
    """构建分析 prompt"""
    prompt = f"""请分析以下 Raft KV 分布式存储系统的性能问题：

## 当前性能指标
- 当前 QPS: {metrics.qps:.1f}
- 目标 QPS: {target_qps}
- 平均延迟: {metrics.avg_latency:.2f} ms
- P99 延迟: {metrics.p99_latency:.2f} ms
- 错误率: {metrics.error_rate:.2%}

## 服务器日志
{logs[:3000] if logs else '无日志数据'}

## 错误日志
{error_logs[:1000] if error_logs else '无错误日志'}

## 要求
1. 每次分析限制在 3 条以内，优先给出最高优先级的建议
2. 评估每个建议的风险等级 (low/medium/high)
3. 给出预期收益估计
4. 提供伪代码实现或具体代码行号

请以 JSON 数组格式输出优化建议，每个建议包含:
- title: 建议标题
- description: 问题分析描述
- pseudocode: 伪代码实现或代码位置
- priority: 优先级 (1-5)
- risk_level: 风险等级 (low/medium/high)
- expected_gain: 预期收益描述
- implementation_steps: 实施步骤列表
- self_confidence: 自评置信度 (0-1)
"""
    return prompt


def _parse_model_suggestions(response: str, model_name: str) -> list:
    """解析模型返回的建议"""
    import json
    import re

    suggestions = []

    # 清理响应：移除 markdown 代码块
    cleaned = response.strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```json?\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    # 尝试提取 JSON
    try:
        # 尝试直接解析
        if cleaned.startswith('['):
            data = json.loads(cleaned)
        elif cleaned.startswith('{'):
            data = json.loads(cleaned)

            # 处理单条建议格式（{"optimization_suggestion": ..., "rationale": ...}）
            if "optimization_suggestion" in data or "suggestion" in data:
                title = data.get("optimization_suggestion", data.get("suggestion", ""))
                description = data.get("rationale", data.get("description", ""))
                suggestions.append({
                    "title": title[:100] if title else "优化建议",
                    "description": description,
                    "priority": 3,
                    "risk_level": "medium",
                    "expected_gain": data.get("expected_gain", "性能提升"),
                    "implementation_steps": data.get("implementation_steps", []),
                    "source_model": model_name,
                })
                return suggestions

            data = data.get("suggestions", data.get("data", data))
        else:
            # 尝试提取 JSON 部分
            json_match = re.search(r'\{[\s\S]*\}|\[[\s\S]*\]', response)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                    if isinstance(data, dict):
                        if "optimization_suggestion" in data or "suggestion" in data:
                            title = data.get("optimization_suggestion", data.get("suggestion", ""))
                            description = data.get("rationale", data.get("description", ""))
                            suggestions.append({
                                "title": title[:100] if title else "优化建议",
                                "description": description,
                                "priority": 3,
                                "risk_level": "medium",
                                "source_model": model_name,
                            })
                            return suggestions
                        data = data.get("suggestions", data.get("data", data))
                except json.JSONDecodeError:
                    data = []
            else:
                data = []
    except Exception:
        data = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                item["source_model"] = model_name
                suggestions.append(item)

    return suggestions


def _build_code_snippets(source_code: dict, logs: str, max_length: int = 6000) -> str:
    """构建供 AI 分析的代码片段"""
    if not source_code:
        return logs[:max_length] if logs else ""

    parts = []

    for file_path, content in source_code.items():
        lines = content.splitlines()[:100]
        snippet = '\n'.join(lines)
        parts.append(f"=== {file_path} ===\n{snippet}")

    if logs:
        parts.append(f"=== 服务器日志 ===\n{logs[:2000]}")

    result = '\n\n'.join(parts)
    return result[:max_length]


def _parallel_model_call(
    enabled_models: List,
    metrics: dict,
    logs: str,
    code_snippets: str,
) -> list:
    """并行调用多个模型"""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    prompt = f"""请分析以下 Raft KV 分布式存储系统的性能问题：

## 当前性能指标
- 当前 QPS: {metrics['qps']:.1f}
- 目标 QPS: {metrics['target_qps']}
- 平均延迟: {metrics['avg_latency']:.2f} ms
- P99 延迟: {metrics['p99_latency']:.2f} ms

## 服务器日志和关键代码
{code_snippets[:6000]}

## 分析要求
1. 识别主要性能瓶颈
2. 提出 1-3 条具体可实施的优化建议
3. 每条建议需包含: 标题、问题分析、伪代码、优先级(1-5)、风险等级(low/medium/high)
4. 输出格式为严格 JSON 数组

请直接输出 JSON 数组，不要有任何前缀或后缀文字。"""

    def call_model(model_config):
        """调用单个模型"""
        try:
            headers = {
                "Authorization": f"Bearer {model_config.api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": model_config.model,
                "messages": [
                    {"role": "system", "content": "你是一个资深的分布式系统性能优化专家。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 2000,
            }

            response = requests.post(
                f"{model_config.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=model_config.timeout,
            )

            if response.status_code != 200:
                return {
                    "model": model_config.name,
                    "success": False,
                    "error": f"API error: {response.status_code}",
                    "suggestions": []
                }

            result_data = response.json()
            response_text = result_data["choices"][0]["message"]["content"]

            parsed = _parse_model_suggestions(response_text, model_config.name)

            return {
                "model": model_config.name,
                "success": True,
                "response": response_text,
                "suggestions": parsed
            }
        except Exception as e:
            return {
                "model": model_config.name,
                "success": False,
                "error": str(e),
                "suggestions": []
            }

    results = []
    with ThreadPoolExecutor(max_workers=len(enabled_models)) as executor:
        futures = {executor.submit(call_model, m): m for m in enabled_models}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass

    return results


def _deduplicate_suggestions(suggestions: list) -> list:
    """去重建议（基于标题相似度）"""
    if not suggestions:
        return []

    unique = []
    for s in suggestions:
        title = s.get("title", "").lower()
        is_duplicate = False

        for u in unique:
            u_title = u.get("title", "").lower()
            if title[:20] == u_title[:20]:
                if s.get("priority", 3) > u.get("priority", 3):
                    unique.remove(u)
                else:
                    is_duplicate = True
                    break

        if not is_duplicate:
            unique.append(s)

    return unique


@app.command()
def check(
    system: str = typer.Option("raft_kv", help="目标系统名称"),
    project_path: str = typer.Option(None, help="项目路径 (覆盖配置文件)"),
):
    """
    检查被测系统健康状态

    示例:
        smartbench check --system raft_kv
        smartbench check --system raft_kv --project-path /path/to/project
    """
    console.print(Panel.fit(
        "[bold blue]SmartBench v0.2[/bold blue] - 系统健康检查",
        border_style="blue"
    ))

    # 尝试从配置文件读取系统配置
    system_config_data = None
    for config_path in ["config/default.yaml", "config/dev.yaml"]:
        try:
            import yaml
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
            for sys in config_data.get('systems', []):
                if sys.get('name') == system:
                    system_config_data = sys
                    break
            if system_config_data:
                break
        except Exception:
            continue

    # 确定项目路径
    if project_path:
        config_project_path = project_path
    elif system_config_data:
        config_project_path = system_config_data.get('project_path', '')
    else:
        config_project_path = None

    # 显示配置信息
    console.print(f"[cyan]目标系统:[/cyan] {system}")
    if config_project_path:
        console.print(f"[cyan]项目路径:[/cyan] {config_project_path}")
    console.print("")

    if system == "raft_kv":
        plugin = RaftKVPlugin(
            project_path=config_project_path or "/home/xianyu-sheng/MyKV_storageBase_Raft_cpp"
        )

        # 检查集群健康
        health = plugin.get_cluster_health()

        # 显示结果
        table = Table(title="集群健康检查", show_header=True)
        table.add_column("检查项", style="cyan")
        table.add_column("状态", justify="center")
        table.add_column("详情", style="dim")

        status_icon = "✅" if health["healthy"] else "❌"
        table.add_row("集群状态", status_icon, health["reason"])

        leader_icon = "✅" if health["leader_elected"] else "❌"
        table.add_row("Leader 选举", leader_icon, str(health["leader_elected"]))

        ready_icon = "✅" if health["all_nodes_ready"] else "⚠️"
        table.add_row("节点就绪", ready_icon, str(health["all_nodes_ready"]))

        table.add_row("节点数量", "ℹ️", f"{health['node_count']}/3")

        console.print(table)

        # 显示错误
        if health["errors"]:
            console.print("\n[bold yellow]⚠️  错误信息:[/bold yellow]")
            for error in health["errors"][:5]:
                console.print(f"  • {error}")
        else:
            console.print("\n[green]✅ 未发现错误[/green]")

    else:
        console.print(f"[red]不支持的系统类型: {system}[/red]")
        sys.exit(1)


@app.command()
def regression(
    days: int = typer.Option(7, help="分析天数"),
    metric: str = typer.Option("qps", help="指标类型: qps, avg_latency, p99_latency, error_rate"),
):
    """
    显示性能回归分析和趋势

    示例:
        smartbench regression --days 7
        smartbench regression --metric qps
    """
    console.print(Panel.fit(
        "[bold blue]性能回归分析[/bold blue]",
        border_style="blue"
    ))

    # 获取回归引擎
    regression = get_regression_engine()

    # 获取趋势分析
    trend = regression.analyze_trend(metric, days)

    # 显示趋势
    trend_icon = "📈" if trend.trend.value == "improving" else "📉" if trend.trend.value == "degrading" else "➡️"
    console.print(f"\n{trend_icon} [bold]{metric.upper()}[/bold] 趋势分析")
    console.print(f"   当前值: {trend.current:.2f}")
    console.print(f"   起始值: {trend.previous:.2f}")
    console.print(f"   变化率: {trend.change_percent:+.1f}%")
    console.print(f"   趋势: {trend.trend.value}")
    console.print("")

    # 显示历史快照
    recent = regression.get_latest(10)
    if recent:
        console.print(f"[bold]最近 {len(recent)} 次测试记录:[/bold]")
        table = Table(show_header=True)
        table.add_column("时间", style="cyan")
        table.add_column("QPS", justify="right", style="yellow")
        table.add_column("延迟", justify="right")
        table.add_column("错误率", justify="right")

        for s in sorted(recent, key=lambda x: x.timestamp, reverse=True):
            time_str = s.timestamp[:16].replace('T', ' ')
            table.add_row(
                time_str,
                f"{s.qps:.0f}",
                f"{s.avg_latency:.1f}ms",
                f"{s.error_rate:.2%}"
            )

        console.print(table)
    else:
        console.print("[yellow]暂无历史数据[/yellow]")


@app.command()
def analyze(
    project_path: str = typer.Option("/home/xianyu-sheng/MyKV_storageBase_Raft_cpp", help="项目路径"),
    suggestions_file: str = typer.Option(None, help="建议文件路径 (JSON 格式)"),
):
    """
    分析优化建议的代码位置和可行性

    示例:
        smartbench analyze --project-path /path/to/project
        smartbench analyze --suggestions-file suggestions.json
    """
    from smartbench.engine.compiler import CodeAnalyzer, ChangeExtractor

    console.print(Panel.fit(
        "[bold blue]优化建议分析[/bold blue]",
        border_style="blue"
    ))

    console.print(f"[cyan]项目路径:[/cyan] {project_path}")
    console.print("")

    # 初始化分析器
    analyzer = CodeAnalyzer(project_path)
    extractor = ChangeExtractor(project_path)

    # 从文件读取建议或使用测试建议
    if suggestions_file and Path(suggestions_file).exists():
        with open(suggestions_file, 'r', encoding='utf-8') as f:
            suggestions = json.load(f)
        if isinstance(suggestions, dict):
            suggestions = suggestions.get('suggestions', [suggestions])
    else:
        # 使用测试建议
        suggestions = [
            {
                "title": "测试建议",
                "description": "这是一个测试建议，用于验证分析器是否正常工作",
                "location": "Raft/Raft.cpp:150",
                "priority": 3,
                "risk_level": "low",
                "solution": "void TestFunction() { return; }"
            }
        ]

    console.print(f"[cyan]分析 {len(suggestions)} 条建议...[/cyan]")
    console.print("")

    # 分析建议
    reports = analyzer.analyze_suggestions(suggestions, read_original=True)

    # 显示报告
    valid_count = sum(1 for r in reports if r.location_valid)
    console.print(f"✅ 有效建议: {valid_count}/{len(reports)}")
    console.print("")

    for i, report in enumerate(reports, 1):
        status = "✅" if report.location_valid else "❌"
        priority = report.suggestion.get('priority', 3)
        risk = report.risk_level

        console.print(f"{status} [{i}] {report.suggestion.get('title', '未命名')}")
        console.print(f"   优先级: {priority} | 风险: {risk}")
        console.print(f"   位置: {report.suggestion.get('location', '未知')}")

        if report.issues:
            console.print(f"   ⚠️ 问题: {', '.join(report.issues)}")

        console.print(f"   📊 {report.estimated_impact}")
        console.print("")

    # 生成详细报告
    report_text = analyzer.generate_report(reports)
    console.print(Panel(report_text, title="详细分析报告", border_style="cyan"))


@app.command()
def export(
    output: str = typer.Option("smartbench_config.yaml", help="导出配置文件路径"),
    format: str = typer.Option("yaml", help="导出格式 (yaml/json)"),
):
    """
    导出默认配置模板

    示例:
        smartbench export --output my_config.yaml
        smartbench export --output my_config.json --format json
    """
    try:
        cfg = _load_config("config/default.yaml")
    except FileNotFoundError:
        console.print("[yellow]使用默认配置[/yellow]")
        cfg = Config()

    output_path = Path(output)

    if format == "json":
        import json
        data = {
            "models": [
                {
                    "name": m.name,
                    "provider": m.provider,
                    "api_key": "${API_KEY}",
                    "base_url": m.base_url or "",
                    "model": m.model,
                    "default_weight": m.default_weight,
                    "enabled": m.enabled,
                    "max_retries": m.max_retries,
                    "timeout": m.timeout,
                }
                for m in cfg.models
            ],
            "systems": [
                {
                    "name": s.name,
                    "system_type": s.system_type,
                    "project_path": s.project_path,
                    "benchmark_command": s.benchmark_command,
                    "log_path": s.log_path,
                }
                for s in cfg.systems
            ],
            "weight_engine": {
                "confidence_threshold": cfg.weight_engine.confidence_threshold,
                "default_model_weight": cfg.weight_engine.default_model_weight,
                "max_suggestions": cfg.weight_engine.max_suggestions,
            },
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        ConfigLoader.save(cfg, str(output_path))

    console.print(f"[green]✅ 配置已导出到: {output_path}[/green]")


def _load_config(config_path: str) -> Config:
    """加载配置"""
    # 支持相对路径
    if not Path(config_path).is_absolute():
        # 尝试相对于当前目录
        local_path = Path.cwd() / config_path
        if local_path.exists():
            return ConfigLoader.load(str(local_path))
        
        # 尝试相对于脚本目录
        script_dir = Path(__file__).parent.parent.parent / config_path
        if script_dir.exists():
            return ConfigLoader.load(str(script_dir))
    
    return ConfigLoader.load(config_path)


def _display_metrics(metrics: Metrics, target_qps: float):
    """显示性能指标"""
    table = Table(title="性能指标", show_header=True, header_style="bold magenta")
    table.add_column("指标", style="cyan")
    table.add_column("当前值", justify="right")
    table.add_column("目标值", justify="right")
    table.add_column("状态", justify="center")
    
    qps_status = "✅" if metrics.qps >= target_qps else "❌"
    latency_status = "✅" if metrics.p99_latency < 100 else "⚠️"
    error_status = "✅" if metrics.error_rate < 0.01 else "❌"
    
    table.add_row("QPS", f"{metrics.qps:.1f}", f"{target_qps}", qps_status)
    table.add_row("平均延迟", f"{metrics.avg_latency:.1f}ms", "-", "-")
    table.add_row("P99 延迟", f"{metrics.p99_latency:.1f}ms", "-", latency_status)
    table.add_row("错误率", f"{metrics.error_rate:.2%}", "< 1%", error_status)
    
    console.print(table)


def _display_model_results(results: List[AnalysisResult]):
    """显示模型分析结果"""
    table = Table(title="模型分析结果", show_header=True, header_style="bold magenta")
    table.add_column("模型", style="cyan")
    table.add_column("建议数", justify="right")
    table.add_column("状态", justify="center")
    table.add_column("耗时", justify="right")
    
    for result in results:
        if result.is_success:
            status = f"[green]✅[/green]"
            suggestions_count = len(result.suggestions)
        else:
            status = f"[red]❌[/red]"
            suggestions_count = "-"
        
        time_str = f"{result.processing_time:.1f}s" if result.processing_time else "-"
        table.add_row(result.model_name, str(suggestions_count), status, time_str)
    
    console.print(table)


def _display_suggestions(suggestions: List[Suggestion]):
    """显示优化建议"""
    if not suggestions:
        console.print("[yellow]未生成有效建议[/yellow]")
        return
    
    table = Table(title="优化建议", show_header=True, header_style="bold magenta")
    table.add_column("#", justify="right", style="dim")
    table.add_column("建议", style="cyan")
    table.add_column("风险", justify="center")
    table.add_column("优先级", justify="center")
    table.add_column("权重", justify="right")
    
    for i, s in enumerate(suggestions, 1):
        risk_color = "green" if s.risk_level == RiskLevel.LOW else "yellow" if s.risk_level == RiskLevel.MEDIUM else "red"
        priority_icon = "⭐" * s.priority
        
        table.add_row(
            str(i),
            s.title[:40] + "..." if len(s.title) > 40 else s.title,
            f"[{risk_color}]{s.risk_level.value}[/{risk_color}]",
            priority_icon,
            f"{s.final_weight:.2f}",
        )

    console.print(table)


@app.command()
def diagnose(
    project_path: str = typer.Option(None, help="项目路径"),
    symptoms: str = typer.Option(None, help="问题症状描述"),
    error_logs: str = typer.Option(None, help="错误日志文件路径"),
    core_dump: str = typer.Option(None, help="core dump 文件路径"),
    performance: bool = typer.Option(False, help="执行性能分析"),
    duration: int = typer.Option(30, help="性能采样时长（秒）"),
    output: str = typer.Option(None, help="输出报告文件路径"),
):
    """
    智能诊断 - 自动检测并分析问题

    支持的诊断类型：
    - 崩溃 (段错误、SIGSEGV)
    - 死锁 (程序无响应)
    - 内存泄漏 (Valgrind 检测)
    - 缺页中断 (OOM)
    - 性能瓶颈 (火焰图分析)
    - 启动失败 (依赖检查)

    示例:
        smartbench diagnose --symptoms "程序崩溃"
        smartbench diagnose --performance --duration 60
        smartbench diagnose --core-dump ./core
    """
    console.print(Panel.fit(
        "[bold blue]🔍 SmartBench 智能诊断[/bold blue]",
        border_style="blue"
    ))

    # 如果没有指定项目路径，使用默认值
    if not project_path:
        project_path = "/home/xianyu-sheng/MyKV_storageBase_Raft_cpp"

    console.print(f"[cyan]项目路径:[/cyan] {project_path}")
    console.print("")

    # 初始化诊断器
    diagnostician = SystemDiagnostician(
        project_path=project_path,
        binary_name="kvserver",
    )

    # 检查二进制文件
    if not diagnostician.binary_path:
        console.print("[yellow]⚠️ 未找到二进制文件，尝试从 build 目录搜索...[/yellow]")
    else:
        console.print(f"[green]✓ 找到二进制: {diagnostician.binary_path}[/green]")

    # 如果指定了 core dump，执行专门诊断
    if core_dump:
        console.print(f"[cyan]分析 core dump:[/cyan] {core_dump}")
        result = diagnostician.diagnose_crash(core_dump)

        if result.get("error"):
            console.print(f"[red]❌ {result['error']}[/red]")
        else:
            summary = result.get("summary", "")
            console.print(f"[green]✓ {summary}[/green]")
            console.print("")

            # 显示调用栈
            analysis = result.get("analysis", {})
            if analysis.get("backtrace"):
                console.print("[yellow]调用栈:[/yellow]")
                for frame in analysis["backtrace"][:10]:
                    console.print(f"  #{frame['frame']} {frame['function']}()")
                console.print("")

    # 如果是性能分析
    elif performance:
        console.print(f"[cyan]执行性能分析 (采样 {duration} 秒)...[/cyan]")
        result = diagnostician.diagnose_performance(
            duration=duration,
            profile_type="cpu",
        )

        # 显示系统信息
        system = result.get("system", {})
        if system.get("cpu_usage"):
            console.print("[yellow]CPU 使用情况:[/yellow]")
            for line in system["cpu_usage"].get("snapshot", "").split('\n'):
                if line.strip():
                    console.print(f"  {line.strip()}")
            console.print("")

        # 显示热点函数
        hotspots = result.get("hotspots", {}).get("hot_functions", [])
        if hotspots:
            console.print(f"[yellow]🔥 CPU 热点 (共 {len(hotspots)} 个):[/yellow]")
            for func in hotspots[:10]:
                console.print(f"  {func['percent']:5.1f}%  {func['name']}")
            console.print("")

        # 显示建议
        recommendations = result.get("recommendations", [])
        if recommendations:
            console.print("[cyan]💡 优化建议:[/cyan]")
            for rec in recommendations:
                console.print(f"  - {rec.get('title', '')}")
                console.print(f"    {rec.get('description', '')}")
            console.print("")

        # 生成的文件
        flamegraph = result.get("flamegraph", {})
        if flamegraph.get("svg_path"):
            console.print(f"[green]✓ 火焰图已生成: {flamegraph['svg_path']}[/green]")
            console.print("[dim]使用浏览器打开查看火焰图[/dim]")

    # 自动诊断
    else:
        # 读取错误日志
        logs_content = None
        if error_logs and Path(error_logs).exists():
            with open(error_logs, 'r', encoding='utf-8', errors='ignore') as f:
                logs_content = f.read()[:5000]

        console.print("[cyan]开始智能诊断...[/cyan]")

        # 执行诊断
        report = diagnostician.diagnose(
            symptoms=symptoms,
            error_logs=logs_content,
        )

        # 显示报告
        text_report = diagnostician.generate_text_report(report)
        console.print(text_report)

        # 保存报告
        if output:
            with open(output, 'w', encoding='utf-8') as f:
                f.write(text_report)
            console.print(f"\n[green]✓ 报告已保存: {output}[/green]")

    console.print("")


@app.command()
def health_check(
    project_path: str = typer.Option(None, help="项目路径"),
    detailed: bool = typer.Option(False, "--verbose", help="详细输出"),
):
    """
    系统健康检查 - 快速检测常见问题

    示例:
        smartbench health-check
        smartbench health-check --verbose
    """
    console.print(Panel.fit(
        "[bold blue]🏥 SmartBench 健康检查[/bold blue]",
        border_style="blue"
    ))

    if not project_path:
        project_path = "/home/xianyu-sheng/MyKV_storageBase_Raft_cpp"

    diagnostician = SystemDiagnostician(
        project_path=project_path,
        binary_name="kvserver",
    )

    # 检查项
    checks = []

    # 1. 二进制文件
    if diagnostician.binary_path:
        checks.append(("二进制文件", True, str(diagnostician.binary_path)))
    else:
        checks.append(("二进制文件", False, "未找到"))

    # 2. GDB 可用性
    import subprocess
    try:
        result = subprocess.run(["gdb", "--version"], capture_output=True, check=True)
        gdb_version = result.stdout.split('\n')[0]
        checks.append(("GDB", True, gdb_version))
    except:
        checks.append(("GDB", False, "未安装"))

    # 3. perf 可用性
    try:
        result = subprocess.run(["perf", "version"], capture_output=True, check=True)
        checks.append(("perf", True, "可用"))
    except:
        checks.append(("perf", False, "未安装或无权限"))

    # 4. Valgrind 可用性
    try:
        result = subprocess.run(["valgrind", "--version"], capture_output=True, check=True)
        checks.append(("Valgrind", True, result.stdout.strip()))
    except:
        checks.append(("Valgrind", False, "未安装"))

    # 5. FlameGraph
    flamegraph_dir = Path.home() / "FlameGraph"
    if flamegraph_dir.exists():
        checks.append(("FlameGraph", True, str(flamegraph_dir)))
    else:
        checks.append(("FlameGraph", False, "未安装"))

    # 6. 系统资源
    try:
        result = subprocess.run(["free", "-h"], capture_output=True, text=True)
        mem_info = result.stdout.strip().split('\n')[1].split()
        mem_available = mem_info[6] if len(mem_info) > 6 else "N/A"
        checks.append(("可用内存", True, mem_available))
    except:
        checks.append(("可用内存", False, "无法获取"))

    # 显示检查结果
    table = Table(title="检查项", show_header=True)
    table.add_column("项目", style="cyan")
    table.add_column("状态", justify="center")
    table.add_column("详情")

    for name, passed, detail in checks:
        status = "✅" if passed else "❌"
        table.add_row(name, status, detail[:50])

    console.print(table)
    console.print("")

    # 总结
    passed_count = sum(1 for _, p, _ in checks if p)
    total_count = len(checks)

    if passed_count == total_count:
        console.print(f"[green]✅ 健康检查通过 ({passed_count}/{total_count})[/green]")
    else:
        console.print(f"[yellow]⚠️  部分工具未安装 ({passed_count}/{total_count})[/yellow]")
        console.print("[dim]安装缺失工具可提升诊断能力[/dim]")


def main():
    """主入口"""
    app()


if __name__ == "__main__":
    main()
