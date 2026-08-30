"""Tests for enhanced _probeable_receiver with chained call support."""
from smartbench.frontends.go_type_checker import _is_single_call_pattern, _probeable_receiver


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
        """Multiple chained calls should be rejected.

        Covers M7 (paren count check removed): the count guard is the first
        line of defence; the two-bracket case 'Get().Do()' must also be
        rejected directly so the oracle kills that mutant.
        """
        assert not _probeable_receiver("Get().Do().Run()")
        assert not _probeable_receiver("client.Do().Body.Read()")
        assert not _probeable_receiver("exec.Command().Start().Wait()")
        # Exactly two bracket-pairs — kills M7 if any path through the
        # mutant leaks.  pkg.Get().Do() has before_call='pkg.Get' (has dot),
        # after_call='.Do()'; startswith('.') passes but 'Do()' contains '('
        # so isidentifier fails on the field chain — still False in both
        # original and mutant.  Document as equivalent for M7.
        assert not _is_single_call_pattern("pkg.Get().Do()")   # 2 pairs
        assert not _is_single_call_pattern("a.F(x).G(y).Z")   # 3 pairs

    def test_string_literals_rejected(self):
        """String literals should be rejected.

        Covers M3 (or→and): tests both single-quote-only and double-quote-only
        inputs independently, ensuring the guard uses 'or' not 'and'.
        """
        assert not _probeable_receiver('"string literal"')  # double-quote only
        assert not _probeable_receiver("'string'")          # single-quote only
        assert not _probeable_receiver("'var'")             # single-quote around identifier chars

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
        """Empty and whitespace-only strings should be rejected.

        Covers M1 (strip removed): tab and newline have no space char so the
        space-guard branch is skipped; only strip() catches them as empty.
        """
        assert not _probeable_receiver("")
        assert not _probeable_receiver("   ")
        assert not _probeable_receiver("\t")      # tab — no space, no paren: needs strip
        assert not _probeable_receiver("\n")      # newline — same
        assert not _probeable_receiver("\t\n\r")  # mixed whitespace

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
