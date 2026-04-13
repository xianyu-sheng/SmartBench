"""
Anthropic Claude 模型插件

支持 Claude 系列模型的调用。
"""

from typing import List, Optional, Any
import time
import json

from smartbench.core.types import (
    AnalysisResult, 
    AnalysisContext, 
    Suggestion, 
    RiskLevel,
    Metrics
)
from smartbench.plugins.models.base import BaseModelPlugin


class AnthropicPlugin(BaseModelPlugin):
    """
    Anthropic Claude 模型插件
    
    支持 Claude 系列模型：
    - claude-3-5-sonnet-20241022
    - claude-3-opus-20240229
    - claude-3-haiku-20240307
    
    Example:
        plugin = AnthropicPlugin(
            api_key="sk-ant-xxx",
            model="claude-3-5-sonnet-20241022",
        )
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-sonnet-20241022",
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        """
        初始化 Anthropic 插件
        
        Args:
            api_key: API 密钥
            model: 模型名称
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大 token 数
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
        """
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        
        if system_prompt:
            self._system_prompt = system_prompt
        
        # 初始化客户端
        self._client: Optional[Any] = None
    
    @property
    def name(self) -> str:
        """返回模型名称"""
        return f"claude-{self.model}"
    
    @property
    def default_weight(self) -> float:
        """根据模型返回默认权重"""
        model_lower = self.model.lower()
        
        # Opus 是最强模型
        if 'opus' in model_lower:
            return 1.5
        
        # Sonnet 是主力模型
        if 'sonnet' in model_lower:
            return 1.3
        
        # Haiku 是轻量模型
        if 'haiku' in model_lower:
            return 0.9
        
        return 1.2
    
    @property
    def provider(self) -> str:
        """返回提供商名称"""
        return "anthropic"
    
    def _get_client(self):
        """获取或初始化客户端"""
        if self._client is None:
            try:
                from anthropic import Anthropic
                self._client = Anthropic(
                    api_key=self.api_key,
                    timeout=self.timeout,
                )
            except ImportError:
                raise ImportError(
                    "请安装 anthropic 包: pip install anthropic"
                )
        return self._client
    
    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        """
        执行性能分析
        
        Args:
            context: 分析上下文
            
        Returns:
            AnalysisResult: 分析结果
        """
        start_time = time.time()
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                # 构建消息
                user_message = self._build_analysis_prompt(context)
                
                # 调用 API
                client = self._get_client()
                response = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=self._system_prompt,
                    messages=[
                        {"role": "user", "content": user_message}
                    ],
                )
                
                # 解析响应
                response_text = response.content[0].text if response.content else ""
                
                # 提取建议
                suggestions = self._parse_suggestions_from_response(response_text)
                
                # 填充来源模型
                for suggestion in suggestions:
                    suggestion.source_model = self.name
                    suggestion.base_weight = self.default_weight
                
                return AnalysisResult(
                    model_name=self.name,
                    suggestions=suggestions,
                    raw_response=response_text,
                    processing_time=time.time() - start_time,
                )
                
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # 指数退避
                continue
        
        return AnalysisResult(
            model_name=self.name,
            error=str(last_error),
            processing_time=time.time() - start_time,
        )
    
    def generate_document(
        self, 
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float
    ) -> str:
        """
        生成技术文档
        
        Args:
            suggestions: 优化建议列表
            metrics: 当前性能指标
            target_qps: 目标 QPS
            
        Returns:
            Markdown 格式的技术文档
        """
        # 构建提示词
        prompt = self._build_document_prompt(suggestions, metrics, target_qps)
        
        try:
            client = self._get_client()
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=0.5,
                system=self._system_prompt,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )
            
            return response.content[0].text if response.content else ""
        except Exception as e:
            return f"文档生成失败: {str(e)}"
    
    def _build_analysis_prompt(self, context: AnalysisContext) -> str:
        """
        构建分析提示词
        
        Args:
            context: 分析上下文
            
        Returns:
            提示词文本
        """
        lines = [
            "# 性能分析请求",
            "",
            "## 系统信息",
            f"- 系统类型: {context.system_type.value}",
            f"- 目标 QPS: {context.target_qps}",
            "",
            "## 当前性能指标",
            f"- QPS: {context.metrics.qps:.1f}",
            f"- 平均延迟: {context.metrics.avg_latency:.1f}ms",
            f"- P50 延迟: {context.metrics.p50_latency:.1f}ms",
            f"- P99 延迟: {context.metrics.p99_latency:.1f}ms",
            f"- 错误率: {context.metrics.error_rate:.2%}",
            "",
        ]
        
        if context.logs:
            lines.extend([
                "## 日志片段（最近 50 行）",
                "```",
                context.logs[-3000:] if len(context.logs) > 3000 else context.logs,
                "```",
                "",
            ])
        
        if context.source_code:
            lines.extend([
                "## 关键代码片段",
                "```cpp",
                context.source_code[:2000] if len(context.source_code) > 2000 else context.source_code,
                "```",
                "",
            ])
        
        lines.extend([
            "## 分析要求",
            "1. 识别主要性能瓶颈",
            "2. 提出 1-3 条具体可实施的优化建议",
            "3. 每条建议需包含: 标题、问题分析、伪代码、优先级、风险等级、预期收益、实施步骤",
            "4. 输出格式为 JSON",
        ])
        
        return "\n".join(lines)
    
    def _build_document_prompt(
        self,
        suggestions: List[Suggestion],
        metrics: Metrics,
        target_qps: float
    ) -> str:
        """
        构建文档生成提示词
        
        Args:
            suggestions: 优化建议列表
            metrics: 当前性能指标
            target_qps: 目标 QPS
            
        Returns:
            提示词文本
        """
        suggestions_text = "\n".join([
            f"- **{s.title}**: {s.description[:100]}..."
            for s in suggestions
        ])
        
        return f"""请为以下优化建议生成详细的技术文档：

## 背景信息
- 当前 QPS: {metrics.qps:.1f}
- 目标 QPS: {target_qps}
- QPS 差距: {((target_qps - metrics.qps) / target_qps * 100):.1f}%

## 优化建议摘要
{suggestions_text}

## 输出要求
请生成 Markdown 格式的技术文档，包含：
1. 每个建议的详细问题分析
2. 完整的伪代码实现
3. 预期性能收益
4. 风险评估和注意事项
"""
