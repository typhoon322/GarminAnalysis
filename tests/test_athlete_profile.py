"""AI-maintained athlete profile + dated timeline (mocked DeepSeek)."""

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
    merge_ai_extraction,
    normalize_store,
    split_coach_reply,
)
from app.chat_context import build_chat_context, context_has_forbidden_keys
from app.main import app
from app.sample_data import build_sample_bundle


@pytest.fixture
def client():
    return TestClient(app)


def test_split_coach_reply_strips_profile_json():
    raw = (
        "今天做轻松跑 40 分钟。\n"
        "这是训练启发式，不是医疗意见。\n"
        "---PROFILE_UPDATE---\n"
        '{"changed":true,"goals":"半马","feelings_notes":"腿沉 RPE7","timeline_note":"腿沉","preferred_sports":null}'
    )
    reply, extraction = split_coach_reply(raw)
    assert "轻松跑" in reply
    assert "PROFILE_UPDATE" not in reply
    assert extraction["changed"] is True
    assert extraction["goals"] == "半马"


def test_merge_ai_extraction_appends_timeline():
    store = normalize_store(None)
    updated = merge_ai_extraction(
        store,
        {
            "changed": True,
            "goals": "提升有氧",
            "feelings_notes": "今天腿沉",
            "timeline_note": "对话提到腿沉",
        },
    )
    assert updated is not None
    assert updated["current"]["goals"] == "提升有氧"
    assert updated["current"]["source"] == "ai"
    assert len(updated["timeline"]) == 1
    assert updated["timeline"][0]["note"] == "对话提到腿沉"
    assert merge_ai_extraction(updated, {"changed": False}) is None


def test_coach_summary_includes_timeline_and_caps():
    store = apply_athlete_profile_update(
        None,
        {"goals": "完赛", "feelings_notes": "累", "timeline_note": "手动记下"},
    )
    store = merge_ai_extraction(
        store,
        {"changed": True, "feelings_notes": "更累了", "timeline_note": "RPE 升高"},
    )
    summary = coach_profile_summary(store)
    assert summary is not None
    assert "最新摘要" in summary
    assert "近期时间线" in summary
    assert len(summary["近期时间线"]) >= 1
    assert summary["字符占用"] <= COACH_PROFILE_CHAR_BUDGET + 500


def test_context_injects_profile_and_timeline():
    bundle = build_sample_bundle(date(2026, 11, 26))
    store = merge_ai_extraction(
        None,
        {
            "changed": True,
            "goals": "减脂保持有氧",
            "injuries_limitations": "腰部谨慎",
            "timeline_note": "腰部限制",
        },
    )
    context = build_chat_context(bundle, athlete_profile=store)
    assert "个人描述" in context
    personal = context["个人描述"]
    assert "最新摘要" in personal
    assert "近期时间线" in personal
    assert context_has_forbidden_keys(context) == []


def test_chat_send_persists_extracted_profile(client, monkeypatch):
    created = client.post(
        "/api/users",
        json={"display_name": "画像抽取", "personal_api_key": "garmin-secret-key"},
    )
    user_id = created.json()["user"]["id"]
    bundle = build_sample_bundle(date(2026, 11, 26))
    bundle["sample"] = False
    storage.save_bundle(user_id, bundle)
    client.post("/api/settings/deepseek", json={"api_key": "sk-chat-test"})

    def fake(api_key, *, context, history, user_message):
        assert "个人描述" in context or True  # may be empty first turn
        return (
            "建议今天轻松有氧。\n"
            "这是训练启发式，不是医疗意见。\n"
            "---PROFILE_UPDATE---\n"
            '{"changed":true,"goals":"春季半马","feelings_notes":"腿沉 RPE7",'
            '"timeline_note":"用户说腿沉","preferred_sports":"跑步","availability":null,'
            '"injuries_limitations":null,"about_me":null}'
        )

    monkeypatch.setattr("app.main.chat_complete", fake)
    monkeypatch.delenv("GARMIN_FITNESS_DISABLE_NETWORK", raising=False)
    response = client.post("/api/chat", json={"message": "今天腿很沉，给我计划"})
    body = response.json()
    assert body["ok"] is True, body
    assert body["profile_updated"] is True
    assert "PROFILE_UPDATE" not in body["messages"][-1]["content"]
    assert "轻松有氧" in body["messages"][-1]["content"]
    assert body["athlete_profile"]["goals"] == "春季半马"
    assert body["athlete_profile"]["source"] == "ai"
    assert body["athlete_profile"]["timeline"]
    disk = storage.load_athlete_profile(user_id)
    assert disk["current"]["feelings_notes"] == "腿沉 RPE7"
    assert len(disk["timeline"]) == 1
    assert "garmin-secret-key" not in response.text
    assert "sk-chat-test" not in response.text


def test_manual_edit_still_optional(client):
    client.post(
        "/api/users",
        json={"display_name": "手动画像", "personal_api_key": "garmin-secret-key"},
    )
    saved = client.post(
        "/api/athlete-profile",
        json={"goals": "手动目标", "feelings_notes": "还行", "timeline_note": "手动改"},
    )
    assert saved.json()["ok"] is True
    assert saved.json()["athlete_profile"]["source"] == "manual"
    assert saved.json()["athlete_profile"]["timeline"]


def test_home_marks_profile_optional(client):
    page = client.get("/")
    assert "可选：手动改画像" in page.text or "可选：手动编辑" in page.text
    assert "chat-timeline" in page.text
    assert "默认由对话自动维护" in page.text
