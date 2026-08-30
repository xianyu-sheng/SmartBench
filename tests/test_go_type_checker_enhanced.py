"""Tests for enhanced _probeable_receiver with chained call support."""
import pytest
from smartbench.frontends.go_type_checker import _probeable_receiver, _is_single_call_pattern


class TestProbeableReceiver:
    """Test the enhanced probeable receiver detection."""

    def test_simple_identifier(self):
        """Simple identifiers should be probeable."""
        assert _probeable_receiver("client")
        assert _probeable_receiver("resp")
        assert _probeable_receiver("conn")

    def test_selector_chain(self):
        """Selector chains without calls should be probeable."""
        assert _probeable_receiver("client.conn")
        assert _probeable_receiver("server.listener.fd")
        assert _probeable_receiver("s.mu")

    def test_single_call_with_field_access(self):
        """Single function call followed by field access should be probeable."""
        assert _probeable_receiver("http.Get(url).Body")
        assert _probeable_receiver("client.Do(req).StatusCode")
        assert _probeable_receiver("context.WithCancel(ctx).Done")
        assert _probeable_receiver("os.Open(path).Fd")

    def test_nested_field_access_after_call(self):
        """Call followed by nested field access should be probeable."""
        assert _probeable_receiver("http.Get(url).Body.Reader")
        assert _probeable_receiver("client.Do(req).Response.Header")

    def test_multiple_chained_calls_rejected(self):
        """Multiple chained calls should be rejected."""
        assert not _probeable_receiver("Get().Do().Run()")
        assert not _probeable_receiver("client.Do().Body.Read()")
        assert not _probeable_receiver("exec.Command().Start().Wait()")

    def test_string_literals_rejected(self):
        """String literals should be rejected."""
        assert not _probeable_receiver('"string literal"')
        assert not _probeable_receiver("'string'")

    def test_operators_rejected(self):
        """Expressions with operators should be rejected."""
        assert not _probeable_receiver("a + b")
        assert not _probeable_receiver("x == y")

    def test_bare_function_call_with_selector(self):
        """Function call without field access but with package selector."""
        assert _probeable_receiver("http.Get(url)")
        assert _probeable_receiver("os.Open(path)")

    def test_bare_function_call_without_selector_rejected(self):
        """Function call without package selector should be rejected."""
        assert not _probeable_receiver("Open(path)")
        assert not _probeable_receiver("Get(url)")

    def test_empty_and_whitespace(self):
        """Empty and whitespace-only strings should be rejected."""
        assert not _probeable_receiver("")
        assert not _probeable_receiver("   ")

    def test_malformed_parentheses(self):
        """Malformed parentheses should be rejected."""
        assert not _probeable_receiver("func(")
        assert not _probeable_receiver("func)")
        assert not _probeable_receiver("func)(")


class TestSingleCallPattern:
    """Test the single call pattern helper."""

    def test_single_call_with_field(self):
        """Valid single call patterns."""
        assert _is_single_call_pattern("http.Get(url).Body")
        assert _is_single_call_pattern("pkg.Func(a, b).Field")

    def test_single_call_with_nested_fields(self):
        """Single call with nested field access."""
        assert _is_single_call_pattern("http.Get(url).Body.Reader")
        assert _is_single_call_pattern("client.Do(req).Response.Header.ContentType")

    def test_single_call_without_field(self):
        """Call without field access - should pass if has package selector."""
        assert _is_single_call_pattern("http.Get(url)")
        assert _is_single_call_pattern("os.Open(path)")

    def test_multiple_calls(self):
        """Multiple calls should fail."""
        assert not _is_single_call_pattern("Get().Do()")
        assert not _is_single_call_pattern("a().b().c()")

    def test_no_call(self):
        """No call should fail."""
        assert not _is_single_call_pattern("simple.chain")

    def test_malformed(self):
        """Malformed expressions should fail."""
        assert not _is_single_call_pattern("func(")
        assert not _is_single_call_pattern(")(")
        assert not _is_single_call_pattern("()")
