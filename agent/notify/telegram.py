"""Telegram notifications for the companion.

Config comes from two env vars, not a config file — a bot token is a secret,
and the .gitignore already keeps .env-style files out of the repo (see
vendor/TradingAgents/.env.example for the existing pattern this follows):

    TELEGRAM_BOT_TOKEN   — from @BotFather after /newbot
    TELEGRAM_CHAT_ID     — your own chat id (message the bot once, then hit
                            https://api.telegram.org/bot<token>/getUpdates
                            to read it back)

If either is unset, send_* calls are no-ops (log a warning once, don't
crash the scan that triggered them) — notifications are a nice-to-have on
top of the dashboard, not a dependency the core pipeline should break on.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_warned_missing_config = False


def _get_config() -> tuple[str, str] | None:
    global _warned_missing_config
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        if not _warned_missing_config:
            logger.warning(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — "
                "Telegram notifications are disabled."
            )
            _warned_missing_config = True
        return None
    return token, chat_id


def is_configured() -> bool:
    return _get_config() is not None


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message. Returns True on success, False if unconfigured or the
    send itself failed (network error, bad token) — never raises, since a
    failed notification shouldn't take down the scan that produced it.
    """
    config = _get_config()
    if config is None:
        return False
    token, chat_id = config

    import httpx

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text[:4096],  # Telegram's hard message-length limit
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:300])
            return False
        return True
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False
