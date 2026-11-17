"""Telegram + ntfy fan-out. Each channel is best-effort; one failing never
blocks the other."""
from __future__ import annotations

import os
from typing import Literal

import httpx

Priority = Literal["low", "normal", "high", "max"]


def _log(msg: str) -> None:
    print(f"[notify] {msg}")


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        _log("telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=10.0,
        )
        if r.status_code == 200:
            return True
        _log(f"telegram error {r.status_code}: {r.text[:200]}")
    except httpx.HTTPError as e:
        _log(f"telegram http error: {e}")
    return False


def send_ntfy(text: str, title: str = "", priority: Priority = "normal", tags: str = "") -> bool:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        _log("ntfy skipped: NTFY_TOPIC not set")
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
    headers = {}
    if title:
        headers["Title"] = title
    if priority:
        headers["Priority"] = priority
    if tags:
        headers["Tags"] = tags
    try:
        r = httpx.post(
            f"{server}/{topic}",
            content=text.encode("utf-8"),
            headers=headers,
            timeout=10.0,
        )
        if r.status_code in (200, 201):
            return True
        _log(f"ntfy error {r.status_code}: {r.text[:200]}")
    except httpx.HTTPError as e:
        _log(f"ntfy http error: {e}")
    return False


def send_alarm(title: str, body: str) -> dict[str, bool]:
    """Loud alarm: max priority + rotating_light tag on ntfy, Telegram as backup."""
    return {
        "ntfy": send_ntfy(body, title=title, priority="max", tags="rotating_light,movie_camera"),
        "telegram": send_telegram(f"🚨 {title}\n\n{body}"),
    }


def send_info(title: str, body: str) -> dict[str, bool]:
    """Routine info: normal priority."""
    return {
        "telegram": send_telegram(f"{title}\n\n{body}"),
        "ntfy": send_ntfy(body, title=title, priority="default"),
    }


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "info"
    msg = " ".join(sys.argv[2:]) or "test message from movie-ticket-notifier"
    if mode == "alarm":
        result = send_alarm("TEST ALARM", msg)
    else:
        result = send_info("TEST", msg)
    print(result)
