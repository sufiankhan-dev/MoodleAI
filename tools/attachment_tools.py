"""Download Moodle attachments into the assignment workspace.

Read-only with respect to Moodle: we only fetch files the logged-in user can
already see. Local writes never overwrite an existing file without approval.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import Field

from app.security import sanitize_filename, validate_http_url, UrlSecurityError
from browser.moodle import take_snapshot
from browser.session import BrowserError
from tools.base import StrictArgs, ToolContext, ToolError, ToolRegistry, ToolSpec
from tools.file_utils import write_bytes_atomic
from tools.workspace import assignment_workspace

_CONTENT_DISPOSITION_STAR = re.compile(r"filename\*\s*=\s*UTF-8''([^;]+)", re.IGNORECASE)
_CONTENT_DISPOSITION_PLAIN = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


class DownloadArgs(StrictArgs):
    url: str = Field(
        description=(
            "Absolute Moodle attachment URL (pluginfile.php, mod/resource/view.php, or "
            "mod/folder/view.php) taken from the assignment page."
        )
    )
    filename: str = Field(
        default="",
        description="Optional output filename. Leave empty to use the server's filename.",
    )
    course_name: str = Field(default="", description="Course name; defaults to the open course.")
    assignment_name: str = Field(
        default="", description="Assignment name; defaults to the open assignment."
    )


def filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    star = _CONTENT_DISPOSITION_STAR.search(header)
    if star:
        return unquote(star.group(1)).strip()
    plain = _CONTENT_DISPOSITION_PLAIN.search(header)
    if plain:
        return plain.group(1).strip()
    return None


def _filename_from_url(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    if name and name not in {"pluginfile.php", "view.php", "download.php"}:
        return name
    return "attachment"


def _is_resource_page(url: str) -> bool:
    lowered = url.lower()
    return "/mod/resource/view.php" in lowered or "/mod/folder/view.php" in lowered


async def _resolve_attachment_url(
    ctx: ToolContext, url: str
) -> tuple[str, str, str | None]:
    """Return (direct_file_url, suggested_name, return_url_or_none)."""
    if not _is_resource_page(url):
        return url, "", None

    try:
        await ctx.browser.navigate(url)
    except BrowserError as exc:
        raise ToolError(str(exc)) from exc

    snapshot = await take_snapshot(ctx.browser.page, max_chars=ctx.settings.max_page_chars)
    if snapshot.get("page_type") == "login":
        raise ToolError("Moodle asked you to log in again. Finish logging in manually in Chrome.")

    candidates = [
        link
        for link in snapshot.get("links", [])
        if link.get("kind") == "attachment" or "pluginfile.php" in link.get("href", "")
    ]
    if len(candidates) == 1:
        link = candidates[0]
        return link["href"], link.get("text", ""), None
    if len(candidates) > 1:
        raise ToolError(
            "That resource page contains several files. Choose one and call download_attachment "
            "for each file.",
            data={
                "candidates": [
                    {"text": c.get("text", ""), "url": c.get("href", "")}
                    for c in candidates[:20]
                ]
            },
        )
    raise ToolError(
        "No downloadable file was found on that resource page. It may be a label, page, or "
        "external link rather than a file."
    )


async def download_attachment(ctx: ToolContext, args: DownloadArgs) -> dict[str, Any]:
    if not ctx.browser.is_running:
        raise ToolError("The browser is not connected. Connect it and log in first.")

    try:
        url = validate_http_url(args.url, allow_loopback=True)
    except UrlSecurityError as exc:
        raise ToolError(f"Download blocked: {exc}") from exc

    if not ctx.browser.policy.permits(url):
        raise ToolError("Download blocked: the file is not hosted on your Moodle site.")

    assignment = ctx.current_assignment() or {}
    course = ctx.current_course() or {}
    course_name = args.course_name.strip() or assignment.get("course_name") or course.get("name", "")
    assignment_name = args.assignment_name.strip() or assignment.get("name", "")
    if not course_name or not assignment_name:
        raise ToolError(
            "Open the assignment first, or pass course_name and assignment_name, so the file "
            "is saved in the right workspace."
        )

    ctx.check_cancel()
    ctx.log("download", f"Fetching attachment: {urlparse(url).path.rsplit('/', 1)[-1]}")
    direct_url, link_text, _return_url = await _resolve_attachment_url(ctx, url)

    try:
        response = await ctx.browser.page.request.get(
            direct_url, timeout=ctx.settings.browser_timeout_ms
        )
    except Exception as exc:
        raise ToolError(f"Download failed: {exc}") from exc

    if response.status >= 400:
        raise ToolError(
            f"Moodle returned HTTP {response.status} for that file. It may require a different "
            "login, or the link may have expired."
        )

    headers = response.headers
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type in {"text/html", "application/xhtml+xml"}:
        raise ToolError(
            "Moodle returned an HTML page instead of a file. The link may point to a view page "
            "or your session may have expired."
        )

    length_header = headers.get("content-length")
    if length_header and length_header.isdigit():
        if int(length_header) > ctx.settings.max_download_bytes:
            raise ToolError(
                f"That file is larger than the {ctx.settings.max_download_mb} MB download limit."
            )

    body = await response.body()
    if len(body) > ctx.settings.max_download_bytes:
        raise ToolError(
            f"That file is larger than the {ctx.settings.max_download_mb} MB download limit."
        )

    suggested = (
        args.filename.strip()
        or filename_from_disposition(headers.get("content-disposition"))
        or (_filename_from_url(direct_url) if direct_url != url else "")
        or link_text
        or "attachment"
    )
    filename = sanitize_filename(suggested, fallback="attachment")
    if "." not in filename:
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            filename += guessed

    base, relative = assignment_workspace(ctx.jail, course_name, assignment_name)
    target = base / "attachments" / filename

    if target.exists():
        approved = await ctx.require_approval(
            "overwrite_file",
            {
                "path": f"{relative}/attachments/{filename}",
                "size_bytes": target.stat().st_size,
                "reason": "An attachment with this name already exists.",
            },
        )
        if not approved:
            raise ToolError(
                "The user declined overwriting the existing attachment. "
                "Pass a different filename if you need a second copy."
            )

    write_bytes_atomic(target, body)
    relative_path = f"{relative}/attachments/{filename}"
    db_id = assignment.get("db_id")
    ctx.db.record_file(relative_path, len(body), "attachment", db_id)
    ctx.log("file", f"Saved attachment: {relative_path}", {"bytes": len(body)})

    return {
        "ok": True,
        "path": relative_path,
        "filename": filename,
        "bytes": len(body),
        "content_type": content_type or "unknown",
        "message": f"Downloaded {filename} ({len(body)} bytes) to the assignment workspace.",
    }


def register_attachment_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="download_attachment",
            description=(
                "Download a file linked on the assignment page into workspace/<course>/<assignment>/"
                "attachments/. Use the URL from the assignment details. Read-only on Moodle; "
                "asks before overwriting a local file."
            ),
            args_model=DownloadArgs,
            handler=download_attachment,
            read_only=True,
        )
    )
