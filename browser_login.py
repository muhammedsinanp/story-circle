"""Opt-in desktop login. Credentials are entered by the user on instagram.com."""
import time
from urllib.parse import urlsplit

from provider_errors import ProviderError


LOGIN_URL = "https://www.instagram.com/accounts/login/"


def browser_session(cancelled, timeout=300, playwright_factory=None):
    if playwright_factory is None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ProviderError("browser_setup", "Install requirements-browser.txt and run python -m playwright install chromium first.") from None
        playwright_factory = sync_playwright
    deadline = time.monotonic() + timeout
    try:
        with playwright_factory() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                # Separate temporary context: never read the user's existing browser profile.
                context = browser.new_context()
                page = context.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
                while time.monotonic() < deadline:
                    if cancelled.is_set():
                        raise ProviderError("cancelled", "Instagram connection cancelled.")
                    if page.is_closed():
                        raise ProviderError("browser_closed", "The Instagram window was closed. Open it again to connect.")
                    location = urlsplit(page.url)
                    # Let the user finish login and any Instagram prompts themselves.
                    if (location.scheme == "https" and location.hostname in {"instagram.com", "www.instagram.com"}
                            and location.path == "/"):
                        cookies = context.cookies(["https://www.instagram.com/"])
                        session_id = next((c["value"] for c in cookies if c["name"] == "sessionid"), None)
                        user_id = next((c["value"] for c in cookies if c["name"] == "ds_user_id"), None)
                        if session_id and user_id:
                            return session_id
                    page.wait_for_timeout(500)
                raise ProviderError("browser_timeout", "Login timed out after five minutes. Open Instagram again to retry.")
            finally:
                browser.close()
    except ProviderError:
        raise
    except Exception:
        # Browser exceptions can include page URLs and internals. Never return them.
        raise ProviderError("browser_unavailable", "The Instagram browser could not finish. Close any remaining login window and retry. Check the local browser setup if no window opened.") from None
