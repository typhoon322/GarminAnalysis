"""Coach chat context, history, and rate limits — DeepSeek is always mocked."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.chat_context import CONTEXT_DOC, build_chat_context, context_has_forbidden_keys
from app.clock import SHANGHAI, now_sh
from app.coach import CHAT_MISSING_REASON, CHAT_SAMPLE_REASON, chat_complete
from app.main import app
from app.sample_data import build_sample_bundle
from app.sync_policy import evaluate_coach_rate_limit


@pytest.fixture
def client():
    return TestClient(app)


def test_chat_context_is_compressed_and_secret_free():
    bundle = build_sample_bundle(date(2026, 11, 26))
    context = build_chat_context(bundle)
    assert context["摘要说明"] == CONTEXT_DOC
    assert "负荷状态" in context
    assert context["负荷状态"]["CTL"] is not None
    assert "生理对比基线" in context
    assert "训练量" in context
    assert "近7天" in context["训练量"]
    assert "近28天" in context["训练量"]
    assert isinstance(context["关键警报"], list)
    assert len(context["最近几课"]) <= 5
    blob = json.dumps(context, ensure_ascii=False)
    # Must not dump full daily series arrays from the analysis report.
    assert '"dates"' not in blob
    assert '"ctl": [' not in blob
    assert context_has_forbidden_keys(context) == []
    assert "password" not in blob.lower()
    assert "personal_api_key" not in blob.lower()


def test_chat_complete_payload_never_embeds_secrets(monkeypatch):
    captured = {}

    def fake_post(api_key, messages, temperature=0.3):
        captured["key"] = api_key
        captured["messages"] = messages
        captured["temperature"] = temperature
        return "今天做轻松有氧 40 分钟，引用 TSB。"

    monkeypatch.setattr("app.coach._post_chat", fake_post)
    context = {
        "负荷状态": {"CTL": 40, "TSB": -5},
        "摘要说明": CONTEXT_DOC,
    }
    history = [{"role": "user", "content": "昨天怎么样"}, {"role": "assistant", "content": "恢复一般"}]
    text = chat_complete(
        "sk-secret-should-not-appear-in-messages",
        context=context,
        history=history,
        user_message="给我一个今天的训练计划",
    )
    assert "轻松有氧" in text
    blob = json.dumps(captured["messages"], ensure_ascii=False)
    assert "sk-secret-should-not-appear-in-messages" not in blob
    assert "password" not in blob.lower()
    assert CONTEXT_DOC in blob
    assert any("今天的训练计划" in msg["content"] for msg in captured["messages"])


def test_chat_api_sample_blocks_deepseek(client, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("deepseek should not be called for sample")

    monkeypatch.setattr("app.main.chat_complete", boom)
    client.post("/api/settings/deepseek", json={"api_key": "sk-unit"})
    # No synced user bundle → sample view.
    response = client.get("/api/chat")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["reason"] == CHAT_SAMPLE_REASON

    client.post(
        "/api/users",
        json={"display_name": "chat-user", "personal_api_key": "garmin-key-local"},
    )
    # Still sample until a real bundle is saved.
    blocked = client.post("/api/chat", json={"message": "今天练什么"})
    assert blocked.json()["ok"] is False
    assert CHAT_SAMPLE_REASON in blocked.json()["message"]


def test_chat_api_missing_key_and_history(client, monkeypatch):
    created = client.post(
        "/api/users",
        json={"display_name": "chat-key", "personal_api_key": "garmin-key-local"},
    )
    user_id = created.json()["user"]["id"]
    bundle = build_sample_bundle(date(2026, 11, 26))
    bundle["sample"] = False
    bundle["fetched_at"] = "2026-11-26T12:00:00+08:00"
    storage.save_bundle(user_id, bundle)

    missing = client.get("/api/chat").json()
    assert missing["ready"] is False
    assert missing["reason"] == CHAT_MISSING_REASON

    client.post("/api/settings/deepseek", json={"api_key": "sk-chat-test"})
    calls = {"n": 0}

    def fake(api_key, *, context, history, user_message):
        calls["n"] += 1
        assert api_key == "sk-chat-test"
        assert "负荷状态" in context
        assert "sk-chat-test" not in json.dumps(context)
        assert "garmin-key-local" not in json.dumps(context)
        assert user_message.startswith("今天")
        return "建议轻松跑 30 分钟，TSB 仍偏累。"

    monkeypatch.setattr("app.main.chat_complete", fake)
    monkeypatch.delenv("GARMIN_FITNESS_DISABLE_NETWORK", raising=False)

    sent = client.post("/api/chat", json={"message": "今天该怎么练？"})
    assert sent.status_code == 200
    body = sent.json()
    assert body["ok"] is True, body
    assert calls["n"] == 1
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "assistant"
    assert "轻松跑" in body["messages"][1]["content"]
    assert "sk-chat-test" not in sent.text
    assert "garmin-key-local" not in sent.text

    cleared = client.delete("/api/chat")
    assert cleared.json()["messages"] == []
    assert storage.load_chat(user_id)["messages"] == []


def test_chat_rate_limit_blocks_without_calling_model(client, monkeypatch):
    created = client.post(
        "/api/users",
        json={"display_name": "chat-limit", "personal_api_key": "garmin-key-local"},
    )
    user_id = created.json()["user"]["id"]
    bundle = build_sample_bundle(date(2026, 11, 26))
    bundle["sample"] = False
    storage.save_bundle(user_id, bundle)
    client.post("/api/settings/deepseek", json={"api_key": "sk-chat-test"})
    storage.record_coach_attempt(user_id, at=now_sh().isoformat(timespec="seconds"))

    def boom(*_a, **_k):
        raise AssertionError("should not call model while rate limited")

    monkeypatch.setattr("app.main.chat_complete", boom)
    monkeypatch.delenv("GARMIN_FITNESS_DISABLE_NETWORK", raising=False)
    response = client.post("/api/chat", json={"message": "再问一次"})
    body = response.json()
    assert body["ok"] is False
    assert body.get("rate_limited") is True
    assert "秒" in body["message"]


def test_coach_rate_limit_helper_chinese():
    now = datetime(2026, 11, 26, 22, 0, tzinfo=SHANGHAI)
    status = evaluate_coach_rate_limit(
        last_coach_at="2026-11-26T21:59:50+08:00",
        coach_day="2026-11-26",
        coach_count_today=1,
        min_interval_seconds=20,
        daily_cap=40,
        now=now,
    )
    assert status["can_chat"] is False
    assert "秒" in status["message"]


def test_home_has_coach_tab(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="tab-chat"' in page.text
    assert 'id="panel-chat"' in page.text
    assert "教练对话" in page.text
