import threading
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import Config, create_app
from instagram import ProviderError


ORIGIN = "http://127.0.0.1:8000"


def setup(peer="127.0.0.1", connector=None, **config):
    adapter = Mock()
    adapter.login_session.return_value = {"id": "123", "username": "alice"}
    connector = connector or Mock(return_value="local-session-secret")
    app = create_app(Config(origin=ORIGIN, enabled=True, browser_login=True, **config),
                     adapter_factory=lambda: adapter, browser_connector=connector)
    client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}, client=(peer, 50000))
    return client, adapter, connector


def begin(client):
    return client.get("/api/session").json()["csrf"]


def connect(client, csrf):
    return client.post("/api/login/browser", json={"consent": True}, headers={"X-CSRF-Token": csrf})


def test_browser_login_rotates_session_and_keeps_cookie_secret_server_side():
    c, adapter, connector = setup()
    with c:
        csrf = begin(c)
        old_cookie = c.cookies.get("storycircle_dev")
        response = connect(c, csrf)
        assert response.status_code == 200
        assert response.json()["user"] == {"id": "123", "username": "alice"}
        assert c.cookies.get("storycircle_dev") != old_cookie
        assert response.json()["csrf"] != csrf
        assert "local-session-secret" not in response.text
        adapter.login_session.assert_called_once_with("local-session-secret")
        assert c.get("/api/session").json()["browser_login"] is True
        assert c.post("/api/disconnect", json={}, headers={"X-CSRF-Token": response.json()["csrf"]}).status_code == 200
        adapter.close.assert_called_once()


def test_remote_origins_and_clients_cannot_open_local_browser():
    with pytest.raises(RuntimeError):
        create_app(Config(origin="https://circle.test", browser_login=True))
    c, _, connector = setup(peer="203.0.113.1")
    with c:
        assert connect(c, begin(c)).status_code == 403
        connector.assert_not_called()


def test_origin_csrf_and_consent_are_required():
    c, _, connector = setup()
    with c:
        csrf = begin(c)
        assert c.post("/api/login/browser", json={"consent": True}).status_code == 403
        assert c.post("/api/login/browser", json={"consent": True}, headers={"X-CSRF-Token": csrf, "Origin": "https://evil.test"}).status_code == 403
        assert c.post("/api/login/browser", json={"consent": False}, headers={"X-CSRF-Token": csrf}).status_code == 400
        connector.assert_not_called()


def test_allowlist_uses_instagram_verified_identity_and_closes_rejected_session():
    c, adapter, _ = setup(allowed_users={"bob"})
    with c:
        assert connect(c, begin(c)).status_code == 403
        adapter.close.assert_called_once()
        assert c.get("/api/session").json()["user"] is None


def test_provider_rejection_clears_adapter_and_allows_retry():
    c, adapter, _ = setup()
    adapter.login_session.side_effect = ProviderError("browser_session", "Instagram declined this session.")
    with c:
        csrf = begin(c)
        assert connect(c, csrf).json()["detail"]["code"] == "browser_session"
        adapter.close.assert_called_once()
        adapter.login_session.side_effect = None
        assert connect(c, csrf).status_code == 200


def test_browser_mode_rejects_password_submission():
    c, adapter, connector = setup()
    with c:
        csrf = begin(c)
        r = c.post("/api/login", headers={"X-CSRF-Token": csrf}, json={"username": "alice", "password": "unused", "consent": True})
        assert r.status_code == 409
        adapter.login.assert_not_called()
        connector.assert_not_called()


def test_disconnect_cancels_pending_window_and_cannot_resurrect_session():
    entered = threading.Event()

    def window(cancelled):
        entered.set()
        assert cancelled.wait(5)
        return "local-session-secret"

    c, adapter, _ = setup(connector=window)
    with c:
        csrf = begin(c)
        responses = []
        worker = threading.Thread(target=lambda: responses.append(connect(c, csrf)))
        worker.start()
        try:
            assert entered.wait(5)
            assert connect(c, csrf).status_code == 409
            assert c.post("/api/disconnect", json={}, headers={"X-CSRF-Token": csrf}).status_code == 200
        finally:
            worker.join(6)
        assert not worker.is_alive()
        assert responses[0].status_code == 401
        adapter.login_session.assert_not_called()
        assert c.get("/api/session").json()["user"] is None
