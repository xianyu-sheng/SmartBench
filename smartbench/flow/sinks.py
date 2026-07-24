"""
危险 Sink 定义 - 确定性识别。

只基于 AST 节点类型和结构，不使用正则。
"""

from dataclasses import dataclass
from typing import List, Set


@dataclass(frozen=True)
class SinkDefinition:
    """危险 Sink 定义。"""

    name: str
    description: str
    language: str
    vulnerability_type: str  # "sql_injection", "command_injection", "path_traversal", etc.
    argument_index: int = 0  # 哪个参数是危险的


# TypeScript/JavaScript Sink 定义

TYPESCRIPT_SINKS: List[SinkDefinition] = [
    # SQL 相关
    SinkDefinition(
        name="db.query",
        description="Database query method",
        language="typescript",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="db.execute",
        description="Database execute method",
        language="typescript",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="db.run",
        description="Database run method",
        language="typescript",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="db.all",
        description="Database all method",
        language="typescript",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="db.get",
        description="Database get method",
        language="typescript",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    # 命令执行相关
    SinkDefinition(
        name="child_process.exec",
        description="Child process exec",
        language="typescript",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="child_process.execSync",
        description="Child process execSync",
        language="typescript",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="child_process.spawn",
        description="Child process spawn",
        language="typescript",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="child_process.spawnSync",
        description="Child process spawnSync",
        language="typescript",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="os.system",
        description="OS system command",
        language="typescript",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    # 路径相关
    SinkDefinition(
        name="fs.readFile",
        description="File system read",
        language="typescript",
        vulnerability_type="path_traversal",
        argument_index=0,
    ),
    SinkDefinition(
        name="fs.writeFile",
        description="File system write",
        language="typescript",
        vulnerability_type="path_traversal",
        argument_index=0,
    ),
    SinkDefinition(
        name="fs.open",
        description="File system open",
        language="typescript",
        vulnerability_type="path_traversal",
        argument_index=0,
    ),
]

# Python Sink 定义

PYTHON_SINKS: List[SinkDefinition] = [
    SinkDefinition(
        name="db.execute",
        description="Database execute",
        language="python",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="cursor.execute",
        description="Cursor execute",
        language="python",
        vulnerability_type="sql_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="os.system",
        description="OS system command",
        language="python",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="subprocess.run",
        description="Subprocess run",
        language="python",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="subprocess.call",
        description="Subprocess call",
        language="python",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="subprocess.Popen",
        description="Subprocess Popen",
        language="python",
        vulnerability_type="command_injection",
        argument_index=0,
    ),
    SinkDefinition(
        name="open",
        description="Builtin open",
        language="python",
        vulnerability_type="path_traversal",
        argument_index=0,
    ),
]


def get_sinks_for_language(language: str) -> List[SinkDefinition]:
    """获取指定语言的 Sink 定义。"""
    if language.lower() in ("typescript", "javascript", "ts", "js"):
        return TYPESCRIPT_SINKS
    if language.lower() in ("python", "py"):
        return PYTHON_SINKS
    return []


# 常见的 Sink 方法名模式

SQL_METHOD_NAMES: Set[str] = {
    "query",
    "execute",
    "run",
    "all",
    "get",
    "fetch",
    "select",
    "insert",
    "update",
    "delete",
}

COMMAND_METHOD_NAMES: Set[str] = {
    "exec",
    "execSync",
    "spawn",
    "spawnSync",
    "system",
    "popen",
    "call",
    "check_output",
    "check_call",
}

PATH_METHOD_NAMES: Set[str] = {
    "readFile",
    "writeFile",
    "open",
    "readFileSync",
    "writeFileSync",
    "createReadStream",
    "createWriteStream",
}
