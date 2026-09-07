"""Run one application process on the host-assigned port."""
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    uvicorn.run("app:app", host="0.0.0.0", port=port, workers=1,
                access_log=False, proxy_headers=False)
