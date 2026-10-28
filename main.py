"""
Main entry point - starts FastAPI + multi-account Telethon + APScheduler.
"""
import asyncio
import logging
import os
import secrets
import sys
import time

# Ensure stdout and stderr are not None when running via pythonw.exe or Windows background tasks
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder, IdentityResponder
from starlette.responses import Response

# Read static assets up to 1 MB in a single chunk so GZipResponder sets an
# exact Content-Length instead of falling back to Transfer-Encoding: chunked.
FileResponse.chunk_size = 1024 * 1024


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
from routes import ai_agents as ai_agents_routes
from routes import analytics, blacklist, logs, settings
from routes import changelog as changelog_routes
from routes import discord as discord_routes
from routes import help as help_routes
from routes import ipc as ipc_routes

if TG_RUNTIME_MODE == "combined":
    import dm_reply_tracker as drt
    import keyword_watcher as kw
    import kol_channel_watcher as kcw
    import message_queue as mq
    import reaction_watcher as rw
    import scheduler as sch
    import telegram_client as tg
    from routes import ai_followup, auth, chats, inbox, invite, members, messages, proxy, reactions, schedules, watchers
    from routes import engagement as engagement_routes
    from routes import referrals as referral_routes
    from routes import warmup as warmup_routes
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


# ── Dashboard username/password login (exchanges credentials for the API key) ─
DASH_USER = os.getenv("DASHBOARD_USERNAME", "")
DASH_PASS = os.getenv("DASHBOARD_PASSWORD", "")


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
            # Under pytest the background engines race the per-test clean_database
            # fixture (it DROPs every table between tests) → "no such table"
            # errors. Tests exercise HTTP/DB directly and never need Telethon.
            if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TG_SKIP_ENGINES"):
                logger.info("Test/opt-out env detected — skipping background engines")
            else:
                # Background connect task, same as before: HTTP must not wait on Telethon.
                _startup_task = await lc.start_engines()
                # Failures inside the background task must be visible in the log, not silent.
                _startup_task.add_done_callback(_log_startup_task_result)
        else:
            logger.info("Running in TG_RUNTIME_MODE=web; skipping all Telethon/background engines")

        logger.info("=" * 50)
        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8888"))
        logger.info(f"Dashboard: http://{host}:{port}")
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning("=" * 50)
            logger.warning("SECURITY: HOST=%s exposes the dashboard on the network.", host)
            logger.warning("The X-API-Key is the ONLY gate. Bind 127.0.0.1 unless you")
            logger.warning("intend remote access (use a VPN/reverse-proxy with TLS + auth).")
            logger.warning("=" * 50)
        if os.getenv("DISABLE_AUTH", "0") == "1":
            logger.warning("SECURITY: DISABLE_AUTH=1 — the entire API is UNAUTHENTICATED.")
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
    """GZipResponder that skips 204/304/1xx responses and already-compressed binary media types."""

    async def send_with_compression(self, message):
        if message["type"] == "http.response.start":
            await super().send_with_compression(message)
            status = message.get("status", 200)
            if status < 200 or status in (204, 304):
                self.content_type_is_excluded = True
                return
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
        allowed = ("/api/logs", "/api/analytics", "/api/changelog", "/api/help", "/api/health/telegram-worker")
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

# ── Dashboard login: exchange username/password for the API key (no auth dep) ─
class DashboardLoginRequest(BaseModel):
    username: str
    password: str


_login_fails: dict[str, list[float]] = {}


@app.post("/api/login")
async def dashboard_login(payload: DashboardLoginRequest, request: Request):
    # Exposed via Cloudflare Tunnel → chặn dò mật khẩu: 5 lần sai / IP thì khoá 15 phút
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < 900]
    if len(fails) >= 5:
        raise HTTPException(status_code=429, detail="Sai quá nhiều lần, thử lại sau 15 phút")
    user_ok = DASH_USER and secrets.compare_digest(payload.username, DASH_USER)
    pass_ok = DASH_PASS and secrets.compare_digest(payload.password, DASH_PASS)
    if not (user_ok and pass_ok):
        _login_fails[ip] = fails + [now]
        await asyncio.sleep(1)
        raise HTTPException(status_code=401, detail="Sai username hoặc mật khẩu")
    _login_fails.pop(ip, None)
    return {"api_key": API_KEY}


# Include API routes (with API key auth dependency injected)
_auth_dep = [Depends(verify_api_key)]
app.include_router(logs.router, dependencies=_auth_dep)
app.include_router(settings.router, dependencies=_auth_dep)
app.include_router(blacklist.router, dependencies=_auth_dep)
app.include_router(discord_routes.router, dependencies=_auth_dep)
app.include_router(analytics.router, dependencies=_auth_dep)
app.include_router(ai_agents_routes.router, dependencies=_auth_dep)
app.include_router(changelog_routes.router, dependencies=_auth_dep)
app.include_router(help_routes.router, dependencies=_auth_dep)
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
    app.include_router(engagement_routes.router, dependencies=_auth_dep)
    app.include_router(referral_routes.router, dependencies=_auth_dep)
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


_FAVICON_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
    b"<rect width='64' height='64' rx='14' fill='#6366f1'/>"
    b"<path d='M49 16L13 30c-2 1-2 3 0 4l9 3 21-13c1-1 2 0 1 1L27 40v9c0 2 2 3 3 1l5-5 9 7c2 1 4 0 4-2l3-31c0-2-1-3-2-3z' fill='#fff'/>"
    b"</svg>"
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"), headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    import socket
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8888"))
    if host in ("127.0.0.1", "localhost"):
        sockets = []
        try:
            s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s4.bind(("127.0.0.1", port))
            sockets.append(s4)
        except OSError:
            pass
        try:
            s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s6.bind(("::1", port))
            sockets.append(s6)
        except OSError:
            pass
        if sockets:
            config = uvicorn.Config("main:app", host=host, port=port, reload=False, timeout_keep_alive=15)
            uvicorn.Server(config).run(sockets=sockets)
        else:
            uvicorn.run("main:app", host=host, port=port, reload=False, timeout_keep_alive=15)
    else:
        uvicorn.run("main:app", host=host, port=port, reload=False, timeout_keep_alive=15)

