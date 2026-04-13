"""
SmartBench 系统插件
"""

from smartbench.plugins.systems.base import BaseSystemPlugin, BenchmarkResult
from smartbench.plugins.systems.raft_kv import RaftKVPlugin

# 导出所有系统插件
__all__ = [
    "BaseSystemPlugin",
    "BenchmarkResult",
    "RaftKVPlugin",
]

# 尝试导入可选插件
try:
    from smartbench.plugins.systems.mysql import MySQLPlugin
    __all__.append("MySQLPlugin")
except ImportError:
    pass

try:
    from smartbench.plugins.systems.redis import RedisPlugin
    __all__.append("RedisPlugin")
except ImportError:
    pass