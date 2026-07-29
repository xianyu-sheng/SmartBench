"""
系统诊断模块 - 整合诊断工具

功能：
1. 整合 GDB、火焰图、Linux 诊断命令
2. 提供统一的诊断接口
3. 生成综合诊断报告

命令参考：
- Linux 性能排查: http://www.brendangregg.com/linuxperf.html
- Linux 手册: https://man7.org/linux/man-pages/index.html
"""

import subprocess
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from smartbench.engine.diagnostic import DiagnosticEngine, ProblemType, Severity
from smartbench.engine.gdb_diagnosis import GDBDiagnostician, CoreDumpAnalyzer
from smartbench.engine.flamegraph import FlameGraphGenerator, SystemProfiler


@dataclass
class DiagnosticReport:
    """综合诊断报告"""
    timestamp: str
    problem_type: str
    severity: str
    summary: str
    symptoms: List[str]
    root_causes: List[str]
    evidence: Dict[str, Any]
    suggestions: List[Dict[str, Any]]
    commands_run: List[str]
    files_generated: List[str]


class SystemDiagnostician:
    """
    系统级诊断器

    整合所有诊断工具，提供智能诊断能力
    """

    def __init__(
        self,
        project_path: str,
        binary_name: str = "kvserver",
    ):
        """
        初始化系统诊断器

        Args:
            project_path: 项目路径
            binary_name: 二进制文件名
        """
        self.project_path = Path(project_path)
        self.binary_name = binary_name

        # 初始化各诊断组件
        self.diagnostic_engine = DiagnosticEngine(
            project_path=str(project_path),
            binary_name=binary_name,
        )

        # 查找二进制文件
        self.binary_path = self._find_binary()

        if self.binary_path:
            try:
                self.gdb_diagnostician = GDBDiagnostician(
                    binary_path=str(self.binary_path),
                )
            except FileNotFoundError:
                self.gdb_diagnostician = None
        else:
            self.gdb_diagnostician = None

        self.flamegraph_generator = FlameGraphGenerator(
            project_path=str(project_path),
        )

        # 诊断结果
        self.results: List[DiagnosticReport] = []

    def _find_binary(self) -> Optional[Path]:
        """查找二进制文件"""
        candidates = [
            self.project_path / "build" / self.binary_name,
            self.project_path / "build" / f"{self.binary_name}.exe",
            self.project_path / "bin" / self.binary_name,
        ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        # 搜索 build 目录
        build_dir = self.project_path / "build"
        if build_dir.exists():
            for f in build_dir.glob("*"):
                if f.is_file() and f.name not in ["CMakeCache.txt"]:
                    return f

        return None

    def diagnose(
        self,
        symptoms: str = None,
        error_logs: str = None,
        auto_detect: bool = True,
    ) -> DiagnosticReport:
        """
        执行综合诊断

        Args:
            symptoms: 问题症状描述
            error_logs: 错误日志
            auto_detect: 是否自动检测问题类型

        Returns:
            DiagnosticReport: 诊断报告
        """
        from datetime import datetime

        # 1. 自动检测问题类型
        if auto_detect:
            problem_type = self.diagnostic_engine._detect_problem_type(
                error_logs=error_logs,
                symptoms=symptoms,
            )
        else:
            problem_type = ProblemType.PERFORMANCE

        # 2. 收集系统信息
        system_info = self.diagnostic_engine._collect_system_info()

        # 3. 根据问题类型运行特定诊断
        evidence = {"system_info": system_info}
        commands_run = []

        # GDB 分析（如果有 core dump 或崩溃）
        if problem_type in [ProblemType.CRASH, ProblemType.STARTUP_FAILURE]:
            if self.gdb_diagnostician:
                gdb_result = self._run_gdb_diagnosis()
                evidence["gdb"] = gdb_result
                commands_run.extend(gdb_result.get("commands_used", []))

        # 火焰图分析（性能问题）
        if problem_type in [ProblemType.PERFORMANCE, ProblemType.PAGE_FAULT]:
            flamegraph_result = self._run_flamegraph_analysis()
            evidence["flamegraph"] = flamegraph_result
            if flamegraph_result.get("files_generated"):
                commands_run.append("perf record + flamegraph")

        # 运行诊断命令
        diagnostic_outputs = self.diagnostic_engine._run_diagnostic_commands(problem_type)
        evidence["diagnostic_commands"] = diagnostic_outputs
        commands_run.extend(list(diagnostic_outputs.keys())[:5])

        # 4. 综合分析
        result = self.diagnostic_engine._analyze_results(
            problem_type=problem_type,
            system_info=system_info,
            diagnostic_outputs=diagnostic_outputs,
            error_logs=error_logs,
        )

        # 5. 生成报告
        report = DiagnosticReport(
            timestamp=datetime.now().isoformat(),
            problem_type=problem_type.value,
            severity=result.severity.value,
            summary=self._generate_summary(problem_type, result, evidence),
            symptoms=result.symptoms,
            root_causes=result.root_causes,
            evidence=evidence,
            suggestions=result.suggestions,
            commands_run=commands_run,
            files_generated=evidence.get("flamegraph", {}).get("files_generated", []),
        )

        self.results.append(report)
        return report

    def diagnose_crash(self, core_path: str = None) -> Dict[str, Any]:
        """专门诊断崩溃问题"""
        if not self.gdb_diagnostician:
            return {"error": "GDB not available or binary not found"}

        # 查找 core dump
        if not core_path:
            core_path = self.gdb_diagnostician.find_core_dump()

        if not core_path:
            return {"error": "No core dump found"}

        # 分析 core dump
        result = self.gdb_diagnostician.analyze_crash(core_path)

        # 生成摘要
        summary = self._summarize_crash(result)

        return {
            "core_file": core_path,
            "analysis": result,
            "summary": summary,
        }

    def diagnose_performance(
        self,
        duration: int = 30,
        profile_type: str = "cpu",
    ) -> Dict[str, Any]:
        """
        专门诊断性能问题

        Args:
            duration: 采样时长（秒）
            profile_type: 分析类型 (cpu, memory, io)

        Returns:
            分析结果
        """
        results = {}

        # 1. 系统级分析
        profiler = SystemProfiler()

        if profile_type == "cpu":
            results["system"] = profiler.profile_cpu(self.binary_name)
        elif profile_type == "memory":
            results["system"] = profiler.profile_memory()
        elif profile_type == "io":
            results["system"] = profiler.profile_io()

        # 2. 生成火焰图
        if profile_type == "cpu":
            flamegraph_result = self.flamegraph_generator.generate_cpu_flamegraph(
                duration=duration,
                process_name=self.binary_name,
            )
            results["flamegraph"] = flamegraph_result

            # 分析热点
            if flamegraph_result.get("success"):
                hotspots = self.flamegraph_generator.analyze_hotspots(
                    flamegraph_result.get("perf_data")
                )
                results["hotspots"] = hotspots

        elif profile_type == "memory":
            flamegraph_result = self.flamegraph_generator.generate_memory_flamegraph(
                duration=duration,
            )
            results["flamegraph"] = flamegraph_result

        # 3. 生成建议
        results["recommendations"] = self._generate_performance_suggestions(results)

        return results

    def _run_gdb_diagnosis(self) -> Dict[str, Any]:
        """运行 GDB 诊断"""
        result = {
            "success": False,
            "analysis": {},
            "commands_used": [],
        }

        try:
            # 检查依赖
            deps = self.gdb_diagnostician.check_dependencies()
            result["dependencies"] = deps

            if deps.get("missing"):
                result["missing_dependencies"] = deps["missing"]
                return result

            # 查找 core dump
            core_path = self.gdb_diagnostician.find_core_dump()

            if core_path:
                analysis = self.gdb_diagnostician.analyze_crash(str(core_path))
                result["analysis"] = analysis
                result["success"] = True
                result["commands_used"].append(f"gdb -c {core_path}")
            else:
                # 尝试运行程序获取崩溃
                analysis = self.gdb_diagnostician.analyze_crash()
                result["analysis"] = analysis
                result["success"] = True
                result["commands_used"].append("gdb ./binary")

        except Exception as e:
            result["error"] = str(e)

        return result

    def _run_flamegraph_analysis(self) -> Dict[str, Any]:
        """运行火焰图分析"""
        result = {
            "success": False,
            "files_generated": [],
        }

        try:
            # 生成 CPU 火焰图
            flamegraph_result = self.flamegraph_generator.generate_cpu_flamegraph(
                duration=10,
                process_name=self.binary_name,
            )

            result.update(flamegraph_result)

            if flamegraph_result.get("success"):
                svg_path = flamegraph_result.get("svg_path")
                if svg_path and Path(svg_path).exists():
                    result["files_generated"].append(str(svg_path))

        except Exception as e:
            result["error"] = str(e)

        return result

    def _generate_summary(
        self,
        problem_type: ProblemType,
        diagnostic_result,
        evidence: Dict[str, Any],
    ) -> str:
        """生成诊断摘要"""
        summaries = {
            ProblemType.CRASH: "检测到程序崩溃，需要分析 core dump",
            ProblemType.DEADLOCK: "检测到死锁，程序无响应",
            ProblemType.PAGE_FAULT: "检测到内存页错误，可能是内存不足",
            ProblemType.MEMORY_LEAK: "检测到内存泄漏",
            ProblemType.PERFORMANCE: "检测到性能问题，需要分析热点",
            ProblemType.STARTUP_FAILURE: "程序无法启动",
            ProblemType.UNKNOWN: "未知问题，需要进一步分析",
        }

        return summaries.get(problem_type, "执行了系统诊断")

    def _summarize_crash(self, analysis: Dict[str, Any]) -> str:
        """生成崩溃摘要"""
        signal = analysis.get("signal", "Unknown")
        backtrace = analysis.get("backtrace", [])

        summary = f"程序收到 {signal} 信号终止"

        if backtrace:
            top_func = backtrace[0].get("function", "unknown")
            summary += f"，崩溃位置: {top_func}()"

        return summary

    def _generate_performance_suggestions(
        self,
        results: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """生成性能优化建议"""
        suggestions = []

        hotspots = results.get("hotspots", {}).get("hot_functions", [])

        for func in hotspots[:5]:
            suggestions.append({
                "title": f"优化 {func['name']}",
                "description": f"该函数占用 {func['percent']:.1f}% CPU 时间",
                "priority": int(5 - hotspots.index(func)),
                "risk": "low",
            })

        if not suggestions:
            suggestions.append({
                "title": "未发现明显热点",
                "description": "性能问题可能在于架构设计",
                "priority": 3,
                "risk": "medium",
            })

        return suggestions

    def generate_text_report(self, report: DiagnosticReport) -> str:
        """生成文本格式报告"""
        from datetime import datetime

        lines = []
        lines.append("=" * 70)
        lines.append("🔍 SmartBench 智能诊断报告")
        lines.append("=" * 70)
        lines.append("")

        # 基本信息
        lines.append(f"⏰ 诊断时间: {report.timestamp}")
        lines.append(f"📋 问题类型: {report.problem_type}")
        lines.append(f"⚠️  严重程度: {report.severity.upper()}")
        lines.append("")

        # 摘要
        lines.append("📝 诊断摘要:")
        lines.append(f"  {report.summary}")
        lines.append("")

        # 症状
        if report.symptoms:
            lines.append("🔎 发现的症状:")
            for symptom in report.symptoms[:5]:
                lines.append(f"  - {symptom}")
            lines.append("")

        # 根本原因
        if report.root_causes:
            lines.append("🔍 根本原因:")
            for cause in report.root_causes:
                lines.append(f"  - {cause}")
            lines.append("")

        # 修复建议
        if report.suggestions:
            lines.append("💡 修复建议:")
            for i, suggestion in enumerate(report.suggestions, 1):
                lines.append(f"  {i}. {suggestion.get('title', 'N/A')}")
                if 'command' in suggestion:
                    lines.append(f"     命令: {suggestion['command']}")
                lines.append(f"     说明: {suggestion.get('description', '')}")
                lines.append("")

        # 生成的文件
        if report.files_generated:
            lines.append("📁 生成的文件:")
            for f in report.files_generated:
                lines.append(f"  - {f}")
            lines.append("")

        # 运行的命令
        if report.commands_run:
            lines.append("🔧 使用的诊断命令:")
            for cmd in report.commands_run[:5]:
                lines.append(f"  - {cmd}")
            lines.append("")

        lines.append("=" * 70)
        lines.append("提示: 使用 --verbose 参数查看完整诊断详情")
        lines.append("=" * 70)

        return '\n'.join(lines)
