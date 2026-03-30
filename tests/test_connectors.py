"""Tests for the base connector and connector registry."""
from __future__ import annotations

from memoreei.connectors.base import BaseConnector, SyncResult
from memoreei.connectors import get_connector_registry


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


def test_sync_result_defaults():
    r = SyncResult()
    assert r.synced == 0
    assert r.source == ""
    assert r.errors == []
    assert r.ok is True


def test_sync_result_ok_when_no_errors():
    r = SyncResult(synced=5, source="discord")
    assert r.ok is True


def test_sync_result_not_ok_with_errors():
    r = SyncResult(synced=0, source="discord", errors=["auth failed"])
    assert r.ok is False


def test_sync_result_to_dict_basic():
    r = SyncResult(synced=3, source="telegram")
    d = r.to_dict()
    assert d["synced"] == 3
    assert d["source"] == "telegram"
    assert "errors" not in d


def test_sync_result_to_dict_includes_errors_when_present():
    r = SyncResult(synced=0, source="slack", errors=["rate limited", "timeout"])
    d = r.to_dict()
    assert d["errors"] == ["rate limited", "timeout"]


def test_sync_result_errors_default_is_independent():
    """Mutable default should not be shared between instances."""
    r1 = SyncResult()
    r2 = SyncResult()
    r1.errors.append("boom")
    assert r2.errors == []


# ---------------------------------------------------------------------------
# Connector registry
# ---------------------------------------------------------------------------


def test_registry_returns_dict():
    registry = get_connector_registry()
    assert isinstance(registry, dict)


def test_registry_values_are_classes():
    registry = get_connector_registry()
    for name, cls in registry.items():
        assert isinstance(name, str)
        assert isinstance(cls, type), f"registry['{name}'] is not a class"


def test_registry_discord_present():
    registry = get_connector_registry()
    assert "discord" in registry


def test_registry_telegram_present():
    registry = get_connector_registry()
    assert "telegram" in registry
