"""
火焰图诊断模块 - 性能分析工具

功能：
1. 自动生成 CPU 火焰图
2. 内存分配火焰图
3. I/O 火焰图
4. 网络火焰图

参考文档：
- Brendan Gregg 火焰图主页: http://www.brendangregg.com/flamegraphs.html
- CPU 火焰图: http://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html
- FlameGraph 工具: https://github.com/brendangregg/FlameGraph
- Linux 性能排查: http://www.brendangregg.com/linuxperf.html
"""

import subprocess
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FlameGraphConfig:
    """火焰图配置"""
    title: str = "Flame Graph"
    width: int = 1200
    height: int = 16
    collapsed: bool = True
    colors: str = "hot"  # hot, mem, io, js
    search: str = ""


class FlameGraphGenerator:
    """
    火焰图生成器

    支持多种火焰图类型：
    1. CPU 火焰图 - 分析 CPU 热点
    2. 内存火焰图 - 分析内存分配
    3. Off-CPU 火焰图 - 分析阻塞时间
    4. I/O 火焰图 - 分析 I/O 操作
    """

    def __init__(
        self,
        project_path: str,
        output_dir: str = "./data/flamegraphs",
    ):
        """
        初始化火焰图生成器

        Args:
            project_path: 项目路径
            output_dir: 输出目录
        """
        self.project_path = Path(project_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 检查依赖
        self._check_dependencies()

    def _check_dependencies(self) -> Dict[str, bool]:
        """检查必要的依赖"""
        deps = {}

        # 检查 perf
        try:
            subprocess.run(["perf", "version"], capture_output=True, check=True)
            deps["perf"] = True
        except:
            deps["perf"] = False

        # 检查火焰图脚本
        flamegraph_dir = Path.home() / "FlameGraph"
        deps["flamegraph"] = flamegraph_dir.exists()

        if not deps["flamegraph"]:
            # 尝试常见位置
            for path in ["/usr/local/bin/", "/opt/FlameGraph/"]:
                if Path(path).exists():
                    deps["flamegraph"] = True
                    break

        return deps

    def generate_cpu_flamegraph(
        self,
        duration: int = 30,
        frequency: int = 99,
        process_name: str = None,
    ) -> Dict[str, Any]:
        """
        生成 CPU 火焰图

        Args:
            duration: 采样时长（秒）
            frequency: 采样频率 (Hz)
            process_name: 进程名（可选，用于过滤）

        Returns:
            生成结果
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        perf_data = self.output_dir / f"perf_{timestamp}.data"
        svg_path = self.output_dir / f"cpu_flamegraph_{timestamp}.svg"

        result = {
            "success": False,
            "type": "cpu",
            "perf_data": str(perf_data),
            "svg_path": str(svg_path),
            "error": None,
        }

        try:
            # 1. 使用 perf 采样
            perf_cmd = [
                "perf", "record",
                "-F", str(frequency),
                "-a",  # 所有 CPU
                "-g",  # 调用图
                "-o", str(perf_data),
            ]

            if process_name:
                perf_cmd.extend(["-p", str(subprocess.getoutput(f"pgrep -f {process_name}").split()[0])])
            else:
                perf_cmd.extend(["--", "sleep", str(duration)])

            subprocess.run(perf_cmd, capture_output=True, check=True, timeout=duration + 30)

            # 2. 生成火焰图
            perf_script = self.output_dir / f"perf_{timestamp}.scripted"
            subprocess.run(
                ["perf", "script", "-i", str(perf_data), "--no-child"],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout  # 保存备用

            # 3. 生成 SVG
            if self._check_dependencies()["flamegraph"]:
                flamegraph_scripts = self._find_flamegraph_scripts()

                # 使用 stackcollapse-perf.pl 折叠
                collapsed = subprocess.run(
                    f"perf script | {flamegraph_scripts['collapse']}",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                ).stdout

                # 生成火焰图
                with open(svg_path, "w") as f:
                    subprocess.run(
                        f"cat | {flamegraph_scripts['flamegraph']} --title='CPU Flame Graph'",
                        shell=True,
                        input=collapsed,
                        stdout=f,
                        timeout=30,
                    )

                result["success"] = True
            else:
                # 如果没有火焰图脚本，至少保留 perf 数据
                result["success"] = True
                result["note"] = "FlameGraph not installed. Use 'perf report' to view."

        except subprocess.CalledProcessError as e:
            result["error"] = f"Command failed: {e.stderr}"
        except subprocess.TimeoutExpired:
            result["error"] = "Timeout"
        except Exception as e:
            result["error"] = str(e)

        return result

    def generate_memory_flamegraph(
        self,
        process_id: int = None,
        duration: int = 30,
    ) -> Dict[str, Any]:
        """
        生成内存分配火焰图

        需要使用 -finstrument-functions 或 valgrind 编译

        Args:
            process_id: 进程 ID
            duration: 采样时长

        Returns:
            生成结果
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        svg_path = self.output_dir / f"memory_flamegraph_{timestamp}.svg"

        result = {
            "success": False,
            "type": "memory",
            "svg_path": str(svg_path),
            "error": None,
        }

        # 内存火焰图需要特殊的跟踪
        # 方法 1: 使用 perf 跟踪 malloc/free
        # 方法 2: 使用 valgrind --tool=massif
        # 方法 3: 使用 bcc/USDT

        # 这里使用 perf 跟踪内存分配
        try:
            perf_cmd = [
                "perf", "record",
                "-e", "kmem:*",
                "-a",
                "-g",
            ]

            if process_id:
                perf_cmd.extend(["-p", str(process_id)])
            else:
                perf_cmd.extend(["--", "sleep", str(duration)])

            subprocess.run(perf_cmd, capture_output=True, check=True, timeout=duration + 30)
            result["success"] = True
            result["note"] = "Use 'perf report' to analyze memory events"

        except subprocess.CalledProcessError:
            result["error"] = "perf kmem events require root privileges or kernel config"

        return result

    def generate_offcpu_flamegraph(
        self,
        duration: int = 30,
    ) -> Dict[str, Any]:
        """
        生成 Off-CPU 火焰图

        分析阻塞时间（I/O、锁等待、调度等）

        Returns:
            生成结果
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        svg_path = self.output_dir / f"offcpu_flamegraph_{timestamp}.svg"

        result = {
            "success": False,
            "type": "offcpu",
            "svg_path": str(svg_path),
            "error": None,
        }

        # Off-CPU 火焰图需要跟踪调度事件
        try:
            perf_cmd = [
                "perf", "record",
                "-e", "sched:sched_switch",
                "-e", "sched:sched_blocked_reason",
                "-a",
                "-g",
                "--", "sleep", str(duration),
            ]

            subprocess.run(perf_cmd, capture_output=True, check=True, timeout=duration + 30)
            result["success"] = True
            result["note"] = "Off-CPU analysis requires complex post-processing"

        except subprocess.CalledProcessError:
            result["error"] = "Requires kernel tracepoints"

        return result

    def analyze_hotspots(self, perf_data_path: str = None) -> Dict[str, Any]:
        """
        分析 CPU 热点

        Args:
            perf_data_path: perf.data 路径

        Returns:
            热点分析结果
        """
        result = {
            "hot_functions": [],
            "hot_modules": [],
            "top_events": [],
        }

        if not perf_data_path:
            # 查找最新的 perf.data
            perf_files = list(self.output_dir.glob("perf_*.data"))
            if perf_files:
                perf_data_path = str(max(perf_files, key=lambda p: p.stat().st_mtime))

        if not perf_data_path or not Path(perf_data_path).exists():
            return {"error": "No perf.data found"}

        try:
            # 使用 perf report 分析
            output = subprocess.run(
                ["perf", "report", "-g", "graph,0.5", "-i", perf_data_path],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout

            # 解析热点函数
            lines = output.split('\n')
            for line in lines[:30]:
                # 匹配格式: 百分比  函数
                match = re.match(r'\s*([\d.]+)%\s+(.+)', line)
                if match:
                    percent = float(match.group(1))
                    func = match.group(2).strip()

                    if percent > 0.5:  # 只保留 > 0.5% 的
                        result["hot_functions"].append({
                            "name": func,
                            "percent": percent,
                        })

        except Exception as e:
            result["error"] = str(e)

        return result

    def _find_flamegraph_scripts(self) -> Dict[str, Path]:
        """查找火焰图脚本"""
        scripts = {
            "flamegraph": Path("~/FlameGraph/flamegraph.pl").expanduser(),
            "stackcollapse": Path("~/FlameGraph/stackcollapse-perf.pl").expanduser(),
            "collapse": Path("~/FlameGraph/stackcollapse-perf.pl").expanduser(),
        }

        # 检查是否存在
        for name, path in scripts.items():
            if not path.exists():
                # 尝试其他位置
                alternatives = [
                    Path("/usr/local/bin") / path.name,
                    Path("/opt/FlameGraph") / path.name,
                    Path.home() / "bin" / path.name,
                ]
                for alt in alternatives:
                    if alt.exists():
                        scripts[name] = alt
                        break

        return scripts

    def quick_profile(self, duration: int = 10) -> Dict[str, Any]:
        """
        快速性能分析

        一键生成多种分析报告

        Returns:
            分析结果
        """
        results = {
            "timestamp": datetime.now().isoformat(),
            "duration": duration,
            "cpu_hotspots": [],
            "recommendations": [],
        }

        # 1. CPU 采样
        cpu_result = self.generate_cpu_flamegraph(duration=duration)
        results["cpu_flamegraph"] = cpu_result

        # 2. 分析热点
        if cpu_result.get("success"):
            perf_data = cpu_result.get("perf_data")
            hotspots = self.analyze_hotspots(perf_data)
            results["cpu_hotspots"] = hotspots.get("hot_functions", [])

            # 生成建议
            for func in hotspots.get("hot_functions", [])[:5]:
                results["recommendations"].append({
                    "type": "cpu",
                    "function": func["name"],
                    "percent": func["percent"],
                    "suggestion": f"函数 {func['name']} 占用了 {func['percent']:.1f}% CPU 时间",
                })

        return results


class SystemProfiler:
    """
    系统性能分析器

    使用标准 Linux 工具进行系统级分析
    """

    @staticmethod
    def profile_cpu(process_name: str = None) -> Dict[str, Any]:
        """CPU 分析"""
        result = {
            "top_processes": [],
            "cpu_usage": {},
        }

        try:
            # top 输出
            output = subprocess.run(
                ["top", "-bn2", "-d", "0.5"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout

            # 解析
            lines = output.split('\n')
            for line in lines:
                if "Cpu(s)" in line:
                    result["cpu_usage"]["snapshot"] = line.strip()
                elif process_name and process_name in line:
                    result["top_processes"].append(line.strip())

        except Exception as e:
            result["error"] = str(e)

        return result

    @staticmethod
    def profile_memory() -> Dict[str, Any]:
        """内存分析"""
        result = {}

        try:
            # free -h
            result["memory"] = subprocess.run(
                ["free", "-h"],
                capture_output=True,
                text=True,
            ).stdout

            # vmstat
            result["vmstat"] = subprocess.run(
                ["vmstat", "-s"],
                capture_output=True,
                text=True,
            ).stdout

        except Exception as e:
            result["error"] = str(e)

        return result

    @staticmethod
    def profile_io() -> Dict[str, Any]:
        """I/O 分析"""
        result = {}

        try:
            # iostat
            result["iostat"] = subprocess.run(
                ["iostat", "-xz", "1", "3"],
                capture_output=True,
                text=True,
                timeout=15,
            ).stdout

            # 解析高 I/O 进程
            result["iotop"] = subprocess.run(
                ["iotop", "-bn1"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout

        except Exception as e:
            result["error"] = str(e)

        return result

    @staticmethod
    def profile_network() -> Dict[str, Any]:
        """网络分析"""
        result = {}

        try:
            # ss
            result["connections"] = subprocess.run(
                ["ss", "-s"],
                capture_output=True,
                text=True,
            ).stdout

            # netstat errors
            result["errors"] = subprocess.run(
                ["netstat", "-s"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout

        except Exception as e:
            result["error"] = str(e)

        return result
