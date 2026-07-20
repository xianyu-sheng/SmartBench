"""
模型插件基类

定义统一的模型接口，所有模型插件需实现此接口。
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import json
import time
from smartbench.core.types import (
    AnalysisResult, 
    AnalysisContext, 
    Suggestion, 
    RiskLevel,
    Metrics
)


class BaseModelPlugin(ABC):
    """
    模型插件抽象基类
    
    所有 AI 模型插件必须继承此类并实现抽象方法。
    
    Example:
        class DeepSeekPlugin(BaseModelPlugin):
            def __init__(self, api_key: str):
                super().__init__()
                self.api_key = api_key
                
            @property
            def name(self) -> str:
                return "deepseek"
                
            def analyze(self, context: AnalysisContext) -> AnalysisResult:
                # 实现分析逻辑
                ...
    """
    
    def __init__(self):
        """初始化模型插件"""
        self._system_prompt = self._default_system_prompt()
    
    @property
    @abstractmethod
    def name(self) -> str:
        """
        返回模型名称
        
        Returns:
            模型名称标识
        """
        pass
    
    @property
    def default_weight(self) -> float:
        """
        返回模型默认权重
        
        基于模型能力设置：
        - 强力模型 (Claude-4, GPT-4): 1.2-1.5
        - 中等模型 (GPT-3.5, DeepSeek): 0.8-1.0
        - 轻量模型 (Qwen-turbo): 0.6-0.8
        
        Returns:
            默认权重值
        """
        return 1.0
    
    @property
    def provider(self) -> str:
        """
        返回模型提供商
        
        Returns:
            提供商名称
        """
        return "unknown"
    
    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        """
        执行性能分析
        
        Args:
            context: 分析上下文（指标、日志、代码等）
            
        Returns:
            AnalysisResult: 分析结果，包含建议列表和原始响应
        """
        pass
    
    @abstractmethod
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
        pass
    
    def _default_system_prompt(self) -> str:
        """
        获取默认系统提示词
        
        Returns:
            系统提示词文本
        """
        return """你是一个资深的分布式系统性能优化专家。
请分析提供的性能指标和日志，给出具体可实施的优化建议。

要求：
1. 每次分析限制在 3 条以内，优先给出最高优先级的建议
2. 评估每个建议的风险等级 (low/medium/high)
3. 给出预期收益估计
4. 提供伪代码实现

输出格式为 JSON，包含以下字段：
- title: 建议标题
- description: 问题分析描述
- pseudocode: 伪代码实现
- priority: 优先级 (1-5)
- risk_level: 风险等级 (low/medium/high)
- expected_gain: 预期收益描述
- implementation_steps: 实施步骤列表
- self_confidence: 自评置信度 (0-1)"""
    
    def _parse_suggestions_from_response(self, response_text: str) -> List[Suggestion]:
        """
        从响应文本中解析建议列表
        
        尝试多种解析方式：
        1. JSON 数组格式
        2. Markdown 代码块中的 JSON
        3. 带有明显标记的列表格式
        
        Args:
            response_text: 模型原始响应
            
        Returns:
            解析出的建议列表
        """
        suggestions = []
        
        # 方法1: 尝试直接解析 JSON
        try:
            data = json.loads(response_text)
            if isinstance(data, list):
                for item in data:
                    suggestion = self._json_to_suggestion(item)
                    if suggestion:
                        suggestions.append(suggestion)
            elif isinstance(data, dict) and 'suggestions' in data:
                for item in data['suggestions']:
                    suggestion = self._json_to_suggestion(item)
                    if suggestion:
                        suggestions.append(suggestion)
            return suggestions
        except json.JSONDecodeError:
            pass
        
        # 方法2: 尝试从 Markdown 代码块中提取 JSON
        import re
        json_blocks = re.findall(r'```(?:json)?\s*([\s\S]*?)```', response_text)
        for block in json_blocks:
            try:
                data = json.loads(block.strip())
                if isinstance(data, list):
                    for item in data:
                        suggestion = self._json_to_suggestion(item)
                        if suggestion:
                            suggestions.append(suggestion)
                elif isinstance(data, dict):
                    suggestion = self._json_to_suggestion(data)
                    if suggestion:
                        suggestions.append(suggestion)
                return suggestions
            except json.JSONDecodeError:
                continue
        
        # 方法3: 降级处理，尝试解析简单格式
        suggestions = self._fallback_parse(response_text)
        return suggestions
    
    def _json_to_suggestion(self, data: dict) -> Optional[Suggestion]:
        """
        将 JSON 数据转换为 Suggestion 对象
        
        Args:
            data: JSON 数据字典
            
        Returns:
            Suggestion 对象，如果数据无效则返回 None
        """
        try:
            # 解析风险等级
            risk_str = data.get('risk_level', 'medium')
            if isinstance(risk_str, str):
                risk_level = RiskLevel(risk_str.lower())
            else:
                risk_level = RiskLevel.MEDIUM
            
            # 解析优先级
            priority = int(data.get('priority', 3))
            priority = max(1, min(5, priority))
            
            # 解析置信度
            confidence = float(data.get('self_confidence', 0.5))
            confidence = max(0.0, min(1.0, confidence))
            
            return Suggestion(
                title=data.get('title', '未命名建议'),
                description=data.get('description', ''),
                pseudocode=data.get('pseudocode', '# 无伪代码'),
                priority=priority,
                risk_level=risk_level,
                expected_gain=data.get('expected_gain', '未知'),
                implementation_steps=data.get('implementation_steps', []),
                source_model=self.name,
                self_confidence=confidence,
                base_weight=self.default_weight,
            )
        except (ValueError, TypeError) as e:
            return None
    
    def _fallback_parse(self, text: str) -> List[Suggestion]:
        """
        降级解析方法
        
        当 JSON 解析失败时，尝试从文本中提取关键信息。
        
        Args:
            text: 响应文本
            
        Returns:
            解析出的建议列表（可能为空或不完整）
        """
        suggestions = []
        
        # 简单的正则匹配尝试
        import re
        
        # 匹配标题
        titles = re.findall(r'(?:^|\n)#{1,3}\s*(.+)', text)
        for i, title in enumerate(titles[:3]):
            suggestion = Suggestion(
                title=title.strip(),
                description="（解析失败，请参考原文）",
                pseudocode="# 解析失败",
                priority=3,
                risk_level=RiskLevel.MEDIUM,
                expected_gain="未知",
                implementation_steps=["请参考完整响应"],
                source_model=self.name,
                self_confidence=0.3,
                base_weight=self.default_weight,
            )
            suggestions.append(suggestion)
        
        return suggestions


class RetryableModelPlugin(BaseModelPlugin):
    """
    支持重试的模型插件基类
    
    在基础模型插件上添加重试逻辑。
    """
    
    def __init__(self, max_retries: int = 3, timeout: int = 60):
        """
        初始化
        
        Args:
            max_retries: 最大重试次数
            timeout: 超时时间（秒）
        """
        super().__init__()
        self.max_retries = max_retries
        self.timeout = timeout
    
    def analyze_with_retry(self, context: AnalysisContext) -> AnalysisResult:
        """
        带重试的分析方法
        
        Args:
            context: 分析上下文
            
        Returns:
            分析结果
        """
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                return self.analyze(context)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    # 指数退避
                    import time
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                continue
        
        return AnalysisResult(
            model_name=self.name,
            error=str(last_error),
        )
