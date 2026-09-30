"""Finds class prep in course modules: readings, videos and questions with no Canvas due date.

Many courses put this work in modules named with the class date, e.g.
"Lecture: AI and Operations I, Wed Sept 30th" or "Class 6: Sep.29_[Analytics] ...".
Items without a due date never reach the Canvas planner, so the digest would miss
them. Items that do have a due date are skipped here, since canvas.upcoming() has them.

Courses whose modules aren't dated (e.g. 15.873's "Week 5" with "Class 8: ..." sub-headings)
are dated from class_schedules.json, a class-number -> date table copied from the syllabus.
"""
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import canvas
import config

MONTHS = {m: n for n, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
# Matches "Sept 30th", "September 29", "Oct 5th", "Sep.29_" (month name, then day number).
DATE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s_]*(\d{1,2})(?:st|nd|rd|th)?(?![\d])", re.I)

# Module item type -> Canvas planner type, for marking done. ExternalUrls (e.g. pre-class
# videos) are shown as links under their module but can't be checked off on their own.
PLANNABLE = {"Assignment": "assignment", "Quiz": "quiz", "Discussion": "discussion_topic", "Page": "wiki_page"}

CLASS_HEADER_RE = re.compile(r"^\s*class\s+(\d+)\b", re.I)
# Post-class readings are shown for this many days after their class instead of dropping off.
POST_CLASS_DAYS = 7

_schedule_file = Path(__file__).parent / "class_schedules.json"
SCHEDULES = json.loads(_schedule_file.read_text()) if _schedule_file.exists() else {}


def module_date(name, today):
    match = DATE_RE.search(name)
    if not match:
        return None
    month, day = MONTHS[match.group(1).lower()[:3]], int(match.group(2))
    # Pick the year that puts the date closest to today (handles Dec/Jan wraparound).
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(today.replace(year=year, month=month, day=day))
        except ValueError:
            pass
    return min(candidates, key=lambda d: abs((d - today).days), default=None)


def _active_courses():
    return canvas._get("courses", {"enrollment_state": "active", "per_page": 50})


def _submitted(course_id, assignment_ids):
    if not assignment_ids:
        return set()
    assignments = canvas._get(f"courses/{course_id}/assignments",
                              {"assignment_ids[]": sorted(assignment_ids), "include[]": ["submission"], "per_page": 100})
    return {a["id"] for a in assignments
            if (a.get("submission") or {}).get("workflow_state") in ("submitted", "graded", "pending_review")
            or (a.get("submission") or {}).get("excused")}


def _groups(module, today, schedule):
    """Splits a module into (date, entries) groups.

    A dated module is one group. In an undated module, each "Class N: ..." sub-heading
    starts a group dated from the course's class schedule; other sub-headings (e.g.
    "Recitation Materials") start an undated group.
    """
    date = module_date(module["name"], today)
    groups = [[date, []]]
    for entry in module.get("items") or []:
        if entry["type"] == "SubHeader" and not date:
            match = CLASS_HEADER_RE.match(entry["title"])
            class_date = schedule.get(match.group(1)) if match else None
            groups.append([today.replace(**dict(zip(("year", "month", "day"), map(int, class_date.split("-")))))
                           if class_date else None, []])
        else:
            groups[-1][1].append(entry)
    return [(d, entries) for d, entries in groups if d and entries]


def _course_items(course, today, days, override_ids, checked_off):
    course_name = course.get("name") or ""
    schedule = SCHEDULES.get(str(course["id"]), {}).get("classes", {})
    modules = canvas._get(f"courses/{course['id']}/modules",
                          {"include[]": ["items", "content_details"], "per_page": 50})
    found = []
    for module in modules:
        for date, entries in _groups(module, today, schedule):
            links = [{"title": e["title"].strip(), "url": e.get("external_url") or e.get("html_url")}
                     for e in entries if e["type"] == "ExternalUrl"]
            for e in entries:
                if e["type"] not in PLANNABLE or (e.get("content_details") or {}).get("due_at"):
                    continue
                post = "post-class" in e["title"].lower()
                earliest = today - timedelta(days=POST_CLASS_DAYS) if post else today
                if earliest <= date <= today + timedelta(days=days):
                    found.append((module, date, e, links, post))

    submitted = _submitted(course["id"], {e["content_id"] for _, _, e, _, _ in found if e["type"] == "Assignment"})
    items = []
    for module, date, e, links, post in found:
        key = (PLANNABLE[e["type"]], e["content_id"])
        items.append({
            "prep": True,
            "post": post,
            "type": key[0],
            "id": key[1],
            "override_id": override_ids.get(key),
            "course": course_name.split(" ")[0],
            "course_name": course_name,
            "title": e["title"].strip(),
            "module": module["name"].strip(),
            "links": links,
            "when": date,
            "done": key in checked_off or e["content_id"] in submitted,
            "missing": False,
            "points": (e.get("content_details") or {}).get("points_possible") or 0,
            "url": urllib.parse.urljoin(config.CANVAS_BASE_URL, e["html_url"]) if e.get("html_url") else None,
        })
    return items


def prep_items(days=7):
    """Undated work in modules whose name has a date from today through `days` ahead."""
    today = datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    overrides = canvas._get("planner/overrides", {"per_page": 100})
    override_ids = {(o["plannable_type"], o["plannable_id"]): o["id"] for o in overrides}
    checked_off = {(o["plannable_type"], o["plannable_id"]) for o in overrides if o["marked_complete"]}

    # Courses are fetched in parallel; one at a time takes ~10 seconds.
    with ThreadPoolExecutor(max_workers=8) as pool:
        per_course = pool.map(lambda c: _course_items(c, today, days, override_ids, checked_off), _active_courses())
        items = [item for course_items in per_course for item in course_items]
    return sorted(items, key=lambda i: (i["when"], i["course"]))
