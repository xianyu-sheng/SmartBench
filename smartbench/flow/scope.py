"""
作用域追踪 - 100% 确定性，不猜测。

变量查找从当前作用域开始，向上遍历，
找不到返回 None，不猜测。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from smartbench.flow.schema import AbstractValue, ScopeType, SourceLocation


@dataclass
class Scope:
    """作用域 - 确定性的变量容器。

    核心原则：
    - 作用域链是确定的
    - 变量查找从当前作用域开始
    - 找不到返回 None，不猜测
    """

    parent: Optional["Scope"]
    variables: Dict[str, AbstractValue] = field(default_factory=dict)
    scope_type: ScopeType = ScopeType.BLOCK
    location: Optional[SourceLocation] = None
    name: Optional[str] = None

    def get(self, name: str) -> Optional[AbstractValue]:
        """确定性地查找变量。

        从当前作用域开始，向上遍历父作用域链。
        找不到返回 None，不猜测。
        """
        if name in self.variables:
            return self.variables[name]
        if self.parent is not None:
            return self.parent.get(name)
        return None

    def set(self, name: str, value: AbstractValue) -> None:
        """设置变量到当前作用域。"""
        self.variables[name] = value

    def has_local(self, name: str) -> bool:
        """检查变量是否在当前作用域（不检查父作用域）。"""
        return name in self.variables


class ScopeManager:
    """作用域管理器 - 负责创建、进入、退出作用域。

    核心原则：
    - 作用域栈是确定的
    - 进入/退出作用域是成对的
    - 不处理闭包或复杂作用域
    """

    def __init__(self):
        self._stack: List[Scope] = []
        self._all_scopes: List[Scope] = []

    def enter_scope(
        self,
        scope_type: ScopeType,
        location: Optional[SourceLocation] = None,
        name: Optional[str] = None,
    ) -> Scope:
        """进入一个新作用域。

        新作用域的父作用域是当前作用域（如果有）。
        """
        parent = self.current_scope if self._stack else None
        scope = Scope(
            parent=parent,
            scope_type=scope_type,
            location=location,
            name=name,
        )
        self._stack.append(scope)
        self._all_scopes.append(scope)
        return scope

    def leave_scope(self) -> Optional[Scope]:
        """离开当前作用域。

        返回被离开的作用域，如果栈已空返回 None。
        """
        if not self._stack:
            return None
        return self._stack.pop()

    @property
    def current_scope(self) -> Optional[Scope]:
        """获取当前作用域。"""
        return self._stack[-1] if self._stack else None

    @property
    def scope_depth(self) -> int:
        """获取当前作用域深度。"""
        return len(self._stack)

    def get(self, name: str) -> Optional[AbstractValue]:
        """在当前作用域链中查找变量。"""
        if self.current_scope is not None:
            return self.current_scope.get(name)
        return None

    def set(self, name: str, value: AbstractValue) -> None:
        """在当前作用域中设置变量。"""
        if self.current_scope is not None:
            self.current_scope.set(name, value)
        # 如果没有当前作用域，静默忽略（不猜测）

    def set_global(self, name: str, value: AbstractValue) -> None:
        """在全局作用域中设置变量。"""
        if self._all_scopes:
            # 第一个作用域是全局作用域
            self._all_scopes[0].set(name, value)

    def get_all_scopes(self) -> List[Scope]:
        """获取所有创建过的作用域。"""
        return list(self._all_scopes)

    def get_scope_at(self, index: int) -> Optional[Scope]:
        """获取指定位置的作用域（用于调试）。"""
        if 0 <= index < len(self._all_scopes):
            return self._all_scopes[index]
        return None
