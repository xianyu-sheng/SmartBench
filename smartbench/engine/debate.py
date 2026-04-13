"""
多模型辩论引擎 - DB-Pilot 的核心诊断组件

实现三个角色：
1. Proposer (方案提出者): 分析日志和源码，生成优化方案
2. Critique (交叉审查者): 审查方案的潜在风险
3. Judge (最终仲裁者): 综合意见，输出最终建议
"""

import json
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class DebateResult:
    """辩论结果"""
    final_suggestions: List[Dict[str, Any]] = field(default_factory=list)
    debate_log: List[Dict[str, str]] = field(default_factory=list)
    consensus_reached: bool = False
    iterations: int = 0
    code_snippets: Dict[str, str] = field(default_factory=dict)


@dataclass
class ModelResponse:
    """模型响应"""
    model_name: str
    role: str
    content: str
    success: bool
    error: Optional[str] = None


class DebateEngine:
    """
    多模型辩论引擎

    工作流程:
    1. Proposer 分析问题，生成初始方案
    2. Critique 审查方案，找出潜在风险
    3. Judge 综合意见，决定是否接受或打回
    4. 迭代直到达成共识或达到最大轮次
    """

    def __init__(
        self,
        api_config: Dict[str, Any],
        code_cache=None,
        max_iterations: int = 2,
        timeout_per_call: int = 45,
    ):
        """
        初始化辩论引擎

        Args:
            api_config: API 配置 (api_key, base_url, model)
            code_cache: 代码缓存实例
            max_iterations: 最大辩论轮次
            timeout_per_call: 每次调用的超时时间
        """
        self.api_config = api_config
        self.code_cache = code_cache
        self.max_iterations = max_iterations
        self.timeout = timeout_per_call

        # 角色 Prompt 模板 - 详细版本
        self.proposer_prompt = """你是一个资深 C++ 分布式系统性能优化专家 (Proposer)。

你的任务是分析以下 Raft KV 系统的性能问题，并提出**具体可实施**的优化方案。

## 当前性能指标
- 当前 QPS: {qps}
- 目标 QPS: {target_qps}
- 当前延迟: {avg_latency} ms (平均), {p99_latency} ms (P99)
- 性能缺口: {target_qps} - {qps} = {gap:.0f} QPS

## 关键源码片段
{code_snippets}

## 重要原则
1. **不要重复已有的实现**: 先检查源码中是否已经实现了该优化
2. **问题根因要具体**: 不能只说"RPC 开销大"，要具体指出是哪段代码、什么机制导致的
3. **效果要量化**: 给出预计 QPS 提升百分比、延迟降低百分比
4. **代码位置要精确**: 给出具体的文件名和行号

## 输出要求
只输出 JSON，不要有任何其他文字：
{{
  "performance_analysis": {{
    "current_bottleneck": "当前最大瓶颈是什么（50字以内）",
    "root_cause": "根本原因分析（100字以内），要具体到代码层面",
    "gap_analysis": "当前 QPS 与目标的差距原因（50字以内）"
  }},
  "proposals": [
    {{
      "title": "优化方案简称（10字以内）",
      "location": "相对项目根目录的文件路径:行号",
      "problem_detail": "问题详细描述（100字以内）",
      "root_cause": "该问题的根本原因（80字以内）",
      "why_not_implemented": "为什么当前代码没有实现这个优化（50字以内）",
      "pseudocode": "伪代码或具体代码片段",
      "implementation_steps": ["步骤1", "步骤2", "步骤3"],
      "expected_improvement": {{
        "qps_increase_percent": "QPS 预计提升百分比，如 15%",
        "latency_reduce_percent": "延迟预计降低百分比，如 20%",
        "target_qps_after": "优化后预计 QPS"
      }},
      "priority": 1-5的数字,
      "risk_level": "low/medium/high",
      "implementation_effort": "实现难度 low/medium/high"
    }}
  ]
}}
"""

        self.critique_prompt = """你是一个严格的 C++ 系统架构评审专家 (Critique)。

你将审查 Proposer 提出的优化方案，找出其中的潜在风险和问题。

## Proposer 的方案分析
{proposals}

## 关键源码参考
{code_snippets}

## 审查维度
1. **正确性**: 是否可能引入 bug 或数据不一致
2. **Raft 协议**: 是否违反 Raft 一致性协议
3. **线程安全**: 多线程环境下是否安全
4. **已有实现**: 源码中是否已经有类似实现
5. **边界情况**: 是否有未处理的边界情况

## 输出要求
只输出 JSON，不要有任何其他文字：
{{
  "verdicts": [
    {{
      "proposal_title": "对应的方案标题",
      "verdict": "accept/modify/reject",
      "root_cause_valid": true或false,
      "implementation_correct": true或false,
      "raft_safe": true或false,
      "concerns": ["问题1", "问题2"],
      "required_modifications": "必须修改的内容（如果有问题）",
      "estimated_effect": "修正后的预计效果"
    }}
  ],
  "overall_assessment": "总体评价（100字以内）",
  "accepted_count": 数字,
  "rejected_count": 数字
}}
"""

        self.judge_prompt = """你是一个最终决策者 (Judge)。

你将综合 Proposer 的方案和 Critique 的评审意见，做出最终决定。

## Proposer 的方案
{proposals}

## Critique 的评审
{critiques}

## 当前性能指标（参考）
- 当前 QPS: {qps}
- 目标 QPS: {target_qps}
- 当前延迟: {avg_latency} ms

## 输出要求
只输出 JSON，不要有任何其他文字：
{{
  "decision_summary": {{
    "total_proposals": 数字,
    "accepted": 数字,
    "rejected": 数字,
    "modified": 数字,
    "reasoning": "决策总体理由（100字以内）"
  }},
  "final_suggestions": [
    {{
      "rank": 优先级排名（1, 2, 3...）,
      "title": "方案标题",
      "status": "accepted/modified/rejected",
      "problem_summary": "问题概述（50字以内）",
      "root_cause": "根本原因（60字以内）",
      "implementation_steps": [
        "步骤1：具体要做什么",
        "步骤2：具体要做什么",
        "步骤3：具体要做什么"
      ],
      "pseudocode": "核心代码片段或伪代码",
      "location": "文件路径:行号",
      "expected_result": {{
        "qps_before": 当前QPS数字,
        "qps_after": 优化后预计QPS数字,
        "qps_improvement_percent": "QPS提升百分比，如 15%",
        "latency_before_ms": 当前延迟数字,
        "latency_after_ms": 优化后预计延迟,
        "latency_improvement_percent": "延迟降低百分比，如 20%"
      }},
      "priority": 1-5,
      "risk_level": "low/medium/high",
      "implementation_effort": "实现难度 low/medium/high",
      "model_consensus": "三个模型的一致性 high/medium/low"
    }}
  ],
  "execution_order": ["方案1标题", "方案2标题"],
  "total_expected_qps": "所有方案实施后预计总QPS"
}}
"""

    def run_debate(
        self,
        metrics: Dict[str, Any],
        code_snippets: str = "",
        logs: str = "",
    ) -> DebateResult:
        """
        运行完整的多轮辩论

        Args:
            metrics: 性能指标
            code_snippets: 代码片段
            logs: 服务器日志

        Returns:
            DebateResult: 包含最终建议和辩论日志
        """
        debate_log = []

        # 填充指标
        qps = metrics.get("qps", 0)
        target_qps = metrics.get("target_qps", 400)
        avg_latency = metrics.get("avg_latency", 0)
        p99_latency = metrics.get("p99_latency", 0)
        gap = target_qps - qps

        # 合并代码和日志
        context = f"=== 关键代码 ===\n{code_snippets}\n\n=== 服务器日志 ===\n{logs[:5000] if logs else ''}"

        # ============ 第 1 轮：Proposer 提出方案 ============
        proposals = self._run_proposer(qps, target_qps, avg_latency, p99_latency, gap, context)
        debate_log.append({
            "role": "proposer",
            "content": json.dumps(proposals, ensure_ascii=False, indent=2),
        })

        # ============ 第 2 轮：Critique 审查 ============
        critiques = self._run_critique(proposals, context)
        debate_log.append({
            "role": "critique",
            "content": json.dumps(critiques, ensure_ascii=False, indent=2),
        })

        # ============ 第 3 轮：Judge 决策 ============
        final_decision = self._run_judge(proposals, critiques, qps, target_qps, avg_latency)
        debate_log.append({
            "role": "judge",
            "content": json.dumps(final_decision, ensure_ascii=False, indent=2),
        })

        # 提取最终建议
        final_suggestions = final_decision.get("final_suggestions", [])

        # 添加来源信息
        model_name = self.api_config.get("model", "deepseek")
        for suggestion in final_suggestions:
            suggestion["source_model"] = model_name

        # 提取代码片段
        code_snippets_dict = {}
        for proposal in proposals.get("proposals", []):
            location = proposal.get("location", "")
            if location and ":" in location:
                code_snippets_dict[location] = proposal.get("pseudocode", "")

        return DebateResult(
            final_suggestions=final_suggestions,
            debate_log=debate_log,
            consensus_reached=final_decision.get("decision_summary", {}).get("accepted", 0) > 0,
            iterations=3,
            code_snippets=code_snippets_dict,
        )

    def _run_proposer(
        self,
        qps: float,
        target_qps: float,
        avg_latency: float,
        p99_latency: float,
        gap: float,
        context: str,
    ) -> Dict[str, Any]:
        """运行 Proposer"""
        prompt = self.proposer_prompt.format(
            qps=qps,
            target_qps=target_qps,
            avg_latency=avg_latency,
            p99_latency=p99_latency,
            gap=gap,
            code_snippets=context[:6000],  # 减少代码量
        )

        response = self._call_model(prompt)
        if response.success:
            try:
                content = self._clean_json(response.content)
                result = json.loads(content)
                return result
            except json.JSONDecodeError as e:
                return {"performance_analysis": {}, "proposals": []}
        return {"performance_analysis": {}, "proposals": []}

    def _run_critique(self, proposals: Dict[str, Any], context: str) -> Dict[str, Any]:
        """运行 Critique"""
        prompt = self.critique_prompt.format(
            proposals=json.dumps(proposals, ensure_ascii=False, indent=2)[:3000],
            code_snippets=context[:3000],
        )

        response = self._call_model(prompt)
        if response.success:
            try:
                content = self._clean_json(response.content)
                return json.loads(content)
            except json.JSONDecodeError as e:
                return {"verdicts": [], "overall_assessment": ""}
        return {"verdicts": [], "overall_assessment": ""}

    def _run_judge(
        self,
        proposals: Dict[str, Any],
        critiques: Dict[str, Any],
        qps: float,
        target_qps: float,
        avg_latency: float,
    ) -> Dict[str, Any]:
        """运行 Judge"""
        prompt = self.judge_prompt.format(
            proposals=json.dumps(proposals, ensure_ascii=False, indent=2)[:4000],
            critiques=json.dumps(critiques, ensure_ascii=False, indent=2)[:3000],
            qps=qps,
            target_qps=target_qps,
            avg_latency=avg_latency,
        )

        response = self._call_model(prompt)
        if response.success:
            try:
                content = self._clean_json(response.content)
                result = json.loads(content)
                return result
            except json.JSONDecodeError as e:
                return {"final_suggestions": []}
        return {"final_suggestions": []}

    def _clean_json(self, content: str) -> str:
        """清理 JSON 响应"""
        content = content.strip()
        if content.startswith('```'):
            parts = content.split('```')
            for part in parts:
                part = part.strip()
                if part.startswith('json'):
                    part = part[4:].strip()
                if part.startswith('{') or part.startswith('['):
                    return part
            return parts[-1] if parts else content
        return content

    def _call_model(self, prompt: str) -> ModelResponse:
        """调用模型"""
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        model = self.api_config.get("model", "deepseek-v3-2-251201")

        # 重试机制
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                headers = {
                    "Authorization": f"Bearer {self.api_config['api_key']}",
                    "Content-Type": "application/json"
                }

                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是一个严格的 C++ 分布式系统性能优化专家。你输出的 JSON 必须严格符合格式要求。"},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 3000,
                }

                response = requests.post(
                    f"{self.api_config['base_url']}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                    verify=False,
                )

                if response.status_code != 200:
                    return ModelResponse(
                        model_name=model,
                        role="",
                        content="",
                        success=False,
                        error=f"API error: {response.status_code}",
                    )

                result = response.json()
                content = result["choices"][0]["message"]["content"]

                return ModelResponse(
                    model_name=model,
                    role="",
                    content=content,
                    success=True,
                )

            except requests.exceptions.SSLError as e:
                # SSL 错误，稍后重试
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
                    continue
                return ModelResponse(
                    model_name=model,
                    role="",
                    content="",
                    success=False,
                    error=f"SSL error after {max_retries} retries: {str(e)[:100]}",
                )

            except requests.exceptions.Timeout as e:
                return ModelResponse(
                    model_name=model,
                    role="",
                    content="",
                    success=False,
                    error=f"Request timeout",
                )

            except Exception as e:
                return ModelResponse(
                    model_name=model,
                    role="",
                    content="",
                    success=False,
                    error=str(e),
                )

        return ModelResponse(
            model_name=model,
            role="",
            content="",
            success=False,
            error="Max retries exceeded",
        )


class MultiModelAggregator:
    """
    多模型结果聚合器

    当只有单个模型或辩论引擎不可用时，使用此聚合器对多个模型的建议进行去重和排序。
    """

    def __init__(self, models: List[Dict[str, Any]]):
        self.models = models

    def aggregate(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """聚合多个模型的结果"""
        all_suggestions = []

        for result in results:
            if result.get("success") and result.get("suggestions"):
                for suggestion in result["suggestions"]:
                    suggestion["source_model"] = result["model"]
                    all_suggestions.append(suggestion)

        unique = self._deduplicate(all_suggestions)
        scored = self._score_and_sort(unique)

        return scored

    def _deduplicate(self, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """基于标题相似度去重"""
        unique = []
        for s in suggestions:
            title = s.get("title", "").lower()
            is_dup = False

            for u in unique:
                u_title = u.get("title", "").lower()
                if title[:30] == u_title[:30]:
                    if s.get("priority", 3) > u.get("priority", 3):
                        unique.remove(u)
                    else:
                        is_dup = True
                        break

            if not is_dup:
                unique.append(s)

        return unique

    def _score_and_sort(self, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """评分并排序"""

        def calc_score(s):
            priority = s.get("priority", 3)
            risk = s.get("risk_level", "medium")
            risk_score = {"low": 1.2, "medium": 1.0, "high": 0.7}.get(risk, 1.0)
            return priority * risk_score

        return sorted(suggestions, key=calc_score, reverse=True)
