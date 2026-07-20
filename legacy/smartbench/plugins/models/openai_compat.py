"""
OpenAI 兼容接口模型插件

支持 OpenAI API 兼容的所有模型，包括 DeepSeek、Qwen 等。
"""

from typing import List, Dict, Any, Optional
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


class OpenAICompatiblePlugin(BaseModelPlugin):
    """
    OpenAI 兼容接口模型插件
    
    支持任何兼容 OpenAI API 格式的服务商：
    - OpenAI (GPT-4, GPT-3.5)
    - DeepSeek
    - 阿里云 DashScope (Qwen)
    - 本地部署的兼容模型
    
    Example:
        # DeepSeek
        plugin = OpenAICompatiblePlugin(
            api_key="sk-xxx",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )
        
        # Qwen
        plugin = OpenAICompatiblePlugin(
            api_key="sk-xxx",
            base_url="https://dashscope.aliyuncs.com",
            model="qwen-turbo",
        )
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-3.5-turbo",
        base_url: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        """
        初始化 OpenAI 兼容插件
        
        Args:
            api_key: API 密钥
            model: 模型名称
            base_url: API 基础 URL
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大 token 数
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
        """
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or "https://api.openai.com/v1"
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
        return self.model
    
    @property
    def default_weight(self) -> float:
        """根据模型名称返回默认权重"""
        model_lower = self.model.lower()
        
        # 强力模型
        if any(x in model_lower for x in ['gpt-4', 'claude', 'deepseek-chat', 'qwen-plus']):
            return 1.2
        
        # 中等模型
        if any(x in model_lower for x in ['gpt-3.5', 'deepseek-coder', 'qwen-turbo']):
            return 0.9
        
        # 轻量模型
        return 0.7
    
    @property
    def provider(self) -> str:
        """返回提供商名称"""
        if 'deepseek' in self.base_url.lower():
            return 'deepseek'
        elif 'dashscope' in self.base_url.lower() or 'qwen' in self.model.lower():
            return 'dashscope'
        elif 'openai' in self.base_url.lower():
            return 'openai'
        return 'openai-compatible'
    
    def _get_client(self):
        """获取或初始化客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                )
            except ImportError:
                raise ImportError(
                    "请安装 openai 包: pip install openai"
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
                messages = self._build_messages(context)

                # 调用 API（添加显式超时）
                client = self._get_client()
                response = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    timeout=self.timeout,  # 显式超时
                )
                
                # 解析响应
                response_text = response.choices[0].message.content or ""
                
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
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
                max_tokens=4096,
            )
            
            return response.choices[0].message.content or ""
        except Exception as e:
            return f"文档生成失败: {str(e)}"
    
    def _build_messages(self, context: AnalysisContext) -> List[Dict[str, str]]:
        """
        构建消息列表
        
        Args:
            context: 分析上下文
            
        Returns:
            消息列表
        """
        # 构建用户消息
        user_content = self._build_analysis_prompt(context)
        
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
        ]
    
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
    
    def _parse_suggestions_from_response(self, response_text: str) -> List[Suggestion]:
        """
        从响应中解析建议列表
        
        继承基类方法，增加额外处理逻辑。
        """
        suggestions = super()._parse_suggestions_from_response(response_text)
        
        # 如果解析失败，尝试更宽松的匹配
        if not suggestions:
            suggestions = self._loose_parse(response_text)
        
        return suggestions
    
    def _loose_parse(self, text: str) -> List[Suggestion]:
        """
        宽松解析
        
        当 JSON 解析失败时，尝试提取结构化信息。
        
        Args:
            text: 响应文本
            
        Returns:
            建议列表
        """
        import re
        suggestions = []
        
        # 尝试匹配常见的标题模式
        patterns = [
            r'(?:^|\n)#{1,3}\s*(优化|优化方案|建议)[:：]?\s*(.+)',
            r'(?:优化|优化方案|建议)\s*\d*[:：]\s*(.+)',
        ]
        
        titles = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.MULTILINE)
            titles.extend([m[-1].strip() for m in matches])
        
        # 去重
        titles = list(dict.fromkeys(titles))[:3]
        
        for title in titles:
            suggestion = Suggestion(
                title=title,
                description="请参考完整响应获取详细分析",
                pseudocode="# 请参考完整响应",
                priority=3,
                risk_level=RiskLevel.MEDIUM,
                expected_gain="待评估",
                implementation_steps=["参考完整响应"],
                source_model=self.name,
                self_confidence=0.3,
                base_weight=self.default_weight,
            )
            suggestions.append(suggestion)
        
        return suggestions
