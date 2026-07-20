"""
建议聚合器

负责聚合多个模型的分析结果，包括：去重、排序、过滤。
"""

from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from smartbench.core.types import Suggestion, AnalysisResult, RiskLevel
from smartbench.engine.weight import WeightEngine


class SuggestionAggregator:
    """
    建议聚合器
    
    核心功能：
    1. 收集所有模型的建议
    2. 计算每个建议的权重
    3. 去重（语义相似）
    4. 按优先级排序
    5. 过滤低质量建议
    
    流程:
        results → 收集 → 权重计算 → 去重 → 排序 → 过滤 → 输出
    
    Example:
        engine = WeightEngine()
        aggregator = SuggestionAggregator(
            weight_engine=engine,
            confidence_threshold=0.3
        )
        
        suggestions = aggregator.aggregate(results, max_suggestions=5)
    """
    
    def __init__(
        self,
        weight_engine: WeightEngine,
        confidence_threshold: float = 0.3,
        similarity_threshold: float = 0.75,
    ):
        """
        初始化聚合器
        
        Args:
            weight_engine: 权重引擎
            confidence_threshold: 置信度阈值
            similarity_threshold: 相似度阈值
        """
        self.weight_engine = weight_engine
        self.confidence_threshold = confidence_threshold
        self.similarity_threshold = similarity_threshold
    
    def aggregate(
        self, 
        results: List[AnalysisResult],
        max_suggestions: int = 5
    ) -> List[Suggestion]:
        """
        聚合所有模型的建议
        
        Args:
            results: 各模型的原始分析结果
            max_suggestions: 最大输出建议数
            
        Returns:
            排序后的优化建议列表
        """
        # Step 1: 收集所有建议
        all_suggestions = self._collect_suggestions(results)
        
        if not all_suggestions:
            return []
        
        # Step 2: 计算权重
        all_suggestions = self._calculate_weights(all_suggestions)
        
        # Step 3: 去重
        deduplicated = self._deduplicate(all_suggestions)
        
        # Step 4: 按优先级分组排序
        sorted_suggestions = self._sort_by_priority(deduplicated)
        
        # Step 5: 过滤低质量建议
        filtered = self._filter_low_quality(sorted_suggestions)
        
        # Step 6: 限制数量
        return filtered[:max_suggestions]
    
    def _collect_suggestions(self, results: List[AnalysisResult]) -> List[Suggestion]:
        """
        收集所有建议
        
        过滤掉失败的模型结果。
        
        Args:
            results: 分析结果列表
            
        Returns:
            建议列表
        """
        suggestions = []
        
        for result in results:
            if not result.is_success:
                continue
            suggestions.extend(result.suggestions)
        
        return suggestions
    
    def _calculate_weights(self, suggestions: List[Suggestion]) -> List[Suggestion]:
        """
        计算每个建议的权重
        
        Args:
            suggestions: 建议列表
            
        Returns:
            更新权重后的建议列表
        """
        for suggestion in suggestions:
            suggestion.final_weight = self.weight_engine.calculate_weight(
                suggestion,
                suggestion.source_model,
                suggestions
            )
        
        return suggestions
    
    def _deduplicate(self, suggestions: List[Suggestion]) -> List[Suggestion]:
        """
        基于语义相似度的去重
        
        保留权重较高的版本，合并相似建议。
        
        Args:
            suggestions: 建议列表
            
        Returns:
            去重后的建议列表
        """
        unique = []
        
        for candidate in suggestions:
            is_duplicate = False
            
            for existing in unique:
                similarity = self._semantic_similarity(candidate, existing)
                
                if similarity > self.similarity_threshold:
                    is_duplicate = True
                    
                    # 如果候选权重更高，替换现有建议
                    if candidate.final_weight > existing.final_weight:
                        unique.remove(existing)
                        unique.append(candidate)
                    
                    # 合并来源信息
                    if candidate.source_model not in existing.source_model:
                        existing.source_model = f"{existing.source_model} + {candidate.source_model}"
                    break
            
            if not is_duplicate:
                unique.append(candidate)
        
        return unique
    
    def _semantic_similarity(self, a: Suggestion, b: Suggestion) -> float:
        """
        计算语义相似度
        
        综合考虑标题、描述、关键词的相似度。
        
        Args:
            a: 建议 A
            b: 建议 B
            
        Returns:
            相似度 (0 - 1)
        """
        # 1. 标题相似度（权重较高）
        title_sim = self._title_similarity(a.title, b.title)
        
        # 2. 描述关键词相似度
        desc_sim = self._keyword_similarity(a.description, b.description)
        
        # 3. 期望收益相似度
        gain_sim = self._keyword_similarity(a.expected_gain, b.expected_gain)
        
        # 综合相似度（加权平均）
        # 标题最重要，描述次之，收益说明最轻
        total_similarity = title_sim * 0.5 + desc_sim * 0.3 + gain_sim * 0.2
        
        return total_similarity
    
    def _title_similarity(self, title1: str, title2: str) -> float:
        """
        计算标题相似度
        
        Args:
            title1: 标题1
            title2: 标题2
            
        Returns:
            相似度
        """
        import re
        
        # 分词
        words1 = set(re.findall(r'[\w]+', title1.lower()))
        words2 = set(re.findall(r'[\w]+', title2.lower()))
        
        # 停用词
        stop_words = {
            "优化", "性能", "方案", "提高", "改进", "建议", "method", "approach",
            "优化", "提升", "改进", "建议", "如何", "怎么",
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
            相似度
        """
        import re
        
        # 提取词
        words1 = set(re.findall(r'[\w]+', text1.lower()))
        words2 = set(re.findall(r'[\w]+', text2.lower()))
        
        # 停用词
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "的", "是", "在", "和", "了", "有", "我", "你", "他",
            "this", "that", "these", "those", "it", "its",
            "可以", "进行", "通过", "使用", "需要", "能够",
            "我们", "这个", "一个", "一些",
        }
        words1 -= stop_words
        words2 -= stop_words
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def _sort_by_priority(self, suggestions: List[Suggestion]) -> List[Suggestion]:
        """
        按优先级排序
        
        排序规则:
        1. 风险等级（低 > 中 > 高）
        2. 权重（高 > 低）
        3. 优先级（高 > 低）
        
        Args:
            suggestions: 建议列表
            
        Returns:
            排序后的列表
        """
        # 风险等级优先级映射
        risk_order = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
        }
        
        return sorted(
            suggestions,
            key=lambda s: (
                risk_order.get(s.risk_level, 1),  # 风险低优先
                -s.final_weight,                    # 权重大优先
                -s.priority,                        # 优先级高优先
            )
        )
    
    def _filter_low_quality(self, suggestions: List[Suggestion]) -> List[Suggestion]:
        """
        过滤低质量建议
        
        过滤条件:
        1. 权重低于阈值
        2. 无效的建议
        
        Args:
            suggestions: 建议列表
            
        Returns:
            过滤后的列表
        """
        filtered = []
        
        for suggestion in suggestions:
            # 检查权重
            if suggestion.final_weight < self.confidence_threshold:
                continue
            
            # 检查有效性
            if not suggestion.title or not suggestion.description:
                continue
            
            # 检查伪代码
            if not suggestion.pseudocode or suggestion.pseudocode == "# 无代码":
                continue
            
            filtered.append(suggestion)
        
        return filtered
    
    def get_summary(self, suggestions: List[Suggestion]) -> Dict:
        """
        获取建议摘要

        Args:
            suggestions: 建议列表

        Returns:
            摘要字典
        """
        if not suggestions:
            return {
                "total": 0,
                "by_risk": {},
                "by_model": {},
                "top_suggestion": None,
            }

        # 按风险等级统计
        by_risk = defaultdict(int)
        for s in suggestions:
            by_risk[s.risk_level.value] += 1

        # 按来源模型统计
        by_model = defaultdict(int)
        for s in suggestions:
            for model in s.source_model.split(" + "):
                by_model[model.strip()] += 1

        return {
            "total": len(suggestions),
            "by_risk": dict(by_risk),
            "by_model": dict(by_model),
            "top_suggestion": suggestions[0].title if suggestions else None,
            "top_weight": suggestions[0].final_weight if suggestions else 0,
        }

    def group_by_risk(self, suggestions: List[Suggestion]) -> Dict[RiskLevel, List[Suggestion]]:
        """
        按风险等级分组建议

        Args:
            suggestions: 建议列表

        Returns:
            按风险等级分组的字典
        """
        groups = {
            RiskLevel.LOW: [],
            RiskLevel.MEDIUM: [],
            RiskLevel.HIGH: [],
        }
        for s in suggestions:
            groups[s.risk_level].append(s)
        return groups

    def group_by_priority(self, suggestions: List[Suggestion]) -> Dict[int, List[Suggestion]]:
        """
        按优先级分组建议

        Args:
            suggestions: 建议列表

        Returns:
            按优先级分组的字典
        """
        groups = defaultdict(list)
        for s in suggestions:
            groups[s.priority].append(s)
        return dict(sorted(groups.items(), reverse=True))

    def get_top_suggestions(
        self,
        results: List[AnalysisResult],
        by_risk: Optional[int] = None,
        by_priority: Optional[int] = None,
        limit: int = 5,
    ) -> List[Suggestion]:
        """
        获取高质量建议

        根据风险等级和优先级筛选建议。

        Args:
            results: 分析结果列表
            by_risk: 筛选特定风险等级（可选）
            by_priority: 筛选特定优先级（可选）
            limit: 返回数量限制

        Returns:
            筛选后的建议列表
        """
        all_suggestions = self._collect_suggestions(results)
        all_suggestions = self._calculate_weights(all_suggestions)
        sorted_suggestions = self._sort_by_priority(all_suggestions)

        if by_risk is not None:
            # by_risk 是索引（0=low, 1=medium, 2=high）
            all_levels = list(RiskLevel)
            if 0 <= by_risk < len(all_levels):
                risk_level = all_levels[by_risk]
                sorted_suggestions = [s for s in sorted_suggestions if s.risk_level == risk_level]

        if by_priority is not None:
            sorted_suggestions = [s for s in sorted_suggestions if s.priority >= by_priority]

        return sorted_suggestions[:limit]
