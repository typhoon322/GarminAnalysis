"""Local personal athlete profile — disk only, short text into coach context."""

from __future__ import annotations

from typing import Any

from app.clock import iso_sh

# Storage caps (characters). Coach injection uses a tighter overall budget.
FIELD_CAPS = {
    "goals": 400,
    "preferred_sports": 200,
    "availability": 300,
    "injuries_limitations": 400,
    "feelings_notes": 600,
    "about_me": 800,
}

COACH_FIELD_CAPS = {
    "goals": 220,
    "preferred_sports": 120,
    "availability": 160,
    "injuries_limitations": 220,
    "feelings_notes": 280,
    "about_me": 320,
}

COACH_PROFILE_CHAR_BUDGET = 900

EMPTY_PROFILE = {
    "goals": "",
    "preferred_sports": "",
    "availability": "",
    "injuries_limitations": "",
    "feelings_notes": "",
    "about_me": "",
    "updated_at": None,
}


def _clean_text(value, *, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned


def normalize_athlete_profile(raw: dict | None) -> dict:
    source = raw if isinstance(raw, dict) else {}
    out = dict(EMPTY_PROFILE)
    for key, limit in FIELD_CAPS.items():
        out[key] = _clean_text(source.get(key), limit=limit)
    updated = source.get("updated_at")
    out["updated_at"] = updated if isinstance(updated, str) and updated.strip() else None
    return out


def public_athlete_profile(profile: dict | None) -> dict:
    """Safe for the browser — never includes secrets."""
    data = normalize_athlete_profile(profile)
    return {
        "goals": data["goals"],
        "preferred_sports": data["preferred_sports"],
        "availability": data["availability"],
        "injuries_limitations": data["injuries_limitations"],
        "feelings_notes": data["feelings_notes"],
        "about_me": data["about_me"],
        "updated_at": data["updated_at"],
        "has_content": any(data[key] for key in FIELD_CAPS),
    }


def apply_athlete_profile_update(existing: dict | None, body: dict | None) -> dict:
    current = normalize_athlete_profile(existing)
    incoming = body if isinstance(body, dict) else {}
    for key, limit in FIELD_CAPS.items():
        if key in incoming:
            current[key] = _clean_text(incoming.get(key), limit=limit)
    current["updated_at"] = iso_sh()
    return current


def coach_profile_summary(profile: dict | None) -> dict[str, Any] | None:
    """Capped Chinese block injected into DeepSeek context. None if empty."""
    data = normalize_athlete_profile(profile)
    if not any(data[key] for key in FIELD_CAPS):
        return None

    pieces: list[tuple[str, str, str]] = [
        ("训练目标", "goals", COACH_FIELD_CAPS["goals"]),
        ("偏好运动", "preferred_sports", COACH_FIELD_CAPS["preferred_sports"]),
        ("可训练时间", "availability", COACH_FIELD_CAPS["availability"]),
        ("伤病与限制", "injuries_limitations", COACH_FIELD_CAPS["injuries_limitations"]),
        ("主观感受与RPE笔记", "feelings_notes", COACH_FIELD_CAPS["feelings_notes"]),
        ("对我的描述", "about_me", COACH_FIELD_CAPS["about_me"]),
    ]
    block: dict[str, str] = {}
    used = 0
    for label, key, limit in pieces:
        value = _clean_text(data.get(key), limit=limit)
        if not value:
            continue
        remaining = COACH_PROFILE_CHAR_BUDGET - used
        if remaining <= 0:
            break
        if len(value) > remaining:
            value = value[:remaining].rstrip()
        if not value:
            break
        block[label] = value
        used += len(value)
    if not block:
        return None
    return {
        "说明": "用户本机填写的个人训练描述（短文本）。不是医疗记录，也不是账号密钥。",
        "内容": block,
        "字符占用": used,
    }
