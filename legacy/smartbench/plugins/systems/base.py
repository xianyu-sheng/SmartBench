"""
系统插件基类

定义统一的系统接口，所有系统插件需实现此接口。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path
import subprocess
import re

from smartbench.core.types import Metrics, SystemType


class BaseSystemPlugin(ABC):
    """
    系统插件抽象基类
    
    所有被测系统插件必须继承此类并实现抽象方法。
    系统插件负责：
    1. 运行压测获取性能指标
    2. 获取系统日志
    3. 获取配置文件
    4. 获取源码片段
    
    Example:
        class RaftKVPlugin(BaseSystemPlugin):
            def __init__(self, project_path: str):
                super().__init__()
                self.project_path = Path(project_path)
                
            def get_metrics(self) -> Metrics:
                # 运行压测脚本
                ...
                
            def get_logs(self, lines: int = 100) -> str:
                # 读取日志
                ...
    """
    
    def __init__(self):
        """初始化系统插件"""
        self._project_path: Optional[Path] = None
    
    @property
    @abstractmethod
    def name(self) -> str:
        """
        返回系统名称
        
        Returns:
            系统名称标识
        """
        pass
    
    @property
    def system_type(self) -> SystemType:
        """
        返回系统类型
        
        Returns:
            SystemType 枚举值
        """
        return SystemType.GENERIC
    
    @property
    def project_path(self) -> Optional[Path]:
        """
        返回项目路径
        
        Returns:
            Path 对象或 None
        """
        return self._project_path
    
    @project_path.setter
    def project_path(self, path: str):
        """设置项目路径"""
        self._project_path = Path(path)
    
    @abstractmethod
    def get_metrics(self) -> Metrics:
        """
        获取性能指标
        
        运行压测并解析输出，返回 Metrics 对象。
        
        Returns:
            Metrics: 性能指标对象
        """
        pass
    
    def get_logs(self, lines: int = 100) -> str:
        """
        获取日志内容
        
        Args:
            lines: 获取最近多少行
            
        Returns:
            日志内容字符串
        """
        return ""
    
    def get_source_code(self, path: str) -> str:
        """
        获取源码片段
        
        Args:
            path: 相对于项目根目录的路径
            
        Returns:
            源码内容字符串
        """
        if not self._project_path:
            return ""
        
        full_path = self._project_path / path
        if full_path.exists():
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        return ""
    
    def get_config(self, config_name: Optional[str] = None) -> Dict[str, Any]:
        """
        获取配置信息
        
        Args:
            config_name: 配置文件名，如果为 None 则返回所有配置
            
        Returns:
            配置字典
        """
        return {}
    
    def run_command(
        self, 
        command: str, 
        timeout: int = 120,
        cwd: Optional[str] = None
    ) -> subprocess.CompletedProcess:
        """
        运行命令
        
        Args:
            command: 命令字符串
            timeout: 超时时间（秒）
            cwd: 工作目录
            
        Returns:
            CompletedProcess 对象
        """
        if cwd is None and self._project_path:
            cwd = str(self._project_path)
        
        return subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )


class BenchmarkResult:
    """
    压测结果解析辅助类
    
    提供常用的正则匹配模式。
    """
    
    # 常用模式
    QPS_PATTERN = re.compile(r'QPS[:\s]+([\d.]+)', re.IGNORECASE)
    LATENCY_AVG_PATTERN = re.compile(r'(?:Avg|Average)\s*Latency[:\s]+([\d.]+)\s*(?:ms|millisec)', re.IGNORECASE)
    LATENCY_P50_PATTERN = re.compile(r'P50\s*Latency[:\s]+([\d.]+)\s*(?:ms|millisec)', re.IGNORECASE)
    LATENCY_P99_PATTERN = re.compile(r'P99\s*Latency[:\s]+([\d.]+)\s*(?:ms|millisec)', re.IGNORECASE)
    ERROR_RATE_PATTERN = re.compile(r'Error\s*Rate[:\s]+([\d.]+)%?', re.IGNORECASE)
    THROUGHPUT_PATTERN = re.compile(r'Throughput[:\s]+([\d.]+)\s*(?:ops/s|req/s)', re.IGNORECASE)
    
    @classmethod
    def extract_metrics(cls, output: str) -> Dict[str, float]:
        """
        从输出中提取指标
        
        Args:
            output: 压测输出文本
            
        Returns:
            指标字典
        """
        metrics = {}
        
        # QPS
        match = cls.QPS_PATTERN.search(output)
        if match:
            metrics['qps'] = float(match.group(1))
        
        # 平均延迟
        match = cls.LATENCY_AVG_PATTERN.search(output)
        if match:
            metrics['avg_latency'] = float(match.group(1))
        
        # P50 延迟
        match = cls.LATENCY_P50_PATTERN.search(output)
        if match:
            metrics['p50_latency'] = float(match.group(1))
        
        # P99 延迟
        match = cls.LATENCY_P99_PATTERN.search(output)
        if match:
            metrics['p99_latency'] = float(match.group(1))
        
        # 错误率
        match = cls.ERROR_RATE_PATTERN.search(output)
        if match:
            error_rate = float(match.group(1))
            # 如果值大于1，说明是百分比形式
            metrics['error_rate'] = error_rate / 100 if error_rate > 1 else error_rate
        
        return metrics
    
    @classmethod
    def create_metrics(cls, output: str) -> Metrics:
        """
        从输出创建 Metrics 对象
        
        Args:
            output: 压测输出文本
            
        Returns:
            Metrics 对象
        """
        metrics_dict = cls.extract_metrics(output)
        
        return Metrics(
            qps=metrics_dict.get('qps', 0.0),
            avg_latency=metrics_dict.get('avg_latency', 0.0),
            p50_latency=metrics_dict.get('p50_latency', 0.0),
            p99_latency=metrics_dict.get('p99_latency', 0.0),
            error_rate=metrics_dict.get('error_rate', 0.0),
        )
