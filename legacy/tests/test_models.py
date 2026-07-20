"""模型插件测试"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import json

from smartbench.plugins.models.base import BaseModelPlugin, RetryableModelPlugin
from smartbench.plugins.models.anthropic import AnthropicPlugin
from smartbench.plugins.models.openai_compat import OpenAICompatiblePlugin
from smartbench.core.types import AnalysisContext, Suggestion, RiskLevel, Metrics, SystemType


class TestBaseModelPlugin:
    """基础模型插件测试"""

    def test_abstract_methods(self):
        """测试抽象方法需要实现"""
        with pytest.raises(TypeError):
            BaseModelPlugin()

    def test_default_system_prompt(self):
        """测试默认系统提示词"""
        # 创建一个具体子类来测试
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()
        assert "性能优化" in plugin._default_system_prompt()
        assert "JSON" in plugin._default_system_prompt()

    def test_default_weight_property(self):
        """测试默认权重属性"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()
        assert plugin.default_weight == 1.0

    def test_parse_suggestions_valid_json(self):
        """测试解析有效的 JSON 响应"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        response = json.dumps([
            {
                "title": "优化建议1",
                "description": "描述1",
                "pseudocode": "code",
                "priority": 4,
                "risk_level": "low",
                "expected_gain": "10%",
                "implementation_steps": ["step1"],
                "self_confidence": 0.8,
            },
            {
                "title": "优化建议2",
                "description": "描述2",
                "pseudocode": "code2",
                "priority": 3,
                "risk_level": "medium",
                "expected_gain": "5%",
                "implementation_steps": [],
                "self_confidence": 0.6,
            },
        ])

        suggestions = plugin._parse_suggestions_from_response(response)
        assert len(suggestions) == 2
        assert suggestions[0].title == "优化建议1"
        assert suggestions[0].priority == 4
        assert suggestions[0].risk_level == RiskLevel.LOW
        assert suggestions[0].source_model == "test"

    def test_parse_suggestions_markdown_json(self):
        """测试解析 Markdown 代码块中的 JSON"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        response = """
        以下是我的分析建议：

        ```json
        [
            {
                "title": "内存优化",
                "description": "减少内存分配",
                "pseudocode": "code",
                "priority": 5,
                "risk_level": "high",
                "expected_gain": "20%",
                "implementation_steps": ["step1"],
                "self_confidence": 0.9
            }
        ]
        ```
        """

        suggestions = plugin._parse_suggestions_from_response(response)
        assert len(suggestions) >= 1

    def test_parse_suggestions_invalid_json(self):
        """测试解析无效 JSON 时的降级处理"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        response = """
        # 优化建议

        ## 建议1: 内存优化
        这是详细的分析...

        ## 建议2: 网络优化
        另一个分析...
        """

        suggestions = plugin._parse_suggestions_from_response(response)
        # 降级解析会提取标题
        assert isinstance(suggestions, list)

    def test_json_to_suggestion_valid(self):
        """测试有效的 JSON 转换为建议"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        data = {
            "title": "测试建议",
            "description": "描述",
            "pseudocode": "code",
            "priority": 5,
            "risk_level": "low",
            "expected_gain": "10%",
            "implementation_steps": ["step1", "step2"],
            "self_confidence": 0.9,
        }

        suggestion = plugin._json_to_suggestion(data)
        assert suggestion is not None
        assert suggestion.title == "测试建议"
        assert suggestion.priority == 5
        assert suggestion.risk_level == RiskLevel.LOW
        assert len(suggestion.implementation_steps) == 2

    def test_json_to_suggestion_missing_optional_fields(self):
        """测试缺少可选字段时使用默认值"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        # 缺少可选字段时，使用默认值
        data = {"title": "只有关键字段"}
        suggestion = plugin._json_to_suggestion(data)
        assert suggestion is not None
        assert suggestion.title == "只有关键字段"
        assert suggestion.description == ""  # 默认值
        assert suggestion.priority == 3  # 默认值

    def test_json_to_suggestion_priority_clamping(self):
        """测试优先级边界处理"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        # 超高优先级
        data = {
            "title": "测试",
            "description": "描述",
            "pseudocode": "code",
            "priority": 100,
            "risk_level": "low",
            "expected_gain": "",
            "self_confidence": 0.5,
        }
        suggestion = plugin._json_to_suggestion(data)
        assert suggestion.priority == 5  # 应该被限制到 5

    def test_json_to_suggestion_confidence_clamping(self):
        """测试置信度边界处理"""
        class TestPlugin(BaseModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                pass

            def generate_document(self, suggestions, metrics, target_qps):
                pass

        plugin = TestPlugin()

        data = {
            "title": "测试",
            "description": "描述",
            "pseudocode": "code",
            "priority": 3,
            "risk_level": "low",
            "expected_gain": "",
            "self_confidence": 1.5,  # 超范围
        }
        suggestion = plugin._json_to_suggestion(data)
        assert suggestion.self_confidence == 1.0


class TestRetryableModelPlugin:
    """可重试模型插件测试"""

    def test_retry_logic(self):
        """测试重试逻辑"""
        call_count = [0]

        class TestPlugin(RetryableModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise Exception("API error")
                return Mock()

            def generate_document(self, suggestions, metrics, target_qps):
                return ""

        plugin = TestPlugin(max_retries=3)
        result = plugin.analyze_with_retry(Mock())
        assert call_count[0] == 3
        assert result is not None

    def test_retry_exhausted(self):
        """测试重试次数耗尽"""

        class TestPlugin(RetryableModelPlugin):
            @property
            def name(self):
                return "test"

            def analyze(self, context):
                raise Exception("API error")

            def generate_document(self, suggestions, metrics, target_qps):
                return ""

        plugin = TestPlugin(max_retries=3)
        result = plugin.analyze_with_retry(Mock())

        assert result.model_name == "test"
        assert "API error" in result.error


class TestAnthropicPlugin:
    """Anthropic Claude 插件测试"""

    def test_plugin_init(self):
        """测试插件初始化"""
        plugin = AnthropicPlugin(
            api_key="test-key",
            model="claude-3-5-sonnet-20241022",
        )
        assert plugin.api_key == "test-key"
        assert plugin.model == "claude-3-5-sonnet-20241022"
        assert plugin.timeout == 60
        assert plugin.max_retries == 3

    def test_plugin_name(self):
        """测试插件名称"""
        plugin = AnthropicPlugin(
            api_key="test-key",
            model="claude-3-5-sonnet-20241022",
        )
        assert "claude" in plugin.name

    def test_default_weight_opus(self):
        """测试 Opus 模型权重"""
        plugin = AnthropicPlugin(api_key="key", model="claude-3-opus")
        assert plugin.default_weight == 1.5

    def test_default_weight_sonnet(self):
        """测试 Sonnet 模型权重"""
        plugin = AnthropicPlugin(api_key="key", model="claude-3-5-sonnet")
        assert plugin.default_weight == 1.3

    def test_default_weight_haiku(self):
        """测试 Haiku 模型权重"""
        plugin = AnthropicPlugin(api_key="key", model="claude-3-haiku")
        assert plugin.default_weight == 0.9

    def test_provider(self):
        """测试提供商"""
        plugin = AnthropicPlugin(api_key="key")
        assert plugin.provider == "anthropic"

    def test_build_analysis_prompt(self, sample_metrics):
        """测试分析提示词构建"""
        plugin = AnthropicPlugin(api_key="key")

        context = AnalysisContext(
            system_name="raft_kv",
            system_type=SystemType.RAFT_KV,
            metrics=sample_metrics,
            logs="log line 1\nlog line 2",
            source_code="void func() {}",
            target_qps=300.0,
        )

        prompt = plugin._build_analysis_prompt(context)

        assert "性能分析请求" in prompt
        assert "raft_kv" in prompt
        assert "300.0" in prompt
        assert "250.0" in prompt  # QPS
        assert "log line 1" in prompt
        assert "func()" in prompt


class TestOpenAICompatiblePlugin:
    """OpenAI 兼容插件测试"""

    def test_plugin_init(self):
        """测试插件初始化"""
        plugin = OpenAICompatiblePlugin(
            api_key="test-key",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        assert plugin.api_key == "test-key"
        assert plugin.model == "deepseek-chat"
        assert plugin.base_url == "https://api.deepseek.com"

    def test_plugin_defaults(self):
        """测试默认参数"""
        plugin = OpenAICompatiblePlugin(api_key="test-key")
        assert plugin.model == "gpt-3.5-turbo"
        assert plugin.base_url == "https://api.openai.com/v1"

    def test_provider_deepseek(self):
        """测试 DeepSeek 提供商检测"""
        plugin = OpenAICompatiblePlugin(
            api_key="key",
            base_url="https://api.deepseek.com",
        )
        assert plugin.provider == "deepseek"

    def test_provider_dashscope(self):
        """测试 DashScope 提供商检测"""
        plugin = OpenAICompatiblePlugin(
            api_key="key",
            model="qwen-turbo",
            base_url="https://dashscope.aliyuncs.com",
        )
        assert plugin.provider == "dashscope"

    def test_provider_openai(self):
        """测试 OpenAI 提供商检测"""
        plugin = OpenAICompatiblePlugin(
            api_key="key",
            base_url="https://api.openai.com/v1",
        )
        assert plugin.provider == "openai"

    def test_default_weight_gpt4(self):
        """测试 GPT-4 权重"""
        plugin = OpenAICompatiblePlugin(api_key="key", model="gpt-4")
        assert plugin.default_weight == 1.2

    def test_default_weight_gpt35(self):
        """测试 GPT-3.5 权重"""
        plugin = OpenAICompatiblePlugin(api_key="key", model="gpt-3.5-turbo")
        assert plugin.default_weight == 0.9

    def test_default_weight_deepseek_chat(self):
        """测试 DeepSeek Chat 权重"""
        plugin = OpenAICompatiblePlugin(api_key="key", model="deepseek-chat")
        assert plugin.default_weight == 1.2

    def test_default_weight_qwen(self):
        """测试 Qwen 权重"""
        plugin = OpenAICompatiblePlugin(api_key="key", model="qwen-turbo")
        assert plugin.default_weight == 0.9

    def test_build_messages(self, sample_metrics):
        """测试消息构建"""
        plugin = OpenAICompatiblePlugin(api_key="key")

        context = AnalysisContext(
            system_name="raft_kv",
            system_type=SystemType.RAFT_KV,
            metrics=sample_metrics,
            target_qps=300.0,
        )

        messages = plugin._build_messages(context)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "raft_kv" in messages[1]["content"]

    def test_loose_parse(self):
        """测试宽松解析"""
        plugin = OpenAICompatiblePlugin(api_key="key")

        response = """
        # 优化建议

        ## 优化1: 内存优化
        详细分析...

        ## 优化2: 网络优化
        详细分析...
        """

        suggestions = plugin._loose_parse(response)
        assert isinstance(suggestions, list)
