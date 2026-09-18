from __future__ import annotations

import os

import pytest

from bing_webmaster_ai_cli_mcp.audit import AuditLog


def test_entries_append_and_survive_new_instance(tmp_path) -> None:
    AuditLog(tmp_path).record("first", plan_id="a")
    AuditLog(tmp_path).record("second", plan_id="b")
    entries = AuditLog(tmp_path).entries()
    assert [entry["event"] for entry in entries] == ["first", "second"]
    assert all("ts" in entry for entry in entries)


def test_audit_file_is_owner_only(tmp_path) -> None:
    AuditLog(tmp_path).record("x")
    assert (tmp_path / "audit.jsonl").stat().st_mode & 0o777 == 0o600


def test_audit_handles_partial_os_writes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_write = os.write

    def partial_write(descriptor: int, data: bytes) -> int:
        return real_write(descriptor, data[:5])

    monkeypatch.setattr("bing_webmaster_ai_cli_mcp.audit.os.write", partial_write)
    AuditLog(tmp_path).record("partial", value="x" * 100)

    assert AuditLog(tmp_path).entries()[0]["event"] == "partial"
