import base64
import importlib
import os
import sys

import pytest

pytest.importorskip("flask")

from services.token_radar_store import insert_snapshots


def _auth_header(user="admin", password="test-password"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _import_dashboard_app(tmp_path, monkeypatch):
    db_path = tmp_path / "token_radar.sqlite3"
    monkeypatch.setenv("BOT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("BOT_SERVICE_ENV", str(tmp_path / ".service.env"))
    monkeypatch.setenv("TOKEN_RADAR_DB", str(db_path))
    monkeypatch.setenv("DASH_USER", "admin")
    monkeypatch.setenv("DASH_PASS", "test-password")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    sys.modules.pop("dashboard.app", None)
    sys.modules.pop("dashboard.routes_radar", None)
    app_mod = importlib.import_module("dashboard.app")
    insert_snapshots([
        {
            "created_at": "2026-06-28T00:00:00+00:00",
            "symbol": "BTCUSDC",
            "price": 65000.0,
            "spread_pct": 0.0001,
            "quote_volume_24h": 10_000_000,
            "change_1h_pct": 0.01,
            "hot_score": 75.0,
            "score": 80.0,
            "global_score": 80.0,
            "volatility_pct": 0.01,
            "amplitude_pct": 0.01,
            "consistency_score": 80.0,
            "reliability_score": 80.0,
            "noise_score": 20.0,
            "movement_risk_score": 20.0,
            "negative_pressure_score": 0.0,
            "risk_level": "LOW",
            "risk_label": "Fiable",
            "risk_reason": "mouvement regulier",
            "signal": "WATCH",
            "reason": "test",
        }
    ], db_path)
    return app_mod


def test_radar_page_requires_auth(tmp_path, monkeypatch):
    app_mod = _import_dashboard_app(tmp_path, monkeypatch)
    client = app_mod.app.test_client()

    res = client.get("/radar")

    assert res.status_code == 401


def test_radar_api_top_and_favorites(tmp_path, monkeypatch):
    app_mod = _import_dashboard_app(tmp_path, monkeypatch)
    client = app_mod.app.test_client()
    headers = _auth_header()

    top = client.get("/api/radar/top", headers=headers)
    add = client.post("/api/radar/favorites", json={"symbol": "BTC", "note": "watch"}, headers=headers)
    favorites = client.get("/api/radar/favorites", headers=headers)
    delete = client.delete("/api/radar/favorites/BTCUSDC", headers=headers)

    assert top.status_code == 200
    assert top.get_json()["items"][0]["symbol"] == "BTCUSDC"
    assert add.status_code == 200
    assert favorites.get_json()["items"][0]["symbol"] == "BTCUSDC"
    assert delete.status_code == 200
