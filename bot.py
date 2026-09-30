"""Two-way Telegram bot. Runs continuously on the Mac: python3 bot.py

Commands: /digest (today's digest), /done (tap items to mark them done),
/readd (bring back one of the last 5 items you marked done).
Marking an item done checks it off in the Canvas planner, so it's hidden
from every future digest, including the ones GitHub Actions sends.
"""
import json
import time
import traceback
import urllib.error
from datetime import datetime
from html import escape
from pathlib import Path

import canvas
import config
import digest
import telegram

HELP = (
    "<b>Commands</b>\n"
    "/digest — today's digest\n"
    "/done — mark items done (e.g. a group member submitted it)\n"
    "/readd — bring back one of the last 5 items you marked done\n\n"
    "Items you mark done are checked off in Canvas and hidden from future digests."
)


def _label(item):
    label = f"{item['course']} · {item['title']}"
    return "✓ " + (label if len(label) <= 45 else label[:44] + "…")


def _key(item):
    return f"{item['type']}:{item['id']}"


REMOVED_LOG = Path(__file__).parent / "state" / "removed.json"
REMOVED_SHOWN = 5


def load_removed():
    """Items marked done through the bot, most recent first."""
    try:
        return json.loads(REMOVED_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_removed(entries):
    REMOVED_LOG.parent.mkdir(exist_ok=True)
    REMOVED_LOG.write_text(json.dumps(entries[:20], indent=2))


def mark_done(item):
    override_id = canvas.set_done(item, True)
    entry = {"key": _key(item), "type": item["type"], "id": item["id"], "override_id": override_id,
             "course": item["course"], "course_name": item["course_name"], "title": item["title"],
             "when": item["when"].isoformat()}
    save_removed([entry] + [e for e in load_removed() if e["key"] != entry["key"]])


def readd(key):
    """Un-ticks a removed item in Canvas so it shows up in the digest again. Returns its entry."""
    entries = load_removed()
    entry = next((e for e in entries if e["key"] == key), None)
    if entry:
        canvas.set_done(entry, False)
        save_removed([e for e in entries if e["key"] != key])
    return entry


def open_items():
    sec = digest.sections(digest.fetch_all())
    return sec["overdue"] + sec["due_today"] + sec["due_week"] + sec["prep"]


def manage_view(undo=None):
    """Text and buttons for the "mark done" list. `undo` is an item just marked done."""
    items = open_items()
    text = "<b>Tap an item to mark it done.</b>\nIt gets checked off in Canvas and won't show up in future digests."
    if not items:
        text = "<b>Nothing left to mark done 🎉</b>"
    buttons = []
    if undo:
        text = f"✅ Marked <b>{escape(undo['title'])}</b> done.\n\n" + text
        buttons.append([(f"↩️ Undo: {undo['title'][:35]}", f"u:{_key(undo)}")])
    buttons += [[(_label(i), f"d:{_key(i)}")] for i in items]
    buttons.append([("♻️ Re-add removed", "removed")])
    buttons.append([("✖️ Close — show updated list", "close")])
    return text, buttons


def removed_view(restored=None):
    """Text and buttons for re-adding one of the last few removed items."""
    entries = load_removed()[:REMOVED_SHOWN]
    text = f"<b>Recently removed</b> (last {REMOVED_SHOWN})\nTap one to put it back on your list."
    if not entries:
        text = "<b>No removed items to re-add.</b>"
    if restored:
        text = f"↩️ Re-added <b>{escape(restored['title'])}</b>.\n\n" + text
    for e in entries:
        when = datetime.fromisoformat(e["when"]).strftime("%a %-m/%-d")
        text += f"\n• <b>{escape(e['course_name'])}</b> {escape(e['title'])} — due {when}"
    buttons = [[("↩️ " + _label(e)[2:], f"r:{e['key']}")] for e in entries]
    buttons.append([("✖️ Close — show updated list", "close")])
    return text, buttons


def _edit(message, text, buttons):
    """Replaces a message in place; falls back to a new message if it's too long to fit."""
    if len(text) > telegram.MAX_LEN:
        telegram.send(text, buttons=buttons)
        return
    telegram.call("editMessageText", chat_id=message["chat"]["id"], message_id=message["message_id"],
                  text=text, parse_mode="HTML", disable_web_page_preview=True,
                  reply_markup=telegram.keyboard(buttons))


def find_item(key):
    """Looks up an item by type:id, including ones already marked done (for undo)."""
    return next((i for i in digest.fetch_all() if _key(i) == key), None)


def handle_message(msg):
    text = (msg.get("text") or "").strip().split("@")[0].lower()
    if text in ("/digest", "/today"):
        telegram.send(digest.build(digest.fetch_all()), buttons=digest.DIGEST_BUTTONS)
    elif text in ("/done", "/manage", "/remove"):
        telegram.send(*manage_view())
    elif text in ("/readd", "/removed", "/restore"):
        telegram.send(*removed_view())
    else:
        telegram.send(HELP)


def handle_callback(cq):
    data = cq.get("data", "")
    message = cq["message"]
    toast = ""

    if data == "manage":
        # Turn the digest message itself into the mark-done menu.
        _edit(message, *manage_view())
    elif data in ("close", "refresh"):
        # Rebuild the digest from Canvas in place (also turns the menu back into the digest).
        try:
            _edit(message, digest.build(digest.fetch_all()), digest.DIGEST_BUTTONS)
            toast = "Refreshed" if data == "refresh" else ""
        except urllib.error.HTTPError as e:
            # Telegram rejects edits that don't change anything.
            if e.code != 400 or b"not modified" not in e.read():
                raise
            toast = "Already up to date"
    elif data == "removed":
        _edit(message, *removed_view())
    elif data.startswith("d:"):
        item = find_item(data[2:])
        if not item:
            toast = "Couldn't find that item — it may have moved out of range."
        else:
            mark_done(item)
            toast = "Marked done: " + item["title"]
            _edit(message, *manage_view(undo=item))
    elif data[:2] in ("u:", "r:"):
        # u: = Undo in the mark-done menu, r: = tap in the re-add menu.
        entry = readd(data[2:])
        if not entry:
            toast = "That item is no longer in the removed list."
        else:
            toast = "Re-added: " + entry["title"]
            _edit(message, *(manage_view() if data.startswith("u:") else removed_view(restored=entry)))

    telegram.call("answerCallbackQuery", callback_query_id=cq["id"], text=toast[:200])


def main():
    print("Bot running. Ctrl+C to stop.", flush=True)
    offset = None
    while True:
        try:
            params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            updates = telegram.call("getUpdates", http_timeout=60, **params)
        except Exception as e:
            # Network drops (e.g. the Mac waking from sleep) — wait and retry.
            print(f"getUpdates failed: {e}", flush=True)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                if "callback_query" in update:
                    cq = update["callback_query"]
                    if str(cq["message"]["chat"]["id"]) == config.TELEGRAM_CHAT_ID:
                        handle_callback(cq)
                elif "message" in update:
                    msg = update["message"]
                    # Ignore anyone who isn't you.
                    if str(msg["chat"]["id"]) == config.TELEGRAM_CHAT_ID:
                        handle_message(msg)
            except Exception:
                traceback.print_exc()
                try:
                    telegram.send("⚠️ Something went wrong handling that. Try again in a moment.")
                except Exception:
                    pass


if __name__ == "__main__":
    main()
