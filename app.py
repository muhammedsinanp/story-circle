"""Single-process web service: isolated, expiring in-memory Instagram sessions."""
import asyncio
import hashlib
import os
import re
import secrets
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr

from instagram import InstagramAdapter, ProviderError, public_error


@dataclass
class Config:
    origin: str = "http://127.0.0.1:8000"
    enabled: bool = False
    allowed_users: set = field(default_factory=set)
    max_sessions: int = 100
    max_accounts: int = 5000
    session_seconds: int = 3600
    max_jobs: int = 4

    @classmethod
    def environment(cls):
        origin = os.getenv("APP_ORIGIN", "http://127.0.0.1:8000").rstrip("/")
        parsed = urlsplit(origin)
        if parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.hostname:
            raise RuntimeError("APP_ORIGIN must be a single origin without a path or credentials")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
            raise RuntimeError("Public deployment requires an HTTPS APP_ORIGIN")
        return cls(origin=origin, enabled=os.getenv("INSTAGRAM_ENABLED", "false").lower() == "true",
                   allowed_users={u.strip().lower().lstrip("@") for u in os.getenv("ALLOWED_IG_USERS", "").split(",") if u.strip()})


@dataclass
class Session:
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    expires: float = field(default_factory=lambda: time.monotonic() + 300)
    adapter: object = None
    user: dict | None = None
    login_name: str = ""
    stories: list = field(default_factory=list)
    job: dict | None = None
    result: dict | None = None
    busy: bool = False
    cancelled: threading.Event = field(default_factory=threading.Event)


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=30, pattern=r"^@?[A-Za-z0-9_.]+$")
    password: SecretStr = Field(min_length=1, max_length=512)
    code: str = Field(default="", max_length=12, pattern=r"^[0-9 ]*$")
    consent: bool


class StoryBody(BaseModel):
    story_id: str = Field(min_length=1, max_length=40, pattern=r"^[0-9]+$")


def create_app(config=None, adapter_factory=InstagramAdapter):
    config = config or Config.environment()
    sessions, buckets, active_accounts, account_runs = {}, {}, set(), {}
    guard = threading.RLock()
    pool = ThreadPoolExecutor(max_workers=config.max_jobs, thread_name_prefix="story-reader")
    web = Path(__file__).parent / "web"
    secure = config.origin.startswith("https://")
    cookie = "__Host-storycircle" if secure else "storycircle_dev"

    def dispose(session):
        session.cancelled.set()
        session.result, session.stories, session.job = None, [], None
        if not session.busy and session.adapter:
            session.adapter.close()
            session.adapter = None

    def cleanup():
        now = time.monotonic()
        with guard:
            for key, s in list(sessions.items()):
                if s.expires < now:
                    sessions.pop(key, None)
                    dispose(s)
            for key, values in list(buckets.items()):
                while values and values[0] <= now - 900:
                    values.popleft()
                if not values:
                    buckets.pop(key, None)
            for key, stamp in list(account_runs.items()):
                if stamp < now - 300:
                    account_runs.pop(key, None)

    @asynccontextmanager
    async def lifespan(app):
        async def janitor():
            while True:
                await asyncio.sleep(30)
                cleanup()
        task = asyncio.create_task(janitor())
        yield
        task.cancel()
        with guard:
            for s in sessions.values():
                dispose(s)
            sessions.clear()
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Story Circle", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Pydantic's default error detail can contain the submitted password.
        return JSONResponse({"detail": "Check the username, password, and verification code format."}, status_code=422)

    @app.middleware("http")
    async def protections(request, call_next):
        railway_probe = (request.method == "GET" and request.url.path == "/health"
                         and request.url.hostname == "healthcheck.railway.app")
        if request.url.netloc != urlsplit(config.origin).netloc and not railway_probe:
            return JSONResponse({"detail": "Unrecognized host."}, status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != config.origin:
                return JSONResponse({"detail": "Request origin rejected."}, status_code=403)
            # Bound actual bytes as well as Content-Length, before request validation.
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 8192:
                    return JSONResponse({"detail": "Request too large."}, status_code=413)
            request._body = bytes(data)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def session_for(request, auth=True, write=False):
        cleanup()
        key = request.cookies.get(cookie, "")
        with guard:
            s = sessions.get(key)
            if not s or s.cancelled.is_set() or (auth and not s.user):
                raise HTTPException(401, "Sign in to continue.")
            if write and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), s.csrf):
                raise HTTPException(403, "Session verification failed. Refresh this page.")
            return key, s

    def set_cookie(response, key, lifetime):
        response.set_cookie(cookie, key, max_age=lifetime, httponly=True, secure=secure, samesite="strict", path="/")

    def limit_login(request, username):
        # Do not trust caller-supplied forwarded IPs; configure edge rate limits for public hosting.
        ip = request.client.host if request.client else "unknown"
        keys = [("ip:" + ip, 20), ("user:" + username, 5)]
        now = time.monotonic()
        with guard:
            for raw, limit in keys:
                key = hashlib.sha256(raw.encode()).hexdigest()
                queue = buckets.setdefault(key, deque())
                while queue and queue[0] < now - 900:
                    queue.popleft()
                if len(queue) >= limit:
                    raise HTTPException(429, "Too many login attempts. Wait 15 minutes before trying again.")
            if len(buckets) > 4000:
                raise HTTPException(503, "Login capacity reached. Try later.")
            for raw, _ in keys:
                buckets[hashlib.sha256(raw.encode()).hexdigest()].append(now)

    @app.get("/api/session")
    def bootstrap(request: Request, response: Response):
        cleanup()
        with guard:
            key = request.cookies.get(cookie, "")
            s = sessions.get(key)
            if not s:
                if len(sessions) >= config.max_sessions:
                    raise HTTPException(503, "All connection slots are in use. Please try later.")
                key, s = secrets.token_urlsafe(32), Session()
                sessions[key] = s
                set_cookie(response, key, 300)
            return {"csrf": s.csrf, "user": s.user, "enabled": config.enabled,
                    "mode": "personal" if config.allowed_users else "multi-user",
                    "session_minutes": config.session_seconds // 60}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response):
        key, s = session_for(request, auth=False, write=True)
        if not config.enabled:
            raise HTTPException(503, "Instagram connection is not enabled on this server.")
        if not body.consent:
            raise HTTPException(400, "Confirm that this is your account and you understand the connection method.")
        username = body.username.lower().lstrip("@")
        if config.allowed_users and username not in config.allowed_users:
            raise HTTPException(403, "This private installation does not allow this account.")
        limit_login(request, username)
        with guard:
            if s.busy or s.user:
                raise HTTPException(409, "Disconnect the current session before starting another login.")
            if s.login_name and s.login_name != username and s.adapter:
                s.adapter.close()
                s.adapter = None
            s.login_name, s.busy = username, True
        try:
            if s.adapter is None:
                s.adapter = adapter_factory()
            user = s.adapter.login(username, body.password.get_secret_value(), body.code.replace(" ", ""))
            with guard:
                if s.cancelled.is_set() or sessions.get(key) is not s:
                    raise HTTPException(401, "Session ended. Refresh to reconnect.")
                s.user, s.csrf = user, secrets.token_urlsafe(32)
                s.expires = time.monotonic() + config.session_seconds
                sessions.pop(key)
                rotated = secrets.token_urlsafe(32)
                sessions[rotated] = s
                set_cookie(response, rotated, config.session_seconds)
            return {"user": s.user, "csrf": s.csrf}
        except HTTPException:
            raise
        except Exception as exc:
            error = public_error(exc)
            if error.code != "two_factor" and s.adapter:
                s.adapter.close()
                s.adapter = None
            raise HTTPException(401, {"code": error.code, "message": error.message}) from None
        finally:
            with guard:
                s.busy = False
                if s.cancelled.is_set():
                    dispose(s)

    @app.get("/api/stories")
    def stories(request: Request):
        _, s = session_for(request)
        with guard:
            if s.busy:
                raise HTTPException(409, "Your account is busy. Wait for the current request.")
            s.busy = True
        try:
            data = s.adapter.stories()
            with guard:
                if s.cancelled.is_set():
                    raise HTTPException(401, "Session ended.")
                s.stories = data
            return {"stories": data}
        except HTTPException:
            raise
        except Exception as exc:
            error = public_error(exc)
            raise HTTPException(502, {"code": error.code, "message": error.message}) from None
        finally:
            with guard:
                s.busy = False
                if s.cancelled.is_set():
                    dispose(s)

    def run_job(s, story_id):
        def check():
            if s.cancelled.is_set() or time.monotonic() > s.expires:
                raise ProviderError("cancelled", "Session ended; collection stopped.")
        def report(stage, count):
            with guard:
                if s.job:
                    s.job.update(stage=stage, count=count)
        try:
            check()
            result = s.adapter.snapshot(story_id, report, check, config.max_accounts)
            check()
            with guard:
                s.result = result
                s.job.update(status="succeeded", stage="Comparison ready")
        except Exception as exc:
            error = public_error(exc)
            with guard:
                s.result = None
                if s.job:
                    s.job.update(status="failed", stage="Collection stopped", error=error.message, code=error.code)
        finally:
            with guard:
                active_accounts.discard(s.user["id"])
                s.busy = False
                if s.cancelled.is_set():
                    dispose(s)

    @app.post("/api/compare")
    def start(body: StoryBody, request: Request):
        _, s = session_for(request, write=True)
        with guard:
            if body.story_id not in {story["id"] for story in s.stories}:
                raise HTTPException(404, "Select an available story belonging to your account.")
            if s.busy or s.user["id"] in active_accounts:
                raise HTTPException(409, "This Instagram account already has a request running.")
            if len(active_accounts) >= config.max_jobs:
                raise HTTPException(503, "Collection is busy. Try again shortly.")
            if time.monotonic() - account_runs.get(s.user["id"], float("-inf")) < 300:
                raise HTTPException(429, "Wait five minutes between comparisons for this Instagram account.")
            s.busy, s.result = True, None
            s.job = {"status": "running", "stage": "Checking your story", "count": 0, "started": time.monotonic()}
            active_accounts.add(s.user["id"])
            account_runs[s.user["id"]] = time.monotonic()
            pool.submit(run_job, s, body.story_id)
        return {"status": "running"}

    @app.get("/api/job")
    def job(request: Request):
        _, s = session_for(request)
        with guard:
            return {"job": {k: v for k, v in s.job.items() if k != "started"} if s.job else None}

    @app.get("/api/results")
    def results(request: Request):
        _, s = session_for(request)
        with guard:
            if s.result is None:
                raise HTTPException(404, "No completed comparison in this session.")
            return s.result

    @app.post("/api/disconnect")
    def disconnect(request: Request, response: Response):
        key, s = session_for(request, auth=False, write=True)
        with guard:
            sessions.pop(key, None)
            dispose(s)
        response.delete_cookie(cookie, path="/", secure=secure, httponly=True, samesite="strict")
        return {"disconnected": True}

    @app.get("/health")
    def health():
        return {"status": "ok", "instagram_enabled": config.enabled}

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in {"app.js", "style.css"}:
            raise HTTPException(404)
        return FileResponse(web / name)

    return app


app = create_app()
