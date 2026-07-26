"""
Sends a push notification via ntfy (https://ntfy.sh or a self-hosted instance).

ntfy was chosen as the default because it's zero-signup, has a solid mobile
app, and self-hosts easily behind the Nginx + Let's Encrypt setup already
running on the VPS — a natural fit if you later want this fully private.
Swapping to a Telegram bot instead is a small, isolated change if preferred.
"""
from __future__ import annotations

import requests

from src.config import NotificationSettings


def send_deal_alert(settings: NotificationSettings, title: str, message: str, url: str | None = None) -> None:
    if not settings.ntfy_topic or settings.ntfy_topic.startswith("CHANGE_ME"):
        raise RuntimeError("Set a real ntfy_topic in config/routes.yaml before running for real.")

    headers = {"Title": title, "Priority": "default", "Tags": "airplane,moneybag"}
    if url:
        headers["Click"] = url

    requests.post(
        f"{settings.ntfy_server}/{settings.ntfy_topic}",
        data=message.encode("utf-8"),
        headers=headers,
        timeout=10,
    )
