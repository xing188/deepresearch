from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(os.getenv('RESEARCH_CONFIG_FILE', str(ROOT / '.env')))
LOCK = threading.Lock()
FIELDS = {
    "base_url": "RESEARCH_API_BASE",
    "model": "RESEARCH_MODEL",
    "api_key": "RESEARCH_API_KEY",
    "search_key": "TAVILY_API_KEY",
}


def config_file() -> Path:
    from user_accounts import CURRENT_USER
    from research_store import DB_FILE
    account = CURRENT_USER.get()
    if account:
        return DB_FILE.parent / 'user-settings' / (account['id'] + '.env')
    return ENV_FILE


def settings() -> dict:
    from user_accounts import CURRENT_USER
    account = CURRENT_USER.get()
    path = config_file()
    # Only the original administrator can inherit the previous single-user settings.
    legacy = not account or (account['role'] == 'admin' and not path.exists())
    source = ENV_FILE if legacy else path
    values = dotenv_values(source, interpolate=False) if source.exists() else {}
    def value(name, default=''):
        return values.get(name) or (os.getenv(name, default) if legacy else default)
    return {
        "base_url": value("RESEARCH_API_BASE", "https://api.deepseek.com/v1"),
        "model": value("RESEARCH_MODEL", "deepseek-chat"),
        "api_key": value("RESEARCH_API_KEY"),
        "search_key": value("TAVILY_API_KEY"),
    }


def public_settings() -> dict:
    current = settings()
    return {
        "base_url": current["base_url"],
        "model": current["model"],
        "api_key_set": bool(current["api_key"]),
        "search_key_set": bool(current["search_key"]),
        "ready": bool(current["api_key"] and current["search_key"]),
    }


def save_settings(payload: dict) -> dict:
    with LOCK:
        current = settings()
        for name in FIELDS:
            if name in payload and (name not in {"api_key", "search_key"} or payload[name].strip()):
                current[name] = payload[name].strip()
        parsed = urlparse(current["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API 地址必须是有效的 HTTP/HTTPS 地址，且不能包含用户名、密码或查询参数。")
        current["base_url"] = current["base_url"].rstrip("/")
        if not current["model"] or len(current["model"]) > 160:
            raise ValueError("请填写服务商提供的模型 ID。")
        if any("\n" in value or "\r" in value or len(value) > 4096 for value in current.values()):
            raise ValueError("配置内容不能包含换行，且长度不能超过 4096 字符。")
        # JSON strings are valid quoted dotenv values and escape special characters.
        text = "# Local credentials. Do not commit this file.\n"
        text += "\n".join(f"{env}={json.dumps(current[name], ensure_ascii=False)}" for name, env in FIELDS.items()) + "\n"
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path != ENV_FILE:
            path.parent.chmod(0o700)
        temporary = path.with_suffix('.env.tmp')
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    return public_settings()


def redact(text: str, current: dict | None = None) -> str:
    result = str(text)
    for value in (current or settings()).values():
        if value and len(value) > 8 and value in result:
            result = result.replace(value, "[redacted]")
    return result
