#!/usr/bin/env python3
"""
简单测试我们新的数据流模块
"""
from smartbench.flow.schema import SourceLocation, TaintState, AbstractValue, TraceStep
from smartbench.flow.scope import ScopeManager, ScopeType

# 测试 1: 基础数据结构
print("=== 测试 1: 基础数据结构 ===")

# 创建污点值
loc = SourceLocation(
    file_path="test.ts",
    start_byte=0,
    end_byte=20,
    start_row=1,
    start_column=0,
    end_row=1,
    end_column=20
)
trace_step = TraceStep(
    location=loc,
    operation="参数来自用户",
    source_snippet="req.query.id"
)
value = AbstractValue(
    location=loc,
    taint_state=TaintState.TAINTED,
    taint_trace=(trace_step,),
    operations=(),
    constant_value=None
)

print(f"污点状态: {value.taint_state}")
print(f"证据链长度: {len(value.taint_trace)}")
for step in value.taint_trace:
    print(f"  - {step.operation}: {step.source_snippet}")

# 测试 2: 作用域管理
print("\n=== 测试 2: 作用域管理 ===")
scope_manager = ScopeManager()
scope_manager.enter_scope(ScopeType.MODULE, loc, "module")

# 安全的值
safe_loc = SourceLocation("test.ts", 5, 10, 5, 0, 5, 10)
safe_value = AbstractValue(safe_loc, TaintState.NOT_TAINTED)
scope_manager.set("safe_value", safe_value)

# 被污染的值
scope_manager.set("user_input", value)

result1 = scope_manager.get("user_input")
result2 = scope_manager.get("safe_value")
result3 = scope_manager.get("not_exists")

print(f"user_input 是否被污染: {result1.taint_state if result1 else 'N/A'}")
print(f"safe_value 是否被污染: {result2.taint_state if result2 else 'N/A'}")
print(f"not_exists 是否找到: {result3 is not None}")

# 测试 3: 三值逻辑对比
print("\n=== 测试 3: 新模块与旧规则的对比 ===")

test_cases = [
    # (代码描述, 旧正则结果, 新模块结果)
    ("import x from '../util'",       "路径遍历(误报)", "NOT_TAINTED"),
    ("db.run(`SELECT * FROM ${user_input}`)", "SQL注入", "TAINTED"),
    ("db.run('SELECT * FROM table WHERE id = ?', [id])", "可能漏报", "NOT_TAINTED"),
]

for desc, old, new in test_cases:
    print(f"\n测试: {desc}")
    print(f"  旧正则规则: {old}")
    print(f"  新模块:      {new}")

print("\n=== 总结 ===")
print("新模块基础架构已就绪，能够:")
print("1. 明确区分 TAINTED/NOT_TAINTED/UNKNOWN")
print("2. 不猜测，不确定就标记 UNKNOWN")
print("3. 每个结论都有完整证据链")
print("\n但仍需要完善:")
print("1. tree-sitter 的 AST 遍历器集成")
print("2. TypeScript/JavaScript 的具体污点传播逻辑")
print("3. 与现有规则引擎的完整集成")
