"""Local config, Personal API keys, and cached health data.

Secret files are mode 0600. Nothing here is sent anywhere by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from app.clock import iso_sh
from app.sync_policy import (
    DEFAULT_COACH_DAILY_CAP,
    DEFAULT_COACH_MIN_INTERVAL_SECONDS,
    DEFAULT_DAILY_CAP,
    DEFAULT_MIN_INTERVAL_MINUTES,
    clamp_coach_daily_cap,
    clamp_coach_interval_seconds,
    clamp_daily_cap,
    clamp_min_interval_minutes,
    evaluate_coach_rate_limit,
    evaluate_rate_limit,
    next_sync_counters,
)

CHAT_HISTORY_CAP = 40

ROOT = Path(__file__).resolve().parent.parent
USER_ID = re.compile(r"^[a-f0-9]{32}$")
_ENV_KEYS = {"DATA_DIR", "PORT", "DEEPSEEK_API_KEY"}


class StorageError(Exception):
    pass


def load_env_file() -> None:
    """Read the allowlisted keys from a local .env. Never logs values."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in _ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file()


def data_dir() -> Path:
    raw = (os.environ.get("DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (ROOT / "data").resolve()


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod(path, 0o700)


def write_private_json(path: Path, payload: object) -> None:
    ensure_private_dir(path.parent)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        _chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _chmod(path, 0o600)


def secrets_path() -> Path:
    return data_dir() / "secrets.json"


def _empty_secrets() -> dict:
    return {
        "deepseek_api_key": "",
        "active_user_id": None,
        "users": {},
        "sync_min_interval_minutes": DEFAULT_MIN_INTERVAL_MINUTES,
        "sync_daily_cap": DEFAULT_DAILY_CAP,
    }


def load_secrets() -> dict:
    path = secrets_path()
    if not path.exists():
        return _empty_secrets()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageError("data/secrets.json 无法解析，已停止写入以免覆盖。") from exc
    except OSError as exc:
        raise StorageError("无法读取 data/secrets.json。") from exc
    if not isinstance(data, dict):
        raise StorageError("data/secrets.json 格式不对，已停止写入以免覆盖。")
    data.setdefault("deepseek_api_key", "")
    data.setdefault("active_user_id", None)
    data["sync_min_interval_minutes"] = clamp_min_interval_minutes(
        data.get("sync_min_interval_minutes", DEFAULT_MIN_INTERVAL_MINUTES)
    )
    data["sync_daily_cap"] = clamp_daily_cap(data.get("sync_daily_cap", DEFAULT_DAILY_CAP))
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    return data


def save_secrets(data: dict) -> None:
    write_private_json(secrets_path(), data)


def assert_user_id(user_id: str) -> str:
    if not USER_ID.fullmatch(user_id or ""):
        raise StorageError("账号编号不正确。")
    return user_id


def public_user(user: dict) -> dict:
    key = str(user.get("personal_api_key") or "").strip()
    region = str(user.get("region") or "global").strip().lower()
    if region not in {"global", "cn"}:
        region = "global"
    return {
        "id": user.get("id"),
        "display_name": user.get("display_name"),
        "label": user.get("label") or "",
        "has_personal_api_key": bool(key),
        "region": region,
        "sex": user.get("sex") or "unknown",
        "age": user.get("age"),
        "hr_max": user.get("hr_max"),
    }


def list_users() -> list[dict]:
    data = load_secrets()
    users = list(data["users"].values())
    users.sort(key=lambda item: item.get("created_at") or "")
    return [public_user(user) for user in users]


def active_user() -> dict | None:
    data = load_secrets()
    user_id = data.get("active_user_id")
    if not user_id:
        return None
    user = data["users"].get(user_id)
    return user if isinstance(user, dict) else None


def set_active_user(user_id: str) -> dict:
    user_id = assert_user_id(user_id)
    data = load_secrets()
    if user_id not in data["users"]:
        raise StorageError("没有这个账号。")
    data["active_user_id"] = user_id
    save_secrets(data)
    return public_user(data["users"][user_id])


def _clean_name(name: str) -> str:
    text = " ".join((name or "").split())
    return text[:40]


def _optional_number(value, name: str, low: float, high: float, as_int: bool) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StorageError(f"{name} 不是数字。") from exc
    if number != number or number < low or number > high:
        raise StorageError(f"{name} 超出合理范围。")
    if as_int:
        return int(round(number))
    return round(number, 1)


def add_user(
    *,
    display_name: str,
    personal_api_key: str,
    label: str = "",
    region: str = "global",
    sex: str = "unknown",
    age=None,
    hr_max=None,
) -> dict:
    name = _clean_name(display_name)
    if len(name) < 1:
        raise StorageError("请填写配置名称。")
    key = str(personal_api_key or "").strip()
    if not key:
        raise StorageError("请填写 Garmin Personal API Key。密钥只保存在本机。")
    if len(key) > 500:
        raise StorageError("Personal API Key 太长。")
    label_text = " ".join((label or "").split())[:60]
    region_value = str(region or "global").strip().lower()
    if region_value in {"china", "garmin.cn"}:
        region_value = "cn"
    if region_value not in {"global", "cn"}:
        raise StorageError("地区请选国际区或中国区。")
    if sex not in {"male", "female", "unknown"}:
        raise StorageError("性别请选男、女，或留空。")
    age_value = _optional_number(age, "年龄", 10, 100, True)
    hr_value = _optional_number(hr_max, "最大心率", 100, 230, True)
    data = load_secrets()
    existing = None
    for user in data["users"].values():
        if str(user.get("display_name", "")).lower() == name.lower():
            existing = user
            break
    now = iso_sh()
    if existing is None:
        user_id = uuid.uuid4().hex
        record = {
            "id": user_id,
            "display_name": name,
            "label": label_text,
            "personal_api_key": key,
            "region": region_value,
            "sex": sex,
            "age": age_value,
            "hr_max": hr_value,
            "created_at": now,
            "updated_at": now,
        }
        data["users"][user_id] = record
    else:
        existing["display_name"] = name
        existing["label"] = label_text
        existing["personal_api_key"] = key
        existing["region"] = region_value
        existing["sex"] = sex
        existing["age"] = age_value
        existing["hr_max"] = hr_value
        existing["updated_at"] = now
        # Drop legacy password-login fields if an old secrets file is reused.
        for legacy in ("email", "password", "is_cn"):
            existing.pop(legacy, None)
        record = existing
        clear_bearer(record["id"])
    data["active_user_id"] = record["id"]
    save_secrets(data)
    return public_user(record)


def delete_user(user_id: str) -> None:
    user_id = assert_user_id(user_id)
    data = load_secrets()
    if user_id not in data["users"]:
        raise StorageError("没有这个账号。")
    del data["users"][user_id]
    if data.get("active_user_id") == user_id:
        remaining = sorted(data["users"])
        data["active_user_id"] = remaining[0] if remaining else None
    save_secrets(data)
    _remove_tree(data_dir() / "cache" / user_id)
    _remove_tree(data_dir() / "tokens" / user_id)
    mfa = data_dir() / "mfa" / f"{user_id}.json"
    if mfa.exists():
        try:
            mfa.unlink()
        except OSError:
            pass


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


def get_api_key() -> str:
    path = secrets_path()
    if path.exists():
        return str(load_secrets().get("deepseek_api_key") or "").strip()
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def deepseek_source() -> str:
    """file, env, or missing. Never returns the key."""
    path = secrets_path()
    if path.exists():
        return "file" if str(load_secrets().get("deepseek_api_key") or "").strip() else "missing"
    return "env" if (os.environ.get("DEEPSEEK_API_KEY") or "").strip() else "missing"


def set_api_key(api_key: str) -> str:
    if api_key is None:
        api_key = ""
    text = str(api_key).strip()
    if len(text) > 300:
        raise StorageError("密钥太长。")
    data = load_secrets()
    data["deepseek_api_key"] = text
    save_secrets(data)
    return "file" if text else "missing"


def token_dir(user_id: str) -> Path:
    path = data_dir() / "tokens" / assert_user_id(user_id)
    ensure_private_dir(path)
    return path


def bearer_path(user_id: str) -> Path:
    return token_dir(user_id) / "bearer.json"


def api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()


def load_bearer(user_id: str, api_key: str) -> dict | None:
    path = bearer_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("fingerprint") != api_key_fingerprint(api_key):
        return None
    token = str(data.get("access_token") or "").strip()
    try:
        expires_at = float(data.get("expires_at"))
    except (TypeError, ValueError):
        return None
    if not token:
        return None
    return {"access_token": token, "expires_at": expires_at}


def save_bearer(user_id: str, api_key: str, access_token: str, expires_at: float) -> None:
    write_private_json(
        bearer_path(user_id),
        {
            "fingerprint": api_key_fingerprint(api_key),
            "access_token": access_token,
            "expires_at": float(expires_at),
        },
    )


def clear_bearer(user_id: str) -> None:
    path = bearer_path(user_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def bundle_path(user_id: str) -> Path:
    return data_dir() / "cache" / assert_user_id(user_id) / "bundle.json"


def load_bundle(user_id: str) -> dict | None:
    path = bundle_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError("已同步的数据文件无法读取。") from exc
    if not isinstance(data, dict):
        raise StorageError("已同步的数据文件格式不对。")
    return data


def save_bundle(user_id: str, bundle: dict) -> None:
    write_private_json(bundle_path(user_id), bundle)


def athlete_profile_path(user_id: str) -> Path:
    return data_dir() / "cache" / assert_user_id(user_id) / "athlete_profile.json"


def load_athlete_profile(user_id: str) -> dict:
    """Return the full store {current, timeline} for a profile."""
    from app.athlete_profile import empty_store, normalize_store

    path = athlete_profile_path(user_id)
    if not path.is_file():
        return empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError("个人描述文件无法读取。") from exc
    if not isinstance(data, dict):
        raise StorageError("个人描述文件格式不对。")
    return normalize_store(data)


def save_athlete_profile(user_id: str, profile: dict) -> dict:
    from app.athlete_profile import normalize_store, public_athlete_profile

    user_id = assert_user_id(user_id)
    cleaned = normalize_store(profile)
    write_private_json(athlete_profile_path(user_id), cleaned)
    return public_athlete_profile(cleaned)


def get_sync_policy() -> dict:
    data = load_secrets()
    return {
        "min_interval_minutes": clamp_min_interval_minutes(
            data.get("sync_min_interval_minutes", DEFAULT_MIN_INTERVAL_MINUTES)
        ),
        "daily_cap": clamp_daily_cap(data.get("sync_daily_cap", DEFAULT_DAILY_CAP)),
    }


def set_sync_policy(*, min_interval_minutes=None, daily_cap=None) -> dict:
    data = load_secrets()
    if min_interval_minutes is not None:
        data["sync_min_interval_minutes"] = clamp_min_interval_minutes(min_interval_minutes)
    if daily_cap is not None:
        data["sync_daily_cap"] = clamp_daily_cap(daily_cap)
    save_secrets(data)
    return get_sync_policy()


def sync_state_for_user(user_id: str | None) -> dict:
    """Public sync status for the UI (no secrets)."""
    policy = get_sync_policy()
    last_sync_at = None
    sync_day = None
    sync_count_today = 0
    if user_id:
        user_id = assert_user_id(user_id)
        data = load_secrets()
        user = data["users"].get(user_id) or {}
        if isinstance(user, dict):
            last_sync_at = user.get("last_sync_at")
            sync_day = user.get("sync_day")
            try:
                sync_count_today = int(user.get("sync_count_today") or 0)
            except (TypeError, ValueError):
                sync_count_today = 0
        bundle = None
        try:
            bundle = load_bundle(user_id)
        except StorageError:
            bundle = None
        if not last_sync_at and isinstance(bundle, dict):
            last_sync_at = bundle.get("fetched_at")
    status = evaluate_rate_limit(
        last_sync_at=last_sync_at if isinstance(last_sync_at, str) else None,
        sync_day=sync_day if isinstance(sync_day, str) else None,
        sync_count_today=sync_count_today,
        min_interval_minutes=policy["min_interval_minutes"],
        daily_cap=policy["daily_cap"],
    )
    return status


def record_sync_attempt(user_id: str, *, at: str | None = None) -> dict:
    """Mark that we hit Garmin for this user (counts toward rate limits)."""
    user_id = assert_user_id(user_id)
    data = load_secrets()
    user = data["users"].get(user_id)
    if not isinstance(user, dict):
        raise StorageError("没有这个账号。")
    stamp = at or iso_sh()
    day, count = next_sync_counters(
        sync_day=user.get("sync_day") if isinstance(user.get("sync_day"), str) else None,
        sync_count_today=int(user.get("sync_count_today") or 0),
    )
    user["last_sync_at"] = stamp
    user["sync_day"] = day
    user["sync_count_today"] = count
    user["updated_at"] = stamp
    save_secrets(data)
    return sync_state_for_user(user_id)


def chat_path(user_id: str) -> Path:
    return data_dir() / "cache" / assert_user_id(user_id) / "chat.json"


def load_chat(user_id: str) -> dict:
    path = chat_path(user_id)
    if not path.is_file():
        return {"messages": [], "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError("教练对话记录无法读取。") from exc
    if not isinstance(data, dict):
        raise StorageError("教练对话记录格式不对。")
    messages = data.get("messages")
    if not isinstance(messages, list):
        messages = []
    cleaned = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str):
            continue
        cleaned.append(
            {
                "role": role,
                "content": content,
                "at": item.get("at") if isinstance(item.get("at"), str) else None,
            }
        )
    return {"messages": cleaned[-CHAT_HISTORY_CAP:], "updated_at": data.get("updated_at")}


def save_chat(user_id: str, messages: list[dict]) -> dict:
    stamp = iso_sh()
    payload = {"updated_at": stamp, "messages": list(messages)[-CHAT_HISTORY_CAP:]}
    write_private_json(chat_path(user_id), payload)
    return payload


def clear_chat(user_id: str) -> None:
    path = chat_path(user_id)
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            raise StorageError("无法清除教练对话。") from exc


def append_chat_turn(user_id: str, *, user_text: str, assistant_text: str) -> dict:
    chat = load_chat(user_id)
    stamp = iso_sh()
    messages = list(chat.get("messages") or [])
    messages.append({"role": "user", "content": user_text, "at": stamp})
    messages.append({"role": "assistant", "content": assistant_text, "at": stamp})
    return save_chat(user_id, messages)


def get_coach_policy() -> dict:
    data = load_secrets()
    return {
        "min_interval_seconds": clamp_coach_interval_seconds(
            data.get("coach_min_interval_seconds", DEFAULT_COACH_MIN_INTERVAL_SECONDS)
        ),
        "daily_cap": clamp_coach_daily_cap(data.get("coach_daily_cap", DEFAULT_COACH_DAILY_CAP)),
    }


def coach_state_for_user(user_id: str | None) -> dict:
    policy = get_coach_policy()
    last_coach_at = None
    coach_day = None
    coach_count_today = 0
    if user_id:
        user_id = assert_user_id(user_id)
        data = load_secrets()
        user = data["users"].get(user_id) or {}
        if isinstance(user, dict):
            last_coach_at = user.get("last_coach_at")
            coach_day = user.get("coach_day")
            try:
                coach_count_today = int(user.get("coach_count_today") or 0)
            except (TypeError, ValueError):
                coach_count_today = 0
    return evaluate_coach_rate_limit(
        last_coach_at=last_coach_at if isinstance(last_coach_at, str) else None,
        coach_day=coach_day if isinstance(coach_day, str) else None,
        coach_count_today=coach_count_today,
        min_interval_seconds=policy["min_interval_seconds"],
        daily_cap=policy["daily_cap"],
    )


def record_coach_attempt(user_id: str, *, at: str | None = None) -> dict:
    user_id = assert_user_id(user_id)
    data = load_secrets()
    user = data["users"].get(user_id)
    if not isinstance(user, dict):
        raise StorageError("没有这个账号。")
    stamp = at or iso_sh()
    day, count = next_sync_counters(
        sync_day=user.get("coach_day") if isinstance(user.get("coach_day"), str) else None,
        sync_count_today=int(user.get("coach_count_today") or 0),
    )
    user["last_coach_at"] = stamp
    user["coach_day"] = day
    user["coach_count_today"] = count
    user["updated_at"] = stamp
    save_secrets(data)
    return coach_state_for_user(user_id)
