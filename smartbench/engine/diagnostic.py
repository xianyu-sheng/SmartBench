"""
智能诊断引擎 - SmartBench 的核心诊断组件

功能：
1. 自动检测系统问题类型
2. 协调多个诊断 Agent 进行并行排查
3. 综合分析诊断结果
4. 生成修复建议

问题类型：
- 崩溃 (crash)
- 死锁 (deadlock)
- 缺页中断 (page_fault)
- 内存泄漏 (memory_leak)
- 性能瓶颈 (performance)
- 运行不起来 (startup_failure)
"""

import re
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProblemType(Enum):
    """问题类型"""
    CRASH = "crash"                      # 崩溃/段错误
    DEADLOCK = "deadlock"                 # 死锁
    PAGE_FAULT = "page_fault"             # 缺页中断
    MEMORY_LEAK = "memory_leak"           # 内存泄漏
    PERFORMANCE = "performance"            # 性能瓶颈
    STARTUP_FAILURE = "startup_failure"   # 启动失败
    UNKNOWN = "unknown"                   # 未知问题


class Severity(Enum):
    """严重程度"""
    CRITICAL = "critical"     # 致命，必须立即修复
    HIGH = "high"             # 高优先级
    MEDIUM = "medium"         # 中等优先级
    LOW = "low"               # 低优先级


@dataclass
class DiagnosticResult:
    """诊断结果"""
    problem_type: ProblemType
    severity: Severity
    symptoms: List[str]                    # 症状描述
    root_causes: List[str]                 # 根本原因
    evidence: Dict[str, Any]                # 诊断证据
    suggestions: List[Dict[str, Any]]       # 修复建议
    commands_used: List[str]               # 使用的诊断命令
    confidence: float                      # 诊断置信度 0-1


@dataclass
class DiagnosticCommand:
    """诊断命令"""
    command: str                           # 命令
    description: str                       # 描述
    expected_output_pattern: str           # 期望的输出模式（用于判断问题类型）
    interpretation: str                    # 结果解释
    applicable_problems: List[ProblemType] # 适用的问题类型


class DiagnosticEngine:
    """
    智能诊断引擎

    工作流程：
    1. 收集系统信息（dmesg、journalctl、日志等）
    2. 自动检测问题类型
    3. 调用相应的诊断 Agent
    4. 综合分析结果
    5. 生成诊断报告和修复建议
    """

    # 预定义的诊断命令知识库
    DIAGNOSTIC_COMMANDS = {
        # 崩溃/段错误相关
        ProblemType.CRASH: [
            DiagnosticCommand(
                command="dmesg | tail -50",
                description="查看内核日志中的崩溃信息",
                expected_output_pattern=r"(segfault|SIGSEGV|SIGABRT|kernel panic|Oops)",
                interpretation="检查是否有段错误或内核崩溃",
                applicable_problems=[ProblemType.CRASH]
            ),
            DiagnosticCommand(
                command="journalctl -b -1 --no-pager | tail -50",
                description="查看上一次启动的系统日志",
                expected_output_pattern=r"(crashed|abort|segmentation fault)",
                interpretation="检查上一次运行是否有崩溃",
                applicable_problems=[ProblemType.CRASH, ProblemType.STARTUP_FAILURE]
            ),
            DiagnosticCommand(
                command="gdb -batch -ex 'run' -ex 'bt' -ex 'quit' ./binary 2>&1 || true",
                description="使用 GDB 获取崩溃堆栈",
                expected_output_pattern=r"(SIGSEGV|SIGABRT|signal|Fatal)",
                interpretation="GDB 堆栈跟踪",
                applicable_problems=[ProblemType.CRASH]
            ),
        ],

        # 内存相关
        ProblemType.MEMORY_LEAK: [
            DiagnosticCommand(
                command="free -h",
                description="查看内存使用情况",
                expected_output_pattern=r"(available|free).*(Buffers|Cached)",
                interpretation="检查内存使用率和可用空间",
                applicable_problems=[ProblemType.MEMORY_LEAK, ProblemType.PERFORMANCE]
            ),
            DiagnosticCommand(
                command="valgrind --leak-check=full --show-leak-kinds=all ./binary 2>&1 | head -100",
                description="使用 Valgrind 检测内存泄漏",
                expected_output_pattern=r"(definitely lost|indirectly lost|possible leak)",
                interpretation="Valgrind 内存泄漏报告",
                applicable_problems=[ProblemType.MEMORY_LEAK]
            ),
            DiagnosticCommand(
                command="ps aux --sort=-%mem | head -10",
                description="查看内存占用最高的进程",
                expected_output_pattern=r"(mem.*%|RSS)",
                interpretation="找出内存消耗大户",
                applicable_problems=[ProblemType.MEMORY_LEAK, ProblemType.PERFORMANCE]
            ),
        ],

        # 缺页中断/内存问题
        ProblemType.PAGE_FAULT: [
            DiagnosticCommand(
                command="vmstat 1 5",
                description="查看虚拟内存统计",
                expected_output_pattern=r"(in|cs|us|sy)",
                interpretation="检查缺页中断率 (pi/po 列)",
                applicable_problems=[ProblemType.PAGE_FAULT, ProblemType.PERFORMANCE]
            ),
            DiagnosticCommand(
                command="cat /proc/meminfo | grep -E '(AnonPages|PageTables|Writeback)'",
                description="查看页面状态",
                expected_output_pattern=r"(AnonPages|PageTables)",
                interpretation="检查匿名页面和页表使用",
                applicable_problems=[ProblemType.PAGE_FAULT]
            ),
            DiagnosticCommand(
                command="dmesg | grep -i 'out of memory\\|oom\\|killed process'",
                description="检查 OOM Killer 是否触发",
                expected_output_pattern=r"(Out of memory|OOM killer|killed process)",
                interpretation="检查是否被 OOM Killer 杀死",
                applicable_problems=[ProblemType.PAGE_FAULT, ProblemType.MEMORY_LEAK, ProblemType.STARTUP_FAILURE]
            ),
        ],

        # 性能问题
        ProblemType.PERFORMANCE: [
            DiagnosticCommand(
                command="top -bn1 | head -20",
                description="查看 CPU 和内存使用",
                expected_output_pattern=r"(Cpu|%MEM|PID)",
                interpretation="检查 CPU 占用和内存使用",
                applicable_problems=[ProblemType.PERFORMANCE]
            ),
            DiagnosticCommand(
                command="perf top -g --call-graph dwarf -F 99 -a 2>&1 | head -30",
                description="使用 perf 进行 CPU 热点分析",
                expected_output_pattern=r"(%|samples|function)",
                interpretation="CPU 热点分析",
                applicable_problems=[ProblemType.PERFORMANCE]
            ),
            DiagnosticCommand(
                command="iostat -xz 1 3",
                description="查看磁盘 I/O 统计",
                expected_output_pattern=r"(Device|%util|iops)",
                interpretation="检查 I/O 瓶颈",
                applicable_problems=[ProblemType.PERFORMANCE]
            ),
            DiagnosticCommand(
                command="netstat -s | tail -20",
                description="查看网络统计",
                expected_output_pattern=r"(errors|retransmit|TCP)",
                interpretation="检查网络问题",
                applicable_problems=[ProblemType.PERFORMANCE]
            ),
        ],

        # 启动失败
        ProblemType.STARTUP_FAILURE: [
            DiagnosticCommand(
                command="./binary 2>&1",
                description="尝试运行二进制文件",
                expected_output_pattern=r"(error|failed|cannot|not found|permission denied)",
                interpretation="检查启动错误",
                applicable_problems=[ProblemType.STARTUP_FAILURE, ProblemType.CRASH]
            ),
            DiagnosticCommand(
                command="ldd ./binary",
                description="检查动态库依赖",
                expected_output_pattern=r"(not found|=>)",
                interpretation="检查缺少的动态库",
                applicable_problems=[ProblemType.STARTUP_FAILURE]
            ),
            DiagnosticCommand(
                command="file ./binary",
                description="检查二进制文件类型",
                expected_output_pattern=r"(ELF|executable|shared object)",
                interpretation="验证文件格式",
                applicable_problems=[ProblemType.STARTUP_FAILURE]
            ),
            DiagnosticCommand(
                command="ls -la ./binary && chmod +x ./binary",
                description="检查文件权限",
                expected_output_pattern=r"(x|\\.\\.)",
                interpretation="检查执行权限",
                applicable_problems=[ProblemType.STARTUP_FAILURE]
            ),
        ],

        # 死锁检测
        ProblemType.DEADLOCK: [
            DiagnosticCommand(
                command="pstack $(pgrep -f binary) 2>&1 || true",
                description="查看进程线程堆栈",
                expected_output_pattern=r"(Thread|LOCK|pthread)",
                interpretation="检查线程是否都在等待锁",
                applicable_problems=[ProblemType.DEADLOCK]
            ),
            DiagnosticCommand(
                command="cat /proc/$(pgrep -f binary)/wchan 2>/dev/null || echo 'N/A'",
                description="查看进程等待的内核函数",
                expected_output_pattern=r"(mutex|lock|schedule)",
                interpretation="检查内核层面的等待状态",
                applicable_problems=[ProblemType.DEADLOCK]
            ),
        ],
    }

    def __init__(
        self,
        project_path: str,
        binary_name: str = None,
        port: int = 8000,
    ):
        """
        初始化诊断引擎

        Args:
            project_path: 项目路径
            binary_name: 二进制文件名（用于定位进程）
            port: 服务端口（用于 Raft KV）
        """
        self.project_path = Path(project_path)
        self.binary_name = binary_name or "kvserver"
        self.port = port

        # 诊断结果缓存
        self.diagnostic_results: List[DiagnosticResult] = []

        # 项目可执行文件
        self.binary_path = self._find_binary()

    def _find_binary(self) -> Optional[Path]:
        """查找项目二进制文件"""
        # 常见的二进制位置
        candidates = [
            self.project_path / "build" / self.binary_name,
            self.project_path / "build" / f"{self.binary_name}.exe",
            self.project_path / "bin" / self.binary_name,
            self.project_path / self.binary_name,
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate

        # 搜索 build 目录
        build_dir = self.project_path / "build"
        if build_dir.exists():
            for f in build_dir.glob("*"):
                if f.is_file() and not f.name.endswith('.log') and not f.name.endswith('.conf'):
                    return f

        return None

    def diagnose(
        self,
        problem_symptoms: str = None,
        error_logs: str = None,
    ) -> DiagnosticResult:
        """
        执行诊断

        Args:
            problem_symptoms: 问题症状描述（可选，用于引导诊断）
            error_logs: 错误日志（可选）

        Returns:
            DiagnosticResult: 诊断结果
        """
        # 1. 检测问题类型
        problem_type = self._detect_problem_type(error_logs, problem_symptoms)

        # 2. 收集系统信息
        system_info = self._collect_system_info()

        # 3. 运行相关诊断命令
        diagnostic_outputs = self._run_diagnostic_commands(problem_type)

        # 4. 分析诊断结果
        result = self._analyze_results(
            problem_type=problem_type,
            system_info=system_info,
            diagnostic_outputs=diagnostic_outputs,
            error_logs=error_logs,
        )

        self.diagnostic_results.append(result)
        return result

    def _detect_problem_type(
        self,
        error_logs: str = None,
        symptoms: str = None,
    ) -> ProblemType:
        """检测问题类型"""
        combined_text = f"{error_logs or ''} {symptoms or ''}".lower()

        # 崩溃相关
        if any(kw in combined_text for kw in ['segfault', 'sigsegv', 'sigabrt', '段错误', '崩溃', 'crash', 'abort']):
            return ProblemType.CRASH

        # 启动失败
        if any(kw in combined_text for kw in ['failed to start', '启动失败', 'cannot start', 'not running', '运行不起来']):
            return ProblemType.STARTUP_FAILURE

        # 缺页中断
        if any(kw in combined_text for kw in ['page fault', '缺页', 'oom', 'out of memory', 'memory']):
            if 'leak' in combined_text or '泄漏' in combined_text:
                return ProblemType.MEMORY_LEAK
            return ProblemType.PAGE_FAULT

        # 内存泄漏
        if any(kw in combined_text for kw in ['memory leak', '内存泄漏', 'leak']):
            return ProblemType.MEMORY_LEAK

        # 死锁
        if any(kw in combined_text for kw in ['deadlock', '死锁', 'hang', 'hung', '卡死', '无响应']):
            return ProblemType.DEADLOCK

        # 性能问题
        if any(kw in combined_text for kw in ['slow', '延迟', '性能', '瓶颈', 'tps低', 'qps低', 'throughput']):
            return ProblemType.PERFORMANCE

        # 默认返回性能问题
        return ProblemType.PERFORMANCE

    def _collect_system_info(self) -> Dict[str, Any]:
        """收集系统基本信息"""
        info = {}

        # 尝试收集多种系统信息
        commands = [
            ("uname", ["uname", "-a"]),
            ("uptime", ["uptime"]),
            ("memory", ["free", "-h"]),
            ("disk", ["df", "-h"]),
            ("processes", ["ps", "aux"]),
        ]

        for name, cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                info[name] = result.stdout if result.returncode == 0 else result.stderr
            except Exception:
                info[name] = "N/A"

        return info

    def _run_diagnostic_commands(
        self,
        problem_type: ProblemType,
    ) -> Dict[str, Any]:
        """运行相关诊断命令"""
        outputs = {}

        # 获取适用的命令
        commands = self.DIAGNOSTIC_COMMANDS.get(problem_type, [])

        # 也运行一些通用诊断
        generic_commands = self.DIAGNOSTIC_COMMANDS.get(ProblemType.PERFORMANCE, [])
        commands.extend(generic_commands)

        # 去重
        seen = set()
        unique_commands = []
        for cmd in commands:
            if cmd.command not in seen:
                seen.add(cmd.command)
                unique_commands.append(cmd)

        for cmd in unique_commands[:10]:  # 限制数量
            try:
                # 替换二进制路径占位符
                actual_cmd = cmd.command
                if './binary' in actual_cmd and self.binary_path:
                    actual_cmd = actual_cmd.replace('./binary', str(self.binary_path))

                result = subprocess.run(
                    actual_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                outputs[cmd.command] = {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "description": cmd.description,
                    "interpretation": cmd.interpretation,
                }

            except subprocess.TimeoutExpired:
                outputs[cmd.command] = {
                    "stdout": "",
                    "stderr": "Command timeout",
                    "returncode": -1,
                }
            except Exception as e:
                outputs[cmd.command] = {
                    "stdout": "",
                    "stderr": str(e),
                    "returncode": -1,
                }

        return outputs

    def _analyze_results(
        self,
        problem_type: ProblemType,
        system_info: Dict[str, Any],
        diagnostic_outputs: Dict[str, Any],
        error_logs: str = None,
    ) -> DiagnosticResult:
        """分析诊断结果，生成报告"""

        # 提取症状
        symptoms = self._extract_symptoms(problem_type, diagnostic_outputs, error_logs)

        # 提取根本原因
        root_causes = self._extract_root_causes(problem_type, diagnostic_outputs)

        # 生成修复建议
        suggestions = self._generate_suggestions(problem_type, diagnostic_outputs, root_causes)

        # 判断严重程度
        severity = self._assess_severity(problem_type, diagnostic_outputs, symptoms)

        # 计算置信度
        confidence = self._calculate_confidence(problem_type, diagnostic_outputs)

        return DiagnosticResult(
            problem_type=problem_type,
            severity=severity,
            symptoms=symptoms,
            root_causes=root_causes,
            evidence=diagnostic_outputs,
            suggestions=suggestions,
            commands_used=list(diagnostic_outputs.keys()),
            confidence=confidence,
        )

    def _extract_symptoms(
        self,
        problem_type: ProblemType,
        outputs: Dict[str, Any],
        error_logs: str = None,
    ) -> List[str]:
        """提取症状"""
        symptoms = []

        # 从错误日志中提取
        if error_logs:
            for line in error_logs.split('\n')[:10]:
                if line.strip():
                    symptoms.append(f"日志错误: {line.strip()[:100]}")

        # 从诊断输出中提取
        for cmd, result in outputs.items():
            stderr = result.get("stderr", "")
            stdout = result.get("stdout", "")

            # 根据问题类型提取相关症状
            if problem_type == ProblemType.CRASH:
                if "SIGSEGV" in stderr or "SIGSEGV" in stdout:
                    symptoms.append("检测到段错误 (SIGSEGV)")
                if "SIGABRT" in stderr or "SIGABRT" in stdout:
                    symptoms.append("检测到程序中止 (SIGABRT)")

            elif problem_type == ProblemType.MEMORY_LEAK:
                if "leak" in stderr.lower():
                    symptoms.append("Valgrind 检测到内存泄漏")

            elif problem_type == ProblemType.PAGE_FAULT:
                if "oom" in stderr.lower() or "out of memory" in stderr.lower():
                    symptoms.append("系统内存不足 (OOM)")

        return symptoms

    def _extract_root_causes(
        self,
        problem_type: ProblemType,
        outputs: Dict[str, Any],
    ) -> List[str]:
        """提取根本原因"""
        causes = []

        for cmd, result in outputs.items():
            stderr = result.get("stderr", "")
            stdout = result.get("stdout", "")

            if problem_type == ProblemType.CRASH:
                if "not found" in stderr:
                    causes.append("缺少动态库依赖")
                if "permission denied" in stderr:
                    causes.append("文件权限不足")

            elif problem_type == ProblemType.MEMORY_LEAK:
                # 分析 Valgrind 输出
                if "definitely lost" in stdout:
                    match = re.search(r'(\d+,?\d*) bytes in', stdout)
                    if match:
                        causes.append(f"确定丢失内存: {match.group(1)} bytes")

            elif problem_type == ProblemType.PAGE_FAULT:
                if "oom" in stderr.lower():
                    causes.append("系统物理内存耗尽，触发 OOM Killer")

        return causes

    def _generate_suggestions(
        self,
        problem_type: ProblemType,
        outputs: Dict[str, Any],
        root_causes: List[str],
    ) -> List[Dict[str, Any]]:
        """生成修复建议"""
        suggestions = []

        # 根据问题类型生成建议
        suggestion_templates = {
            ProblemType.CRASH: [
                {
                    "title": "使用 GDB 调试",
                    "command": "gdb ./binary core",
                    "description": "加载 core 文件进行调试",
                    "priority": 1,
                    "risk": "low",
                },
                {
                    "title": "检查动态库依赖",
                    "command": "ldd ./binary",
                    "description": "确保所有动态库都已找到",
                    "priority": 2,
                    "risk": "low",
                },
            ],
            ProblemType.MEMORY_LEAK: [
                {
                    "title": "使用 Valgrind 检测",
                    "command": "valgrind --leak-check=full ./binary",
                    "description": "完整检测内存泄漏",
                    "priority": 1,
                    "risk": "low",
                },
                {
                    "title": "使用 AddressSanitizer",
                    "command": "g++ -fsanitize=address -g main.cpp -o main",
                    "description": "编译时启用 ASan 检测内存问题",
                    "priority": 2,
                    "risk": "low",
                },
            ],
            ProblemType.PERFORMANCE: [
                {
                    "title": "生成火焰图",
                    "command": "perf record -F 99 -a -g -- sleep 30",
                    "description": "使用 perf 采样 CPU 热点",
                    "priority": 1,
                    "risk": "low",
                },
                {
                    "title": "检查 CPU 使用",
                    "command": "top -bn1 | head -20",
                    "description": "查看 CPU 占用情况",
                    "priority": 2,
                    "risk": "low",
                },
            ],
        }

        # 获取模板建议
        templates = suggestion_templates.get(problem_type, [])
        for tmpl in templates:
            suggestions.append(tmpl)

        # 根据诊断输出添加动态建议
        for cmd, result in outputs.items():
            stderr = result.get("stderr", "")
            if "not found" in stderr:
                suggestions.append({
                    "title": "安装缺失依赖",
                    "command": f"# 检查具体缺失的库: {stderr}",
                    "description": "根据 ldd 输出安装缺失的库",
                    "priority": 1,
                    "risk": "low",
                })

        return suggestions

    def _assess_severity(
        self,
        problem_type: ProblemType,
        outputs: Dict[str, Any],
        symptoms: List[str],
    ) -> Severity:
        """评估严重程度"""
        # 崩溃最严重
        if problem_type == ProblemType.CRASH:
            return Severity.CRITICAL

        # 启动失败也是高优先级
        if problem_type == ProblemType.STARTUP_FAILURE:
            return Severity.HIGH

        # 检查是否有严重症状
        for symptom in symptoms:
            if any(kw in symptom for kw in ['段错误', 'SIGSEGV', 'SIGABRT', 'OOM']):
                return Severity.CRITICAL
            if any(kw in symptom for kw in ['内存泄漏', '死锁', 'leak', 'deadlock']):
                return Severity.HIGH

        return Severity.MEDIUM

    def _calculate_confidence(
        self,
        problem_type: ProblemType,
        outputs: Dict[str, Any],
    ) -> float:
        """计算诊断置信度"""
        base_confidence = 0.5

        # 根据收集到的信息量调整
        if len(outputs) >= 5:
            base_confidence += 0.2
        elif len(outputs) >= 3:
            base_confidence += 0.1

        # 根据输出内容调整
        for cmd, result in outputs.items():
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")

            if result.get("returncode") == 0 and (stdout or stderr):
                base_confidence += 0.05

        return min(base_confidence, 0.95)

    def generate_report(self, result: DiagnosticResult) -> str:
        """生成诊断报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("🔍 智能诊断报告")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"问题类型: {result.problem_type.value}")
        lines.append(f"严重程度: {result.severity.value}")
        lines.append(f"置信度: {result.confidence:.0%}")
        lines.append("")

        # 症状
        if result.symptoms:
            lines.append("📋 症状:")
            for symptom in result.symptoms:
                lines.append(f"  - {symptom}")
            lines.append("")

        # 根本原因
        if result.root_causes:
            lines.append("🔍 根本原因:")
            for cause in result.root_causes:
                lines.append(f"  - {cause}")
            lines.append("")

        # 建议
        if result.suggestions:
            lines.append("💡 修复建议:")
            for i, suggestion in enumerate(result.suggestions, 1):
                lines.append(f"  {i}. {suggestion['title']}")
                lines.append(f"     命令: {suggestion.get('command', 'N/A')}")
                lines.append(f"     说明: {suggestion.get('description', '')}")
            lines.append("")

        # 使用的诊断命令
        lines.append("📊 诊断命令:")
        for cmd in result.commands_used[:5]:
            lines.append(f"  - {cmd}")
        lines.append("")

        lines.append("=" * 60)
        lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 60)

        return '\n'.join(lines)
