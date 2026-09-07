"""Browser lifecycle tests; runnable with standard-library unittest, without Instagram."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from browser_login import LOGIN_URL, browser_session
from provider_errors import ProviderError


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.cancelled = threading.Event()
        self.page = Mock(url="https://www.instagram.com/")
        self.page.is_closed.return_value = False
        self.context = Mock()
        self.context.new_page.return_value = self.page
        self.context.cookies.return_value = [
            {"name": "sessionid", "value": "session-secret"},
            {"name": "ds_user_id", "value": "123"},
        ]
        self.browser = Mock()
        self.browser.new_context.return_value = self.context
        self.chromium = Mock()
        self.chromium.launch.return_value = self.browser
        self.manager = Mock()
        self.manager.__enter__ = Mock(return_value=SimpleNamespace(chromium=self.chromium))
        self.manager.__exit__ = Mock(return_value=False)

    def connect(self, **kwargs):
        return browser_session(self.cancelled, playwright_factory=lambda: self.manager, **kwargs)

    def test_login_opens_real_visible_page_and_closes_browser(self):
        self.assertEqual(self.connect(), "session-secret")
        self.chromium.launch.assert_called_once_with(headless=False)
        self.page.goto.assert_called_once_with(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        self.context.cookies.assert_called_once_with(["https://www.instagram.com/"])
        self.browser.close.assert_called_once()

    def test_cancel_discards_session_and_closes_browser(self):
        self.cancelled.set()
        with self.assertRaises(ProviderError) as error:
            self.connect()
        self.assertEqual(error.exception.code, "cancelled")
        self.context.cookies.assert_not_called()
        self.browser.close.assert_called_once()

    def test_timeout_closes_browser(self):
        with self.assertRaises(ProviderError) as error:
            self.connect(timeout=0)
        self.assertEqual(error.exception.code, "browser_timeout")
        self.browser.close.assert_called_once()

    def test_closed_window_is_reported(self):
        self.page.is_closed.return_value = True
        with self.assertRaises(ProviderError) as error:
            self.connect()
        self.assertEqual(error.exception.code, "browser_closed")

    def test_prompts_and_other_origins_are_not_treated_as_complete(self):
        for url in ("https://www.instagram.com/challenge/", "https://www.instagram.com/accounts/login/",
                    "https://www.instagram.com.evil.test/", "http://www.instagram.com/"):
            with self.subTest(url=url):
                self.cancelled.clear()
                self.context.cookies.reset_mock()
                self.page.url = url
                self.page.wait_for_timeout.side_effect = lambda _: self.cancelled.set()
                with self.assertRaises(ProviderError):
                    self.connect()
                self.context.cookies.assert_not_called()

    def test_partial_cookie_state_does_not_finish_login(self):
        self.context.cookies.return_value = [{"name": "sessionid", "value": "session-secret"}]
        self.page.wait_for_timeout.side_effect = lambda _: self.cancelled.set()
        with self.assertRaises(ProviderError) as error:
            self.connect()
        self.assertEqual(error.exception.code, "cancelled")

    def test_browser_errors_do_not_expose_internals(self):
        self.page.goto.side_effect = RuntimeError("session-secret private page contents")
        with self.assertRaises(ProviderError) as error:
            self.connect()
        self.assertEqual(error.exception.code, "browser_unavailable")
        self.assertNotIn("session-secret", str(error.exception))
        self.browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
