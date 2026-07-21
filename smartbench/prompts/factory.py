"""
PromptFactory — 根据项目指纹动态生成所有 Prompt（全中文）。

所有 Prompt 在运行时从 ProjectFingerprint 组装，零硬编码假设。
"""

from typing import Optional, List, Dict
from smartbench.detector.fingerprint import ProjectFingerprint, Language


class PromptFactory:
    """根据 ProjectFingerprint 动态生成所有 LLM Prompt。"""

    def __init__(self, fingerprint: ProjectFingerprint):
        self.fp = fingerprint

    # ── 阶段 2：项目理解 ──────────────────────────────────────────────

    def build_project_understanding_prompt(self, readme_content: str = "") -> str:
        """让 LLM 通过 README + 指纹信息理解项目。"""
        lang = self.fp.primary_language.value
        fw = self.fp.framework.value
        ptype = self.fp.project_type.value

        base = f"""你是一位资深软件架构师，正在分析一个陌生代码仓库。请用中文回复。

## 确定性信号（来自文件系统扫描）
- **主要语言**：{lang}（置信度：{self.fp.language_confidence:.0%}）
- **次要语言**：{[lang.value for lang in self.fp.secondary_languages] or '无'}
- **框架**：{fw}（置信度：{self.fp.framework_confidence:.0%}）
- **项目类型**：{ptype}
- **构建系统**：{self.fp.build_system or '未知'}
- **源文件**：{self.fp.source_files} 个（预估约 {self.fp.lines_of_code_estimate:,} 行）
- **入口文件**：{', '.join(self.fp.entry_points[:5]) or '未检测到'}
- **依赖项**：{', '.join(self.fp.dependencies[:20]) or '未检测到'}

## Git 信息
- **是否 Git 仓库**：{self.fp.is_git_repo}
- **最近提交数**：{self.fp.recent_commit_count}
- **近期变更的热点文件**：{', '.join(self.fp.hot_files[:10]) or '无'}"""

        if readme_content:
            base += f"""

## README 内容
{readme_content[:4000]}"""

        base += """

## 你的任务
基于以上信息，给出简洁的项目分析。请用中文输出 JSON：
```json
{
  "project_summary": "用1-2句话概括这个项目是做什么的",
  "primary_domain": "web_service / database / distributed_system / cli_tool / desktop_app / ...",
  "key_concerns": ["需要关注的问题1", "问题2", "问题3"],
  "suggested_diagnostic_focus": "从 性能/正确性/安全性/架构 中选择最应关注的维度",
  "additional_context_needed": "诊断前还需要了解什么（没有则填'无'）"
}
```
只返回 JSON，不要其他文字。"""

        return base

    # ── 阶段 3：策略选择 ──────────────────────────────────────────────

    def build_strategy_prompt(self, user_concern: str, available_strategies: List[Dict]) -> str:
        """让 LLM 选择并参数化诊断策略。"""
        lang = self.fp.primary_language.value
        fw = self.fp.framework.value

        strategy_list = "\n".join(
            f"- **{s['name']}**：{s['description']}（工具：{', '.join(s.get('tools', []))}）"
            for s in available_strategies
        )

        return f"""你正在为一个 {lang} {fw} 项目选择诊断策略。请用中文回复。

## 项目概况
- 语言：{lang}
- 框架：{fw}
- 类型：{self.fp.project_type.value}
- 规模：{self.fp.source_files} 个文件，约 {self.fp.lines_of_code_estimate:,} 行代码

## 用户关心的内容
{user_concern}

## 可选策略（已验证的诊断模板）
{strategy_list}

## 你的任务
选择最佳策略并参数化。请用中文输出 JSON：
```json
{{
  "selected_strategy": "策略名称",
  "confidence": 0.0到1.0,
  "reasoning": "为什么这个策略最合适（一句话，中文）",
  "parameter_overrides": {{
    "focus_areas": ["关注领域1"],
    "exclude_patterns": ["排除模式1"],
    "custom_thresholds": {{}}
  }},
  "alternative_strategies": ["备选策略1"],
  "estimated_duration_minutes": 5
}}
```
只返回 JSON，不要其他文字。"""

    # ── 阶段 5：多 Agent 辩论 ─────────────────────────────────────────

    def build_analysis_context(self, metrics: Optional[Dict] = None,
                                logs: str = "", error_logs: str = "",
                                code_context: str = "",
                                user_symptoms: str = "") -> str:
        """构建注入所有辩论 Prompt 的上下文块。"""
        parts = [f"## 项目信息\n"
                 f"- **项目名**：{self.fp.project_name}\n"
                 f"- **语言**：{self.fp.primary_language.value}\n"
                 f"- **框架**：{self.fp.framework.value}\n"
                 f"- **类型**：{self.fp.project_type.value}\n"
                 f"- **构建系统**：{self.fp.build_system}\n"]

        if metrics:
            parts.append(f"\n## 性能指标\n"
                        f"- QPS：{metrics.get('qps', 'N/A')}\n"
                        f"- 平均延迟：{metrics.get('avg_latency', 'N/A')} ms\n"
                        f"- P99 延迟：{metrics.get('p99_latency', 'N/A')} ms\n"
                        f"- 错误率：{metrics.get('error_rate', 'N/A')}\n")

        if user_symptoms:
            parts.append(f"\n## 用户反馈的问题\n{user_symptoms}\n")

        if code_context:
            parts.append(f"\n## 相关代码上下文\n{code_context[:4000]}\n")

        if logs:
            parts.append(f"\n## 应用日志\n{logs[:2000]}\n")

        if error_logs:
            parts.append(f"\n## 错误日志\n{error_logs[:1500]}\n")

        return "\n".join(parts)

    def build_proposer_prompt(self, analysis_context: str,
                               target_improvement: str = "找出并修复最严重的问题") -> str:
        """为特定语言/项目生成 Proposer（方案提出者）Prompt。"""
        lang = self.fp.primary_language.value
        ptype = self.fp.project_type.value

        lang_guidance = self._language_specific_guidance()

        return f"""你是一位 {lang} {ptype} 诊断专家（Proposer / 方案提出者）。
请用中文输出所有分析内容。

你的任务：分析以下项目上下文，提出具体、可落地的优化或修复方案。

{analysis_context}

## 诊断目标
{target_improvement}

## {lang} 语言专项指导
{lang_guidance}

## 输出要求
返回 JSON 对象，包含你的分析和方案。**每条方案必须附带 evidence_claims（可验证证据声明）。**

**重要**: 至少提供 2 种类型的 evidence_claims:
1. `file_location` - 问题所在的文件路径和行号
2. `call_chain` - 涉及的函数调用链（从代码上下文中找真实存在的函数名）

```json
{{
  "analysis": {{
    "root_cause": "根因分析（中文，100字以内）",
    "impact_assessment": "影响评估（中文，说明影响范围和严重程度）"
  }},
  "proposals": [
    {{
      "title": "方案标题（中文，15字以内）",
      "location": "文件路径:行号（必须是真实存在的文件）",
      "problem": "具体问题描述（中文，150字以内）",
      "solution": "具体修复方案，可用伪代码说明",
      "implementation_steps": ["步骤1（中文）", "步骤2（中文）", "步骤3（中文）"],
      "evidence_claims": [
        {{
          "type": "file_location",
          "target": "相对于项目根目录的文件路径:行号（必须是代码上下文中真实存在的文件）",
          "description": "此处存在你描述的问题"
        }},
        {{
          "type": "call_chain",
          "target": "函数A -> 函数B -> 函数C",
          "description": "该问题涉及的调用链（使用代码图中实际存在的函数名）"
        }},
        {{
          "type": "code_pattern",
          "target": "具体代码模式问题",
          "description": "如：缺少错误处理的异步调用"
        }}
      ],
      "expected_improvement": "预期改进效果（中文，如：延迟降低约15%）",
      "priority": 1至5,
      "risk_level": "low/medium/high"
    }}
  ]
}}
```
**重要**：evidence_claims 是必须提供的！每条方案至少提供 2 个 evidence_claims。
- file_location 的 target 字段必须使用上面提供的「代码上下文」中真实存在的文件路径和行号。
- call_chain 的 target 字段使用 "funcA -> funcB -> funcC" 格式，函数名必须来自代码图中真实存在的函数。
只能引用代码上下文中实际列出的文件，不要凭借猜测编造路径。
只返回 JSON，不要其他文字。"""

    def build_critique_prompt(self, proposals_json: str, analysis_context: str,
                              verification_summary: str = "") -> str:
        """生成 Critique（交叉审查者）Prompt。"""
        lang = self.fp.primary_language.value

        verif_section = ""
        if verification_summary:
            verif_section = f"""
## 自动事实核查结果
以下为 Proposer 方案中声明的文件和代码位置的自动验证结果。**标记为 [✗ 不存在] 的文件或调用链是 Proposer 编造的，在审查中必须明确指出并降低其可信度。**
{verification_summary}

"""

        return f"""你是一位严谨的 {lang} 软件架构审查专家（Critique / 交叉审查者）。
请用中文输出所有审查意见。

你的任务：审查 Proposer 提出的方案，从正确性、安全性、可行性角度评估。
**特别提示**：请仔细阅读下方的「自动事实核查结果」。如果某方案引用了不存在的文件（标记 [✗ 不存在]），
在裁决中必须明确指出这是虚假引用。

## 项目上下文
{analysis_context}

{verif_section}
## Proposer 的方案
{proposals_json}

## 审查维度
1. **正确性**：这个修复是否会引入新的 Bug？
2. **真实性**：方案引用的文件和代码位置是否真实存在？（参考上方的自动核查结果）
3. **安全性**：线程安全、数据一致性、异常处理是否考虑周全？
4. **可行性**：在当前代码库中实施这个方案的现实程度如何？
5. **副作用**：改动可能导致什么其他问题？

## 输出格式
返回 JSON（请用中文）：
```json
{{
  "verdicts": [
    {{
      "proposal_title": "对应的方案标题",
      "verdict": "accept（接受） / modify（需修改） / reject（拒绝）",
      "concerns": ["需要关注的问题1（中文）", "问题2（中文）"],
      "evidence_issues": ["文件不存在: xxx", "行号超出范围: yyy"],
      "suggested_modifications": "如果需要修改，给出具体修改建议（中文）"
    }}
  ],
  "overall_assessment": "总体评价（一句话，中文）"
}}
```
只返回 JSON，不要其他文字。"""

    def build_judge_prompt(self, proposals_json: str, critiques_json: str,
                            analysis_context: str,
                            verification_summary: str = "") -> str:
        """生成 Judge（最终仲裁者）Prompt。"""
        lang = self.fp.primary_language.value

        verif_section = ""
        if verification_summary:
            verif_section = f"""
## 事实核查证据链
以下为自动验证结果，标注了哪些声明有真实代码支撑、哪些是虚构的。
{verification_summary}

"""

        return f"""你是一位 {lang} 技术负责人（Judge / 最终仲裁者），需要做出最终决策。
请用中文输出最终诊断报告。

**重要提示**：请优先采纳有真实代码支撑的方案。对于自动核查结果中标记为 [✗ 不存在] 的方案，
除非 Critique 提供了额外验证，否则应降低其权重或直接拒绝。

## 项目上下文
{analysis_context}

{verif_section}
## Proposer 的方案（含事实核查标注）
{proposals_json}

## Critique 的审查意见（含证据核查）
{critiques_json}

## 你的任务
综合双方观点和事实核查证据链，产出一份最终的可执行诊断报告。

返回 JSON（请用中文）：
```json
{{
  "decision": "accepted（接受） / mixed（部分接受） / rejected（拒绝）",
  "reasoning": "决策理由（中文，150字以内）",
  "final_suggestions": [
    {{
      "title": "最终建议标题（中文）",
      "description": "问题分析 + 解决方案描述（中文）",
      "implementation": "具体实施步骤（中文）",
      "location": "文件:行号（如适用，确保引用真实存在的文件）",
      "evidence_status": "该建议的证据验证状态",
      "priority": 1至5,
      "risk_level": "low/medium/high",
      "consensus": "high（高共识） / medium（中等） / low（低共识）"
    }}
  ],
  "rejected_proposals": [
    {{
      "title": "被拒绝的建议标题",
      "reason": "拒绝原因（如：引用的文件不存在、调用链无法验证等）"
    }}
  ],
  "risk_summary": "实施过程中需要注意的顶层风险（中文）"
}}
```
只返回 JSON，不要其他文字。"""

    # ── 核查结果格式化 ─────────────────────────────────────────────

    def build_verification_summary(self, proposals: list) -> str:
        """
        构建人类可读的核查结果摘要（中文），用于注入下一轮 Prompt。

        Args:
            proposals: 已添加 __verification 字段的方案列表

        Returns:
            格式化的 Markdown 字符串
        """
        parts = ["\n## 事实核查结果（自动验证）\n"]

        for p in proposals:
            if not isinstance(p, dict):
                continue
            verif = p.get("__verification", {})
            if not verif:
                continue

            score = verif.get("verification_score", 0)
            verdict = verif.get("verdict", "unknown")
            title = p.get("title", "?")

            if verdict == "verified":
                parts.append(f"- [✓ 已验证] **{title}** (得分: {score:.0%})")
                for loc in verif.get("verified_locations", []):
                    parts.append(f"  - 文件已验证: `{loc}`")
            elif verdict == "partial":
                parts.append(f"- [⚠ 部分匹配] **{title}** (得分: {score:.0%})")
                for loc in verif.get("partial_locations", []):
                    parts.append(f"  - 路径偏离: {loc}")
                for loc in verif.get("hallucinated_locations", []):
                    parts.append(f"  - ✗ 不存在: `{loc}`")
            elif verdict == "hallucinated":
                parts.append(f"- [✗ 不存在] **{title}** (得分: {score:.0%})")
                for loc in verif.get("hallucinated_locations", []):
                    parts.append(f"  - ✗ 文件不存在: `{loc}`")
            else:
                parts.append(f"- [? 无法验证] **{title}**")

            for flag in verif.get("flags", []):
                parts.append(f"  - {flag}")

        parts.append(
            "\n**注意**：以上核查结果由代码图 + 磁盘扫描自动生成，不含 LLM 调用。"
            "请据此调整审查判断。\n"
        )
        return "\n".join(parts)

    # ── 语言专项诊断指导 ─────────────────────────────────────────────

    def _language_specific_guidance(self) -> str:
        """返回语言专项诊断提示。"""
        lang = self.fp.primary_language

        guidance = {
            Language.PYTHON: (
                "- 检查 CPU 密集型线程中的 GIL 争用\n"
                "- 关注 asyncio 事件循环阻塞\n"
                "- 使用 tracemalloc / py-spy 进行内存分析\n"
                "- 考虑 pydantic 模型在热路径中的开销\n"
                "- 检查 Flask/FastAPI 中是否存在同步阻塞调用"
            ),
            Language.GO: (
                "- 使用 pprof 进行 CPU/内存分析\n"
                "- 检查 goroutine 泄漏（runtime.NumGoroutine()）\n"
                "- 关注 channel 阻塞和 select{} 死锁\n"
                "- 使用竞态检测器：go test -race\n"
                "- 考虑在分配密集路径中使用 sync.Pool"
            ),
            Language.RUST: (
                "- 检查不必要的 .clone() 调用\n"
                "- 关注 async task 未 join 导致泄漏\n"
                "- 使用 cargo-flamegraph 进行 CPU 分析\n"
                "- 考虑锁竞争：std::sync::Mutex vs tokio::sync::Mutex"
            ),
            Language.CPP: (
                "- 使用 perf + FlameGraph 进行 CPU 分析\n"
                "- 使用 Valgrind/ASAN 检查内存泄漏\n"
                "- 关注多线程路径中的锁竞争\n"
                "- 考虑移动语义和拷贝省略优化\n"
                "- 检查热路径中虚函数调度的开销"
            ),
            Language.JAVA: (
                "- 使用 JFR（Java Flight Recorder）进行性能分析\n"
                "- 检查 GC 暂停时间和分配速率\n"
                "- 关注线程池耗尽问题\n"
                "- 使用 Arthas 进行在线诊断"
            ),
            Language.JAVASCRIPT: (
                "- 使用 clinic.js / 0x 生成火焰图\n"
                "- 检查 Promise.all() 是否缺少错误处理\n"
                "- 关注同步 I/O 阻塞事件循环\n"
                "- 使用 --inspect + Chrome DevTools 进行 CPU 分析\n"
                "- 检查前端是否有未清理的定时器和事件监听器"
            ),
            Language.TYPESCRIPT: (
                "- 同上 JavaScript 指导，外加：\n"
                "- 检查过度类型推断的性能开销\n"
                "- 关注 NestJS 中装饰器的运行时开销"
            ),
            Language.RUBY: (
                "- 使用 ruby-prof / stackprof 进行 CPU 分析\n"
                "- 检查 ActiveRecord 中的 N+1 查询\n"
                "- 关注对象分配导致的内存膨胀\n"
                "- 使用 rbtrace 进行在线方法追踪"
            ),
            Language.SWIFT: (
                "- 使用 Instruments（Time Profiler、Allocations）进行分析\n"
                "- 检查循环引用导致的内存泄漏\n"
                "- 关注主线程阻塞\n"
                "- 使用 swift-concurrency 检查 actor 隔离"
            ),
            Language.CSHARP: (
                "- 使用 dotnet-trace / PerfView 进行 CPU 分析\n"
                "- 检查 async/await 死锁（ConfigureAwait）\n"
                "- 关注 LINQ 物化开销\n"
                "- 使用 dotnet-counters 进行实时监控"
            ),
            Language.KOTLIN: (
                "- 复用 JVM 工具：JFR、jstack、jmap\n"
                "- 检查协程取消泄漏\n"
                "- 关注不必要的对象装箱\n"
                "- 使用 kotlinx-benchmark 进行微基准测试"
            ),
            Language.ZIG: (
                "- 使用 valgrind + kcachegrind 进行分析\n"
                "- 使用 zig test 检查未定义行为\n"
                "- 关注分配器不匹配问题\n"
                "- 浮点数比较使用 std.testing.expectApproxEqRel"
            ),
        }

        return guidance.get(lang, "- 使用该语言的通用分析工具\n"
                            "- 检查常见的并发和内存问题")
