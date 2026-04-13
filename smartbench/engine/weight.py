"""
智能权重引擎

动态计算每个建议的最终权重，过滤低质量建议。

权重计算公式：
    final_weight = base_weight × accuracy_weight × consensus_weight × self_confidence

权重因子：
1. base_weight: 模型默认权重（基于模型能力）
2. accuracy_weight: 历史准确率权重（贝叶斯更新）
3. consensus_weight: 一致性权重（多模型赞同加分）
4. self_confidence: 自评置信度
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set
from collections import defaultdict

from smartbench.core.types import Suggestion, RiskLevel


class WeightEngine:
    """
    智能权重引擎
    
    核心功能：
    1. 基于模型能力设置默认权重
    2. 基于历史采纳率动态调整权重
    3. 基于多模型一致性调整权重
    4. 综合计算最终权重
    
    Example:
        engine = WeightEngine(history_db_path="./data/history.json")
        
        # 计算权重
        weight = engine.calculate_weight(suggestion, "deepseek", all_suggestions)
        
        # 更新历史
        engine.update_history("deepseek", adopted=True)
    """
    
    def __init__(
        self,
        history_db_path: str = "./data/history.json",
        confidence_threshold: float = 0.3,
    ):
        """
        初始化权重引擎
        
        Args:
            history_db_path: 历史数据存储路径
            confidence_threshold: 置信度阈值
        """
        self.history_db_path = Path(history_db_path)
        self.history_db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.confidence_threshold = confidence_threshold
        self.history = self._load_history()
    
    def calculate_weight(
        self, 
        suggestion: Suggestion, 
        model_name: str,
        all_suggestions: List[Suggestion]
    ) -> float:
        """
        计算建议的最终权重
        
        Args:
            suggestion: 优化建议
            model_name: 来源模型名称
            all_suggestions: 所有建议列表（用于计算一致性）
            
        Returns:
            最终权重 (范围 0.1 - 2.0)
        """
        # 1. 基础权重
        base = suggestion.base_weight
        
        # 2. 历史准确率权重
        accuracy = self._get_accuracy_weight(model_name)
        
        # 3. 一致性权重
        consensus = self._get_consensus_weight(suggestion, all_suggestions)
        
        # 4. 自评置信度
        confidence = suggestion.self_confidence
        
        # 5. 风险调整因子
        risk_factor = self._get_risk_factor(suggestion.risk_level)
        
        # 最终权重 = 乘积
        final_weight = base * accuracy * consensus * confidence * risk_factor
        
        # 限制范围
        return max(0.1, min(2.0, final_weight))
    
    def _get_accuracy_weight(self, model_name: str) -> float:
        """
        获取历史准确率权重

        使用拉普拉斯平滑的贝叶斯估计：
        accuracy = (adopted + alpha) / (total + alpha + beta)

        Args:
            model_name: 模型名称

        Returns:
            准确率权重
        """
        history = self.history.get(model_name, {"adopted": 0, "total": 0})
        adopted = history.get("adopted", 0)
        total = history.get("total", 0)

        # Beta 先验参数 (拉普拉斯平滑)
        alpha = 1.0  # 成功次数的伪计数
        beta = 1.0   # 失败次数的伪计数

        # 后验均值
        accuracy = (adopted + alpha) / (total + alpha + beta)

        # 映射到 0.5 - 1.5 范围
        weight = 0.5 + accuracy

        return weight
    
    def _get_consensus_weight(
        self, 
        suggestion: Suggestion, 
        all_suggestions: List[Suggestion]
    ) -> float:
        """
        计算一致性权重
        
        如果多个模型都提出相似的建议，权重应该更高。
        
        Args:
            suggestion: 当前建议
            all_suggestions: 所有建议列表
            
        Returns:
            一致性权重
        """
        similar = self._count_similar(suggestion, all_suggestions)
        
        # 权重映射
        if similar == 0:
            return 0.8      # 独特建议，稍低权重
        elif similar == 1:
            return 0.9      # 有一个相似
        elif similar == 2:
            return 1.0      # 有两个相似
        elif similar <= 4:
            return 1.2      # 多数一致
        else:
            return 1.5      # 高度一致
    
    def _get_risk_factor(self, risk_level: RiskLevel) -> float:
        """
        获取风险调整因子
        
        低风险建议权重稍高，高风险建议权重降低。
        
        Args:
            risk_level: 风险等级
            
        Returns:
            风险因子
        """
        if risk_level == RiskLevel.LOW:
            return 1.1     # 低风险，略微加分
        elif risk_level == RiskLevel.MEDIUM:
            return 1.0     # 中风险，无调整
        else:
            return 0.8     # 高风险，降低权重
    
    def _count_similar(self, suggestion: Suggestion, all_suggestions: List[Suggestion]) -> int:
        """
        计算相似的建议数量
        
        Args:
            suggestion: 当前建议
            all_suggestions: 所有建议
            
        Returns:
            相似建议数量
        """
        count = 0
        for other in all_suggestions:
            if other is suggestion:
                continue
            if other.source_model == suggestion.source_model:
                continue  # 同一模型的重复建议不算
            if self._is_similar(suggestion, other):
                count += 1
        return count
    
    def _is_similar(self, a: Suggestion, b: Suggestion) -> bool:
        """
        判断两条建议是否相似
        
        使用关键词重叠度和标题相似度判断。
        
        Args:
            a: 建议 A
            b: 建议 B
            
        Returns:
            是否相似
        """
        # 标题相似度
        title_sim = self._title_similarity(a.title, b.title)
        if title_sim > 0.8:
            return True
        
        # 描述关键词重叠
        desc_sim = self._keyword_similarity(a.description, b.description)
        if desc_sim > 0.6:
            return True
        
        return False
    
    def _title_similarity(self, title1: str, title2: str) -> float:
        """
        计算标题相似度
        
        Args:
            title1: 标题1
            title2: 标题2
            
        Returns:
            相似度 (0 - 1)
        """
        # 标准化
        words1 = set(title1.lower().split())
        words2 = set(title2.lower().split())
        
        # 移除常见词（中英文停用词）
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "to", "of", "in", "for", "on", "with", "by", "at", "as",
            "this", "that", "these", "those", "it", "its",
            "优化", "性能", "方案", "提高", "改进", "的", "和",
            "建议", "可以", "进行", "通过", "使用", "需要",
            "and", "or", "but", "if", "then", "so", "not",
        }
        words1 -= stop_words
        words2 -= stop_words
        
        if not words1 or not words2:
            return 0.0
        
        # Jaccard 相似度
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def _keyword_similarity(self, text1: str, text2: str) -> float:
        """
        计算文本关键词相似度
        
        Args:
            text1: 文本1
            text2: 文本2
            
        Returns:
            相似度 (0 - 1)
        """
        # 简单的关键词提取（去除停用词后的词集合）
        import re
        words1 = set(re.findall(r'[\w]+', text1.lower()))
        words2 = set(re.findall(r'[\w]+', text2.lower()))
        
        # 停用词
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "的", "是", "在", "和", "了", "有", "我", "你", "他",
            "this", "that", "these", "those", "it", "its",
            "建议", "可以", "进行", "通过", "使用", "需要",
        }
        words1 -= stop_words
        words2 -= stop_words
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def update_history(self, model_name: str, adopted: bool, suggestion: Optional[Suggestion] = None):
        """
        更新历史记录
        
        记录模型的采纳情况，用于后续权重调整。
        
        Args:
            model_name: 模型名称
            adopted: 是否被采纳
            suggestion: 相关建议（可选）
        """
        if model_name not in self.history:
            self.history[model_name] = {
                "adopted": 0,
                "total": 0,
                "details": []
            }
        
        self.history[model_name]["total"] += 1
        if adopted:
            self.history[model_name]["adopted"] += 1
        
        # 记录详情（可选）
        if suggestion:
            self.history[model_name]["details"].append({
                "suggestion_title": suggestion.title,
                "adopted": adopted,
                "priority": suggestion.priority,
                "risk_level": suggestion.risk_level.value,
            })
            
            # 限制详情数量
            if len(self.history[model_name]["details"]) > 100:
                self.history[model_name]["details"] = self.history[model_name]["details"][-100:]
        
        self._save_history()
    
    def get_model_stats(self, model_name: str) -> Dict[str, any]:
        """
        获取模型统计信息
        
        Args:
            model_name: 模型名称
            
        Returns:
            统计信息字典
        """
        history = self.history.get(model_name, {"adopted": 0, "total": 0})
        adopted = history.get("adopted", 0)
        total = history.get("total", 0)
        
        return {
            "model_name": model_name,
            "total_suggestions": total,
            "adopted_suggestions": adopted,
            "accuracy": adopted / total if total > 0 else None,
            "weight": self._get_accuracy_weight(model_name),
        }
    
    def get_all_stats(self) -> List[Dict[str, any]]:
        """
        获取所有模型的统计信息
        
        Returns:
            统计信息列表
        """
        return [
            self.get_model_stats(name)
            for name in self.history.keys()
        ]
    
    def _load_history(self) -> Dict:
        """
        加载历史记录
        
        Returns:
            历史记录字典
        """
        if self.history_db_path.exists():
            try:
                with open(self.history_db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save_history(self):
        """保存历史记录"""
        try:
            with open(self.history_db_path, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except IOError:
            pass  # 静默处理保存失败
    
    def reset_history(self):
        """重置历史记录"""
        self.history = {}
        self._save_history()
