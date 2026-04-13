"""
GDB 诊断模块 - 智能调试助手

功能：
1. 自动分析 core dump 文件
2. 多线程/进程调试分析
3. 内存分析
4. 生成调试报告

参考文档：
- GDB 官方文档: https://www.gnu.org/software/gdb/documentation/
- GDB User Manual: https://sourceware.org/gdb/current/onlinedocs/gdb.html/
- GDB Internals: https://sourceware.org/gdb/wiki/Internals
"""

import subprocess
import re
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class GDBCommand:
    """GDB 命令"""
    command: str
    description: str
    category: str  # crash, memory, thread, general


class GDBDiagnostician:
    """
    GDB 智能诊断器

    使用 GDB 自动分析程序崩溃和调试问题
    """

    # GDB 命令知识库
    GDB_COMMANDS = {
        # 崩溃分析
        "backtrace": GDBCommand(
            command="bt",
            description="显示完整调用栈",
            category="crash"
        ),
        "backtrace_full": GDBCommand(
            command="bt full",
            description="显示完整调用栈（包含局部变量）",
            category="crash"
        ),
        "thread_info": GDBCommand(
            command="info threads",
            description="显示所有线程信息",
            category="thread"
        ),
        "thread_apply_all": GDBCommand(
            command="thread apply all bt",
            description="显示所有线程的调用栈",
            category="thread"
        ),

        # 内存分析
        "registers": GDBCommand(
            command="info registers",
            description="显示寄存器值",
            category="memory"
        ),
        "stack": GDBCommand(
            command="x/20x $sp",
            description="查看栈内容",
            category="memory"
        ),
        "heap": GDBCommand(
            command="heap",
            description="查看堆信息（需要 pwndbg）",
            category="memory"
        ),

        # 信号分析
        "signals": GDBCommand(
            command="info signals",
            description="显示信号处理信息",
            category="general"
        ),
        "breakpoints": GDBCommand(
            command="info breakpoints",
            description="显示所有断点",
            category="general"
        ),
        "locals": GDBCommand(
            command="info locals",
            description="显示当前函数局部变量",
            category="general"
        ),
        "args": GDBCommand(
            command="info args",
            description="显示函数参数",
            category="general"
        ),
    }

    def __init__(
        self,
        binary_path: str,
        core_path: str = None,
    ):
        """
        初始化 GDB 诊断器

        Args:
            binary_path: 可执行文件路径
            core_path: core dump 文件路径（可选）
        """
        self.binary_path = Path(binary_path)
        self.core_path = Path(core_path) if core_path else None

        # 检查文件是否存在
        if not self.binary_path.exists():
            raise FileNotFoundError(f"Binary not found: {binary_path}")

    def find_core_dump(self) -> Optional[Path]:
        """查找 core dump 文件"""
        possible_cores = [
            Path("core"),
            Path("core dump"),
            Path("core.*"),
            self.binary_path.parent / "core",
        ]

        for pattern in possible_cores:
            if pattern.name == "core.*":
                # 查找匹配的 core 文件
                for core in pattern.parent.glob("core*"):
                    return core
            elif pattern.exists():
                return pattern

        return None

    def analyze_crash(self, core_path: str = None) -> Dict[str, Any]:
        """
        分析崩溃

        Args:
            core_path: core dump 路径

        Returns:
            崩溃分析结果
        """
        core = core_path or self.core_path

        if core and not Path(core).exists():
            return {"error": f"Core dump not found: {core}"}

        # 构建 GDB 命令
        commands = self._build_gdb_commands(core)

        # 执行 GDB
        result = self._run_gdb(commands, core)

        # 解析结果
        analysis = self._parse_analysis(result)

        return analysis

    def _build_gdb_commands(self, core_path: str = None) -> List[str]:
        """构建 GDB 命令列表"""
        commands = [
            # 基本信息
            "set pagination off",
            "set print pretty on",
            "set print array on",

            # 崩溃分析
            "echo \n=== SIGNAL INFO ===\n",
            "info program",

            "echo \n=== BACKTRACE ===\n",
            "bt",

            "echo \n=== BACKTRACE FULL ===\n",
            "bt full",

            # 线程信息
            "echo \n=== THREAD INFO ===\n",
            "info threads",

            "echo \n=== ALL THREADS STACK ===\n",
            "thread apply all bt",

            # 寄存器
            "echo \n=== REGISTERS ===\n",
            "info registers",

            # 栈内容
            "echo \n=== STACK DUMP ===\n",
            "x/40x $sp",

            # 信号信息
            "echo \n=== SIGNAL INFO ===\n",
            "info signals",

            "quit",
        ]

        return commands

    def _run_gdb(
        self,
        commands: List[str],
        core_path: str = None,
    ) -> Tuple[str, str, int]:
        """运行 GDB"""
        cmd = ["gdb", "-batch", "-ex", "set pagination off"]

        if core_path:
            cmd.extend(["-c", str(core_path)])

        cmd.extend(["--args", str(self.binary_path)])

        # 添加命令
        for gdb_cmd in commands:
            cmd.extend(["-ex", gdb_cmd])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", "GDB timeout", -1
        except Exception as e:
            return "", str(e), -1

    def _parse_analysis(self, gdb_output: Tuple[str, str, int]) -> Dict[str, Any]:
        """解析 GDB 输出"""
        stdout, stderr, returncode = gdb_output

        analysis = {
            "success": returncode == 0,
            "signal": None,
            "crash_location": None,
            "backtrace": [],
            "threads": [],
            "memory_summary": {},
            "recommendations": [],
        }

        # 提取崩溃信号
        signal_match = re.search(r'Signal:\s*(\w+)', stdout)
        if signal_match:
            analysis["signal"] = signal_match.group(1)

        # 提取崩溃位置
        crash_match = re.search(r'Program terminated with signal (\w+)', stdout)
        if crash_match:
            analysis["signal"] = crash_match.group(1)

        # 提取调用栈
        bt_section = self._extract_section(stdout, "=== BACKTRACE ===", "=== ")
        if bt_section:
            frames = []
            for line in bt_section.split('\n'):
                # 匹配帧格式: #0  0x... in function () at file.c:123
                match = re.match(r'#(\d+)\s+(0x[0-9a-f]+)\s+in\s+(\S+)\s+\(\)', line)
                if match:
                    frames.append({
                        "frame": int(match.group(1)),
                        "address": match.group(2),
                        "function": match.group(3),
                    })
            analysis["backtrace"] = frames

        # 提取线程信息
        thread_section = self._extract_section(stdout, "=== THREAD INFO ===", "===")
        if thread_section:
            threads = []
            for line in thread_section.split('\n'):
                if line.strip():
                    threads.append(line.strip())
            analysis["threads"] = threads

        # 根据信号类型生成建议
        analysis["recommendations"] = self._generate_recommendations(analysis)

        return analysis

    def _extract_section(self, text: str, start_marker: str, end_marker: str) -> str:
        """提取文本中的特定部分"""
        start = text.find(start_marker)
        if start == -1:
            return ""

        start += len(start_marker)
        end = text.find(end_marker, start)
        if end == -1:
            end = len(text)

        return text[start:end].strip()

    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[Dict[str, str]]:
        """根据分析结果生成建议"""
        recommendations = []

        signal = analysis.get("signal", "")

        # 根据信号类型建议
        if signal == "SIGSEGV":
            recommendations.append({
                "type": "crash",
                "title": "段错误 (SIGSEGV)",
                "suggestion": "检查空指针解引用或访问非法内存地址",
                "commands": [
                    "print variable_name",
                    "x/10x address",
                ],
            })
        elif signal == "SIGABRT":
            recommendations.append({
                "type": "crash",
                "title": "程序中止 (SIGABRT)",
                "suggestion": "可能是 assert 失败或 abort() 调用",
                "commands": [
                    "bt full",
                    "info locals",
                ],
            })
        elif signal == "SIGFPE":
            recommendations.append({
                "type": "crash",
                "title": "浮点异常 (SIGFPE)",
                "suggestion": "检查除零操作",
                "commands": [
                    "print variable",
                ],
            })

        # 调用栈建议
        if analysis.get("backtrace"):
            top_frame = analysis["backtrace"][0]
            recommendations.append({
                "type": "debug",
                "title": "查看崩溃函数",
                "suggestion": f"崩溃发生在 {top_frame.get('function', 'unknown')}()",
                "commands": [
                    f"frame 0",
                    "info locals",
                    "print *pointer",
                ],
            })

        return recommendations

    def live_debug(self, args: str = None) -> str:
        """
        交互式调试

        Args:
            args: 程序参数

        Returns:
            调试会话输出
        """
        cmd = ["gdb", str(self.binary_path)]

        if args:
            cmd.extend(["--args"] + args.split())

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
            input="\n".join(["bt", "quit"]),
        )

        return result.stdout + result.stderr

    def check_dependencies(self) -> Dict[str, Any]:
        """检查动态库依赖"""
        result = subprocess.run(
            ["ldd", str(self.binary_path)],
            capture_output=True,
            text=True,
        )

        dependencies = {
            "found": [],
            "missing": [],
            "not_found_paths": [],
        }

        for line in result.stdout.split('\n'):
            if "not found" in line:
                dependencies["missing"].append(line.strip())
            elif "=>" in line:
                deps = line.split("=>")
                if len(deps) == 2 and "not found" in deps[1]:
                    dependencies["not_found_paths"].append(deps[0].strip())

        return dependencies


class CoreDumpAnalyzer:
    """Core Dump 分析器"""

    def __init__(self, core_path: str, binary_path: str):
        self.core_path = Path(core_path)
        self.binary_path = Path(binary_path)

    def get_crash_summary(self) -> str:
        """获取崩溃摘要"""
        commands = [
            "bt 5",
            "info registers",
            "print $_siginfo",
            "quit",
        ]

        cmd = ["gdb", "-batch", "-c", str(self.core_path), str(self.binary_path)]
        for c in commands:
            cmd.extend(["-ex", c])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        return result.stdout

    def extract_core_info(self) -> Dict[str, Any]:
        """提取 core 文件信息"""
        info = {}

        # 使用 file 命令
        result = subprocess.run(
            ["file", str(self.core_path)],
            capture_output=True,
            text=True,
        )
        info["file_type"] = result.stdout.strip()

        # 使用 size 命令（如果可用）
        if self.binary_path.exists():
            result = subprocess.run(
                ["size", str(self.binary_path)],
                capture_output=True,
                text=True,
            )
            info["binary_size"] = result.stdout

        # core 文件大小
        info["core_size"] = self.core_path.stat().st_size

        return info
