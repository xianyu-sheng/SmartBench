"""
配置管理模块

支持 YAML 配置文件 + 环境变量替换。
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from pathlib import Path
import yaml


@dataclass
class ModelConfig:
    """
    模型配置数据类
    
    Attributes:
        name: 模型名称标识
        provider: 提供商类型 (openai_compatible, anthropic, dashscope)
        api_key: API 密钥
        base_url: API 基础 URL（可选，用于兼容接口）
        model: 模型名称
        default_weight: 默认权重
        enabled: 是否启用
        max_retries: 最大重试次数
        timeout: 超时时间（秒）
    """
    name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model: str = "gpt-3.5-turbo"
    default_weight: float = 1.0
    enabled: bool = True
    max_retries: int = 3
    timeout: int = 60
    
    def __post_init__(self):
        """验证配置有效性"""
        if not self.name:
            raise ValueError("模型名称不能为空")
        if not self.api_key:
            raise ValueError(f"模型 {self.name} 的 API Key 不能为空")


@dataclass
class SystemConfig:
    """
    系统配置数据类
    
    Attributes:
        name: 系统名称标识
        system_type: 系统类型
        project_path: 项目路径
        benchmark_command: 压测命令
        log_path: 日志路径
        config_paths: 配置文件路径列表
        custom_settings: 自定义设置
    """
    name: str
    system_type: str
    project_path: str
    benchmark_command: str
    log_path: str
    config_paths: List[str] = field(default_factory=list)
    custom_settings: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """验证配置有效性"""
        if not self.name:
            raise ValueError("系统名称不能为空")
        if not Path(self.project_path).exists():
            raise ValueError(f"项目路径不存在: {self.project_path}")


@dataclass
class WeightEngineConfig:
    """
    权重引擎配置数据类
    
    Attributes:
        confidence_threshold: 置信度阈值，低于此值的建议将被过滤
        default_model_weight: 默认模型权重
        max_suggestions: 最大输出建议数量
        history_db_path: 历史数据存储路径
    """
    confidence_threshold: float = 0.3
    default_model_weight: float = 0.7
    max_suggestions: int = 5
    history_db_path: str = "./data/history.json"


@dataclass
class PromptConfig:
    """
    提示词配置数据类
    
    Attributes:
        system_prompt: 系统提示词
        analysis_template: 分析提示词模板
        refine_template: 精化提示词模板
    """
    system_prompt: str = ""
    analysis_template: str = ""
    refine_template: str = ""
    
    @staticmethod
    def default() -> "PromptConfig":
        """获取默认提示词配置"""
        return PromptConfig(
            system_prompt="""你是一个资深的分布式系统性能优化专家。
请分析提供的性能指标和日志，给出具体可实施的优化建议。
每次分析限制在 3 条以内，优先给出最高优先级的建议。
输出格式为 JSON，包含 title、description、pseudocode、priority、risk_level、expected_gain、implementation_steps、self_confidence 字段。""",
            
            analysis_template="""你是一个性能分析助手。请分析以下压测结果：

## 性能指标
- QPS: {qps}
- 目标 QPS: {target_qps}
- 平均延迟: {avg_latency}ms
- P99延迟: {p99_latency}ms
- 错误率: {error_rate}%

## 日志片段（最近30行）
```
{logs}
```

请快速分析并返回 JSON 格式的建议列表。""",
            
            refine_template="""你是一个资深的分布式系统性能优化专家。请为以下优化方向生成详细技术文档。

## 背景信息
- 系统：{system_type}
- 当前 QPS: {current_qps}
- 目标 QPS: {target_qps}
- 主要瓶颈: {bottleneck}

## 初步优化方向
{lightweight_suggestions}

请生成详细的技术文档和伪代码实现。"""
        )


@dataclass
class Config:
    """
    全局配置数据类
    
    Attributes:
        models: 模型配置列表
        systems: 系统配置列表
        weight_engine: 权重引擎配置
        prompts: 提示词配置
        output_dir: 输出目录
        data_dir: 数据目录
    """
    models: List[ModelConfig] = field(default_factory=list)
    systems: List[SystemConfig] = field(default_factory=list)
    weight_engine: WeightEngineConfig = field(default_factory=WeightEngineConfig)
    prompts: PromptConfig = field(default_factory=PromptConfig.default)
    output_dir: str = "./output"
    data_dir: str = "./data"
    
    def get_enabled_models(self) -> List[ModelConfig]:
        """获取所有启用的模型"""
        return [m for m in self.models if m.enabled]
    
    def get_system(self, name: str) -> Optional[SystemConfig]:
        """根据名称获取系统配置"""
        for system in self.systems:
            if system.name == name:
                return system
        return None
    
    def get_model(self, name: str) -> Optional[ModelConfig]:
        """根据名称获取模型配置"""
        for model in self.models:
            if model.name == name:
                return model
        return None


class ConfigLoader:
    """
    配置加载器
    
    支持从 YAML 文件加载配置，自动替换环境变量。
    """
    
    @staticmethod
    def load(config_path: str = "config/default.yaml") -> Config:
        """
        加载配置文件
        
        Args:
            config_path: 配置文件路径
            
        Returns:
            Config 对象
        """
        config_path = Path(config_path)
        
        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        # 处理环境变量替换
        data = ConfigLoader._resolve_env_vars(data)
        
        # 构建配置对象
        models = [ModelConfig(**m) for m in data.get('models', [])]
        systems = [SystemConfig(**s) for s in data.get('systems', [])]
        
        weight_config_data = data.get('weight_engine', {})
        weight_config = WeightEngineConfig(
            confidence_threshold=weight_config_data.get('confidence_threshold', 0.3),
            default_model_weight=weight_config_data.get('default_model_weight', 0.7),
            max_suggestions=weight_config_data.get('max_suggestions', 5),
            history_db_path=weight_config_data.get('history_db_path', './data/history.json'),
        )
        
        # 提示词配置
        prompt_data = data.get('prompts', {})
        prompts = PromptConfig(
            system_prompt=prompt_data.get('system_prompt', PromptConfig.default().system_prompt),
            analysis_template=prompt_data.get('analysis_template', PromptConfig.default().analysis_template),
            refine_template=prompt_data.get('refine_template', PromptConfig.default().refine_template),
        )
        
        return Config(
            models=models,
            systems=systems,
            weight_engine=weight_config,
            prompts=prompts,
            output_dir=data.get('output_dir', './output'),
            data_dir=data.get('data_dir', './data'),
        )
    
    @staticmethod
    def load_from_dict(data: Dict[str, Any]) -> Config:
        """
        从字典加载配置
        
        Args:
            data: 配置字典
            
        Returns:
            Config 对象
        """
        # 处理环境变量替换
        data = ConfigLoader._resolve_env_vars(data)
        
        models = [ModelConfig(**m) for m in data.get('models', [])]
        systems = [SystemConfig(**s) for s in data.get('systems', [])]
        
        weight_config_data = data.get('weight_engine', {})
        weight_config = WeightEngineConfig(**weight_config_data) if weight_config_data else WeightEngineConfig()
        
        prompts = PromptConfig(**data.get('prompts', {})) if data.get('prompts') else PromptConfig.default()
        
        return Config(
            models=models,
            systems=systems,
            weight_engine=weight_config,
            prompts=prompts,
            output_dir=data.get('output_dir', './output'),
            data_dir=data.get('data_dir', './data'),
        )
    
    @staticmethod
    def _resolve_env_vars(data: Any) -> Any:
        """
        递归替换 ${VAR} 为环境变量值
        
        Args:
            data: 任意类型的数据
            
        Returns:
            替换后的数据
        """
        if isinstance(data, dict):
            return {k: ConfigLoader._resolve_env_vars(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [ConfigLoader._resolve_env_vars(item) for item in data]
        elif isinstance(data, str):
            # 匹配 ${VAR} 格式的环境变量
            if data.startswith('${') and data.endswith('}'):
                env_var = data[2:-1]
                return os.environ.get(env_var, '')
            return data
        return data
    
    @staticmethod
    def save(config: Config, output_path: str):
        """
        保存配置到文件
        
        Args:
            config: Config 对象
            output_path: 输出路径
        """
        data = {
            'models': [
                {
                    'name': m.name,
                    'provider': m.provider,
                    'api_key': m.api_key,
                    'base_url': m.base_url,
                    'model': m.model,
                    'default_weight': m.default_weight,
                    'enabled': m.enabled,
                    'max_retries': m.max_retries,
                    'timeout': m.timeout,
                }
                for m in config.models
            ],
            'systems': [
                {
                    'name': s.name,
                    'system_type': s.system_type,
                    'project_path': s.project_path,
                    'benchmark_command': s.benchmark_command,
                    'log_path': s.log_path,
                    'config_paths': s.config_paths,
                }
                for s in config.systems
            ],
            'weight_engine': {
                'confidence_threshold': config.weight_engine.confidence_threshold,
                'default_model_weight': config.weight_engine.default_model_weight,
                'max_suggestions': config.weight_engine.max_suggestions,
                'history_db_path': config.weight_engine.history_db_path,
            },
            'prompts': {
                'system_prompt': config.prompts.system_prompt,
                'analysis_template': config.prompts.analysis_template,
                'refine_template': config.prompts.refine_template,
            },
            'output_dir': config.output_dir,
            'data_dir': config.data_dir,
        }
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
