"""Fetches upcoming assignments, quizzes, events and announcements from Canvas."""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import config


def _request(method, path, params):
    req = urllib.request.Request(
        f"{config.CANVAS_BASE_URL}/api/v1/{path}",
        data=urllib.parse.urlencode(params).encode(),
        method=method,
        headers={"Authorization": f"Bearer {config.CANVAS_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _get(path, params):
    url = f"{config.CANVAS_BASE_URL}/api/v1/{path}?{urllib.parse.urlencode(params, doseq=True)}"
    results = []
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.CANVAS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            results.extend(json.load(resp))
            # Canvas paginates via the Link header.
            match = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link", ""))
            url = match.group(1) if match else None
    return results


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(config.TZ)


def upcoming(days=7, lookback_days=14):
    """Returns planner items from `lookback_days` ago (to catch unsubmitted work) through `days` ahead."""
    today = datetime.now(config.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=lookback_days)
    end = today + timedelta(days=days)
    raw = _get("planner/items", {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "per_page": 100,
    })
    items = []
    for item in raw:
        plannable = item.get("plannable") or {}
        submissions = item.get("submissions") or {}
        override = item.get("planner_override") or {}
        items.append({
            "type": item.get("plannable_type"),
            "id": item.get("plannable_id"),
            "override_id": override.get("id"),
            "course": (item.get("context_name") or "").split(" ")[0],
            "course_name": item.get("context_name") or "",
            "title": (plannable.get("title") or "").strip(),
            "when": _parse_time(item["plannable_date"]),
            # Done = submitted, graded, excused, or checked off in the Canvas planner.
            "done": any(submissions.get(k) for k in ("submitted", "graded", "excused")) or bool(override.get("marked_complete")),
            "missing": bool(submissions.get("missing")),
            "points": plannable.get("points_possible") or 0,
            "url": urllib.parse.urljoin(config.CANVAS_BASE_URL, item["html_url"]) if item.get("html_url") else None,
        })
    return sorted(items, key=lambda i: i["when"])


def set_done(item, done=True):
    """Checks an item off (or back on) in the Canvas planner, hiding it from the digest.

    Returns the planner override's id, which is all that's needed to reverse it later.
    """
    params = {"marked_complete": str(done).lower()}
    if item.get("override_id"):
        return _request("PUT", f"planner/overrides/{item['override_id']}", params)["id"]
    return _request("POST", "planner/overrides", {**params, "plannable_type": item["type"], "plannable_id": item["id"]})["id"]
