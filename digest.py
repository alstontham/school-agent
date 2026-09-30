"""Builds the daily digest and sends it to Telegram. Run: python3 digest.py [--dry-run]"""
import sys
import time
from datetime import datetime, timedelta
from html import escape

from concurrent.futures import ThreadPoolExecutor

import canvas
import config
import modules
import telegram

DUE_TYPES = {"assignment", "quiz", "discussion_topic"}


def _time(dt):
    if dt.hour == 23 and dt.minute == 59:
        return "end of day"
    return dt.strftime("%-I:%M %p").replace(":00 ", " ").lower()


def _line(item, show_day=False):
    when = (item["when"].strftime("%a %-m/%-d, ") if show_day else "") + _time(item["when"])
    title = escape(item["title"])
    if item["url"]:
        title = f'<a href="{escape(item["url"])}">{title}</a>'
    return f"• <b>{escape(item['course_name'])}</b> {title} — {when}"


def fetch_all(days=7):
    """Canvas planner items plus undated class prep from course modules, fetched in parallel."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        planner = pool.submit(canvas.upcoming, days=days)
        prep = pool.submit(modules.prep_items, days=days)
        return planner.result() + prep.result()


def _prep_line(item, today):
    day = "today" if item["when"].date() == today else item["when"].strftime("%a %-m/%-d")
    # Modules like "Individual Assignment 2 (Due Wed Sept 30th)" are deadlines, not class prep.
    when = f"due {day}" if "due" in item["module"].lower() else f"for {day}'s class"
    title = escape(item["title"])
    if item["url"]:
        title = f'<a href="{escape(item["url"])}">{title}</a>'
    lines = [f"• <b>{escape(item['course_name'])}</b> {title} — {when}"]
    for link in item["links"]:
        lines.append(f'    ↳ <a href="{escape(link["url"] or "")}">{escape(link["title"])}</a>')
    return lines


def sections(items, now=None):
    now = now or datetime.now(config.TZ)
    today = now.date()
    todo = [i for i in items if i["type"] in DUE_TYPES and not i["done"] and not i.get("prep")]
    return {
        "now": now,
        # Only flag past items Canvas marks missing or that are worth points (skips 0-pt readings/prep).
        "overdue": [i for i in todo if i["when"] < now and (i["missing"] or i["points"] > 0)],
        "due_today": [i for i in todo if i["when"] >= now and i["when"].date() == today],
        "due_week": [i for i in todo if i["when"].date() > today],
        "classes": [i for i in items if i["type"] == "calendar_event" and i["when"].date() == today],
        "announcements": [i for i in items if i["type"] == "announcement" and i["when"] > now - timedelta(days=1)],
        # Undated readings/videos/questions from course modules, dated by the module's class date.
        "prep": [i for i in items if i.get("prep") and not i["done"]],
    }


def build(items, now=None):
    sec = sections(items, now)
    now, overdue, due_today, due_week = sec["now"], sec["overdue"], sec["due_today"], sec["due_week"]
    classes, announcements, prep = sec["classes"], sec["announcements"], sec["prep"]

    heading = "☀️ Morning digest" if now.hour < 17 else "🌙 Evening check-in"
    out = [f"<b>{heading} — {now.strftime('%A, %B %-d')}</b>", ""]
    if overdue:
        out += ["<b>⚠️ Past due (not submitted)</b>"] + [_line(i, show_day=True) for i in overdue] + [""]
    out += ["<b>🔥 Due today</b>"] + ([_line(i) for i in due_today] or ["Nothing due today 🎉"]) + [""]
    if due_week:
        out += ["<b>📅 Next 7 days</b>"] + [_line(i, show_day=True) for i in due_week] + [""]
    if prep:
        out += ["<b>📖 Class prep & readings</b>"] + [l for i in prep for l in _prep_line(i, now.date())] + [""]
    if classes:
        out += ["<b>🏫 Classes today</b>"] + [_line(i) for i in classes] + [""]
    if announcements:
        out += ["<b>📣 New announcements</b>"] + [f"• <b>{escape(i['course_name'])}</b> {escape(i['title'])}" for i in announcements] + [""]
    return "\n".join(out).strip()


DIGEST_BUTTONS = [[("✅ Mark items done", "manage"), ("🔄 Refresh", "refresh")],
                  [("♻️ Re-add removed", "removed")]]


def main():
    if "--dry-run" in sys.argv:
        print(build(fetch_all()))
        return
    # Retry so a run right after the Mac wakes survives Wi-Fi not being connected yet.
    for attempt in range(1, 6):
        try:
            telegram.send(build(fetch_all()), buttons=DIGEST_BUTTONS)
            print(f"{datetime.now(config.TZ):%Y-%m-%d %H:%M} digest sent.", flush=True)
            return
        except Exception as e:
            print(f"Attempt {attempt} failed: {e}", flush=True)
            time.sleep(30)
    sys.exit(1)


if __name__ == "__main__":
    main()

