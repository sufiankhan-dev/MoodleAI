"""Moodle-aware page inspection.

Moodle themes differ, so extraction is layered:
1. Prefer the main content region (``#region-main`` / ``[role=main]``).
2. Collect semantic markers (activities, assignment links, attachments).
3. Always return the visible text so qwen3:8b can reason about what is there.

Nothing in this module trusts page text as instructions; it is returned as data.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from app.security import redact_url, truncate_text

_SNAPSHOT_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const root = document.querySelector('#region-main, [role="main"], #maincontent, main') || document.body;
  const mainText = clean(root.innerText || '');
  const headings = Array.from(root.querySelectorAll('h1,h2,h3,h4'))
    .map(h => clean(h.innerText)).filter(Boolean).slice(0, 80);

  const links = [];
  const seen = new Map();
  const priority = {link: 0, resource: 1, attachment: 2};
  const addLink = (a, kind) => {
    const href = a.href || '';
    if (!/^https?:/i.test(href)) return;
    const text = clean(a.innerText || a.getAttribute('aria-label') || a.title || '');
    const key = href + '|' + text;
    if (seen.has(key)) {
      const index = seen.get(key);
      if ((priority[kind] || 0) > (priority[links[index].kind] || 0)) {
        links[index].kind = kind;
      }
      return;
    }
    seen.set(key, links.length);
    links.push({text: text.slice(0, 250), href, kind});
  };
  root.querySelectorAll('a[href]').forEach(a => addLink(a, 'link'));
  // Themes such as Moove put course cards, drawers and blocks outside the main
  // region, so also scan the whole document (main-region links stay first).
  document.querySelectorAll('a[href]').forEach(a => addLink(a, 'link'));
  document.querySelectorAll('a[href*="pluginfile.php"]').forEach(a => addLink(a, 'attachment'));
  document.querySelectorAll('a[href*="/mod/resource/view.php"], a[href*="/mod/folder/view.php"]')
    .forEach(a => addLink(a, 'resource'));
  document.querySelectorAll('a[href]').forEach(a => {
    const label = (a.innerText || '') + ' ' + (a.href || '');
    if (/\.(pdf|docx?|pptx?|xlsx?|zip|txt|csv|py|sql|java)\b/i.test(label)) addLink(a, 'attachment');
  });

  const courseCards = [];
  const cardSeen = new Set();
  document.querySelectorAll(
    '[data-course-id], [data-courseid], .coursebox, [data-region="course-content"]'
  ).forEach(el => {
    const card = el.closest('[data-course-id], [data-courseid], .coursebox') || el;
    const id = card.getAttribute('data-course-id') || card.getAttribute('data-courseid') || '';
    const a = card.querySelector('a[href*="course/view.php"]');
    const nameEl = card.querySelector(
      '.coursename a, .coursename, .course-name, [data-region="course-name"], '
      + 'h3 a, h4 a, h3, h4, img[alt]'
    );
    const name = clean(
      nameEl ? (nameEl.innerText || nameEl.getAttribute('alt') || '') : ''
    ).slice(0, 250);
    const href = a ? a.href : '';
    const key = (id || href) + '|' + name;
    if ((!id && !href) || cardSeen.has(key)) return;
    cardSeen.add(key);
    courseCards.push({course_id: id, name, href});
  });

  const activities = [];
  const seenActivities = new Set();
  document.querySelectorAll('li.activity, .activity-item, [data-region="activity-card"]').forEach(li => {
    const a = li.querySelector('.activityname a, a.aalink, a[href*="/mod/"]');
    const href = a ? a.href : '';
    const nameEl = li.querySelector('.activityname a, .instancename, .activityname');
    const name = clean(nameEl ? nameEl.innerText : (a ? a.innerText : '')).slice(0, 250);
    const key = href + '|' + name;
    if (!name || seenActivities.has(key)) return;
    seenActivities.add(key);
    const dateEl = li.querySelector('[data-region="activity-dates"], .activity-dates, .badge');
    const statusEl = li.querySelector('.submissionstatus, .autocompletion, .badge-success, .text-success');
    const classes = Array.from(li.classList).join(' ');
    const modMatch = classes.match(/modtype_([a-z]+)/);
    activities.push({
      name,
      href,
      modtype: modMatch ? modMatch[1] : '',
      dates: clean(dateEl ? dateEl.innerText : ''),
      status: clean(statusEl ? statusEl.innerText : ''),
      classes: classes.slice(0, 200)
    });
  });

  const intro = document.querySelector('#intro, [data-region="activity-description"], .activity-description');
  const activityInfo = document.querySelector('[data-region="activity-information"]');
  const submission = document.querySelector('.submissionstatustable, [data-region="submission-status"]');
  const due = document.querySelector('[data-region="activity-dates"], .duedate, #assign_due_date');
  const breadcrumbs = Array.from(document.querySelectorAll('nav[aria-label*="readcrumb"] a, .breadcrumb a'))
    .map(a => clean(a.innerText)).filter(Boolean);

  return {
    title: document.title,
    pageType: (() => {
      const b = document.body.className || '';
      if (b.includes('path-mod-assign')) return 'assignment';
      if (b.includes('path-course-view')) return 'course';
      if (b.includes('path-my')) return 'dashboard';
      if (b.includes('path-login')) return 'login';
      return 'other';
    })(),
    bodyClasses: (document.body.className || '').slice(0, 300),
    mainText,
    headings,
    links: links.slice(0, 800),
    courseCards: courseCards.slice(0, 100),
    activities: activities.slice(0, 200),
    introText: clean(intro ? intro.innerText : ''),
    activityInfoText: clean(activityInfo ? activityInfo.innerText : ''),
    submissionStatusText: clean(submission ? submission.innerText : ''),
    dueText: clean(due ? due.innerText : ''),
    breadcrumbs
  };
}
"""


class MoodleParseError(RuntimeError):
    """Raised when the page cannot be inspected."""


async def take_snapshot(page: Page, *, max_chars: int = 14000) -> dict[str, Any]:
    """Return a structured, text-first snapshot of the visible Moodle page."""
    try:
        raw = await page.evaluate(_SNAPSHOT_JS)
    except PlaywrightTimeoutError as exc:
        raise MoodleParseError("The page did not finish loading in time.") from exc
    except Exception as exc:  # Playwright's Error hierarchy differs by version
        raise MoodleParseError(f"Could not inspect the page: {exc}") from exc

    if not isinstance(raw, dict):
        raise MoodleParseError("Unexpected page inspection result.")

    url = page.url
    main_text = truncate_text(str(raw.get("mainText", "")), max_chars)
    snapshot = {
        "url": redact_url(url),
        "full_url": url,
        "title": str(raw.get("title", ""))[:250],
        "page_type": str(raw.get("pageType", "other")),
        "body_classes": str(raw.get("bodyClasses", ""))[:300],
        "text": main_text,
        "headings": [str(h)[:250] for h in raw.get("headings", []) if h][:80],
        "links": [
            {
                "text": str(link.get("text", ""))[:250],
                "href": str(link.get("href", "")),
                "kind": str(link.get("kind", "link")),
            }
            for link in raw.get("links", [])
            if isinstance(link, dict)
        ][:800],
        "course_cards": [
            {
                "course_id": str(card.get("course_id", ""))[:20],
                "name": str(card.get("name", ""))[:250],
                "href": str(card.get("href", "")),
            }
            for card in raw.get("courseCards", [])
            if isinstance(card, dict)
        ][:100],
        "activities": [
            {
                "name": str(activity.get("name", ""))[:250],
                "href": str(activity.get("href", "")),
                "modtype": str(activity.get("modtype", ""))[:30],
                "dates": str(activity.get("dates", ""))[:300],
                "status": str(activity.get("status", ""))[:200],
                "classes": str(activity.get("classes", ""))[:200],
            }
            for activity in raw.get("activities", [])
            if isinstance(activity, dict)
        ][:200],
        "intro_text": truncate_text(str(raw.get("introText", "")), 6000),
        "activity_info_text": truncate_text(str(raw.get("activityInfoText", "")), 4000),
        "submission_status_text": truncate_text(
            str(raw.get("submissionStatusText", "")), 2000
        ),
        "due_text": truncate_text(str(raw.get("dueText", "")), 1000),
        "breadcrumbs": [str(b)[:200] for b in raw.get("breadcrumbs", []) if b][:20],
    }
    return snapshot


def _course_key(url: str) -> str:
    parsed = urlparse(url)
    course_id = parse_qs(parsed.query).get("id", [""])[0]
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={course_id}"


def parse_courses(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract unique courses from any Moodle page that links to courses.

    Moodle themes differ: standard themes render ``/course/view.php?id=N``
    anchors, while card-based themes (Moove, Boost Union variants) may render
    ``[data-course-id]`` cards outside the main region, sometimes without an
    anchor at all. Both are supported.
    """

    courses: dict[str, dict[str, Any]] = {}
    origin = ""
    full_url = snapshot.get("full_url", "")
    if full_url:
        parsed_page = urlparse(full_url)
        if parsed_page.scheme and parsed_page.netloc:
            origin = f"{parsed_page.scheme}://{parsed_page.netloc}"

    def add(href: str, name: str, fallback_id: str = "") -> None:
        href = (href or "").strip()
        if href:
            parsed = urlparse(href)
            if "/course/view.php" not in parsed.path.lower():
                return
            course_id = parse_qs(parsed.query).get("id", [""])[0]
        else:
            course_id = ""
        if not course_id:
            course_id = (fallback_id or "").strip()
        if not course_id:
            return
        if href:
            key = _course_key(href)
        else:
            key = f"{origin}/course/view.php?id={course_id}"
        cleaned = " ".join(str(name).split())
        if not cleaned or cleaned.lower() in {"course", "open course", "view course"}:
            cleaned = f"Course {course_id}"
        current = courses.get(key)
        # Prefer the longest label: image links are often empty/short.
        if current is None or len(cleaned) > len(current["name"]):
            courses[key] = {
                "name": cleaned,
                "url": key,
                "course_id": course_id,
            }

    for link in snapshot.get("links", []):
        href = link.get("href", "")
        if "/course/view.php" in href.lower():
            add(href, link.get("text", ""))

    for card in snapshot.get("course_cards", []):
        add(card.get("href", ""), card.get("name", ""), card.get("course_id", ""))

    return sorted(courses.values(), key=lambda item: item["name"].lower())


def _looks_submitted(status: str) -> bool:
    text = status.lower()
    if "not submitted" in text or "no attempt" in text or "nothing submitted" in text:
        return False
    return any(token in text for token in ("submitted", "graded", "submission received"))


def parse_assignments(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract assignment activities with raw due/status text for the model."""
    found: dict[str, dict[str, Any]] = {}

    def add(
        name: str,
        href: str,
        due: str = "",
        status: str = "",
        modtype: str = "",
    ) -> None:
        if not href or "/mod/" not in href:
            return
        parsed = urlparse(href)
        activity_id = parse_qs(parsed.query).get("id", [""])[0]
        key = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={activity_id}"
        is_assign = modtype == "assign" or "/mod/assign/view.php" in href
        if not is_assign:
            return
        pretty = " ".join(name.split()) or f"Assignment {activity_id}"
        submitted = _looks_submitted(status)
        entry = {
            "name": pretty,
            "url": key,
            "activity_id": activity_id,
            "due_text": " ".join(due.split())[:300],
            "status_text": " ".join(status.split())[:200],
            "submitted": submitted,
            "pending": not submitted,
        }
        existing = found.get(key)
        if existing is None:
            found[key] = entry
        else:
            if not existing["due_text"] and entry["due_text"]:
                existing["due_text"] = entry["due_text"]
            if not existing["status_text"] and entry["status_text"]:
                existing["status_text"] = entry["status_text"]
                existing["submitted"] = entry["submitted"]
                existing["pending"] = entry["pending"]
            if len(entry["name"]) > len(existing["name"]):
                existing["name"] = entry["name"]

    for activity in snapshot.get("activities", []):
        add(
            activity.get("name", ""),
            activity.get("href", ""),
            activity.get("dates", ""),
            activity.get("status", ""),
            activity.get("modtype", ""),
        )

    for link in snapshot.get("links", []):
        href = link.get("href", "")
        if "/mod/assign/view.php" in href:
            add(link.get("text", ""), href)

    return sorted(found.values(), key=lambda item: item["name"].lower())


def parse_assignment_detail(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Structured view of an assignment page, preserving the full text."""
    breadcrumbs = snapshot.get("breadcrumbs", [])
    generic = {"dashboard", "my courses", "home", "site home", "courses", "my home"}
    meaningful = [b for b in breadcrumbs if b.strip().lower() not in generic]
    if len(meaningful) >= 2:
        course_name = meaningful[-2]
    elif meaningful:
        course_name = meaningful[-1]
    else:
        course_name = ""
    name = snapshot.get("headings", [""])[0] if snapshot.get("headings") else ""
    if not name:
        name = re.sub(r"\s*\|\s*.*$", "", snapshot.get("title", "")).strip()

    attachments = []
    seen = set()
    for link in snapshot.get("links", []):
        if link.get("kind") not in {"attachment", "resource"}:
            continue
        href = link.get("href", "")
        if href in seen:
            continue
        seen.add(href)
        attachments.append({"text": link.get("text", ""), "href": href, "kind": link.get("kind")})

    instructions_parts = [
        part
        for part in (
            snapshot.get("intro_text", ""),
            snapshot.get("activity_info_text", ""),
        )
        if part
    ]

    return {
        "name": name[:250],
        "course_name": course_name[:250],
        "url": snapshot.get("url", ""),
        "due_text": snapshot.get("due_text", ""),
        "submission_status_text": snapshot.get("submission_status_text", ""),
        "instructions": "\n\n".join(instructions_parts)[:10000],
        "attachments": attachments[:50],
        "full_text": snapshot.get("text", ""),
        "headings": snapshot.get("headings", []),
    }
