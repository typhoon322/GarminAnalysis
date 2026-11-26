"""Personal athlete profile: local disk only, capped coach injection."""

from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.athlete_profile import (
    COACH_PROFILE_CHAR_BUDGET,
    apply_athlete_profile_update,
    coach_profile_summary,
    normalize_athlete_profile,
)
from app.chat_context import build_chat_context, context_has_forbidden_keys
from app.main import app
from app.sample_data import build_sample_bundle


@pytest.fixture
def client():
    return TestClient(app)


def test_normalize_and_coach_summary_caps():
    raw = {
        "goals": "半马 " + ("目标" * 200),
        "preferred_sports": "跑步",
        "availability": "周末",
        "injuries_limitations": "右膝",
        "feelings_notes": "RPE 7",
        "about_me": "业余跑者 " + ("描述" * 300),
        "password": "should-be-dropped",
        "personal_api_key": "nope",
    }
    cleaned = normalize_athlete_profile(raw)
    assert "password" not in cleaned
    assert len(cleaned["goals"]) <= 400
    assert len(cleaned["about_me"]) <= 800
    summary = coach_profile_summary(cleaned)
    assert summary is not None
    assert summary["字符占用"] <= COACH_PROFILE_CHAR_BUDGET
    assert "训练目标" in summary["内容"]
    assert "password" not in json.dumps(summary)


def test_empty_profile_omitted_from_context():
    bundle = build_sample_bundle(date(2026, 11, 26))
    context = build_chat_context(bundle, athlete_profile=normalize_athlete_profile(None))
    assert "个人描述" not in context


def test_profile_injected_into_chat_context_without_secrets():
    bundle = build_sample_bundle(date(2026, 11, 26))
    profile = apply_athlete_profile_update(
        None,
        {
            "goals": "提升有氧基础",
            "preferred_sports": "跑步、骑行",
            "availability": "工作日晚 1 小时",
            "injuries_limitations": "左跟腱需热身",
            "feelings_notes": "本周睡眠一般，RPE 偏高",
            "about_me": "备战春季半马",
        },
    )
    context = build_chat_context(bundle, athlete_profile=profile)
    assert "个人描述" in context
    assert context["个人描述"]["内容"]["训练目标"] == "提升有氧基础"
    assert context["个人描述"]["内容"]["伤病与限制"] == "左跟腱需热身"
    assert context_has_forbidden_keys(context) == []
    blob = json.dumps(context, ensure_ascii=False)
    assert "personal_api_key" not in blob
    assert "password" not in blob


def test_athlete_profile_api_persists_per_user(client, monkeypatch):
    created = client.post(
        "/api/users",
        json={"display_name": "画像用户", "personal_api_key": "garmin-secret-key"},
    )
    user_id = created.json()["user"]["id"]
    saved = client.post(
        "/api/athlete-profile",
        json={
            "goals": "完赛马拉松",
            "preferred_sports": "跑步",
            "availability": "周二周四晚",
            "injuries_limitations": "",
            "feelings_notes": "今天腿沉",
            "about_me": "周末长距离",
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["athlete_profile"]["goals"] == "完赛马拉松"
    assert body["athlete_profile"]["has_content"] is True
    assert "garmin-secret-key" not in saved.text

    path = storage.athlete_profile_path(user_id)
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["goals"] == "完赛马拉松"
    assert "personal_api_key" not in disk

    fetched = client.get("/api/athlete-profile").json()
    assert fetched["athlete_profile"]["feelings_notes"] == "今天腿沉"
    status = client.get("/api/status").json()
    assert status["athlete_profile"]["goals"] == "完赛马拉松"
    assert "garmin-secret-key" not in client.get("/").text


def test_chat_send_includes_profile_in_mocked_context(client, monkeypatch):
    created = client.post(
        "/api/users",
        json={"display_name": "教练画像", "personal_api_key": "garmin-secret-key"},
    )
    user_id = created.json()["user"]["id"]
    bundle = build_sample_bundle(date(2026, 11, 26))
    bundle["sample"] = False
    storage.save_bundle(user_id, bundle)
    storage.save_athlete_profile(
        user_id,
        apply_athlete_profile_update(None, {"goals": "减脂保持有氧", "injuries_limitations": "腰部谨慎负重"}),
    )
    client.post("/api/settings/deepseek", json={"api_key": "sk-chat-test"})
    captured = {}

    def fake(api_key, *, context, history, user_message):
        captured["context"] = context
        return "按你的目标安排轻松有氧，并避开腰部负重。"

    monkeypatch.setattr("app.main.chat_complete", fake)
    monkeypatch.delenv("GARMIN_FITNESS_DISABLE_NETWORK", raising=False)
    response = client.post("/api/chat", json={"message": "今天练什么"})
    assert response.json()["ok"] is True
    assert "个人描述" in captured["context"]
    assert "减脂保持有氧" in json.dumps(captured["context"], ensure_ascii=False)
    assert "腰部谨慎负重" in json.dumps(captured["context"], ensure_ascii=False)
    assert "garmin-secret-key" not in json.dumps(captured["context"])
    assert "sk-chat-test" not in json.dumps(captured["context"])


def test_home_shows_athlete_profile_editors(client):
    page = client.get("/")
    assert 'id="athlete-settings"' in page.text
    assert 'id="chat-profile-box"' in page.text
    assert "个人描述" in page.text
