import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import Config, create_app
from instagram import InstagramAdapter, ProviderError, compare


ORIGIN = "https://circle.test"


class FakeAdapter:
    def __init__(self):
        self.username = None
        self.closed = False

    def login(self, username, password, code=""):
        if username == "twofactor" and code != "123456":
            raise ProviderError("two_factor", "Authenticator code required.")
        if password != "test-password":
            raise ProviderError("credentials", "Login rejected.")
        self.username = username
        return {"id": username, "username": username}

    def stories(self):
        return [{"id": "100" if self.username == "alice" else "200", "taken_at": "2026-09-06T10:00:00+00:00", "kind": "Photo"}]

    def snapshot(self, story_id, report, check, limit):
        check()
        report("Reading viewers", 2)
        followers = {"1": {"id": "1", "username": self.username + "_friend", "name": ""},
                     "2": {"id": "2", "username": "absent", "name": ""}}
        return compare(followers, {"1": followers["1"]}, {"1": followers["1"]}, self.stories()[0])

    def close(self):
        self.closed = True


def client(app):
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def begin(c):
    return c.get("/api/session").json()["csrf"]


def login(c, username="alice", csrf=None, **kwargs):
    csrf = csrf or begin(c)
    return c.post("/api/login", headers={"X-CSRF-Token": csrf}, json={"username": username, "password": "test-password", "consent": True, **kwargs})


def wait_result(c):
    for _ in range(100):
        state = c.get("/api/job").json()["job"]
        if state["status"] != "running":
            return state
        time.sleep(.005)
    raise AssertionError("Job did not finish")


def test_sessions_are_isolated_and_ownership_is_enforced():
    app = create_app(Config(origin=ORIGIN, enabled=True), FakeAdapter)
    with client(app) as alice, client(app) as bob:
        a = login(alice).json()["csrf"]
        b = login(bob, "bob").json()["csrf"]
        alice.get("/api/stories")
        bob.get("/api/stories")
        assert bob.post("/api/compare", headers={"X-CSRF-Token": b}, json={"story_id": "100"}).status_code == 404
        assert alice.post("/api/compare", headers={"X-CSRF-Token": a}, json={"story_id": "100"}).status_code == 200
        assert wait_result(alice)["status"] == "succeeded"
        assert bob.get("/api/results").status_code == 404
        data = alice.get("/api/results").json()
        assert data["counts"]["not_listed"] == 1
        assert data["counts"]["follower_viewers"] == 1
        assert data["rows"][1]["username"] == "alice_friend"
        assert alice.post("/api/disconnect", headers={"X-CSRF-Token": a}, json={}).status_code == 200
        assert alice.get("/api/results").status_code == 401
        assert bob.get("/api/stories").status_code == 200


def test_cookie_rotation_csrf_and_no_secret_echo():
    app = create_app(Config(origin=ORIGIN, enabled=True), FakeAdapter)
    with client(app) as c:
        token = begin(c)
        initial = c.cookies.get("__Host-storycircle")
        body = {"username": "alice", "password": "test-password", "consent": True}
        assert c.post("/api/login", json=body).status_code == 403
        assert c.post("/api/login", headers={"Origin": "https://evil.test", "X-CSRF-Token": token}, json=body).status_code == 403
        response = login(c, csrf=token)
        assert response.status_code == 200
        assert c.cookies.get("__Host-storycircle") != initial
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
        assert "password" not in response.text
        assert c.post("/api/disconnect", headers={"X-CSRF-Token": token}, json={}).status_code == 403
        r = c.post("/api/login", headers={"X-CSRF-Token": response.json()["csrf"]}, json={"password": "SECRET_" * 100})
        assert r.status_code == 422 and "SECRET" not in r.text
        assert c.get("/api/session").headers["cache-control"] == "no-store"


def test_two_factor_disabled_connection_and_personal_allowlist():
    disabled = create_app(Config(origin=ORIGIN), FakeAdapter)
    with client(disabled) as c:
        assert login(c).status_code == 503
    app = create_app(Config(origin=ORIGIN, enabled=True, allowed_users={"twofactor"}), FakeAdapter)
    with client(app) as c:
        csrf = begin(c)
        assert login(c, csrf=csrf).status_code == 403
        r = login(c, "twofactor", csrf=csrf)
        assert r.json()["detail"]["code"] == "two_factor"
        assert login(c, "twofactor", csrf=csrf, code="123456").status_code == 200


def test_failed_collection_never_exposes_partial_result():
    class Failing(FakeAdapter):
        def snapshot(self, *args):
            raise ProviderError("incomplete", "Missing page; no result.")
    app = create_app(Config(origin=ORIGIN, enabled=True), Failing)
    with client(app) as c:
        csrf = login(c).json()["csrf"]
        c.get("/api/stories")
        c.post("/api/compare", headers={"X-CSRF-Token": csrf}, json={"story_id": "100"})
        assert wait_result(c)["code"] == "incomplete"
        assert c.get("/api/results").status_code == 404


def test_disconnect_cancels_collection_and_removes_session():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Slow(FakeAdapter):
        def snapshot(self, sid, report, check, limit):
            entered.set()
            release.wait(2)
            check()
            return super().snapshot(sid, report, check, limit)
        def close(self):
            closed.set()
    app = create_app(Config(origin=ORIGIN, enabled=True), Slow)
    with client(app) as c:
        csrf = login(c).json()["csrf"]
        c.get("/api/stories")
        c.post("/api/compare", headers={"X-CSRF-Token": csrf}, json={"story_id": "100"})
        assert entered.wait(1)
        c.post("/api/disconnect", headers={"X-CSRF-Token": csrf}, json={})
        assert c.get("/api/results").status_code == 401
        release.set()
        assert closed.wait(1)


def test_ids_not_usernames_drive_comparison():
    followers = {"1": {"id": "1", "username": "old_name"}, "2": {"id": "2", "username": "same_name"}}
    viewers = {"1": {"id": "1", "username": "new_name"}, "3": {"id": "3", "username": "same_name"}}
    data = compare(followers, {}, viewers, {"id": "100"})
    assert data["counts"]["not_listed"] == 1
    assert data["counts"]["follower_viewers"] == 1
    assert next(r for r in data["rows"] if r["id"] == "1")["username"] == "new_name"


def test_adapter_pagination_preserves_all_users_and_rejects_loop():
    adapter = InstagramAdapter.__new__(InstagramAdapter)
    adapter._call = lambda method, *args, **kwargs: method(*args, **kwargs)
    pages = {"": ([SimpleNamespace(pk="1", username="one", full_name="")], "next"),
             "next": ([SimpleNamespace(pk="2", username="two", full_name="")], None)}
    method = lambda target, max_amount, max_id: pages[max_id]
    result = adapter._collect(method, "own", "followers", lambda *_: None, lambda: None, 5000)
    assert set(result) == {"1", "2"}
    pages["next"] = (pages["next"][0], "next")
    try:
        adapter._collect(method, "own", "followers", lambda *_: None, lambda: None, 5000)
    except ProviderError as e:
        assert e.code == "incomplete"
    else:
        raise AssertionError("Repeated pagination cursor accepted")


def test_expiry_login_rate_limit_and_payload_limits():
    app = create_app(Config(origin=ORIGIN, enabled=True, session_seconds=-1), FakeAdapter)
    with client(app) as c:
        assert login(c).status_code == 200
        assert c.get("/api/results").status_code == 401
    app = create_app(Config(origin=ORIGIN, enabled=True), FakeAdapter)
    with client(app) as c:
        csrf = begin(c)
        for _ in range(5):
            assert login(c, csrf=csrf, password="wrong").status_code == 401
        assert login(c, csrf=csrf, password="wrong").status_code == 429
        assert c.post("/api/login", content="x" * 9000).status_code == 413
        assert c.get("/", headers={"Host": "evil.test"}).status_code == 400


def test_served_frontend_and_static_files():
    app = create_app(Config(origin=ORIGIN), FakeAdapter)
    with client(app) as c:
        assert "Connect your account" in c.get("/").text
        assert c.get("/assets/app.js").status_code == 200
        assert c.get("/assets/style.css").status_code == 200
        assert c.get("/assets/app.py").status_code == 404


def test_railway_probe_host_is_limited_to_health():
    app = create_app(Config(origin=ORIGIN), FakeAdapter)
    with client(app) as c:
        headers = {"Host": "healthcheck.railway.app"}
        assert c.get("/health", headers=headers).status_code == 200
        assert c.get("/api/session", headers=headers).status_code == 400
        assert c.get("/", headers=headers).status_code == 400
        assert c.post("/api/login", headers=headers, json={}).status_code == 400
