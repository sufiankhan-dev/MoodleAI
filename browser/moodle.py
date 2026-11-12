"""Moodle-aware page inspection.

Moodle themes differ, so extraction is layered:
1. Prefer the main content region (``#region-main`` / ``[role=main]``).
2. Collect semantic markers (activities, assignment links, attachments).
3. Always return the visible text so qwen3:8b can reason about what is there.

Nothing in this module trusts page text as instructions; it is returned as data.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

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
    const text = clean(
      a.innerText || a.textContent || a.getAttribute('aria-label') || a.title || ''
    );
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
  const codeLike = (label) => {
    const text = clean(label);
    if (!text || text.length > 60) return false;
    if (!/\d/.test(text)) return false;
    if (/[a-z]{2,}/.test(text)) return false;
    return /^[A-Z0-9][A-Z0-9 \-_/.()]*$/.test(text);
  };
  const placeholder = (label) => {
    const text = clean(label).toLowerCase();
    return !text || text === 'course' || /^course \d+$/.test(text)
      || text === 'open course' || text === 'view course' || text === 'more';
  };
  const labelFrom = (el) => clean(
    (el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')
    + ' ' + (el.getAttribute('title') || '') + ' ' + (el.getAttribute('alt') || '')
  );
  document.querySelectorAll(
    '[data-course-id], [data-courseid], .coursebox, [data-region="course-content"]'
  ).forEach(el => {
    const card = el.closest('[data-course-id], [data-courseid], .coursebox') || el;
    const id = card.getAttribute('data-course-id') || card.getAttribute('data-courseid') || '';
    const a = card.querySelector('a[href*="course/view.php"]');
    const selectors = [
      '.coursename a', '.coursename', '.course-name', '[data-region="course-name"]',
      '.card-title a', '.card-title', 'h3 a', 'h4 a', 'h3', 'h4',
      'a[href*="course/view.php"]', 'img[alt]'
    ];
    let name = '';
    let fallback = '';
    for (const selector of selectors) {
      const node = card.querySelector(selector);
      if (!node) continue;
      const candidate = labelFrom(node).slice(0, 250);
      if (!candidate) continue;
      if (!fallback && !placeholder(candidate)) fallback = candidate;
      if (!placeholder(candidate) && !codeLike(candidate)) { name = candidate; break; }
    }
    if (!name) name = fallback;
    if (!name) {
      // Last resort: the card's own visible text without the badge/toolbar noise.
      const clone = card.cloneNode(true);
      clone.querySelectorAll('.badge, .progress, .dropdown, .course-info-container .text-muted')
        .forEach(n => n.remove());
      const text = clean(clone.innerText || clone.textContent || '');
      if (text) name = text.split(/\n| {2,}/)[0].slice(0, 250);
    }
    if (placeholder(name)) name = '';
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
    const timeEl = li.querySelector('time[datetime], [data-timestamp]');
    const classes = Array.from(li.classList).join(' ');
    const modMatch = classes.match(/modtype_([a-z]+)/);
    activities.push({
      name,
      href,
      modtype: modMatch ? modMatch[1] : '',
      dates: clean(dateEl ? dateEl.innerText : ''),
      datetime: timeEl
        ? (timeEl.getAttribute('datetime') || timeEl.getAttribute('data-timestamp') || '')
        : '',
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


class MoodleLoginLost(MoodleParseError):
    """Raised when a page fetch lands on the login screen mid-sync."""


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
                "datetime": str(activity.get("datetime", ""))[:40],
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


_CODE_LABEL = re.compile(r"[A-Z0-9][A-Z0-9 \-_/.()]*")
_LOWERCASE_WORD = re.compile(r"[a-z]{2,}")
_LOGIN_HEADINGS = {"log in", "login", "log in to the site", "sign in"}
_PLACEHOLDER_NAME = re.compile(r"course \d+|course|open course|view course|more", re.IGNORECASE)


def is_placeholder_course_name(name: str, course_id: str = "") -> bool:
    """True for labels such as ``Course 10581`` that carry no real name."""

    text = " ".join(str(name or "").split())
    if not text:
        return True
    if course_id and text.lower() == f"course {course_id}".strip().lower():
        return True
    return bool(_PLACEHOLDER_NAME.fullmatch(text))


def looks_like_course_code(label: str) -> bool:
    """True for shortnames such as ``CS-3301-CS-261-BS-SE-5B``.

    Moodle lists both a fullname ("Database Systems") and a shortname. A code is
    uppercase, contains a digit, and has no lowercase words; a real name almost
    always has lowercase words even when fully capitalised.
    """

    text = " ".join(str(label or "").split())
    if not text or len(text) > 60:
        return False
    if not any(char.isdigit() for char in text):
        return False
    if _LOWERCASE_WORD.search(text):
        return False
    return bool(_CODE_LABEL.fullmatch(text))


def _plain_text(fragment: str) -> str:
    """Strip tags and unescape entities from a small HTML fragment."""

    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_course_display_name(html: str, *, site_title: str = "") -> str:
    """Read the full course name from a course page's header.

    Every Moodle theme renders the course fullname in the page ``<h1>`` (Boost
    puts it in ``.page-header-headings``); older themes fall back to
    ``<title>Course: <fullname> | <site>``. Returns ``""`` when the page is a
    login screen or holds no usable heading.
    """

    if not html:
        return ""

    for match in re.finditer(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL):
        text = _plain_text(match.group(1))
        if text and text.lower() not in _LOGIN_HEADINGS:
            return text[:250]

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not title_match:
        return ""

    title = _plain_text(title_match.group(1))
    if not title:
        return ""
    head = title.split("|")[0].strip()
    for prefix in ("Course:", "Course -", "Unit:"):
        if head.lower().startswith(prefix.lower()):
            head = head[len(prefix):].strip()
            break
    if not head or head.lower() in _LOGIN_HEADINGS:
        return ""
    if site_title and head.lower() == site_title.strip().lower():
        return ""
    return head[:250]


def course_name_from_heading(
    heading: str, title: str = "", *, site_title: str = ""
) -> str:
    """Pick the course fullname from a page's ``<h1>`` or ``<title>`` text."""

    heading = str(heading or "").strip()
    title = str(title or "").strip()
    if not heading and not title:
        return ""
    fragment = (
        f"<h1>{html_lib.escape(heading)}</h1><title>{html_lib.escape(title)}</title>"
    )
    return parse_course_display_name(fragment, site_title=site_title)


def parse_courses(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract unique courses from any Moodle page that links to courses.

    Moodle themes differ: standard themes render ``/course/view.php?id=N``
    anchors, while card-based themes (Moove, Boost Union variants) may render
    ``[data-course-id]`` cards outside the main region, sometimes without an
    anchor at all. Both are supported.

    The same course is usually listed twice (fullname and shortname, or a card
    and its anchor), so labels are ranked: a readable name always beats a code
    such as ``BS-1402-BS-261-BS-SE-5A``, then card labels beat loose anchors,
    then the richer label wins.
    """

    courses: dict[str, dict[str, Any]] = {}
    scores: dict[str, tuple[int, int, int]] = {}
    origin = ""
    full_url = snapshot.get("full_url", "")
    if full_url:
        parsed_page = urlparse(full_url)
        if parsed_page.scheme and parsed_page.netloc:
            origin = f"{parsed_page.scheme}://{parsed_page.netloc}"

    def rank(label: str, source: str, placeholder: bool) -> tuple[int, int, int]:
        if placeholder:
            return (0, 0, 0)
        return (
            1 if looks_like_course_code(label) else 2,
            1 if source == "card" else 0,
            len(label),
        )

    def add(href: str, name: str, fallback_id: str = "", source: str = "link") -> None:
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
        if course_id == "1":
            # Moodle's site course (front page) is never an enrolled course; links
            # to it are calendar/timeline artifacts such as "Activity event".
            return
        if href:
            key = _course_key(href)
        else:
            key = f"{origin}/course/view.php?id={course_id}"
        cleaned = " ".join(str(name).split())
        placeholder_label = is_placeholder_course_name(cleaned, course_id)
        if placeholder_label:
            cleaned = f"Course {course_id}"
        score = rank(cleaned, source, placeholder_label)
        if key in courses and score <= scores[key]:
            return
        scores[key] = score
        courses[key] = {
            "name": cleaned,
            "url": key,
            "course_id": course_id,
            "placeholder_name": placeholder_label,
        }

    for link in snapshot.get("links", []):
        href = link.get("href", "")
        if "/course/view.php" in href.lower():
            add(href, link.get("text", ""), source="link")

    for card in snapshot.get("course_cards", []):
        add(
            card.get("href", ""),
            card.get("name", ""),
            card.get("course_id", ""),
            source="card",
        )

    return sorted(courses.values(), key=lambda item: item["name"].lower())


STATUS_UNKNOWN = "unknown"
STATUS_NOT_SUBMITTED = "not_submitted"
STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_SUBMITTED_FOR_GRADING = "submitted_for_grading"
STATUS_GRADED = "graded"
STATUS_NOT_REQUIRED = "no_submission_required"
STATUS_REOPENED = "reopened"

#: Statuses that should never be flagged as pending work.
SUBMITTED_STATUSES = frozenset(
    {STATUS_SUBMITTED, STATUS_SUBMITTED_FOR_GRADING, STATUS_GRADED, STATUS_NOT_REQUIRED}
)

#: Human labels used by the dashboard.
STATUS_LABELS = {
    STATUS_UNKNOWN: "Status unknown",
    STATUS_NOT_SUBMITTED: "Not submitted",
    STATUS_DRAFT: "Draft",
    STATUS_SUBMITTED: "Submitted",
    STATUS_SUBMITTED_FOR_GRADING: "Submitted for grading",
    STATUS_GRADED: "Graded",
    STATUS_NOT_REQUIRED: "No submission required",
    STATUS_REOPENED: "Reopened",
}

_EPOCH = re.compile(r"\d{9,13}")


def normalize_submission_status(text: str) -> str:
    """Map Moodle's free-form submission status text onto a fixed vocabulary.

    Moodle themes and language packs phrase this differently ("No attempt",
    "Not submitted", "Submitted for grading"), and an empty cell means the page
    said nothing rather than "pending". Callers get ``unknown`` in that case so
    the dashboard can stay quiet instead of inventing a status.
    """

    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return STATUS_UNKNOWN
    if (
        "submission not required" in lowered
        or "no submission required" in lowered
        or "nothing to submit" in lowered
    ):
        return STATUS_NOT_REQUIRED
    if "submitted for grading" in lowered:
        return STATUS_SUBMITTED_FOR_GRADING
    if "draft" in lowered:
        # Moodle shows "Draft (not submitted)" when a draft exists, which is
        # more useful than collapsing it into plain "not submitted".
        return STATUS_DRAFT
    if "reopened" in lowered:
        # A new attempt was opened after grading: the student owes a resubmission.
        return STATUS_REOPENED
    if (
        "not submitted" in lowered
        or "no attempt" in lowered
        or "nothing submitted" in lowered
        # Moodle's own string for a student with no submission yet
        # (submissionstatus_ / submissionstatus_new) on the assignment index.
        or lowered.startswith("no submission")
    ):
        return STATUS_NOT_SUBMITTED
    if "graded" in lowered:
        return STATUS_GRADED
    if "submitted" in lowered or "submission received" in lowered:
        return STATUS_SUBMITTED
    return STATUS_UNKNOWN


def parse_due_at(value: str) -> str | None:
    """Turn Moodle's machine-readable timestamp into an ISO UTC string.

    Moodle puts the real instant in ``<time datetime>`` (ISO 8601 with an
    offset) or ``data-timestamp`` (epoch seconds). Display text such as
    "Sunday, 15 November 2026, 11:59 PM" is deliberately *not* parsed: it is
    rendered in the site's timezone and guessing would put the badge hours off.
    A value without a timezone is rejected for the same reason.
    """

    raw = str(value or "").strip()
    if not raw:
        return None
    if _EPOCH.fullmatch(raw):
        seconds = int(raw)
        if seconds > 10**11:  # milliseconds
            seconds //= 1000
        try:
            moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return moment.isoformat(timespec="seconds")
    text = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _looks_submitted(status: str) -> bool:
    return normalize_submission_status(status) in SUBMITTED_STATUSES


def parse_assignments(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract assignment activities with raw due/status text for the model."""
    found: dict[str, dict[str, Any]] = {}

    def add(
        name: str,
        href: str,
        due: str = "",
        status: str = "",
        modtype: str = "",
        timestamp: str = "",
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
            "due_at": parse_due_at(timestamp),
            "status_text": " ".join(status.split())[:200],
            "status": normalize_submission_status(status),
            "submitted": submitted,
            "pending": not submitted,
        }
        existing = found.get(key)
        if existing is None:
            found[key] = entry
        else:
            if not existing["due_text"] and entry["due_text"]:
                existing["due_text"] = entry["due_text"]
            if not existing["due_at"] and entry["due_at"]:
                existing["due_at"] = entry["due_at"]
            if not existing["status_text"] and entry["status_text"]:
                existing["status_text"] = entry["status_text"]
                existing["status"] = entry["status"]
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
            activity.get("datetime", ""),
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


def is_pending(status: str) -> bool:
    """True when a known status still means work to do.

    ``unknown`` is deliberately not pending: the dashboard says "status
    unknown" instead of claiming the student owes something.
    """

    return status in {STATUS_NOT_SUBMITTED, STATUS_DRAFT, STATUS_REOPENED}


_INDEX_JS = r"""
async (url) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  let response;
  try {
    response = await fetch(url, {credentials: 'same-origin', redirect: 'follow'});
  } catch (error) {
    return {error: 'request_failed'};
  }
  if (response.url && /\/login\//i.test(response.url)) return {login: true};
  if (!response.ok) return {error: 'http_' + response.status};
  const html = await response.text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  if ((doc.body.className || '').includes('path-login')) return {login: true};

  // The per-course assignment index lists every assignment with its due date
  // and submission status. Pick the table that actually holds assignment links.
  let best = null;
  doc.querySelectorAll('table').forEach((table) => {
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const links = table.querySelectorAll('a[href*="/mod/assign/view.php"]');
    if (!rows.length || !links.length) return;
    if (!best || rows.length > best.rows.length) best = {table, rows};
  });
  if (!best) return {headers: [], rows: []};

  const headRow = best.table.querySelector('thead tr');
  const headers = headRow
    ? Array.from(headRow.children).map((cell) => clean(cell.textContent))
    : [];
  const rows = [];
  best.rows.forEach((tr) => {
    const anchor = tr.querySelector('a[href*="/mod/assign/view.php"]');
    if (!anchor) return;
    const timeEl = tr.querySelector('time[datetime], [data-timestamp]');
    rows.push({
      name: clean(anchor.textContent),
      href: anchor.getAttribute('href') || '',
      cells: Array.from(tr.children).map((cell) => clean(cell.textContent)),
      datetime: timeEl
        ? (timeEl.getAttribute('datetime') || timeEl.getAttribute('data-timestamp') || '')
        : ''
    });
  });
  return {headers, rows: rows.slice(0, 300)};
}
"""


def _index_column(headers: list[str], *needles: str) -> int | None:
    for index, header in enumerate(headers):
        lowered = header.lower()
        if any(needle in lowered for needle in needles):
            return index
    return None


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def map_assignment_index(
    payload: dict[str, Any], *, index_url: str
) -> list[dict[str, Any]]:
    """Normalise the parsed assignment-index table into sync rows.

    Columns are located by header text because Moodle themes and plugins add or
    reorder them (grade, feedback, group). When a theme renders no header row,
    the due date and status are found by looking for a date-shaped or
    status-shaped cell instead of trusting a column position.
    """

    headers = [str(header) for header in payload.get("headers", []) if isinstance(header, str)]
    due_index = _index_column(headers, "due")
    status_index = _index_column(headers, "submission", "status")
    found: dict[str, dict[str, Any]] = {}

    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(index_url, href)
        parsed = urlparse(absolute)
        if "/mod/assign/view.php" not in parsed.path.lower():
            continue
        activity_id = parse_qs(parsed.query).get("id", [""])[0]
        key = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={activity_id}"

        cells = [str(cell) for cell in row.get("cells", []) if isinstance(cell, str)]
        due_text = ""
        if due_index is not None and due_index < len(cells):
            due_text = cells[due_index]
        if not due_text:
            due_text = next((cell for cell in cells if _YEAR.search(cell)), "")

        status_text = ""
        if status_index is not None and status_index < len(cells):
            status_text = cells[status_index]
        if not status_text:
            status_text = next(
                (
                    cell
                    for cell in cells
                    if normalize_submission_status(cell) != STATUS_UNKNOWN
                ),
                "",
            )

        status = normalize_submission_status(status_text)
        name = " ".join(str(row.get("name") or "").split()) or f"Assignment {activity_id}"
        found[key] = {
            "name": name[:250],
            "url": key,
            "activity_id": activity_id,
            "due_text": " ".join(due_text.split())[:300],
            "due_at": parse_due_at(str(row.get("datetime") or "")),
            "status_text": " ".join(status_text.split())[:200],
            "status": status,
            "submitted": status in SUBMITTED_STATUSES,
            "pending": is_pending(status),
        }

    return sorted(found.values(), key=lambda item: item["name"].lower())


async def fetch_assignment_index(page: Page, url: str) -> dict[str, Any]:
    """Read a course's assignment index through the logged-in page.

    The request goes through Chrome's own network stack (same cookies, same
    proxy/Basic-auth cache) and never navigates, so the user's visible tab stays
    where it is. Only ``/mod/assign/index.php`` is ever fetched.
    """

    try:
        raw = await page.evaluate(_INDEX_JS, url)
    except PlaywrightTimeoutError as exc:
        raise MoodleParseError("The assignment list did not load in time.") from exc
    except Exception as exc:  # Playwright's Error hierarchy differs by version
        raise MoodleParseError(f"Could not read the assignment list: {exc}") from exc
    if not isinstance(raw, dict):
        raise MoodleParseError("Unexpected assignment list result.")
    if raw.get("login"):
        raise MoodleLoginLost(
            "Moodle asked you to log in again. Finish logging in manually in Chrome."
        )
    if raw.get("error"):
        raise MoodleParseError(
            "The assignment list could not be read "
            f"({raw.get('error')}). That course may open its assignments differently."
        )
    return raw


_ASSIGN_DETAIL_JS = r"""
async (url) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  let response;
  try {
    response = await fetch(url, {credentials: 'same-origin', redirect: 'follow'});
  } catch (error) {
    return {error: 'request_failed'};
  }
  if (response.url && /\/login\//i.test(response.url)) return {login: true};
  if (!response.ok) return {error: 'http_' + response.status};
  const html = await response.text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  if ((doc.body.className || '').includes('path-login')) return {login: true};
  doc.querySelectorAll('script, style, noscript').forEach((node) => node.remove());

  const base = response.url || url;
  const text = (selector) => {
    const node = doc.querySelector(selector);
    return node ? clean(node.textContent) : '';
  };
  const root = doc.querySelector('#region-main, [role="main"], #maincontent, main') || doc.body;
  // In Moodle 4 the page header h1 is the course; the activity name is inside main.
  const heading = root.querySelector('h1, h2');

  // Only the teacher's files: the description and its attachment list. Links in
  // the submission or feedback area are the student's own uploads.
  const attachments = [];
  const seen = new Set();
  doc.querySelectorAll(
    '#intro, [data-region="activity-description"], .activity-description, .introattachments'
  ).forEach((region) => {
    region.querySelectorAll('a[href*="pluginfile.php"]').forEach((a) => {
      if (a.closest('.submissionstatustable, .feedback, [data-region="submission-status"]')) return;
      let href = '';
      try { href = new URL(a.getAttribute('href') || '', base).href; } catch (error) { return; }
      if (!/^https?:/i.test(href) || seen.has(href)) return;
      seen.add(href);
      attachments.push({text: clean(a.textContent).slice(0, 250), href});
    });
  });

  return {
    title: clean(doc.title),
    name: heading ? clean(heading.textContent) : '',
    intro: text('#intro, [data-region="activity-description"], .activity-description'),
    activity_info: text('[data-region="activity-information"]'),
    due: text('[data-region="activity-dates"], .duedate, #assign_due_date'),
    submission: text('.submissionstatustable, [data-region="submission-status"]'),
    text: clean(root.textContent),
    attachments: attachments.slice(0, 30)
  };
}
"""


def map_assignment_detail(raw: dict[str, Any], *, max_chars: int = 14000) -> dict[str, Any]:
    """Shape a background-fetched assignment page like :func:`parse_assignment_detail`."""

    name = str(raw.get("name") or "").strip()
    if not name:
        name = re.sub(r"\s*\|\s*.*$", "", str(raw.get("title") or "")).strip()
    instructions = "\n\n".join(
        part
        for part in (
            truncate_text(str(raw.get("intro") or ""), 6000),
            truncate_text(str(raw.get("activity_info") or ""), 4000),
        )
        if part
    )
    attachments = []
    for link in raw.get("attachments", []):
        if not isinstance(link, dict):
            continue
        href = str(link.get("href") or "")
        if "pluginfile.php" not in urlparse(href).path:
            continue
        attachments.append(
            {"text": str(link.get("text") or "")[:250], "href": href, "kind": "attachment"}
        )
    return {
        "name": name[:250],
        "due_text": truncate_text(str(raw.get("due") or ""), 1000),
        "submission_status_text": truncate_text(str(raw.get("submission") or ""), 2000),
        "instructions": instructions[:10000],
        "attachments": attachments[:30],
        "full_text": truncate_text(str(raw.get("text") or ""), max_chars),
    }


async def fetch_assignment_detail(
    page: Page, url: str, *, max_chars: int = 14000
) -> dict[str, Any]:
    """Read one assignment page in the background, without navigating the tab.

    Same approach as :func:`fetch_assignment_index`: Chrome's own session does
    the request, and only ``/mod/assign/view.php`` is ever fetched.
    """

    if "/mod/assign/view.php" not in urlparse(url).path.lower():
        raise MoodleParseError("Only assignment pages can be read in the background.")
    try:
        raw = await page.evaluate(_ASSIGN_DETAIL_JS, url)
    except PlaywrightTimeoutError as exc:
        raise MoodleParseError("The assignment page did not load in time.") from exc
    except Exception as exc:  # Playwright's Error hierarchy differs by version
        raise MoodleParseError(f"Could not read the assignment page: {exc}") from exc
    if not isinstance(raw, dict):
        raise MoodleParseError("Unexpected assignment page result.")
    if raw.get("login"):
        raise MoodleLoginLost(
            "Moodle asked you to log in again. Finish logging in manually in Chrome."
        )
    if raw.get("error"):
        raise MoodleParseError(f"The assignment page could not be read ({raw.get('error')}).")
    return map_assignment_detail(raw, max_chars=max_chars)


# The Timeline block's own data source. Only this one read-only web service is
# ever called; the method name is fixed here and never comes from the model.
ACTION_EVENTS_METHOD = "core_calendar_get_action_events_by_timesort"

_ACTION_EVENTS_JS = r"""
async ({origin, method, timesortfrom, limit, pages}) => {
  // The session key is read and used inside the page only. It is never
  // returned to Python, so it cannot reach logs, events, or the database.
  let sesskey = (window.M && M.cfg && M.cfg.sesskey) || '';
  if (!sesskey) {
    try {
      const home = await fetch(origin + '/my/', {credentials: 'same-origin'});
      if (home.url && /\/login\//i.test(home.url)) return {login: true};
      const match = (await home.text()).match(/"sesskey":"([A-Za-z0-9]+)"/);
      sesskey = match ? match[1] : '';
    } catch (error) {
      return {error: 'sesskey_unavailable'};
    }
  }
  if (!sesskey) return {error: 'sesskey_unavailable'};

  const endpoint = origin + '/lib/ajax/service.php?sesskey=' +
    encodeURIComponent(sesskey) + '&info=' + encodeURIComponent(method);
  const events = [];
  let after = 0;
  for (let page = 0; page < pages; page++) {
    const args = {timesortfrom, limitnum: limit, limittononsuspendedevents: true};
    if (after) args.aftereventid = after;
    let body;
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify([{index: 0, methodname: method, args}])
      });
      if (response.url && /\/login\//i.test(response.url)) return {login: true};
      if (!response.ok) return {error: 'http_' + response.status};
      body = await response.json();
    } catch (error) {
      return {error: 'request_failed'};
    }
    const first = Array.isArray(body) ? body[0] : null;
    if (!first || first.error) {
      const code = first && first.exception ? String(first.exception.errorcode || '') : '';
      if (/login|sesskey/i.test(code)) return {login: true};
      return {error: 'service_' + (code || 'error').slice(0, 40)};
    }
    const batch = (first.data && first.data.events) || [];
    batch.forEach((event) => events.push({
      modulename: String(event.modulename || ''),
      eventtype: String(event.eventtype || ''),
      url: String(event.url || ''),
      timesort: Number(event.timesort || event.timestart || 0),
      name: String(event.name || '').slice(0, 250)
    }));
    if (batch.length < limit) break;
    after = Number(first.data.lastid || 0);
    if (!after) break;
  }
  return {events};
}
"""


def map_action_events(payload: dict[str, Any], *, origin: str) -> dict[str, str]:
    """Map assignment URL (same key as the index rows) to its ISO UTC due instant.

    ``timesort`` is epoch seconds, so the badge needs no timezone guessing.
    Teacher-side ``gradingdue`` events and other modules are ignored.
    """

    deadlines: dict[str, str] = {}
    for event in payload.get("events", []):
        if not isinstance(event, dict):
            continue
        if str(event.get("modulename") or "") != "assign":
            continue
        if str(event.get("eventtype") or "due") != "due":
            continue
        absolute = urljoin(origin + "/", str(event.get("url") or ""))
        parsed = urlparse(absolute)
        if "/mod/assign/view.php" not in parsed.path.lower():
            continue
        activity_id = parse_qs(parsed.query).get("id", [""])[0]
        if not activity_id:
            continue
        due_at = parse_due_at(str(int(event.get("timesort") or 0)))
        if due_at is None:
            continue
        key = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?id={activity_id}"
        # Keep the earliest instant if Moodle reports more than one.
        if key not in deadlines or due_at < deadlines[key]:
            deadlines[key] = due_at
    return deadlines


async def fetch_assign_deadlines(
    page: Page,
    origin: str,
    *,
    lookback_days: int = 30,
    limit: int = 50,
    pages: int = 6,
) -> dict[str, str]:
    """Read upcoming and recently overdue assignment deadlines from Moodle.

    Uses the same calendar action events the Timeline block shows. Moodle only
    lists work still to act on, so submitted assignments are usually absent;
    their badge does not matter because they are no longer pending.
    """

    timesortfrom = int(datetime.now(timezone.utc).timestamp()) - lookback_days * 86400
    try:
        raw = await page.evaluate(
            _ACTION_EVENTS_JS,
            {
                "origin": origin,
                "method": ACTION_EVENTS_METHOD,
                "timesortfrom": timesortfrom,
                "limit": limit,
                "pages": pages,
            },
        )
    except PlaywrightTimeoutError as exc:
        raise MoodleParseError("Moodle's deadline list did not load in time.") from exc
    except Exception as exc:  # Playwright's Error hierarchy differs by version
        raise MoodleParseError(f"Could not read Moodle's deadline list: {exc}") from exc
    if not isinstance(raw, dict):
        raise MoodleParseError("Unexpected deadline list result.")
    if raw.get("login"):
        raise MoodleLoginLost(
            "Moodle asked you to log in again. Finish logging in manually in Chrome."
        )
    if raw.get("error"):
        raise MoodleParseError(
            f"Moodle's deadline list is unavailable ({raw.get('error')})."
        )
    return map_action_events(raw, origin=origin)
