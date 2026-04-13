"""
代码缓存模块 - 避免重复读取和 API 调用

功能：
1. 缓存源码文件，只在文件变更时重新读取
2. 缓存 LLM 分析结果，避免重复 token 消耗
3. 支持按文件/行号查询
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class CachedAnalysis:
    """缓存的分析结果"""
    file_path: str
    file_hash: str
    analysis: str
    suggestions: List[Dict[str, Any]]
    timestamp: str
    model_name: str


@dataclass
class FileCache:
    """文件缓存"""
    path: str
    hash: str
    content: str
    lines: int
    timestamp: str


class CodeCache:
    """
    代码缓存管理器

    功能：
    1. 文件级缓存：检测文件变更，只在变更时重新读取
    2. 分析缓存：缓存 LLM 分析结果
    3. 内容查询：按行号范围查询代码片段
    """

    def __init__(self, cache_dir: str = "./data/cache"):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.file_cache_path = self.cache_dir / "file_cache.json"
        self.analysis_cache_path = self.cache_dir / "analysis_cache.json"
        self.stats_path = self.cache_dir / "stats.json"

        # 加载缓存
        self.file_cache: Dict[str, FileCache] = self._load_cache(self.file_cache_path)
        self.analysis_cache: Dict[str, CachedAnalysis] = self._load_analysis_cache()
        self.stats = self._load_stats()

    def _load_cache(self, path: Path) -> Dict[str, FileCache]:
        """加载文件缓存"""
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return {k: FileCache(**v) for k, v in data.items()}
            except Exception:
                pass
        return {}

    def _load_analysis_cache(self) -> Dict[str, CachedAnalysis]:
        """加载分析缓存"""
        if self.analysis_cache_path.exists():
            try:
                with open(self.analysis_cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return {k: CachedAnalysis(**v) for k, v in data.items()}
            except Exception:
                pass
        return {}

    def _load_stats(self) -> Dict[str, Any]:
        """加载统计信息"""
        if self.stats_path.exists():
            try:
                with open(self.stats_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"total_api_calls": 0, "total_tokens_saved": 0, "cache_hits": 0}

    def _save_file_cache(self):
        """保存文件缓存"""
        try:
            with open(self.file_cache_path, 'w', encoding='utf-8') as f:
                json.dump({k: asdict(v) for k, v in self.file_cache.items()}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _save_analysis_cache(self):
        """保存分析缓存"""
        try:
            with open(self.analysis_cache_path, 'w', encoding='utf-8') as f:
                json.dump({k: asdict(v) for k, v in self.analysis_cache.items()}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _save_stats(self):
        """保存统计信息"""
        try:
            with open(self.stats_path, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_file_hash(self, path: Path) -> str:
        """计算文件哈希"""
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def read_file(self, path: str, force_refresh: bool = False) -> Optional[str]:
        """
        读取文件，使用缓存

        Args:
            path: 文件路径
            force_refresh: 强制重新读取

        Returns:
            文件内容或 None
        """
        file_path = Path(path)

        if not file_path.exists():
            return None

        current_hash = self.get_file_hash(file_path)

        # 检查缓存
        if not force_refresh and path in self.file_cache:
            cached = self.file_cache[path]
            if cached.hash == current_hash:
                # 缓存命中
                self.stats["cache_hits"] = self.stats.get("cache_hits", 0) + 1
                return cached.content

        # 重新读取
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            lines = len(content.splitlines())

            # 更新缓存
            self.file_cache[path] = FileCache(
                path=path,
                hash=current_hash,
                content=content,
                lines=lines,
                timestamp=datetime.now().isoformat()
            )

            self._save_file_cache()
            return content

        except Exception:
            return None

    def get_snippet(self, path: str, start_line: int, end_line: int) -> Optional[str]:
        """
        获取代码片段

        Args:
            path: 文件路径
            start_line: 起始行号（1-indexed）
            end_line: 结束行号

        Returns:
            代码片段
        """
        content = self.read_file(path)
        if not content:
            return None

        lines = content.splitlines()
        if start_line < 1:
            start_line = 1
        if end_line > len(lines):
            end_line = len(lines)

        return '\n'.join(lines[start_line-1:end_line])

    def get_analysis(
        self,
        file_path: str,
        focus_area: str,
        model_name: str,
    ) -> Optional[CachedAnalysis]:
        """
        获取缓存的分析结果

        Args:
            file_path: 文件路径
            focus_area: 关注区域（如 "logistics", "performance"）
            model_name: 模型名称

        Returns:
            缓存的分析结果
        """
        cache_key = f"{file_path}:{focus_area}:{model_name}"
        return self.analysis_cache.get(cache_key)

    def cache_analysis(
        self,
        file_path: str,
        focus_area: str,
        model_name: str,
        file_hash: str,
        analysis: str,
        suggestions: List[Dict[str, Any]],
    ):
        """
        缓存分析结果

        Args:
            file_path: 文件路径
            focus_area: 关注区域
            model_name: 模型名称
            file_hash: 文件哈希
            analysis: 分析内容
            suggestions: 建议列表
        """
        cache_key = f"{file_path}:{focus_area}:{model_name}"

        self.analysis_cache[cache_key] = CachedAnalysis(
            file_path=file_path,
            file_hash=file_hash,
            analysis=analysis,
            suggestions=suggestions,
            timestamp=datetime.now().isoformat(),
            model_name=model_name,
        )

        self._save_analysis_cache()

        # 更新统计
        self.stats["total_api_calls"] = self.stats.get("total_api_calls", 0) + 1

    def get_key_files(self, project_path: str) -> Dict[str, str]:
        """
        获取关键源码文件（使用缓存）

        Args:
            project_path: 项目路径

        Returns:
            文件路径到内容的字典
        """
        project = Path(project_path)

        # 关键文件列表
        key_files = [
            "Raft/Raft.cpp",
            "Raft/Raft.h",
            "KvServer/KvServer.cpp",
            "Clerk/clerk.cpp",
            "Skiplist-CPP/skiplist.h",
            "Raft/Persister.cpp",
            "myRPC/User/KrpcChannel.cc",
        ]

        result = {}
        for rel_path in key_files:
            full_path = project / rel_path
            if full_path.exists():
                content = self.read_file(str(full_path))
                if content:
                    result[rel_path] = content

        return result

    def is_cache_valid(self, file_path: str, expected_hash: str) -> bool:
        """
        检查缓存是否有效

        Args:
            file_path: 文件路径
            expected_hash: 期望的文件哈希

        Returns:
            缓存是否有效
        """
        if file_path not in self.file_cache:
            return False

        current_hash = self.get_file_hash(Path(file_path))
        return self.file_cache[file_path].hash == current_hash == expected_hash

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self.stats,
            "files_cached": len(self.file_cache),
            "analyses_cached": len(self.analysis_cache),
        }

    def clear_cache(self, keep_stats: bool = True):
        """清除缓存"""
        self.file_cache.clear()
        self.analysis_cache.clear()

        if not keep_stats:
            self.stats = {"total_api_calls": 0, "total_tokens_saved": 0, "cache_hits": 0}

        self._save_file_cache()
        self._save_analysis_cache()
        if not keep_stats:
            self._save_stats()


# 全局缓存实例
_global_cache: Optional[CodeCache] = None


def get_code_cache(cache_dir: str = "./data/cache") -> CodeCache:
    """获取全局缓存实例"""
    global _global_cache
    if _global_cache is None:
        _global_cache = CodeCache(cache_dir)
    return _global_cache
