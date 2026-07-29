"""Import-order regressions for SmartBench's public modules."""

import subprocess
import sys


def test_engine_can_be_imported_before_cli_and_provider():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from smartbench.engine.debate import DebateEngine; "
                "from smartbench.llm.provider import PROVIDER_REGISTRY; "
                "from smartbench.cli.main import app"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
