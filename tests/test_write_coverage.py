from __future__ import annotations

import re
from pathlib import Path

from bing_webmaster_ai_cli_mcp.writes import WRITE_OPS

DOC = Path(__file__).resolve().parents[1] / "docs" / "api-surface.md"
ROW = re.compile(r"^\|\s*`(?P<method>\w+)`\s*\|.*\|\s*(?P<rw>[RW])\s*\|")


def test_every_supported_write_is_registered_once() -> None:
    registered = [
        operation.method for operation in WRITE_OPS.values() if operation.method != "IndexNow"
    ]
    expected = [
        match["method"]
        for line in DOC.read_text().splitlines()
        if (match := ROW.match(line)) and match["rw"] == "W" and match["method"] != "UpdateDeepLink"
    ]
    assert sorted(registered) == sorted(expected)
    assert len(registered) == len(set(registered))
