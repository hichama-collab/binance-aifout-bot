import base64
import importlib
import os
import subprocess
import sys

import pytest

pytest.importorskip("flask")


def _auth_header(user="admin", password="test-password"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _import_dashboard_app(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("BOT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("BOT_SERVICE_ENV", str(tmp_path / ".service.env"))
    monkeypatch.setenv("DASH_USER", "admin")
    monkeypatch.setenv("DASH_PASS", "test-password")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret-key")
    sys.modules.pop("dashboard.app", None)
    return importlib.import_module("dashboard.app")


def test_api_stream_requires_basic_auth(tmp_path, monkeypatch):
    app_mod = _import_dashboard_app(tmp_path, monkeypatch)
    client = app_mod.app.test_client()

    res = client.get("/api/stream")

    assert res.status_code == 401


def test_api_control_rejects_forbidden_unit(tmp_path, monkeypatch):
    app_mod = _import_dashboard_app(tmp_path, monkeypatch)
    client = app_mod.app.test_client()

    res = client.post(
        "/api/control",
        json={"action": "restart", "unit": "ssh.service"},
        headers=_auth_header(),
    )

    assert res.status_code == 400
    assert res.get_json()["error"] == "unit_not_allowed"


def test_dashboard_import_rejects_default_password(tmp_path):
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": f"/tmp/aifout-test-deps:{os.getcwd()}",
        "BOT_LOG_DIR": str(tmp_path / "logs"),
        "BOT_BASE_DIR": str(tmp_path),
        "BOT_RUNTIME_DIR": str(tmp_path / "runtime"),
        "DASH_PASS": "changeme",
        "FLASK_SECRET_KEY": "test-secret-key",
    })

    result = subprocess.run(
        [sys.executable, "-c", "import dashboard.app"],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "DASH_PASS must be changed" in (result.stderr + result.stdout)
