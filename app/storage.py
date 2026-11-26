"""Local config, passwords, and cached health data.

Secret files are mode 0600. Nothing here is sent anywhere by itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path

from app.clock import iso_sh

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
    return {"deepseek_api_key": "", "active_user_id": None, "users": {}}


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
    return {
        "id": user.get("id"),
        "email": user.get("email"),
        "display_name": user.get("display_name"),
        "is_cn": bool(user.get("is_cn")),
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


def _clean_display_name(name: str, email: str) -> str:
    text = " ".join((name or "").split())
    if not text:
        text = email.split("@", 1)[0] or email
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
    email: str,
    password: str,
    display_name: str = "",
    is_cn: bool = False,
    sex: str = "unknown",
    age=None,
    hr_max=None,
) -> dict:
    cleaned = (email or "").strip()
    if len(cleaned) < 3 or len(cleaned) > 200 or any(ch.isspace() for ch in cleaned):
        raise StorageError("请填写邮箱或手机号。")
    if password is None or str(password) == "":
        raise StorageError("请填写密码。密码只保存在本机。")
    if len(str(password)) > 200:
        raise StorageError("密码太长。")
    if sex not in {"male", "female", "unknown"}:
        raise StorageError("性别请选男、女，或留空。")
    age_value = _optional_number(age, "年龄", 10, 100, True)
    hr_value = _optional_number(hr_max, "最大心率", 100, 230, True)
    data = load_secrets()
    existing = None
    for user in data["users"].values():
        if str(user.get("email", "")).lower() == cleaned.lower():
            existing = user
            break
    now = iso_sh()
    if existing is None:
        user_id = uuid.uuid4().hex
        record = {
            "id": user_id,
            "email": cleaned,
            "display_name": _clean_display_name(display_name, cleaned),
            "password": str(password),
            "is_cn": bool(is_cn),
            "sex": sex,
            "age": age_value,
            "hr_max": hr_value,
            "created_at": now,
            "updated_at": now,
        }
        data["users"][user_id] = record
    else:
        existing["email"] = cleaned
        existing["display_name"] = _clean_display_name(display_name, cleaned)
        existing["password"] = str(password)
        existing["is_cn"] = bool(is_cn)
        existing["sex"] = sex
        existing["age"] = age_value
        existing["hr_max"] = hr_value
        existing["updated_at"] = now
        record = existing
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
    mfa = _mfa_path(user_id)
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


def _mfa_path(user_id: str) -> Path:
    return data_dir() / "mfa" / f"{assert_user_id(user_id)}.json"


def save_mfa(user_id: str, state: object) -> None:
    write_private_json(_mfa_path(user_id), {"client_state": state})


def load_mfa(user_id: str) -> object | None:
    path = _mfa_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and "client_state" in data:
        return data["client_state"]
    return None


def has_mfa(user_id: str) -> bool:
    try:
        return _mfa_path(user_id).is_file()
    except StorageError:
        return False


def clear_mfa(user_id: str) -> None:
    path = _mfa_path(user_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
