"""User-editable runtime settings: LLM provider/model choice and Telegram
credentials, stored in a local JSON file so they can be set from the
dashboard's Settings page instead of only via environment variables.

Precedence: an explicit environment variable always wins (useful for
scripted/CI runs), falling back to this file, falling back to the hardcoded
defaults already used throughout agent/pipeline.py. This mirrors the
override pattern TradingAgents itself uses for its own env vars (see
vendor/TradingAgents/tradingagents/default_config.py's _apply_env_overrides).

Secrets note: this file holds a Telegram bot token in plaintext, same as
any local .env file would — acceptable for a single-user local tool but
worth knowing if this project ever runs somewhere shared. It's covered by
the repo's existing .gitignore entry for data/ contents.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"
_lock = threading.Lock()

# Ollama models actually verified working in this project (see project
# history — kimi-k2.6:cloud requires a paid subscription on this account,
# qwen2.5-coder:7b is a local coding model unsuited to financial reasoning).
KNOWN_OLLAMA_MODELS = ["gpt-oss:120b-cloud", "qwen2.5-coder:7b", "kimi-k2.6:cloud"]


@dataclass
class Settings:
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434/v1"
    deep_think_model: str = "gpt-oss:120b-cloud"
    quick_think_model: str = "gpt-oss:120b-cloud"
    # Only relevant when llm_provider is a hosted API, not "ollama" — see
    # vendor/TradingAgents/tradingagents/llm_clients/api_key_env.py for the
    # full provider list TradingAgents itself supports (openai, anthropic,
    # google, xai, deepseek, etc.). Stored here so the dashboard can hold a
    # key without the user editing a .env file by hand.
    llm_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    max_concurrent_analyses: int = 4


def _read_raw() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_settings() -> Settings:
    with _lock:
        raw = _read_raw()
    defaults = Settings()
    merged = {**asdict(defaults), **{k: v for k, v in raw.items() if k in asdict(defaults)}}
    return Settings(**merged)


def update_settings(**kwargs) -> Settings:
    with _lock:
        raw = _read_raw()
        raw.update({k: v for k, v in kwargs.items() if v is not None})
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2))
    return get_settings()


def resolve_llm_config() -> dict:
    """What agent/pipeline.py should actually use: env var > settings file > default."""
    s = get_settings()
    return {
        "llm_provider": os.environ.get("TRADINGAGENTS_LLM_PROVIDER") or s.llm_provider,
        "deep_think_llm": os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM") or s.deep_think_model,
        "quick_think_llm": os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM") or s.quick_think_model,
    }


def resolve_telegram_config() -> tuple[str, str] | None:
    """(token, chat_id) from env vars first, then the settings file. None if unset."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or get_settings().telegram_bot_token
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or get_settings().telegram_chat_id
    if not token or not chat_id:
        return None
    return token, chat_id
