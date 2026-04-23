"""
main.py — FastAPI application entry point.
Run with:  uvicorn main:app --reload --port 8000
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routes import router

load_dotenv()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Live Meeting Assistant",
    description="Real-time transcript → suggestions → chat, powered by Groq",
    version="1.0.0",
)

# ── Body size limit ───────────────────────────────────────────────────────────
# Whisper's hard limit is 25 MB. We allow 26 MB to give a little headroom.
# Set via uvicorn's --limit-concurrency or here via middleware.
# The cleanest way in FastAPI is to configure it on the ASGI server side,
# but we also guard in the route itself. We set it here via a custom middleware
# so oversized uploads get a clean 413 instead of a cryptic connection reset.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

MAX_UPLOAD_BYTES = 26 * 1024 * 1024  # 26 MB

class LimitUploadSizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and "/api/transcribe" in request.url.path:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_UPLOAD_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Audio chunk too large (max 26 MB). Chunk at 30s is ~1-3 MB — check your encoder."},
                )
        return await call_next(request)

app.add_middleware(LimitUploadSizeMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
default_origins = [
    "http://localhost:8000",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5500",
]
raw_origins = os.getenv("CORS_ORIGINS", "")
extra = [o.strip() for o in raw_origins.split(",") if o.strip()]
all_origins = list(dict.fromkeys(default_origins + extra))

app.add_middleware(
    CORSMiddleware,
    allow_origins=all_origins,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Avoid noisy 404 logs when browsers auto-request a favicon.
    return Response(status_code=204)

# ── Serve frontend static files ───────────────────────────────────────────────
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    print(f"✅  Serving frontend from {frontend_dir}")
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
else:
    print(f"ℹ️   No frontend directory found at {frontend_dir} — API-only mode")


# ── Dev entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
