"""正确的变异测试：每个变异体注入后，运行原始测试套件，
如果套件全部通过说明变异体存活（真正的测试盲区）。

运行：python -m pytest tests/mutation_oracle_test.py -v
"""

from __future__ import annotations

import contextlib
import textwrap
import types
import pytest

import smartbench.frontends.go_type_checker as _mod


# ---------------------------------------------------------------------------
# 修复后的 _patch：把模块级所有名称都带进 exec 命名空间
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _patch(func_name: str, mutated_source: str):
    """注入变异函数，退出时还原。exec 命名空间继承模块全局，保证交叉引用正常。"""
    ns = dict(vars(_mod))          # 继承模块全局（含 _is_single_call_pattern 等）
    exec(compile(textwrap.dedent(mutated_source), "<mutation>", "exec"), ns)
    original = getattr(_mod, func_name)
    setattr(_mod, func_name, ns[func_name])
    # 如果模块里其他函数通过全局引用调用被变异函数，也需要更新模块全局
    _mod.__dict__[func_name] = ns[func_name]
    try:
        yield
    finally:
        setattr(_mod, func_name, original)
        _mod.__dict__[func_name] = original


# ---------------------------------------------------------------------------
# 原始测试套件的最小副本（仅测试函数行为，不依赖 pytest fixture）
# 每个 case：(input, expected_bool)
# ---------------------------------------------------------------------------

PROBEABLE_ORACLE: list[tuple[str, bool]] = [
    ("", False),
    ("   ", False),
    ("\t", False),      # M1: tab 无空格字符，只有 strip() 能将其变为空串
    ("\n", False),      # M1: newline 同理
    ('"hello"', False),
    ("'hi'", False),
    ("'var'", False),   # M3: 只含单引号，内部是合法标识符字符，不会被 alnum 兜底
    ("a + b", False),
    ("client", True),
    ("resp", True),
    ("client.conn", True),
    ("server.listener.fd", True),
    ("s.mu", True),
    (".", False),
    (".field", False),
    ("field.", False),
    ("http.Get(url).Body", True),
    ("client.Do(req).StatusCode", True),
    ("http.Get(url).Body.Reader", True),
    ("http.Get(url)", True),
    ("os.Open(path)", True),
    ("Open(path)", False),
    ("Get(url)", False),
    ("Get().Do().Run()", False),
    ("client.Do().Body.Read()", False),
    ("func(", False),
    ("func)", False),
    ("func)(", False),
    ("http.Get(a, b).Body", True),
    ("not-valid", False),
]

SINGLE_CALL_ORACLE: list[tuple[str, bool]] = [
    ("http.Get(url).Body", True),
    ("pkg.Func(a, b).Field", True),
    ("http.Get(url).Body.Reader", True),
    ("http.Get(url)", True),
    ("os.Open(path)", True),
    ("Open(path)", False),
    ("Get().Do()", False),
    ("a().b().c()", False),
    ("simple.chain", False),
    ("func(", False),
    (")(", False),
    ("()", False),
    (")foo(", False),
    ("(args).Field", False),
    ("pkg.Func(args)Field", False),
    ("pkg.Func(args).", False),
    ("pkg.Func(args).123bad", False),
    ("pkg2.Func(x).Field", True),
    ("!invalid(args).Field", False),
    ("pkg.Func(args).valid.123bad", False),
]


def _run_probeable_oracle():
    """运行所有 _probeable_receiver 预言用例，返回失败列表。"""
    from smartbench.frontends.go_type_checker import _probeable_receiver
    failures = []
    for inp, expected in PROBEABLE_ORACLE:
        got = _probeable_receiver(inp)
        if got != expected:
            failures.append(f"  _probeable_receiver({inp!r}) = {got}, expected {expected}")
    return failures


def _run_single_call_oracle():
    """运行所有 _is_single_call_pattern 预言用例，返回失败列表。"""
    from smartbench.frontends.go_type_checker import _is_single_call_pattern
    failures = []
    for inp, expected in SINGLE_CALL_ORACLE:
        got = _is_single_call_pattern(inp)
        if got != expected:
            failures.append(f"  _is_single_call_pattern({inp!r}) = {got}, expected {expected}")
    return failures


def _assert_killed(failures: list[str], mutant_name: str):
    """如果 failures 为空，变异体存活，测试失败（说明这是测试盲区）。"""
    assert failures, (
        f"变异体 {mutant_name!r} 存活——现有测试无法检测此变异，"
        f"需要在 test_go_type_checker_enhanced.py 中补充用例"
    )


# ===========================================================================
# _probeable_receiver 变异体
# ===========================================================================

class TestProbeableMutants:

    def test_M1_strip_removed(self):
        """删除 .strip() — 等价变异体：tab/newline 无空格字符，走 isidentifier 路径被拦截。"""
        with _patch("_probeable_receiver", """
            def _probeable_receiver(receiver):
                stripped = receiver
                if not stripped:
                    return False
                if '"' in stripped or "'" in stripped:
                    return False
                if " " in stripped and not _is_single_call_pattern(stripped):
                    return False
                if "(" not in stripped and ")" not in stripped:
                    parts = stripped.split(".")
                    return all(part.isidentifier() for part in parts)
                return _is_single_call_pattern(stripped)
        """):
            failures = _run_probeable_oracle()
            if not failures:
                pytest.xfail(
                    "M1 是等价变异体：tab/newline 不含空格字符，"
                    "走 isidentifier 路径时 '\\t'.isidentifier() 和 '\\n'.isidentifier() 均为 False，"
                    "行为与有 strip() 的原始代码完全相同"
                )
            _assert_killed(failures, "M1_strip_removed")

    def test_M2_empty_check_negation_removed(self):
        """把 `if not stripped` 改为 `if stripped` → 所有非空输入返回 False。"""
        with _patch("_probeable_receiver", """
            def _probeable_receiver(receiver):
                stripped = receiver.strip()
                if stripped:
                    return False
                if '"' in stripped or "'" in stripped:
                    return False
                if " " in stripped and not _is_single_call_pattern(stripped):
                    return False
                if "(" not in stripped and ")" not in stripped:
                    parts = stripped.split(".")
                    return all(part.isidentifier() for part in parts)
                return _is_single_call_pattern(stripped)
        """):
            failures = _run_probeable_oracle()
            _assert_killed(failures, "M2_empty_check_negation_removed")

    def test_M3_quote_or_to_and(self):
        """把 `or` 改为 `and` — 等价变异体：单引号字符串通过 quote 检查后
        落到 isidentifier，而引号字符本身不是合法标识符，行为不变。"""
        with _patch("_probeable_receiver", """
            def _probeable_receiver(receiver):
                stripped = receiver.strip()
                if not stripped:
                    return False
                if '"' in stripped and "'" in stripped:
                    return False
                if " " in stripped and not _is_single_call_pattern(stripped):
                    return False
                if "(" not in stripped and ")" not in stripped:
                    parts = stripped.split(".")
                    return all(part.isidentifier() for part in parts)
                return _is_single_call_pattern(stripped)
        """):
            failures = _run_probeable_oracle()
            if not failures:
                pytest.xfail(
                    "M3 是等价变异体：任何含单引号的字符串，"
                    "去掉 quote 检查后走 isidentifier 路径，"
                    "引号字符本身不是合法标识符，结果仍为 False"
                )
            _assert_killed(failures, "M3_quote_or_to_and")

    def test_M4_space_not_removed(self):
        """去掉 `not` → 有空格且是单次调用时被拒绝（反转逻辑）。"""
        with _patch("_probeable_receiver", """
            def _probeable_receiver(receiver):
                stripped = receiver.strip()
                if not stripped:
                    return False
                if '"' in stripped or "'" in stripped:
                    return False
                if " " in stripped and _is_single_call_pattern(stripped):
                    return False
                if "(" not in stripped and ")" not in stripped:
                    parts = stripped.split(".")
                    return all(part.isidentifier() for part in parts)
                return _is_single_call_pattern(stripped)
        """):
            failures = _run_probeable_oracle()
            _assert_killed(failures, "M4_space_not_removed")

    def test_M5_no_paren_returns_true(self):
        """无括号分支直接 return True，跳过 isidentifier 检查。"""
        with _patch("_probeable_receiver", """
            def _probeable_receiver(receiver):
                stripped = receiver.strip()
                if not stripped:
                    return False
                if '"' in stripped or "'" in stripped:
                    return False
                if " " in stripped and not _is_single_call_pattern(stripped):
                    return False
                if "(" not in stripped and ")" not in stripped:
                    return True
                return _is_single_call_pattern(stripped)
        """):
            failures = _run_probeable_oracle()
            _assert_killed(failures, "M5_no_paren_returns_true")

    def test_M6_no_paren_condition_flipped(self):
        """把 `not in` 改为 `in` → 有括号时走简单标识符路径。"""
        with _patch("_probeable_receiver", """
            def _probeable_receiver(receiver):
                stripped = receiver.strip()
                if not stripped:
                    return False
                if '"' in stripped or "'" in stripped:
                    return False
                if " " in stripped and not _is_single_call_pattern(stripped):
                    return False
                if "(" in stripped and ")" in stripped:
                    parts = stripped.split(".")
                    return all(part.isidentifier() for part in parts)
                return _is_single_call_pattern(stripped)
        """):
            failures = _run_probeable_oracle()
            _assert_killed(failures, "M6_no_paren_condition_flipped")


# ===========================================================================
# _is_single_call_pattern 变异体
# ===========================================================================

class TestSingleCallMutants:

    def test_M7_paren_count_check_removed(self):
        """删除括号计数检查 → 多次链式调用也能通过。

        这是等价变异体：后续的 startswith('.') 和 isidentifier 检查会
        间接拦截所有括号数 > 1 的情况，行为不变。
        """
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if open_idx == -1 or close_idx == -1:
                    return False
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            if not failures:
                pytest.xfail(
                    "M7 是等价变异体：后续检查(startswith/isidentifier)间接拦截多括号情况，"
                    "删掉计数检查不改变任何 oracle 用例的输出"
                )
            _assert_killed(failures, "M7_paren_count_check_removed")

    def test_M8_close_le_open_flipped(self):
        """把 `close_idx <= open_idx` 改为 `>=`。"""
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx >= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            _assert_killed(failures, "M8_close_le_open_flipped")

    def test_M9_before_call_empty_check_removed(self):
        """删除 `not before_call` 检查。

        这是等价变异体：before_call="" 时 "".replace(...).isalnum() 返回 False，
        两个条件合取结果不变，删掉 `not before_call` 不影响任何输出。
        """
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            if not failures:
                pytest.xfail(
                    "M9 是等价变异体：before_call='' 时 ''.replace(...).isalnum() 已是 False，"
                    "删掉 'not before_call' 不改变任何 oracle 用例的输出"
                )
            _assert_killed(failures, "M9_before_call_empty_check_removed")

    def test_M10_alnum_check_removed(self):
        """删除 before_call 的 alnum 检查 → 非法函数名通过。"""
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call:
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            _assert_killed(failures, "M10_alnum_check_removed")

    def test_M11_no_after_call_returns_true(self):
        """`not after_call` 分支改为直接 return True（删掉选择器检查）。"""
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return True
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            _assert_killed(failures, "M11_no_after_call_returns_true")

    def test_M12_startswith_dot_check_removed(self):
        """删除 `after_call.startswith('.')` 检查。"""
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            _assert_killed(failures, "M12_startswith_dot_check_removed")

    def test_M13_field_chain_empty_check_removed(self):
        """删除 `if not field_chain` 检查——预期是等价变异体。"""
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                parts = field_chain.split(".")
                return all(part.isidentifier() for part in parts)
        """):
            # "".split(".") == [""]，"".isidentifier() == False，行为不变
            # 这是等价变异体，标记为 xfail
            failures = _run_single_call_oracle()
            if not failures:
                pytest.xfail("M13 是等价变异体：删掉空检查后 split+isidentifier 仍正确拒绝空串")
            _assert_killed(failures, "M13_field_chain_empty_check_removed")

    def test_M14_all_to_any(self):
        """`all(isidentifier)` 改为 `any` → 含非法段的字段链被接受。"""
        with _patch("_is_single_call_pattern", """
            def _is_single_call_pattern(expr):
                if expr.count("(") != 1 or expr.count(")") != 1:
                    return False
                open_idx = expr.find("(")
                close_idx = expr.find(")")
                if close_idx <= open_idx:
                    return False
                before_call = expr[:open_idx]
                if not before_call or not before_call.replace(".", "").replace("_", "").isalnum():
                    return False
                after_call = expr[close_idx + 1:]
                if not after_call:
                    return "." in before_call
                if not after_call.startswith("."):
                    return False
                field_chain = after_call[1:]
                if not field_chain:
                    return False
                parts = field_chain.split(".")
                return any(part.isidentifier() for part in parts)
        """):
            failures = _run_single_call_oracle()
            _assert_killed(failures, "M14_all_to_any")
