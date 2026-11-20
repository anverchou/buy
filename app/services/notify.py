"""Optional push notifications through ntfy (https://ntfy.sh). Off unless NTFY_TOPIC is set."""
from __future__ import annotations

import logging
import unicodedata

import requests

log = logging.getLogger(__name__)


def _ascii(text: str) -> str:
    text = text.replace("–", "-").replace("—", "-").replace("·", "-")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def send_push(config: dict, title: str, message: str, click_url: str | None = None) -> bool:
    topic = config.get("NTFY_TOPIC")
    if not topic:
        return False
    headers = {"Title": _ascii(title), "Tags": "moneybag"}
    if click_url:
        headers["Click"] = click_url
    try:
        resp = requests.post(f"{config.get('NTFY_SERVER', 'https://ntfy.sh')}/{topic}",
                             data=message.encode("utf-8"), headers=headers, timeout=15)
        return resp.status_code < 300
    except requests.RequestException as exc:
        log.warning("ntfy push failed: %s", exc)
        return False
