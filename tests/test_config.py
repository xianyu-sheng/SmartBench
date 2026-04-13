"""配置管理测试"""

import pytest
import os
from pathlib import Path

from smartbench.core.config import (
    ModelConfig,
    SystemConfig,
    WeightEngineConfig,
    PromptConfig,
    Config,
    ConfigLoader,
)


class TestModelConfig:
    """模型配置测试"""

    def test_model_config_creation(self):
        config = ModelConfig(
            name="deepseek",
            provider="openai_compatible",
            api_key="test-key-123",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
            default_weight=0.8,
            enabled=True,
            max_retries=3,
            timeout=60,
        )
        assert config.name == "deepseek"
        assert config.provider == "openai_compatible"
        assert config.model == "deepseek-chat"
        assert config.default_weight == 0.8
        assert config.enabled is True

    def test_model_config_defaults(self):
        config = ModelConfig(
            name="test",
            provider="openai_compatible",
            api_key="key",
        )
        assert config.model == "gpt-3.5-turbo"
        assert config.default_weight == 1.0
        assert config.enabled is True
        assert config.max_retries == 3
        assert config.timeout == 60

    def test_model_config_name_required(self):
        with pytest.raises(ValueError, match="名称不能为空"):
            ModelConfig(name="", provider="openai", api_key="key")

    def test_model_config_api_key_required(self):
        with pytest.raises(ValueError, match="API Key 不能为空"):
            ModelConfig(name="test", provider="openai", api_key="")


class TestSystemConfig:
    """系统配置测试"""

    def test_system_config_creation(self, tmp_path):
        # 创建临时项目目录
        project_dir = tmp_path / "test_project"
        project_dir.mkdir()

        config = SystemConfig(
            name="raft_kv",
            system_type="raft_kv",
            project_path=str(project_dir),
            benchmark_command="./bench.sh",
            log_path="./logs",
            config_paths=["config1.conf", "config2.conf"],
            custom_settings={"threads": 8},
        )
        assert config.name == "raft_kv"
        assert config.system_type == "raft_kv"
        assert config.benchmark_command == "./bench.sh"
        assert config.custom_settings["threads"] == 8

    def test_system_config_name_required(self, tmp_path):
        project_dir = tmp_path / "test"
        project_dir.mkdir()

        with pytest.raises(ValueError, match="名称不能为空"):
            SystemConfig(
                name="",
                system_type="raft_kv",
                project_path=str(project_dir),
                benchmark_command="./bench.sh",
                log_path="./logs",
            )

    def test_system_config_path_required(self):
        with pytest.raises(ValueError, match="项目路径不存在"):
            SystemConfig(
                name="raft_kv",
                system_type="raft_kv",
                project_path="/nonexistent/path/12345",
                benchmark_command="./bench.sh",
                log_path="./logs",
            )


class TestWeightEngineConfig:
    """权重引擎配置测试"""

    def test_weight_engine_config_defaults(self):
        config = WeightEngineConfig()
        assert config.confidence_threshold == 0.3
        assert config.default_model_weight == 0.7
        assert config.max_suggestions == 5
        assert config.history_db_path == "./data/history.json"

    def test_weight_engine_config_custom(self):
        config = WeightEngineConfig(
            confidence_threshold=0.5,
            default_model_weight=0.8,
            max_suggestions=10,
            history_db_path="/custom/path.json",
        )
        assert config.confidence_threshold == 0.5
        assert config.max_suggestions == 10


class TestPromptConfig:
    """提示词配置测试"""

    def test_prompt_config_defaults(self):
        config = PromptConfig.default()
        assert "分布式系统性能优化" in config.system_prompt
        assert "JSON" in config.analysis_template
        assert "详细技术文档" in config.refine_template

    def test_prompt_config_creation(self):
        config = PromptConfig(
            system_prompt="custom system prompt",
            analysis_template="custom analysis",
            refine_template="custom refine",
        )
        assert config.system_prompt == "custom system prompt"
        assert config.analysis_template == "custom analysis"


class TestConfig:
    """全局配置测试"""

    def test_config_creation(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        config = Config(
            models=[
                ModelConfig(name="test", provider="openai", api_key="key")
            ],
            systems=[
                SystemConfig(
                    name="raft_kv",
                    system_type="raft_kv",
                    project_path=str(project_dir),
                    benchmark_command="./bench.sh",
                    log_path="./logs",
                )
            ],
            weight_engine=WeightEngineConfig(),
            prompts=PromptConfig.default(),
            output_dir="./output",
            data_dir="./data",
        )
        assert len(config.models) == 1
        assert len(config.systems) == 1
        assert config.output_dir == "./output"

    def test_config_get_enabled_models(self):
        config = Config(
            models=[
                ModelConfig(name="a", provider="openai", api_key="key", enabled=True),
                ModelConfig(name="b", provider="openai", api_key="key", enabled=False),
                ModelConfig(name="c", provider="openai", api_key="key", enabled=True),
            ],
        )
        enabled = config.get_enabled_models()
        assert len(enabled) == 2
        assert [m.name for m in enabled] == ["a", "c"]

    def test_config_get_system(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        config = Config(
            systems=[
                SystemConfig(
                    name="raft_kv",
                    system_type="raft_kv",
                    project_path=str(project_dir),
                    benchmark_command="./bench.sh",
                    log_path="./logs",
                ),
                SystemConfig(
                    name="http_api",
                    system_type="generic",
                    project_path=str(project_dir),
                    benchmark_command="./bench.sh",
                    log_path="./logs",
                ),
            ],
        )
        assert config.get_system("raft_kv").name == "raft_kv"
        assert config.get_system("http_api").name == "http_api"
        assert config.get_system("nonexistent") is None

    def test_config_get_model(self):
        config = Config(
            models=[
                ModelConfig(name="deepseek", provider="openai", api_key="key"),
                ModelConfig(name="claude", provider="anthropic", api_key="key"),
            ],
        )
        assert config.get_model("deepseek").name == "deepseek"
        assert config.get_model("claude").provider == "anthropic"
        assert config.get_model("nonexistent") is None


class TestConfigLoader:
    """配置加载器测试"""

    def test_load_yaml(self, sample_yaml_config, tmp_path):
        config = ConfigLoader.load(str(sample_yaml_config))
        assert len(config.models) == 1
        assert config.models[0].name == "deepseek"
        assert config.models[0].model == "deepseek-chat"
        assert config.weight_engine.confidence_threshold == 0.35

    def test_load_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            ConfigLoader.load("/nonexistent/path/config.yaml")

    def test_load_from_dict(self, tmp_path):
        data = {
            "models": [
                {
                    "name": "test",
                    "provider": "openai_compatible",
                    "api_key": "test-key",
                    "model": "test-model",
                }
            ],
            "systems": [],
            "weight_engine": {
                "confidence_threshold": 0.4,
            },
        }
        config = ConfigLoader.load_from_dict(data)
        assert config.models[0].name == "test"
        assert config.weight_engine.confidence_threshold == 0.4

    def test_env_var_substitution(self, tmp_path):
        os.environ["TEST_API_KEY"] = "secret-key-123"

        config_path = tmp_path / "env_config.yaml"
        config_path.write_text(
            """
models:
  - name: "test"
    provider: "openai"
    api_key: "${TEST_API_KEY}"
    model: "gpt-4"

systems: []
"""
        )

        config = ConfigLoader.load(str(config_path))
        assert config.models[0].api_key == "secret-key-123"

        del os.environ["TEST_API_KEY"]

    def test_env_var_missing_with_valid_key(self, tmp_path):
        """测试缺失环境变量但提供有效 key 时正常加载"""
        config_path = tmp_path / "missing_env.yaml"
        mock_project = tmp_path / "test_project"
        mock_project.mkdir()

        config_path.write_text(
            f"""
models:
  - name: "test"
    provider: "openai"
    api_key: "valid-dummy-key-123"
    model: "gpt-4"
    enabled: false

systems:
  - name: "raft_kv"
    system_type: "raft_kv"
    project_path: "{mock_project}"
    benchmark_command: "./bench.sh"
    log_path: "./build"
"""
        )

        config = ConfigLoader.load(str(config_path))
        assert config.models[0].api_key == "valid-dummy-key-123"
        assert config.models[0].enabled is False

    def test_save_config(self, sample_yaml_config, tmp_path):
        config = ConfigLoader.load(str(sample_yaml_config))
        output_path = tmp_path / "saved_config.yaml"

        ConfigLoader.save(config, str(output_path))
        assert output_path.exists()

        # 重新加载验证
        reloaded = ConfigLoader.load(str(output_path))
        assert len(reloaded.models) == len(config.models)
        assert reloaded.weight_engine.confidence_threshold == config.weight_engine.confidence_threshold

    def test_resolve_env_vars_nested(self):
        data = {
            "key1": "${TEST_VAR_1}",
            "nested": {
                "key2": "${TEST_VAR_2}",
                "list": ["${TEST_VAR_3}", "static"],
            },
        }

        os.environ["TEST_VAR_1"] = "value1"
        os.environ["TEST_VAR_2"] = "value2"
        os.environ["TEST_VAR_3"] = "value3"

        result = ConfigLoader._resolve_env_vars(data)

        assert result["key1"] == "value1"
        assert result["nested"]["key2"] == "value2"
        assert result["nested"]["list"][0] == "value3"
        assert result["nested"]["list"][1] == "static"

        del os.environ["TEST_VAR_1"]
        del os.environ["TEST_VAR_2"]
        del os.environ["TEST_VAR_3"]
