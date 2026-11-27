"""Regenerate the checked-in frontend conformance golden IR files."""

from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_frontend_conformance import (
    FRONTEND_CASE_VALUES,
    _parse_snapshot,
    _stable_ir_json,
)


def main() -> None:
    for case in FRONTEND_CASE_VALUES:
        for snapshot in ("before", "after"):
            with TemporaryDirectory() as directory:
                ir = _parse_snapshot(case, snapshot, Path(directory))
                target = case.fixture_dir / f"golden_{snapshot}.json"
                target.write_text(_stable_ir_json(ir), encoding="utf-8", newline="\n")
                print(target)


if __name__ == "__main__":
    main()
