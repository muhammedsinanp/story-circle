"""Read-only integration with instagrapi. No credentials or results are written to disk."""
import logging
import time
from datetime import datetime, timezone

from instagrapi import Client


from provider_errors import ProviderError, public_error


class ReadClient(Client):
    """Stop on restrictions instead of attempting challenge resolution or rate-limit retries."""
    def _send_private_request(self, *args, **kwargs):
        if getattr(self, "collection_check", None):
            self.collection_check()
        last = getattr(self, "last_sent_at", 0)
        if time.monotonic() - last < 1:
            time.sleep(1 - (time.monotonic() - last))
        self.last_sent_at = time.monotonic()
        try:
            return super()._send_private_request(*args, **kwargs)
        except Exception as exc:
            error = public_error(exc)
            if error.code in {"restricted", "verification"} or "Timeout" in type(exc).__name__:
                raise error from None
            raise

    def challenge_resolve(self, *args, **kwargs):
        raise ProviderError("verification", "Complete the account check in the Instagram app. Automatic collection has stopped.")

    def login_flow(self):
        # No inbox, notification, or other post-login interaction is needed here.
        return True


def user_record(user):
    return {"id": str(user.pk), "username": user.username, "name": user.full_name or ""}


class InstagramAdapter:
    def __init__(self):
        self.client = ReadClient(request_timeout=20, session_retry_total=0, public_request_retries_count=0)
        logger = logging.getLogger("storycircle.instagram.silent")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        self.client.logger = logger
        self.client.request_logger = logger
        self.last_request = 0.0

    def login(self, username, password, code=""):
        try:
            if not self.client.login(username, password, verification_code=code) or not self.client.user_id:
                raise ProviderError("credentials", "Instagram did not complete this login.")
            return {"id": str(self.client.user_id), "username": self.client.username}
        except Exception as exc:
            raise public_error(exc) from None
        finally:
            self.client.password = ""
            self.client.last_response = None
            self.client.last_json = {}

    def _call(self, method, *args, **kwargs):
        elapsed = time.monotonic() - self.last_request
        if elapsed < 1:
            time.sleep(1 - elapsed)
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            raise public_error(exc) from None
        finally:
            self.last_request = time.monotonic()

    def login_session(self, session_id):
        """Use only a session from the explicitly opened local Instagram window."""
        try:
            if not self.client.login_by_sessionid(session_id) or not self.client.user_id:
                raise ProviderError("browser_session", "Instagram did not accept the browser session for collection.")
            # Verify identity with the provider before applying the account allowlist.
            info = self._call(self.client.user_info_v1, str(self.client.user_id))
            return {"id": str(info.pk), "username": info.username}
        except Exception as exc:
            error = public_error(exc)
            if error.code in {"provider", "expired", "credentials"}:
                error = ProviderError("browser_session", "Instagram did not accept this browser session for data collection. Browser sign-in alone does not guarantee collection access.")
            raise error from None
        finally:
            self.client.password = ""
            self.client.last_response = None
            self.client.last_json = {}

    def stories(self):
        uid = str(self.client.user_id)
        stories = self._call(self.client.user_stories_v1, uid)
        return [{"id": str(s.pk), "taken_at": s.taken_at.isoformat(),
                 "kind": "Video" if s.media_type == 2 else "Photo"}
                for s in stories if str(s.user.pk) == uid]

    def _collect(self, method, target, label, report, check, limit):
        accounts, cursor, seen = {}, "", set()
        while True:
            check()
            users, next_cursor = self._call(method, target, max_amount=1 if label == "story viewers" else 200, max_id=cursor)
            check()
            for user in users:
                accounts[str(user.pk)] = user_record(user)
            report(f"Reading {label}", len(accounts))
            if len(accounts) > limit:
                raise ProviderError("capacity", f"This version supports up to {limit:,} accounts per list. No partial comparison is shown.")
            if not next_cursor:
                return accounts
            if next_cursor in seen or not users:
                raise ProviderError("incomplete", f"Instagram returned an incomplete {label} list. No missing-viewer conclusion is available.")
            seen.add(next_cursor)
            cursor = next_cursor

    def snapshot(self, story_id, report, check, limit):
        deadline = time.monotonic() + 600
        requests = 0
        def bounded_check():
            nonlocal requests
            check()
            requests += 1
            if requests > 500 or time.monotonic() > deadline:
                raise ProviderError("capacity", "Collection reached its time or request limit. No partial comparison is shown.")
        self.client.collection_check = bounded_check
        try:
            return self._snapshot(story_id, report, check, limit)
        finally:
            self.client.collection_check = None

    def _snapshot(self, story_id, report, check, limit):
        # Re-read only the authenticated account's stories before accepting a story ID.
        story = next((s for s in self.stories() if s["id"] == story_id), None)
        if story is None:
            raise ProviderError("unavailable", "This story is no longer available on your account.")
        check()
        info = self._call(self.client.user_info_v1, str(self.client.user_id))
        followers = self._collect(self.client.user_followers_v1_chunk, str(self.client.user_id), "followers", report, check, limit)
        following = self._collect(self.client.user_following_v1_chunk, str(self.client.user_id), "following", report, check, limit)
        viewers = self._collect(self.client.story_viewers_chunk, int(story_id), "story viewers", report, check, limit)
        if len(followers) != info.follower_count or len(following) != info.following_count:
            raise ProviderError("incomplete", "Returned connection lists do not match Instagram’s account counts. Your account may have changed, or Instagram returned partial data. No missing-viewer conclusion is shown.")
        check()
        return compare(followers, following, viewers, story)

    def close(self):
        self.client.password = ""
        self.client.authorization_data = {}
        self.client.settings = {}
        self.client.collection_check = None
        self.client.last_json = {}
        self.client.last_response = None
        for connection in (self.client.private, self.client.public):
            connection.cookies.clear()
            connection.headers.clear()
            connection.close()


def compare(followers, following, viewers, story):
    rows = []
    for uid in followers.keys() | following.keys() | viewers.keys():
        record = viewers.get(uid) or following.get(uid) or followers[uid]
        rows.append({**record, "follower": uid in followers, "following": uid in following,
                     "viewed": uid in viewers})
    rows.sort(key=lambda r: r["username"].lower())
    return {"story": story, "captured_at": datetime.now(timezone.utc).isoformat(), "rows": rows,
            "counts": {"followers": len(followers), "following": len(following), "viewers": len(viewers),
                       "follower_viewers": len(followers.keys() & viewers.keys()),
                       "following_viewers": len(following.keys() & viewers.keys()),
                       "not_listed": len((followers.keys() | following.keys()) - viewers.keys())},
            "note": "Not listed means absent from the returned viewer list at collection time. This cannot reveal anonymous views, prove someone never watched, or explain why they did not appear. Story audience settings and later views can affect results."}
