"""Local page and secret handling. Network calls are blocked by the test fixture."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.coach import SAMPLE_REASON, CoachError, complete
from app.garmin_sync import public_sync_result
from app.sample_data import build_sample_bundle

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_report_and_home_render_sample_without_calling_the_model(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("model was called")

    monkeypatch.setattr("app.coach.complete", boom)
    client.post("/api/settings/deepseek", json={"api_key": "sk-should-stay-local"})
    report = client.get("/api/report")
    assert report.status_code == 200
    body = report.json()
    assert body["sample"] is True
    assert body["summary"]["ctl"] is not None
    assert body["summary"]["acwr"] > 1.5
    assert any(item["id"] == "recovery_needed" for item in body["flags"])
    assert body["coaching"]["available"] is False
    assert body["coaching"]["reason"] == SAMPLE_REASON
    assert "sk-should-stay-local" not in report.text

    page = client.get("/")
    assert page.status_code == 200
    assert "示例数据" in page.text
    assert f"{body['summary']['acwr']:.2f}" in page.text
    assert "sk-should-stay-local" not in page.text
    assert "本地建议" in page.text


def test_password_stays_in_the_secret_file_only(client):
    secret = "local-secret-pass"
    created = client.post(
        "/api/users",
        json={"email": "runner@example.com", "password": secret, "display_name": "测试"},
    )
    assert created.status_code == 200
    assert secret not in created.text
    user_id = created.json()["user"]["id"]
    assert secret not in client.get("/api/users").text
    assert secret not in client.get("/api/status").text
    assert secret not in client.get("/").text
    saved = json.loads(storage.secrets_path().read_text(encoding="utf-8"))
    assert saved["users"][user_id]["password"] == secret
    assert storage.secrets_path().stat().st_mode & 0o777 == 0o600
    assert saved["active_user_id"] == user_id


def test_corrupt_secrets_are_not_overwritten(client):
    path = storage.secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    response = client.post("/api/users", json={"email": "a@example.com", "password": "x"})
    assert response.status_code == 400
    assert path.read_text(encoding="utf-8") == "{broken"


def test_delete_rejects_a_path_like_id(client):
    assert client.delete("/api/users/not-a-uuid").status_code == 400


def test_sync_does_not_call_garmin_when_network_is_disabled(client):
    client.post("/api/users", json={"email": "runner@example.com", "password": "local-secret-pass"})
    response = client.post("/api/sync")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "佳明" in body["message"]
    assert "local-secret-pass" not in response.text
    assert "client_state" not in body


def test_sync_result_strips_mfa_state():
    public = public_sync_result(
        {"ok": False, "needs_mfa": True, "message": "需要验证码", "client_state": {"cookie": "secret-state"}}
    )
    assert public == {"ok": False, "needs_mfa": True, "message": "需要验证码"}
    assert "secret-state" not in json.dumps(public)


def test_sample_coach_endpoint_never_calls_deepseek(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("deepseek was called")

    monkeypatch.setattr("app.coach.complete", boom)
    client.post("/api/settings/deepseek", json={"api_key": "sk-should-not-send"})
    response = client.get("/api/coach")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == SAMPLE_REASON
    assert "sk-should-not-send" not in response.text


def test_real_bundle_sends_metrics_only(client, monkeypatch):
    captured = {}

    def fake(api_key, payload):
        captured["key"] = api_key
        captured["payload"] = payload
        return "今天按已给出的 TSB 安排轻松跑。"

    monkeypatch.delenv("GARMIN_FITNESS_DISABLE_NETWORK", raising=False)
    monkeypatch.setattr("app.coach.complete", fake)
    created = client.post(
        "/api/users",
        json={"email": "runner@example.com", "password": "local-secret-pass", "display_name": "测试"},
    )
    user_id = created.json()["user"]["id"]
    bundle = build_sample_bundle(date(2026, 11, 26))
    bundle["sample"] = False
    bundle["fetched_at"] = "2026-11-26T12:00:00+08:00"
    storage.save_bundle(user_id, bundle)
    client.post("/api/settings/deepseek", json={"api_key": "sk-unit-test"})
    response = client.get("/api/coach")
    assert response.status_code == 200
    assert response.json()["available"] is True
    blob = json.dumps(captured["payload"], ensure_ascii=False)
    assert captured["key"] == "sk-unit-test"
    assert "local-secret-pass" not in blob
    assert "sk-unit-test" not in blob
    assert "password" not in blob
    assert "runner@example.com" not in blob
    assert "activities" not in captured["payload"]
    assert captured["payload"]["summary"]["ctl"] is not None


def test_complete_is_blocked_by_the_network_guard():
    with pytest.raises(CoachError):
        complete("sk-test", {"summary": {"ctl": 1}})


def test_plotly_is_served_from_the_local_package(client):
    response = client.get("/static/plotly.min.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert len(response.content) > 10_000
