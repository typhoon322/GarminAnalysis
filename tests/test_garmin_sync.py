"""Personal API client with mocked token exchange and JSON fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app import garmin_sync, storage

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _token_body() -> str:
    return (FIXTURES / "access_token.txt").read_text(encoding="utf-8").strip()


class FakeTransport(httpx.BaseTransport):
    def __init__(self):
        self.token_posts = 0
        self.gets: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/personal-api-key/token"):
            self.token_posts += 1
            assert request.headers.get("Authorization", "").startswith("ApiKey ")
            return httpx.Response(200, text=_token_body())
        self.gets.append(path + (("?" + request.url.query.decode()) if request.url.query else ""))
        if path.endswith("/search/activities"):
            return httpx.Response(200, json=_load_json("activities.json"))
        if path.endswith("/dailysummaries"):
            return httpx.Response(200, json=_load_json("daily_summaries.json"))
        if path.endswith("/dailySleepSummariesByDate"):
            return httpx.Response(200, json=_load_json("sleep_summaries.json"))
        if "/hrv/daily/" in path:
            return httpx.Response(200, json=_load_json("hrv_summaries.json"))
        if path.endswith("/bodyBattery/reports/daily"):
            return httpx.Response(200, json=_load_json("body_battery.json"))
        if "/stats/stress/daily/" in path:
            return httpx.Response(200, json=_load_json("stress.json"))
        if "/stats/steps/daily/" in path:
            return httpx.Response(200, json=_load_json("steps.json"))
        if path.endswith("/userprofile/settings"):
            return httpx.Response(200, json=_load_json("profile_settings.json"))
        if "/maxmet/daily/" in path:
            return httpx.Response(200, json=_load_json("vo2max.json"))
        if "/trainingstatus/daily/" in path:
            return httpx.Response(200, json=_load_json("training_status.json"))
        if path.endswith("/dailyHeartRate"):
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected {path}")


@pytest.fixture
def fake_http(monkeypatch):
    transport = FakeTransport()
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = transport
        kwargs.setdefault("base_url", garmin_sync.BASE_URL)
        return real_client(*args, **kwargs)

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url, headers=kwargs.get("headers"))
        return transport.handle_request(request)

    monkeypatch.setattr(garmin_sync.httpx, "Client", fake_client)
    monkeypatch.setattr(garmin_sync.httpx, "post", fake_post)
    monkeypatch.delenv("GARMIN_FITNESS_DISABLE_NETWORK", raising=False)
    return transport


def test_exchange_and_fetch_bundle_with_fixtures(fake_http, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    user = storage.add_user(
        display_name="fixture-runner",
        personal_api_key="test-personal-api-key",
        sex="male",
        age=35,
        hr_max=185,
    )
    full = storage.active_user()
    assert full is not None
    result = garmin_sync.sync_account(full)
    assert result["ok"] is True, result["message"]
    bundle = result["bundle"]
    assert bundle["sample"] is False
    assert len(bundle["activities"]) == 1
    assert bundle["activities"][0]["training_load"] == 85
    days = {row["date"]: row for row in bundle["days"]}
    assert days["2026-11-20"]["resting_hr"] == 52
    assert days["2026-11-20"]["hrv"] == 48
    assert days["2026-11-20"]["sleep_seconds"] == 27000
    assert days["2026-11-20"]["body_battery"] == 61
    assert days["2026-11-20"]["stress"] == 28
    assert bundle["profile"]["vo2max_running"] == 49.2
    assert bundle["profile"]["training_status"] == "PRODUCTIVE"
    assert bundle["profile"]["garmin_name"] == "测试跑者"
    assert fake_http.token_posts == 1
    # Second sync should reuse cached bearer (still >1h remaining).
    result2 = garmin_sync.sync_account(storage.active_user())
    assert result2["ok"] is True
    assert fake_http.token_posts == 1
    public = garmin_sync.public_sync_result(result)
    assert "bundle" not in public
    assert "test-personal-api-key" not in json.dumps(public)


def test_token_refresh_when_near_expiry(fake_http, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    storage.add_user(display_name="near-expiry", personal_api_key="key-near-expiry")
    user = storage.active_user()
    storage.save_bearer(user["id"], "key-near-expiry", _token_body(), expires_at=10.0)
    token = garmin_sync.obtain_access_token(user, now=100.0)
    assert token == _token_body()
    assert fake_http.token_posts == 1
