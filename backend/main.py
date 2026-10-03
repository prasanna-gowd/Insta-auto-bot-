"""
main.py - FastAPI application entry point for the Instagram Auto-Reply Bot.

Responsibilities:
  - Serve the frontend (HTML/CSS/JS) as static files.
  - Expose a REST API consumed by the frontend.
  - Initialise the database on startup.
  - Delegate Instagram actions to instagram.py and bot control to bot.py.
"""

import logging
import os

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import bot
import database
import instagram
from config import FRONTEND_DIR

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    database.init_db()
    logger.info("Database initialised.")
    
    # Auto-login if credentials are present in env
    from config import INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD
    if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
        try:
            logger.info("Attempting auto-login on startup for: %s", INSTAGRAM_USERNAME)
            instagram.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            logger.info("Auto-login successful on startup!")
        except Exception as exc:
            logger.warning("Auto-login on startup failed: %s", exc)

    yield
    # Shutdown
    bot.stop_bot()
    logger.info("Bot stopped on shutdown.")

app = FastAPI(
    title="Instagram Auto-Reply Bot",
    description="Automatically reply to comments and send DMs on your Instagram reels.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static frontend assets (CSS, JS, images)
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
else:
    logger.warning("Frontend directory not found: %s", FRONTEND_DIR)


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class ReelConfigRequest(BaseModel):
    comment_reply: str = ""
    dm_message: str = ""
    is_enabled: bool = False


# ---------------------------------------------------------------------------
# Helper: serve a frontend HTML file
# ---------------------------------------------------------------------------

def _serve(filename: str) -> FileResponse:
    path = os.path.join(FRONTEND_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Frontend file not found: {filename}")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """Login to Instagram with provided credentials."""
    try:
        instagram.login(req.username, req.password)
        return {"success": True, "username": req.username}
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@app.post("/api/auth/logout")
async def auth_logout():
    """Logout from Instagram and stop the bot."""
    bot.stop_bot()
    instagram.logout()
    return {"success": True}


@app.get("/api/auth/status")
async def auth_status():
    """Return current login status and username."""
    logged_in = instagram.is_logged_in()
    username = None
    if logged_in:
        try:
            username = instagram.get_current_username()
        except Exception:
            pass
    return {"logged_in": logged_in, "username": username}


# ---------------------------------------------------------------------------
# Reels endpoints
# ---------------------------------------------------------------------------

@app.get("/api/reels")
async def get_reels():
    """
    Fetch the user's reels from Instagram, sync them to the local DB,
    and return the merged list (Instagram data + saved config).
    """
    if not instagram.is_logged_in():
        raise HTTPException(status_code=401, detail="Not logged in to Instagram.")

    try:
        ig_reels = instagram.get_user_reels(count=20)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Instagram error: {exc}")

    # Sync to database (upsert)
    for r in ig_reels:
        database.upsert_reel_config(
            reel_id=r["reel_id"],
            shortcode=r["shortcode"],
            thumbnail_url=r["thumbnail_url"],
            caption=r["caption"],
            taken_at=r["taken_at"],
        )

    # Load configs from DB (includes comment_reply, dm_message, is_enabled)
    db_reels = database.get_all_reel_configs()

    # Attach live Instagram metrics (comment_count, like_count)
    ig_map = {r["reel_id"]: r for r in ig_reels}
    for reel in db_reels:
        ig_data = ig_map.get(reel["reel_id"], {})
        reel["comment_count"] = ig_data.get("comment_count", 0)
        reel["like_count"] = ig_data.get("like_count", 0)

    return {"reels": db_reels, "count": len(db_reels)}


@app.get("/api/reels/{reel_id}/config")
async def get_reel_config(reel_id: str):
    """Return the saved configuration for a single reel."""
    config = database.get_reel_config(reel_id)
    if not config:
        raise HTTPException(status_code=404, detail="Reel not found in database.")
    return config


@app.post("/api/reels/{reel_id}/config")
async def save_reel_config(reel_id: str, req: ReelConfigRequest):
    """Save comment reply message, DM message, and enabled flag for a reel."""
    config = database.get_reel_config(reel_id)
    if not config:
        raise HTTPException(
            status_code=404,
            detail="Reel not found. Please fetch reels first.",
        )
    database.update_reel_config(
        reel_id=reel_id,
        comment_reply=req.comment_reply,
        dm_message=req.dm_message,
        is_enabled=req.is_enabled,
    )
    return {"success": True, "message": "Configuration saved."}


# ---------------------------------------------------------------------------
# Bot control endpoints
# ---------------------------------------------------------------------------

@app.post("/api/bot/start")
async def bot_start():
    """Start the auto-reply bot."""
    if not instagram.is_logged_in():
        raise HTTPException(status_code=401, detail="Not logged in to Instagram.")
    started = bot.start_bot()
    if not started:
        return {"success": False, "running": bot.bot_status(), "detail": "Bot is already running."}
    return {"success": True, "running": True}


@app.post("/api/bot/stop")
async def bot_stop():
    """Stop the auto-reply bot."""
    bot.stop_bot()
    return {"success": True, "running": False}


@app.get("/api/bot/status")
async def get_bot_status():
    """Return current bot running state and login status."""
    return {
        "running": bot.bot_status(),
        "logged_in": instagram.is_logged_in(),
    }


# ---------------------------------------------------------------------------
# Logs endpoints
# ---------------------------------------------------------------------------

@app.get("/api/logs")
async def get_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Return paginated reply logs."""
    logs = database.get_logs(limit=limit, offset=offset)
    total = database.get_logs_count()
    return {"logs": logs, "total": total, "limit": limit, "offset": offset}


@app.delete("/api/logs")
async def delete_logs():
    """Clear all reply logs."""
    database.clear_logs()
    return {"success": True, "message": "All logs cleared."}


# ---------------------------------------------------------------------------
# Stats endpoint
# ---------------------------------------------------------------------------

@app.get("/api/stats")
async def get_stats():
    """Return aggregated statistics."""
    return database.get_stats()


# ---------------------------------------------------------------------------
# Frontend routes
# ---------------------------------------------------------------------------

@app.get("/")
@app.get("/index.html")
async def serve_index():
    return _serve("index.html")


@app.get("/logs-page")
@app.get("/logs.html")
async def serve_logs():
    return _serve("logs.html")


@app.get("/settings")
@app.get("/settings.html")
async def serve_settings():
    return _serve("settings.html")


@app.get("/style.css")
async def serve_style_css():
    return _serve("style.css")


@app.get("/app.js")
async def serve_app_js():
    return _serve("app.js")



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
