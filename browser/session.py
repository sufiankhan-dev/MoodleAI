"""A visible, persistent Playwright browser session for manual Moodle login.

Design rules enforced here:
* The browser is never headless: the user must see what the agent is doing.
* No password is read, typed, stored, or logged. Login is manual.
* All navigation is http(s) and, for model-requested navigation, restricted to
  the configured Moodle host (and its subdomains).
* The persistent profile lives under ``browser/profile`` and is gitignored.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable

from playwright.async_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.config import Settings
from app.security import UrlPolicy, UrlSecurityError, redact_url, validate_http_url

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, str, dict[str, Any] | None], None]


class BrowserError(RuntimeError):
    """A browser failure that should be shown to the user verbatim."""


class BrowserState(str, Enum):
    DISCONNECTED = "disconnected"
    STARTING = "starting"
    LOGIN_REQUIRED = "login_required"
    READY = "ready"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass
class BrowserStatus:
    state: BrowserState = BrowserState.DISCONNECTED
    url: str = ""
    title: str = ""
    logged_in: bool | None = None
    profile_dir: str = ""
    channel: str = "chrome"
    error: str | None = None
    opened_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["connected"] = self.state not in {
            BrowserState.DISCONNECTED,
            BrowserState.ERROR,
        }
        return payload


class BrowserSession:
    """Owns the single visible Chrome window used by the agent."""

    def __init__(self, settings: Settings, on_event: EventCallback | None = None) -> None:
        self.settings = settings
        self._on_event = on_event
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._policy = UrlPolicy.from_settings(settings.moodle_url)
        self._status = BrowserStatus(
            profile_dir=str(settings.browser_profile_dir),
            channel=settings.browser_channel,
        )
        self._launch_lock = asyncio.Lock()
        self._closed_by_user = False
        self._native_auth_prompt = False
        self._http_credentials: dict[str, str] | None = None

    # ---------- public state ----------

    @property
    def status(self) -> BrowserStatus:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._context is not None and self._page is not None

    @property
    def policy(self) -> UrlPolicy:
        return self._policy

    @property
    def http_credentials(self) -> dict[str, str] | None:
        """In-memory network credentials, if Chrome was launched with them."""

        return self._http_credentials

    @property
    def page(self) -> Page:
        """The visible Playwright page (raises if the browser is disconnected)."""
        return self._require_page()

    def _emit(self, kind: str, message: str, data: dict[str, Any] | None = None) -> None:
        logger.info("%s: %s", kind, message)
        if self._on_event is not None:
            try:
                self._on_event(kind, message, data)
            except Exception:  # an event sink must never break the browser
                logger.exception("Browser event callback failed")

    def _set_state(
        self,
        state: BrowserState,
        *,
        error: str | None = None,
        logged_in: bool | None = None,
    ) -> None:
        self._status.state = state
        self._status.error = error
        if logged_in is not None:
            self._status.logged_in = logged_in

    def _require_page(self) -> Page:
        if self._context is None or self._page is None:
            raise BrowserError(
                "The browser is not connected. Open the dashboard, enter your Moodle URL, "
                "and click Connect (then log in manually in the Chrome window)."
            )
        return self._page

    # ---------- lifecycle ----------

    async def start(
        self,
        url: str,
        *,
        http_credentials: dict[str, str] | None = None,
    ) -> BrowserStatus:
        """Launch visible Chrome with the persistent profile and open ``url``.

        ``http_credentials`` are optional and exist only to answer a network/HTTP
        Basic-auth challenge (campus proxies that pop a username/password dialog
        before the LMS loads). They are kept in memory for the lifetime of the
        Chrome context, never logged and never written to disk.
        """
        if self.settings.browser_headless:
            raise BrowserError(
                "BROWSER_HEADLESS is enabled, but this agent must stay visible. "
                "Set BROWSER_HEADLESS=false in .env and restart."
            )

        validated = validate_http_url(url, allow_loopback=True)
        async with self._launch_lock:
            if self.is_running:
                if http_credentials and http_credentials != self._http_credentials:
                    # Playwright can only set HTTP credentials at context launch.
                    await self._hard_cleanup()
                else:
                    await self.navigate(validated, allow_external=False)
                    return self._status

            self._closed_by_user = False
            self._http_credentials = dict(http_credentials) if http_credentials else None
            self._set_state(BrowserState.STARTING)
            self._emit("browser", "Launching visible Chrome with the persistent profile")
            self.settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)

            launch_options: dict[str, object] = {}
            if self._http_credentials:
                launch_options["http_credentials"] = {
                    "username": self._http_credentials["username"],
                    "password": self._http_credentials["password"],
                }

            try:
                self._playwright = await async_playwright().start()
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.settings.browser_profile_dir),
                    channel=self.settings.browser_channel,
                    headless=False,
                    no_viewport=True,
                    accept_downloads=True,
                    args=["--start-maximized"],
                    **launch_options,
                )
            except PlaywrightError as exc:
                message = str(exc)
                await self._hard_cleanup()
                if "ProcessSingleton" in message or "already in use" in message.lower():
                    friendly = (
                        "This browser profile is already open in another Chrome window "
                        "or another MoodleAI instance. Close it, then reconnect."
                    )
                elif "Executable doesn't exist" in message:
                    friendly = (
                        f"Playwright could not find the {self.settings.browser_channel} "
                        "browser. Install Chrome, or run: playwright install chromium"
                    )
                else:
                    friendly = f"Could not launch the browser: {message}"
                self._set_state(BrowserState.ERROR, error=friendly)
                self._emit("error", friendly)
                raise BrowserError(friendly) from exc

            self._context.set_default_timeout(self.settings.browser_timeout_ms)
            self._context.on("close", self._handle_context_closed)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            self._page.on("close", self._handle_page_closed)

            if not self._policy.base_hosts:
                self._policy = self._policy.add_host(validated)
            self._status.opened_at = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._emit("browser", "Chrome launched. Waiting for manual Moodle login.")

            try:
                await self.navigate(validated, allow_external=True)
            except BrowserError as exc:
                # Browser is up even if the initial page failed; keep it visible.
                self._set_state(BrowserState.UNKNOWN, error=str(exc))
                self._emit("error", str(exc))

            await self.refresh_status()
            return self._status

    async def _enable_native_auth_prompt(self) -> bool:
        """Best-effort: stop Playwright from cancelling HTTP auth challenges.

        Playwright answers HTTP auth challenges itself and cancels them when no
        ``httpCredentials`` are configured, surfacing as
        ``net::ERR_INVALID_AUTH_CREDENTIALS`` instead of Chrome's dialog. Disabling
        the Fetch domain lets some Chrome builds show the native prompt again; when
        it does not, the dashboard asks the user for network credentials instead.
        """

        if self._native_auth_prompt:
            return True
        page = self._require_page()
        try:
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Fetch.disable")
        except PlaywrightError:
            logger.debug("Could not disable auth interception", exc_info=True)
            return False
        self._native_auth_prompt = True
        logger.debug("Fetch domain disabled to allow Chrome's native auth prompt")
        return True

    async def _goto_with_auth_fallback(self, url: str) -> None:
        """Navigate, explaining a Basic-auth proxy challenge if Chrome cannot answer it."""

        page = self._require_page()
        timeout = self.settings.browser_timeout_ms
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            if "ERR_INVALID_AUTH_CREDENTIALS" not in str(exc):
                raise
            # Playwright answers HTTP auth challenges itself; retry once with the
            # Fetch domain disabled in case this Chrome build shows its own dialog.
            if not await self._enable_native_auth_prompt():
                raise

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=min(timeout, 15000))
        except PlaywrightTimeoutError:
            raise BrowserError(
                "Chrome is waiting for the site credentials. Enter them in the Chrome "
                "window, then log in to Moodle."
            )
        except PlaywrightError as exc:
            if "ERR_INVALID_AUTH_CREDENTIALS" not in str(exc):
                raise
            raise BrowserError(
                "This Moodle site asks for a network username and password (HTTP Basic "
                "auth) before the page can open. Open 'Network login' in the Moodle "
                "connection panel, enter those credentials, and click Connect again."
            ) from exc

    async def navigate(self, url: str, *, allow_external: bool = False) -> BrowserStatus:
        """Navigate the visible page to a validated URL."""
        page = self._require_page()
        try:
            validated = validate_http_url(url, allow_loopback=True)
        except UrlSecurityError as exc:
            raise BrowserError(f"Navigation blocked: {exc}") from exc

        if not allow_external and self._policy.base_hosts and not self._policy.permits(validated):
            raise BrowserError(
                "Navigation blocked: that address is outside your Moodle site. "
                "If it is a legitimate university login page, open it manually in Chrome."
            )

        self._emit("navigate", f"Navigating to {redact_url(validated)}")
        try:
            await self._goto_with_auth_fallback(validated)
        except PlaywrightTimeoutError as exc:
            current = page.url
            if current and current != "about:blank":
                self._emit(
                    "warning",
                    f"Page load timed out, but the browser is on {redact_url(current)}. "
                    "Continuing with the loaded content.",
                )
            else:
                raise BrowserError(
                    "Moodle did not respond in time. Check your connection/VPN and try again."
                ) from exc
        except PlaywrightError as exc:
            raise BrowserError(f"Browser navigation failed: {exc}") from exc

        self._status.error = None
        await page.wait_for_timeout(250)
        await self.refresh_status()
        return self._status

    async def refresh_status(self) -> BrowserStatus:
        """Re-check the visible page and login markers."""
        if not self.is_running:
            if self._status.state != BrowserState.ERROR:
                self._set_state(BrowserState.DISCONNECTED)
            return self._status

        page = self._require_page()
        try:
            self._status.url = redact_url(page.url)
            self._status.title = (await page.title())[:200]
        except PlaywrightError:
            return self._status

        logged_in = await self._detect_login(page)
        self._status.logged_in = logged_in
        if logged_in is True:
            if self._status.state != BrowserState.READY:
                self._emit("browser", "Moodle session detected: logged in")
            self._set_state(BrowserState.READY, logged_in=True)
        elif logged_in is False:
            if self._status.state != BrowserState.LOGIN_REQUIRED:
                self._emit(
                    "browser",
                    "Moodle login required. Log in manually in the Chrome window.",
                )
            self._set_state(BrowserState.LOGIN_REQUIRED, logged_in=False)
        else:
            if self._status.state not in {BrowserState.ERROR, BrowserState.STARTING}:
                self._set_state(BrowserState.UNKNOWN, error=self._status.error)
        return self._status

    async def _detect_login(self, page: Page) -> bool | None:
        """Heuristic login detection that works across common Moodle themes.

        Returns True (logged in), False (login form/SSO), or None (unknown).
        """
        try:
            url = page.url.lower()
            if url in {"about:blank", ""}:
                return None

            password_fields = await page.locator('input[type="password"]').count()
            username_fields = await page.locator(
                'input[name="username"], input[name="loginfmt"], #username'
            ).count()
            user_menu = await page.locator(
                '#user-menu-toggle, [data-region="user-menu"], .usermenu, '
                'a[href*="logout.php"], [data-region="user-menu-toggle"]'
            ).count()

            if password_fields > 0 or username_fields > 0:
                return False
            if user_menu > 0:
                return True
            if "/login/" in url or "login/index.php" in url:
                return False

            body_text = (await page.locator("body").inner_text(timeout=3000))[:6000].lower()
            if "log out" in body_text or "logout" in body_text:
                return True
            if "dashboard" in url or "/my/" in url:
                return True
            return None
        except PlaywrightError:
            return None

    def add_allowed_host(self, url: str) -> None:
        """Allow an explicitly user-entered Moodle host (e.g. after a URL change)."""
        self._policy = self._policy.add_host(url)

    async def require_login(self) -> None:
        """Raise a clear error if the user has not completed manual login."""
        if not self.is_running:
            raise BrowserError(
                "The browser is not connected. Enter your Moodle URL in the dashboard, "
                "click Connect, and log in manually in the visible Chrome window."
            )
        await self.refresh_status()
        if self._status.logged_in is not True:
            raise BrowserError(
                "Moodle is not logged in yet. Please finish logging in manually in the "
                "Chrome window (including any SSO/MFA step), then ask again."
            )

    async def current_page(self) -> dict[str, Any]:
        page = self._require_page()
        try:
            return {
                "url": redact_url(page.url),
                "title": (await page.title())[:200],
                "state": self._status.state.value,
                "logged_in": self._status.logged_in,
            }
        except PlaywrightError as exc:
            raise BrowserError(f"Could not read the current page: {exc}") from exc

    async def close(self) -> None:
        """Close the browser the user asked to close."""
        self._closed_by_user = True
        await self._hard_cleanup()
        self._set_state(BrowserState.DISCONNECTED)
        self._emit("browser", "Browser closed")

    async def _hard_cleanup(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._page = None
        self._playwright = None
        self._native_auth_prompt = False
        self._http_credentials = None
        if context is not None:
            try:
                await context.close()
            except Exception:
                logger.debug("Context already closed", exc_info=True)
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                logger.debug("Playwright already stopped", exc_info=True)

    def _handle_context_closed(self, _context: BrowserContext | None = None) -> None:
        if self._closed_by_user:
            return
        self._context = None
        self._page = None
        self._set_state(
            BrowserState.DISCONNECTED,
            error="The Chrome window was closed. Reconnect to continue.",
        )
        self._emit("browser", "Chrome window closed by user")

    def _handle_page_closed(self, _page: Page | None = None) -> None:
        # Chrome may keep the context alive with another tab; if none remain,
        # the context close handler will fire.
        if self._context is not None and not self._context.pages:
            self._handle_context_closed()
