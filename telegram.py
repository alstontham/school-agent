"""Talks to the Telegram Bot API."""
import json
import urllib.request

import config

MAX_LEN = 4096  # Telegram's per-message limit


def call(method, http_timeout=30, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=http_timeout) as resp:
        return json.load(resp)["result"]


def send(text, buttons=None):
    """Sends HTML-formatted text, splitting on line breaks if it exceeds Telegram's limit.

    `buttons` is a list of rows of (label, callback_data) pairs, attached to the last chunk.
    """
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > MAX_LEN:
            chunks.append(current)
            current = ""
        current += line
    chunks.append(current)

    for n, chunk in enumerate(chunks):
        extra = {"reply_markup": keyboard(buttons)} if buttons and n == len(chunks) - 1 else {}
        call("sendMessage", chat_id=config.TELEGRAM_CHAT_ID, text=chunk,
             parse_mode="HTML", disable_web_page_preview=True, **extra)


def keyboard(buttons):
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in buttons]}
