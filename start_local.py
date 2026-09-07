"""Desktop-only entry point for signing in through Instagram's real website."""
import os


def main():
    # Ignore deployment host/port settings: this feature must stay on loopback.
    os.environ["APP_ORIGIN"] = "http://127.0.0.1:8000"
    os.environ["INSTAGRAM_ENABLED"] = "true"
    os.environ["INSTAGRAM_BROWSER_LOGIN"] = "true"
    import uvicorn
    print("Open http://127.0.0.1:8000 and choose Open Instagram to connect.")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, workers=1,
                access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
