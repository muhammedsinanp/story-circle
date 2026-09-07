# Story Circle — automatic Instagram story comparison

A responsive website for personal use or multiple independent users. Each user connects their own Instagram account, completes supported authenticator verification if needed, and gets a comparison for one of their own available stories. The newest returned story is selected and compared automatically after login.

**Status:** Working application code with automated tests using a simulated Instagram adapter. Real Instagram login and retrieval have not been tested. This is an experimental pilot, not a verified public production service. This repository contains the automatic Python web app; the earlier manual-list website is a separate prototype.

## What it includes

- Instagram username/password connection with an optional authenticator code.
- Own-account story selection; arbitrary account and story IDs are rejected.
- Automatic followers, following, and story-viewer collection through the pinned `instagrapi` integration.
- Comparison by stable Instagram account IDs, so username changes do not break matching.
- Followers who viewed, followed accounts who viewed, mutual connections, and connections absent from returned viewers.
- Status and relationship filters, username search, and CSV download.
- Distinct, server-side sessions for every connected browser. No shared account client or shared result collection.
- Disconnect, one-hour absolute session expiry, five-minute pending-login expiry, background cleanup, and collection cancellation.
- Personal account allowlist or multiple-user mode, without a separate Story Circle registration step.

## Connection method and limits

This is **not official Instagram OAuth**. The server operator receives the username, password, and any submitted authenticator code in order to sign in through an unofficial integration. The UI explains this before submission. Passwords are not deliberately persisted to disk, retained on the adapter after login, or included in application error responses. Request bodies exist in process memory during handling. Authenticated session tokens, returned accounts, and results remain in server memory until disconnect, expiry, or process restart. This is memory retention, not encrypted persistent storage. Server operators can access the process and are part of the trust boundary.

The service does not publish posts, follow/unfollow accounts, send messages, mark stories seen, recover expired viewer lists, or attempt to resolve checkpoints automatically. Authentication necessarily creates an Instagram login session. It stops on detected rate restrictions and account challenges. Complete challenges in Instagram itself. Authenticator-code login is implemented; SMS/email challenge automation is not.

No absence result can prove that someone never watched a story. Returned lists may be affected by account changes, audience settings, anonymous viewing, unavailable data, and later views. A connection count mismatch, pagination loop, error, collection timeout, or capacity limit suppresses the entire comparison. Reaching the end of Instagram's returned viewer pagination is not proof that Instagram exposed every real view.

The initial capacity is 100 active/pending browser sessions, four concurrent comparisons, and 5,000 accounts per list. Collection has a 10-minute/500-private-request bound, a minimum interval between private requests, and an account-wide five-minute comparison cooldown. These limits protect this deployment's resources; they are not published Instagram allowance guarantees. The code does not use proxy rotation or CAPTCHA bypasses.

Primary integration references:

- [Account login](https://subzeroid.github.io/instagrapi/usage-guide/interactions.html)
- [Follower and following methods](https://subzeroid.github.io/instagrapi/usage-guide/user.html)
- [Story viewers](https://subzeroid.github.io/instagrapi/usage-guide/story.html)
- [Integration limitations](https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html)

## Run on your computer

### Open Instagram's real login page (desktop browser mode)

This opt-in flow opens a separate visible Chromium window at
`https://www.instagram.com/accounts/login/`. You enter your password and any
verification codes on Instagram itself. Once you reach Instagram's home page,
the window closes and Story Circle tries to connect the browser session to its
existing collector. It then finds your own stories and automatically compares
the newest returned story's viewers with your followers and following.

**This is an experimental browser-session connection, not official OAuth or
DOM scraping.** A personal account can sign in on Instagram's website, but
Instagram may reject that browser session for the unofficial collection API.
Successful browser login does not guarantee data access. Restrictions and
unavailable/incomplete lists stop collection; no bypass is attempted.

Run this on your own desktop computer with a graphical display and Python 3.12:

```bash
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# Windows PowerShell instead: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python start_local.py
```

Open `http://127.0.0.1:8000`, confirm the own-account connection checkbox, and
choose **Open Instagram to connect**. Finish login and any prompts in Instagram;
reach its home page within five minutes. Return to Story Circle for results.
Use **Cancel connection** or close the Instagram window to stop a pending login.
Use **Disconnect** to stop collection and clear the local app session.

The launcher binds only to `127.0.0.1` and disables proxy headers. Browser login
also requires a loopback origin and a loopback client, plus the existing origin
and CSRF checks. Do not put this desktop endpoint behind a public reverse proxy
or tunnel. It is not a Railway/Docker login window for remote visitors.
The normal hosted password flow remains unchanged when browser mode is off;
password submission is disabled when browser mode is on.

Only the explicitly opened temporary browser context is used: the app does not
read your existing browser profile, fill login fields, save screenshots/traces,
or export cookies to the frontend. The session cookie stays in the local process
for the existing collector. The temporary browser context closes after login,
cancellation, timeout, or an error. Browser implementation/OS temporary data is
outside the app's in-memory retention guarantee. Disconnect clears the app's
copy of the session; it does not revoke Instagram's server-side session. You can
revoke that session through Instagram's own account settings.

Automated browser-mode tests use simulated browser and provider objects. A real
Instagram login and collection have **not** been verified.

The browser lifecycle tests can also run without the app dependencies:
`python -m unittest discover -s tests -p test_browser_login.py -v`.
The complete API/session test suite still requires `requirements-test.txt`.

References: [Playwright browser contexts](https://playwright.dev/python/docs/browser-contexts)
and [instagrapi session-login limitations](https://subzeroid.github.io/instagrapi/usage-guide/interactions.html).

### Existing password-based connection

Requires Python 3.12. From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
APP_ORIGIN=http://127.0.0.1:8000 INSTAGRAM_ENABLED=true ALLOWED_IG_USERS=your_username uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --no-proxy-headers
```

Open `http://127.0.0.1:8000`. The site defaults to connection-disabled until `INSTAGRAM_ENABLED=true` is set. Do not put Instagram passwords or session tokens in configuration files. Enter them only in the actual running app.

For local multiple-user development, remove `ALLOWED_IG_USERS`. Different browsers or browser profiles have isolated sessions. The single browser login is intentionally replaced only after disconnecting.

## Deploy for other users

Run on a Python/Docker-capable HTTPS host. This service cannot run directly in the existing Sites static/JavaScript hosting environment. The repository is prepared for deployment with Railway or another Docker-capable host.

For a managed Docker host:

1. Deploy this directory with its Dockerfile. The startup script uses the assigned `PORT`, defaulting to `8000`. Set the health path to `/health`.
2. Set `APP_ORIGIN` to the exact HTTPS origin assigned to the service, with no path. Requests with a different Host header are rejected. Railway's documented `healthcheck.railway.app` hostname is allowed only for `GET /health`, without exposing account APIs on that host.
3. Set `INSTAGRAM_ENABLED=true` only when you are ready to validate a real account connection.
4. Personal use: set `ALLOWED_IG_USERS` to your username or a comma-separated allowlist. Multiple-user use: leave it empty so visitors may connect their own accounts.
5. Use exactly **one worker and one replica**. The session registry is intentionally process-local. Restarting clears all sessions and results.

For a server where you control a domain and Docker Compose:

```bash
cp .env.example .env
# Edit APP_DOMAIN, INSTAGRAM_ENABLED, and ALLOWED_IG_USERS in .env.
docker compose up --build -d
```

Point the chosen domain's DNS to that server and allow incoming HTTPS traffic. Caddy terminates HTTPS; the Python app is only exposed on the internal container network. Only Caddy's certificate configuration is stored in volumes; Instagram sessions/results are not stored in volumes. The Caddy image is a maintained major-version tag; pin a validated image digest in your deployment policy if required.

For a public launch, first validate the login/verification and viewer pagination with consenting test accounts. Configure your host's edge rate limits and ensure request-body logging and crash-dump capture are disabled for credential routes. The application provides per-account/per-peer login limits; behind the included reverse proxy, peer limits are shared unless edge controls distinguish visitors. An operator-specific privacy notice, contact details, and operational monitoring still need to be supplied before general availability. This package does not represent a legal or security review.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `APP_ORIGIN` | Exact browser origin, including scheme and port when needed | `http://127.0.0.1:8000` |
| `INSTAGRAM_ENABLED` | Whether to allow connection requests | `false` |
| `INSTAGRAM_BROWSER_LOGIN` | Desktop-only browser-session connection; enabled by `start_local.py` | `false` |
| `ALLOWED_IG_USERS` | Comma-separated permitted Instagram usernames; empty enables multiple-user access | Empty |
| `APP_DOMAIN` | Compose/Caddy public hostname only | Required for Compose |

Production origins must use HTTPS. HTTP is permitted only for `localhost` and `127.0.0.1`. The server sets HttpOnly, SameSite=Strict cookies, Secure cookies for HTTPS, host validation, origin and CSRF checks, no-store headers, and a restrictive content security policy. It does not trust caller-supplied forwarding headers.

## Verification

```bash
pip install -r requirements-test.txt
python -m pytest -q tests
```

Automated checks exercise separate-user isolation, story ownership, cookie rotation, CSRF/origin rejection, secret redaction, disabled connection mode, the personal allowlist, two-factor states, incomplete results, disconnect/cancellation, pagination loops, account-ID comparisons, session expiry, login throttling, payload limits, and served assets. No real credentials are used. Browser visual testing and real Instagram integration testing have not been performed.

## Files

- `app.py`: HTTP API, sessions, login, jobs, access controls, and static serving.
- `instagram.py`: bounded read-only Instagram adapter and comparison.
- `browser_login.py`, `start_local.py`: opt-in local Instagram browser login and loopback launcher.
- `requirements-browser.txt`: optional desktop browser dependencies.
- `web/`: responsive login and comparison interface.
- `tests/`: simulated-provider security and behavior checks.
- `requirements.lock.txt`: pinned runtime dependencies used for packaging.
- `Dockerfile`, `compose.yaml`, `Caddyfile`: deployment setup.

There is no database migration, scheduled background account polling, billing, or native mobile binary in this version. The mobile experience is the responsive website.
