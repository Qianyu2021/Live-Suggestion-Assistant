"""
main.py — FastAPI application entry point.
Run with:  uvicorn main:app --reload --port 8000
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.routes import router

# Load .env file (silently ignored if it doesn't exist)
load_dotenv()

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Live Meeting Assistant",
    description="Real-time transcript → suggestions → chat, powered by Groq",
    version="1.0.0",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Always allow localhost for dev; extend via CORS_ORIGINS env var for production.
default_origins = [
    "http://localhost:8000",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5500",
]
raw_origins = os.getenv("CORS_ORIGINS", "")
extra = [o.strip() for o in raw_origins.split(",") if o.strip()]
all_origins = list(dict.fromkeys(default_origins + extra))  # deduplicate, preserve order

app.add_middleware(
    CORSMiddleware,
    allow_origins=all_origins,
    allow_origin_regex=r"http://localhost:\d+",  # catch any localhost port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(router)

# ── Serve frontend static files ───────────────────────────────────────────────
# Looks for a /frontend folder next to /python-backend (sibling directory).
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
