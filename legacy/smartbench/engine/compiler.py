"""
代码分析器 - 验证 AI 建议的可行性

功能：
1. 解析 AI 建议中的代码修改
2. 验证代码位置是否正确
3. 分析代码修改的可行性
4. 输出建议的详细分析报告

注意：本模块仅用于分析和验证，不修改任何被测项目代码
"""

import re
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class CodeChange:
    """代码修改（仅用于分析，不写入文件）"""
    file_path: str
    line_start: int
    line_end: int
    original_code: str
    new_code: str
    location_confirmed: bool = False
    syntax_valid: bool = False


@dataclass
class ChangeAnalysis:
    """修改分析结果"""
    change: CodeChange
    is_valid: bool
    issues: List[str]
    suggestions: List[str]


@dataclass
class SuggestionReport:
    """建议报告"""
    suggestion: Dict[str, Any]
    location_valid: bool
    code_snippet: str
    analysis: str
    issues: List[str]
    estimated_impact: str
    risk_level: str


class CodeAnalyzer:
    """
    代码分析器

    功能：
    1. 解析 AI 建议中的代码修改位置
    2. 验证代码位置是否存在
    3. 提取原始代码片段
    4. 分析修改的可行性
    5. 生成详细的建议报告

    注意：本类仅分析代码，不修改任何文件
    """

    def __init__(self, project_path: str):
        """
        初始化代码分析器

        Args:
            project_path: 项目路径
        """
        self.project_path = Path(project_path)

        # C++ 语法关键字
        self.cpp_keywords = {
            'int', 'void', 'char', 'float', 'double', 'bool', 'auto',
            'class', 'struct', 'enum', 'union',
            'public', 'private', 'protected',
            'if', 'else', 'for', 'while', 'do', 'switch', 'case',
            'return', 'break', 'continue', 'goto',
            'try', 'catch', 'throw',
            'new', 'delete', 'nullptr',
            'std::', 'std::vector', 'std::map', 'std::unordered_map',
            'pthread_mutex', 'lock_guard', 'unique_lock',
        }

    def analyze_suggestion(
        self,
        suggestion: Dict[str, Any],
        read_original: bool = True,
    ) -> SuggestionReport:
        """
        分析一条优化建议

        Args:
            suggestion: AI 建议
            read_original: 是否读取原始代码

        Returns:
            建议报告
        """
        location = suggestion.get("location", "")
        title = suggestion.get("title", "未命名建议")
        description = suggestion.get("description", "")
        solution = suggestion.get("solution", "")
        priority = suggestion.get("priority", 3)
        risk = suggestion.get("risk_level", "medium")

        # 解析位置
        file_path, line_start, line_end = self._parse_location(location)

        # 读取原始代码
        code_snippet = ""
        location_valid = False
        issues = []
        suggestions_list = []

        if file_path:
            full_path = self.project_path / file_path
            if full_path.exists():
                location_valid = True
                if read_original:
                    code_snippet = self._read_code_snippet(full_path, line_start, line_end)
            else:
                issues.append(f"文件不存在: {file_path}")
        else:
            issues.append("无法解析代码位置")

        # 分析修改内容
        if solution:
            solution_issues, solution_suggestions = self._analyze_solution(solution)
            issues.extend(solution_issues)
            suggestions_list.extend(solution_suggestions)

        # 生成分析文本
        analysis = self._generate_analysis(title, description, priority, risk)

        # 评估影响
        estimated_impact = self._estimate_impact(priority, risk, location_valid)

        return SuggestionReport(
            suggestion=suggestion,
            location_valid=location_valid,
            code_snippet=code_snippet,
            analysis=analysis,
            issues=issues,
            estimated_impact=estimated_impact,
            risk_level=risk,
        )

    def analyze_suggestions(
        self,
        suggestions: List[Dict[str, Any]],
        read_original: bool = True,
    ) -> List[SuggestionReport]:
        """
        分析多条优化建议

        Args:
            suggestions: AI 建议列表
            read_original: 是否读取原始代码

        Returns:
            建议报告列表
        """
        reports = []
        for suggestion in suggestions:
            report = self.analyze_suggestion(suggestion, read_original)
            reports.append(report)
        return reports

    def _parse_location(self, location: str) -> Tuple[Optional[str], int, int]:
        """
        解析代码位置

        支持格式:
        - "Raft/Raft.cpp:156"
        - "Raft/Raft.cpp line 156"
        - "Raft/Raft.cpp 第 156 行"
        """
        if not location:
            return None, 0, 0

        # 尝试多种格式
        patterns = [
            r'([^\s:]+\.(?:cpp|h|cc|cxx)):\s*(\d+)',
            r'([^\s:]+)\s+line\s+(\d+)',
            r'([^\s:]+)\s+第\s+(\d+)\s+行',
        ]

        for pattern in patterns:
            match = re.search(pattern, location)
            if match:
                file_path = match.group(1)
                line_start = int(match.group(2))
                return file_path, line_start, line_start + 5  # 假设修改影响 5 行

        return None, 0, 0

    def _read_code_snippet(
        self,
        file_path: Path,
        start_line: int,
        end_line: int,
        context_lines: int = 3,
    ) -> str:
        """
        读取代码片段

        Args:
            file_path: 文件路径
            start_line: 起始行号
            end_line: 结束行号
            context_lines: 上下文行数

        Returns:
            代码片段
        """
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            total_lines = len(lines)

            # 添加上下文
            actual_start = max(0, start_line - context_lines - 1)
            actual_end = min(total_lines, end_line + context_lines)

            snippet_lines = []
            for i, line in enumerate(lines[actual_start:actual_end], actual_start + 1):
                prefix = ">>> " if start_line <= i <= end_line else "    "
                snippet_lines.append(f"{prefix}{i:4d}: {line.rstrip()}")

            return '\n'.join(snippet_lines)

        except Exception:
            return ""

    def _analyze_solution(self, solution: str) -> Tuple[List[str], List[str]]:
        """
        分析解决方案

        Returns:
            (issues, suggestions)
        """
        issues = []
        suggestions = []

        # 检查基本语法
        if not solution.strip():
            issues.append("解决方案为空")
            return issues, suggestions

        # 检查括号匹配
        if solution.count('{') != solution.count('}'):
            issues.append("花括号不匹配")
        if solution.count('(') != solution.count(')'):
            issues.append("圆括号不匹配")
        if solution.count('[') != solution.count(']'):
            issues.append("方括号不匹配")

        # 检查分号
        lines = solution.splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            # 非空行应该是语句（以分号结尾）或代码块（以 { 或 } 结尾）
            if line and not line.endswith(';') and not line.endswith('{') and not line.endswith('}') and not line.startswith('//') and not line.startswith('/*'):
                # 可能是多行语句的一部分
                pass

        # 检查常见问题
        if 'while' in solution and 'lock' in solution.lower():
            suggestions.append("注意：在持有锁的循环中需设置超时，防止死锁")

        if 'delete' in solution and 'lock' in solution.lower():
            suggestions.append("注意：在持有锁时 delete 可能导致问题，建议使用智能指针")

        if 'memcpy' in solution:
            suggestions.append("注意：使用 memcpy 时需确保内存区域不重叠")

        return issues, suggestions

    def _generate_analysis(
        self,
        title: str,
        description: str,
        priority: int,
        risk: str,
    ) -> str:
        """生成分析文本"""
        priority_text = {1: "低", 2: "较低", 3: "中", 4: "较高", 5: "高"}.get(priority, "中")
        risk_text = {"low": "低风险", "medium": "中等风险", "high": "高风险"}.get(risk, "中等风险")

        analysis = f"""建议: {title}
优先级: {priority_text} | 风险: {risk_text}
分析: {description}"""

        return analysis

    def _estimate_impact(
        self,
        priority: int,
        risk: str,
        location_valid: bool,
    ) -> str:
        """估算影响"""
        if not location_valid:
            return "无法估算（代码位置无效）"

        if priority >= 4 and risk == "low":
            return "预期收益高，风险低，建议优先实施"
        elif priority >= 4 and risk == "medium":
            return "预期收益高，需谨慎实施，建议先在测试环境验证"
        elif priority >= 4 and risk == "high":
            return "预期收益高，但风险也高，建议最后实施"
        elif priority >= 3:
            return "预期收益中等，可以考虑实施"
        else:
            return "优先级较低，可以延后处理"

    def generate_report(
        self,
        reports: List[SuggestionReport],
    ) -> str:
        """
        生成分析报告

        Args:
            reports: 建议报告列表

        Returns:
            报告文本
        """
        lines = []
        lines.append("=" * 60)
        lines.append("📋 AI 优化建议分析报告")
        lines.append("=" * 60)
        lines.append("")

        valid_count = sum(1 for r in reports if r.location_valid)
        lines.append(f"总计 {len(reports)} 条建议，其中 {valid_count} 条可定位到代码")

        for i, report in enumerate(reports, 1):
            lines.append("")
            lines.append(f"【建议 {i}】{'✅' if report.location_valid else '❌'}")
            lines.append("-" * 40)

            # 标题和描述
            suggestion = report.suggestion
            lines.append(f"标题: {suggestion.get('title', '未命名')}")
            lines.append(f"优先级: {suggestion.get('priority', 3)} | 风险: {report.risk_level}")
            lines.append("")

            # 位置信息
            location = suggestion.get("location", "未知")
            lines.append(f"代码位置: {location}")
            if report.location_valid:
                lines.append("✅ 位置有效")
            else:
                lines.append("❌ 位置无效")

            lines.append("")

            # 原始代码
            if report.code_snippet:
                lines.append("原始代码:")
                lines.append("```cpp")
                lines.append(report.code_snippet)
                lines.append("```")
                lines.append("")

            # 问题
            if report.issues:
                lines.append("⚠️  发现问题:")
                for issue in report.issues:
                    lines.append(f"  - {issue}")
                lines.append("")

            # 建议
            lines.append(f"📊 影响评估: {report.estimated_impact}")
            lines.append("")
            lines.append(f"💡 分析: {report.analysis}")

        lines.append("")
        lines.append("=" * 60)
        lines.append("报告生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("=" * 60)

        return '\n'.join(lines)


class ChangeExtractor:
    """
    修改提取器

    功能：从 AI 建议中提取代码修改信息

    注意：本类仅提取和验证信息，不修改任何文件
    """

    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.analyzer = CodeAnalyzer(project_path)

    def extract_changes(
        self,
        suggestions: List[Dict[str, Any]],
    ) -> List[CodeChange]:
        """
        从建议中提取代码修改信息

        Args:
            suggestions: AI 建议列表

        Returns:
            代码修改列表（仅用于分析，不写入文件）
        """
        changes = []

        for suggestion in suggestions:
            location = suggestion.get("location", "")
            if not location:
                continue

            file_path, line_start, line_end = self.analyzer._parse_location(location)
            if not file_path:
                continue

            new_code = suggestion.get("solution", "")
            if not new_code:
                new_code = suggestion.get("pseudocode", "")

            if not new_code:
                continue

            # 读取原始代码
            full_path = self.project_path / file_path
            original_code = ""
            location_confirmed = False

            if full_path.exists():
                location_confirmed = True
                original_code = self.analyzer._read_code_snippet(
                    full_path, line_start, line_end, context_lines=0
                )

            changes.append(CodeChange(
                file_path=file_path,
                line_start=line_start,
                line_end=line_end,
                original_code=original_code,
                new_code=new_code,
                location_confirmed=location_confirmed,
                syntax_valid=bool(new_code.strip()),
            ))

        return changes
