from __future__ import annotations

import httpx

from smartalpha.config import Settings


def notify(text: str, settings: Settings | None = None) -> None:
    s = settings or Settings()
    print(text)
    if not s.telegram_token or not s.telegram_chat:
        return
    url = f"https://api.telegram.org/bot{s.telegram_token}/sendMessage"
    with httpx.Client(timeout=15.0) as client:
        client.post(
            url,
            json={"chat_id": s.telegram_chat, "text": text[:4000], "disable_web_page_preview": True},
        )


def notify_cluster(alert_json: str, settings: Settings | None = None) -> None:
    notify(f"🟢 CLUSTER\n{alert_json}", settings)


def notify_dump(report_json: str, settings: Settings | None = None) -> None:
    notify(f"🔴 DUMP\n{report_json}", settings)
