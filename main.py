"""
Main entry point - starts FastAPI + multi-account Telethon + APScheduler.
"""
import os
import sys
import secrets
import asyncio
import subprocess
import signal
import logging

# Ensure stdout and stderr are not None when running via pythonw.exe or Windows background tasks
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Security, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.gzip import GZipResponder, IdentityResponder
from starlette.datastructures import Headers
import uvicorn

class CacheControlledStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

import database as db

TG_RUNTIME_MODE = os.getenv("TG_RUNTIME_MODE", "combined").strip().lower()
if TG_RUNTIME_MODE not in ("combined", "web"):
    raise RuntimeError("TG_RUNTIME_MODE must be 'combined' or 'web'")

# Web mode deliberately imports only DB/read-only routers. Telegram-backed routes are
# rejected below until Phase 2 adds IPC commands; never report a mutation as accepted.
from routes import logs, settings, blacklist, analytics
from routes import discord as discord_routes
from routes import ai_agents as ai_agents_routes
from routes import changelog as changelog_routes
from routes import ipc as ipc_routes

if TG_RUNTIME_MODE == "combined":
    import telegram_client as tg
    import scheduler as sch
    import message_queue as mq
    import keyword_watcher as kw
    import reaction_watcher as rw
    import dm_reply_tracker as drt
    import kol_channel_watcher as kcw
    from routes import auth, chats, schedules, messages, watchers, reactions, inbox, members, proxy, invite
    from routes import warmup as warmup_routes
    from routes import ai_followup
else:
    tg = sch = mq = kw = rw = drt = kcw = None

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tg-scheduler")

# ── CRIT-03: API Key authentication ──────────────────────────────────────────
API_KEY = os.getenv("DASHBOARD_SECRET_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str = Security(api_key_header)):
    """Verify X-API-Key header. Always enforced — startup refuses to run without a key.

    Dev bypass: set DISABLE_AUTH=1 in .env to skip the check (local development only).
    """
    if os.getenv("DISABLE_AUTH", "0") == "1":
        return
    if not secrets.compare_digest(key or "", API_KEY):
        raise HTTPException(status_code=403, detail="Unauthorized")


_startup_task = None
_runtime_lock_acquired = False


def _log_startup_task_result(task: "asyncio.Task") -> None:
    """Surface background engine-startup failures instead of losing them."""
    if task.cancelled():
        logger.warning("Background engine startup task was cancelled")
        return
    exc = task.exception()
    if exc:
        logger.error("Background engine startup task FAILED: %s", exc, exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global _startup_task, _runtime_lock_acquired
    # ── Startup ──
    logger.info("=" * 50)
    logger.info("TG Scheduler starting up...")

    # ── Security gate: never run without API auth ──
    if not API_KEY:
        logger.error("=" * 50)
        logger.error("FATAL: DASHBOARD_SECRET_KEY is not set in .env")
        logger.error("The dashboard API would be completely unauthenticated.")
        logger.error("Add this line to your .env file:")
        logger.error("  DASHBOARD_SECRET_KEY=%s", secrets.token_urlsafe(32))
        logger.error("=" * 50)
        raise SystemExit(1)

    import engine_lifecycle as lc

    if TG_RUNTIME_MODE == "combined":
        # Singleton lock: block combined+worker or two combined from sharing DATA_DIR
        lc.acquire_data_lock()
        _runtime_lock_acquired = True

    try:
        await db.init_db()
        # IPC queue schema must exist before the first enqueue/claim in BOTH modes:
        # web enqueues, worker consumes, combined serves /api/ipc status reads.
        import runtime_commands as rc
        async with db.get_db() as conn:
            await rc.init_schema(conn)
        logger.info("Database initialized")
        if TG_RUNTIME_MODE == "combined":
            # Background connect task, same as before: HTTP must not wait on Telethon.
            _startup_task = await lc.start_engines()
            # Failures inside the background task must be visible in the log, not silent.
            _startup_task.add_done_callback(_log_startup_task_result)
        else:
            logger.info("Running in TG_RUNTIME_MODE=web; skipping all Telethon/background engines")

        logger.info("=" * 50)
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "8888"))
        logger.info(f"Dashboard: http://{host}:{port}")
        logger.info("=" * 50)
        yield
    finally:
        logger.info("Shutting down...")
        try:
            if TG_RUNTIME_MODE == "combined":
                await lc.stop_engines(_startup_task)
        finally:
            try:
                await db.close_db()
            finally:
                if _runtime_lock_acquired:
                    lc.release_data_lock()
                    _runtime_lock_acquired = False
        logger.info("Goodbye!")


# Media types already compressed — gzipping them wastes CPU and can grow the payload
GZIP_EXCLUDED_CONTENT_TYPES = (
    "text/event-stream",
    "image/",
    "video/",
    "audio/",
    "application/zip",
    "application/gzip",
    "application/octet-stream",
)


class BinaryAwareGZipResponder(GZipResponder):
    """GZipResponder that also skips already-compressed binary media types."""

    async def send_with_compression(self, message):
        if message["type"] == "http.response.start":
            await super().send_with_compression(message)
            headers = Headers(raw=message["headers"])
            content_type = headers.get("content-type", "").lower()
            if content_type.startswith(GZIP_EXCLUDED_CONTENT_TYPES):
                self.content_type_is_excluded = True
            return
        await super().send_with_compression(message)


class SafeGZipMiddleware(GZipMiddleware):
    """GZip middleware that (1) never compresses Range responses (would corrupt
    byte offsets) and (2) skips already-compressed binary media types."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Range requests must pass through uncompressed (offsets must stay valid)
        if b"range" in dict(scope.get("headers", [])):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if "gzip" in headers.get("Accept-Encoding", ""):
            responder = BinaryAwareGZipResponder(self.app, self.minimum_size, compresslevel=self.compresslevel)
        else:
            responder = IdentityResponder(self.app, self.minimum_size)
        await responder(scope, receive, send)


app = FastAPI(title="TG Scheduler", lifespan=lifespan)

app.add_middleware(SafeGZipMiddleware, minimum_size=1000)

if TG_RUNTIME_MODE == "web":
    @app.middleware("http")
    async def split_read_only_boundary(request, call_next):
        # ponytail: Phase 1 is read-only; widen this allowlist only after route/IPC audit.
        from fastapi.responses import JSONResponse
        path = request.url.path
        if path.startswith("/api/ipc"):
            return await call_next(request)
        allowed = ("/api/logs", "/api/analytics", "/api/changelog", "/api/health/telegram-worker")
        if path.startswith("/api/") and (
            request.method != "GET" or not any(path == p or path.startswith(p + "/") for p in allowed)
        ):
            return JSONResponse(status_code=503, content={"detail": "Requires TG_RUNTIME_MODE=combined until Phase 2 IPC; web mode is read-only"})
        return await call_next(request)

# ── BONUS: CORS ───────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8888", "http://localhost:8888"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes (with API key auth dependency injected)
_auth_dep = [Depends(verify_api_key)]
app.include_router(logs.router, dependencies=_auth_dep)
app.include_router(settings.router, dependencies=_auth_dep)
app.include_router(blacklist.router, dependencies=_auth_dep)
app.include_router(discord_routes.router, dependencies=_auth_dep)
app.include_router(analytics.router, dependencies=_auth_dep)
app.include_router(ai_agents_routes.router, dependencies=_auth_dep)
app.include_router(changelog_routes.router, dependencies=_auth_dep)
app.include_router(ipc_routes.router, dependencies=_auth_dep)

if TG_RUNTIME_MODE == "combined":
    app.include_router(auth.router, dependencies=_auth_dep)
    app.include_router(chats.router, dependencies=_auth_dep)
    app.include_router(schedules.router, dependencies=_auth_dep)
    app.include_router(messages.router, dependencies=_auth_dep)
    app.include_router(watchers.router, dependencies=_auth_dep)
    app.include_router(reactions.router, dependencies=_auth_dep)
    app.include_router(inbox.router, dependencies=_auth_dep)
    app.include_router(members.router, dependencies=_auth_dep)
    app.include_router(proxy.router, dependencies=_auth_dep)
    app.include_router(invite.router, dependencies=_auth_dep)
    app.include_router(warmup_routes.router, dependencies=_auth_dep)
    app.include_router(ai_followup.router, dependencies=_auth_dep)
else:
    def _split_runtime_pending(route_name: str):
        async def endpoint():
            raise HTTPException(status_code=503, detail=f"{route_name} requires TG_RUNTIME_MODE=combined until split IPC ships in Phase 2")
        return endpoint

    for methods, path, name in (
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/watchers{rest:path}", "Watcher routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/reactions{rest:path}", "Reaction routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/auth{rest:path}", "Telegram auth routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/chats{rest:path}", "Telegram chat routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/messages{rest:path}", "Telegram message routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/schedules{rest:path}", "Schedule mutation routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/members{rest:path}", "Member/campaign routes"),
        (["GET", "POST", "PUT", "DELETE", "PATCH"], "/api/ai-followup{rest:path}", "AI follow-up routes"),
    ):
        app.add_api_route(path, _split_runtime_pending(name), methods=methods, dependencies=_auth_dep)

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", CacheControlledStaticFiles(directory=static_dir), name="static")


@app.get("/api/health/telegram-worker", dependencies=_auth_dep)
async def telegram_worker_health():
    """Authenticated worker status with stale-heartbeat classification."""
    import engine_lifecycle as lc
    return {"runtime_mode": TG_RUNTIME_MODE, "worker": await lc.read_worker_health()}


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"), headers={"Cache-Control": "public, max-age=3600"})


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8888"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
