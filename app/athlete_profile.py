"""Local athlete profile: AI-maintained from coach chat, optional manual edit.

Stored only under data/cache/<profile>/. A capped summary + recent timeline
are injected into DeepSeek context — never secrets.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.clock import iso_sh

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

COACH_PROFILE_CHAR_BUDGET = 700
COACH_TIMELINE_CHAR_BUDGET = 500
TIMELINE_NOTE_CAP = 240
TIMELINE_KEEP = 60
TIMELINE_INJECT = 8

PROFILE_MARKER = "---PROFILE_UPDATE---"
PROFILE_MARKER_RE = re.compile(
    r"\n*\s*---PROFILE_UPDATE---\s*\n(?P<body>\{.*\})\s*\Z",
    re.DOTALL,
)

EMPTY_FIELDS = {key: "" for key in FIELD_CAPS}


def _clean_text(value, *, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    cleaned = "\n".join(lines).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned


def _fields_from(raw: dict | None) -> dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    return {key: _clean_text(source.get(key), limit=limit) for key, limit in FIELD_CAPS.items()}


def empty_store() -> dict:
    return {
        "current": {
            **EMPTY_FIELDS,
            "updated_at": None,
            "source": None,
        },
        "timeline": [],
    }


def normalize_store(raw: dict | None) -> dict:
    """Accept legacy flat profiles or {current, timeline} stores."""
    if not isinstance(raw, dict):
        return empty_store()
    if "current" in raw or "timeline" in raw:
        current_raw = raw.get("current") if isinstance(raw.get("current"), dict) else {}
        current = {
            **_fields_from(current_raw),
            "updated_at": current_raw.get("updated_at")
            if isinstance(current_raw.get("updated_at"), str)
            else None,
            "source": current_raw.get("source")
            if current_raw.get("source") in {"ai", "manual"}
            else None,
        }
        timeline = []
        for item in raw.get("timeline") or []:
            parsed = _normalize_timeline_entry(item)
            if parsed:
                timeline.append(parsed)
        return {"current": current, "timeline": timeline[-TIMELINE_KEEP:]}

    # Legacy flat file from the first personal-description version.
    current = {
        **_fields_from(raw),
        "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
        "source": "manual" if any(_fields_from(raw).values()) else None,
    }
    return {"current": current, "timeline": []}


def _normalize_timeline_entry(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    at = item.get("at")
    if not isinstance(at, str) or not at.strip():
        return None
    note = _clean_text(item.get("note"), limit=TIMELINE_NOTE_CAP)
    source = item.get("source") if item.get("source") in {"ai", "manual"} else "ai"
    snapshot = _fields_from(item.get("snapshot") if isinstance(item.get("snapshot"), dict) else item)
    return {"at": at.strip(), "source": source, "note": note, "snapshot": snapshot}


def normalize_athlete_profile(raw: dict | None) -> dict:
    """Return the current field blob (compat helper)."""
    store = normalize_store(raw)
    return dict(store["current"])


def public_athlete_profile(store_or_profile: dict | None) -> dict:
    store = normalize_store(store_or_profile)
    current = store["current"]
    timeline = store["timeline"][-12:]
    return {
        "goals": current.get("goals") or "",
        "preferred_sports": current.get("preferred_sports") or "",
        "availability": current.get("availability") or "",
        "injuries_limitations": current.get("injuries_limitations") or "",
        "feelings_notes": current.get("feelings_notes") or "",
        "about_me": current.get("about_me") or "",
        "updated_at": current.get("updated_at"),
        "source": current.get("source"),
        "has_content": any(current.get(key) for key in FIELD_CAPS),
        "timeline": [
            {
                "at": item["at"],
                "source": item["source"],
                "note": item["note"],
            }
            for item in timeline
        ],
        "maintained_by": "ai_default",
    }


def apply_athlete_profile_update(existing: dict | None, body: dict | None) -> dict:
    """Manual edit: replace current fields and append a timeline snapshot."""
    store = normalize_store(existing)
    incoming = body if isinstance(body, dict) else {}
    current = dict(store["current"])
    for key, limit in FIELD_CAPS.items():
        if key in incoming:
            current[key] = _clean_text(incoming.get(key), limit=limit)
    stamp = iso_sh()
    current["updated_at"] = stamp
    current["source"] = "manual"
    note = _clean_text(incoming.get("timeline_note") or "手动更新个人描述", limit=TIMELINE_NOTE_CAP)
    entry = {
        "at": stamp,
        "source": "manual",
        "note": note or "手动更新个人描述",
        "snapshot": {key: current.get(key) or "" for key in FIELD_CAPS},
    }
    timeline = list(store["timeline"])
    timeline.append(entry)
    return {"current": current, "timeline": timeline[-TIMELINE_KEEP:]}


def merge_ai_extraction(existing: dict | None, extraction: dict | None) -> dict | None:
    """Merge model-extracted fields. Returns new store if anything changed, else None."""
    if not isinstance(extraction, dict):
        return None
    if extraction.get("changed") is False:
        return None
    store = normalize_store(existing)
    current = dict(store["current"])
    changed = False
    for key, limit in FIELD_CAPS.items():
        if key not in extraction:
            continue
        value = extraction.get(key)
        if value is None:
            continue
        cleaned = _clean_text(value, limit=limit)
        if cleaned != (current.get(key) or ""):
            current[key] = cleaned
            changed = True
    note = _clean_text(extraction.get("timeline_note") or extraction.get("note"), limit=TIMELINE_NOTE_CAP)
    if not changed and not note:
        return None
    stamp = iso_sh()
    current["updated_at"] = stamp
    current["source"] = "ai"
    entry = {
        "at": stamp,
        "source": "ai",
        "note": note or "对话中更新了个人描述",
        "snapshot": {key: current.get(key) or "" for key in FIELD_CAPS},
    }
    timeline = list(store["timeline"])
    timeline.append(entry)
    return {"current": current, "timeline": timeline[-TIMELINE_KEEP:]}


def split_coach_reply(raw_text: str) -> tuple[str, dict | None]:
    """Strip trailing PROFILE_UPDATE JSON from the model reply."""
    text = str(raw_text or "").strip()
    if not text:
        return "", None
    match = PROFILE_MARKER_RE.search(text)
    if not match:
        if PROFILE_MARKER in text:
            head, _, tail = text.rpartition(PROFILE_MARKER)
            reply = head.strip()
            try:
                payload = json.loads(tail.strip())
            except json.JSONDecodeError:
                return text, None
            return reply, payload if isinstance(payload, dict) else None
        return text, None
    reply = text[: match.start()].strip()
    try:
        payload = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return text, None
    return reply, payload if isinstance(payload, dict) else None


def coach_profile_summary(store_or_profile: dict | None) -> dict[str, Any] | None:
    """Capped latest profile + recent timeline for DeepSeek context."""
    store = normalize_store(store_or_profile)
    current = store["current"]
    if not any(current.get(key) for key in FIELD_CAPS) and not store["timeline"]:
        return None

    pieces = [
        ("训练目标", "goals", COACH_FIELD_CAPS["goals"]),
        ("偏好运动", "preferred_sports", COACH_FIELD_CAPS["preferred_sports"]),
        ("可训练时间", "availability", COACH_FIELD_CAPS["availability"]),
        ("伤病与限制", "injuries_limitations", COACH_FIELD_CAPS["injuries_limitations"]),
        ("主观感受与RPE", "feelings_notes", COACH_FIELD_CAPS["feelings_notes"]),
        ("对我的描述", "about_me", COACH_FIELD_CAPS["about_me"]),
    ]
    block: dict[str, str] = {}
    used = 0
    for label, key, limit in pieces:
        value = _clean_text(current.get(key), limit=limit)
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

    timeline_out = []
    t_used = 0
    for item in reversed(store["timeline"][-TIMELINE_INJECT:]):
        note = item.get("note") or ""
        line = f"{item.get('at', '')} · {item.get('source', '')} · {note}"
        line = _clean_text(line, limit=160)
        if not line:
            continue
        if t_used + len(line) > COACH_TIMELINE_CHAR_BUDGET:
            break
        timeline_out.append(
            {
                "时间": item.get("at"),
                "来源": item.get("source"),
                "笔记": item.get("note"),
            }
        )
        t_used += len(line)
    timeline_out.reverse()

    if not block and not timeline_out:
        return None
    return {
        "说明": (
            "长期本机运动员画像：默认由教练对话自动抽取更新；用户也可手动改。"
            "含最新摘要与近期时间线条目，便于观察身体/感受变化。不是医疗记录，不含密钥。"
        ),
        "维护方式": current.get("source") or "未知",
        "更新时间": current.get("updated_at"),
        "最新摘要": block,
        "近期时间线": timeline_out,
        "字符占用": used + t_used,
    }
