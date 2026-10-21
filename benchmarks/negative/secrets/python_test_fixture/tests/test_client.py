"""Negative fixture: fake credentials in a test fixture.

Resembles: a hardcoded secret (hardcoded_secret).
Not a bug because: the value is an obviously fake test key in a test file.
True positive if: a production key is committed here, or the fixture is copied
into runtime code.
"""


def test_client_accepts_fixture_key():
    api_key = "sk-test-000000000000000000000000000000000000000000000000"
    assert api_key.startswith("sk-test-")
