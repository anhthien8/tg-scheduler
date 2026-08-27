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
import telegram_client as tg
import scheduler as sch
import message_queue as mq
import keyword_watcher as kw
from routes import auth, chats, schedules, messages, logs, watchers, settings, blacklist, reactions, inbox, members, analytics, proxy, invite
from routes import discord as discord_routes
from routes import warmup as warmup_routes
from routes import ai_followup
from routes import ai_agents as ai_agents_routes
from routes import changelog as changelog_routes
import reaction_watcher as rw
import dm_reply_tracker as drt

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


async def connect_accounts_background():
    """Connect all Telegram clients in the background to avoid blocking server startup."""
    try:
        logger.info("Connecting Telegram clients in the background (concurrently)...")
        accounts = await db.get_all_accounts()

        async def connect_single(acc):
            try:
                proxy_url = acc.get("proxy_url")  # Load per-account proxy from DB
                await tg.create_client(acc["id"], int(acc["api_id"]), acc["api_hash"], acc["session_name"], proxy_url=proxy_url)
                authorized = await asyncio.wait_for(tg.start_client(acc["id"]), timeout=30)
                if authorized:
                    await db.update_account_login_status(acc["id"], True)
                    return True
                else:
                    await db.update_account_login_status(acc["id"], False)
                    return False
            except asyncio.TimeoutError:
                logger.warning(f"Account {acc['id']} ({acc['name']}): connect timed out after 30s")
                await db.update_account_login_status(acc["id"], False)
            except Exception as e:
                logger.warning(f"Account {acc['id']} ({acc['name']}): connect failed: {e}")
                await db.update_account_login_status(acc["id"], False)
            return False

        results = await asyncio.gather(*(connect_single(acc) for acc in accounts), return_exceptions=True)
        logged_count = sum(1 for r in results if r is True)

        logger.info(f"Loaded {len(accounts)} accounts, {logged_count} successfully logged in")

        # Start keyword watchers
        await kw.start_all_watchers()

        # Start reaction watchers
        await rw.start_all()

        # Start DM reply tracker (inbox)
        await drt.start_reply_tracker()

        # Connect Discord bots
        try:
            from platforms.discord_adapter import DiscordAdapter
            import discord_watcher as dw
            import discord_reaction_watcher as drw
            import discord_reply_tracker as drt_discord

            adapter = DiscordAdapter()
            discord_routes._adapter = adapter

            dw.set_adapter(adapter)
            drw.set_adapter(adapter)
            drt_discord.set_adapter(adapter)

            discord_bots = await db.get_all_discord_bots()
            for bot in discord_bots:
                try:
                    success = await adapter.connect_bot(bot["id"], bot["bot_token"])
                    if success:
                        info = await adapter.get_account_info(bot["id"])
                        await db.update_discord_bot_status(
                            bot["id"], True,
                            user_id=str(info.get("user_id", "")),
                            username=info.get("username", ""),
                            guild_count=info.get("guild_count", 0),
                        )
                        logger.info(f"Discord bot {bot['id']} ({bot['name']}): connected")
                    else:
                        logger.warning(f"Discord bot {bot['id']} ({bot['name']}): connect failed")
                except Exception as e:
                    logger.warning(f"Discord bot {bot['id']} ({bot['name']}): {e}")
            logger.info(f"Discord: {len(discord_bots)} bots loaded")

            # Start Discord engines
            await dw.start_all_watchers()
            await drw.start_all()
            await drt_discord.start_reply_tracker()
            logger.info("Discord engines started (watcher + reaction + reply)")
        except ImportError:
            logger.info("Discord adapter not available (discord.py not installed)")
        except Exception as e:
            logger.warning(f"Discord startup error: {e}")

        logger.info("All background engines started successfully.")
    except Exception as e:
        logger.error(f"Error in background account startup: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    global _startup_task
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

    # Init database
    await db.init_db()
    logger.info("Database initialized")

    # Start scheduler
    sch.start_scheduler()
    await sch.load_all_jobs()

    # Reload scheduled DM campaigns
    scheduled_campaigns = await db.get_scheduled_campaigns()
    for sc in scheduled_campaigns:
        if sc["scheduled_at"] and sc["target_timezone"]:
            sch.add_campaign_schedule_job(sc["id"], sc["scheduled_at"], sc["target_timezone"])
    if scheduled_campaigns:
        logger.info(f"Reloaded {len(scheduled_campaigns)} scheduled DM campaigns")

    # Auto-resume active running DM campaigns after app restart
    try:
        all_campaigns = await db.get_all_dm_campaigns()
        running_cnt = 0
        for rc in all_campaigns:
            if rc.get("status") == "running":
                from routes.members import _run_campaign, _active_campaigns
                curr_task = _active_campaigns.get(rc["id"])
                is_running = curr_task is True or (isinstance(curr_task, asyncio.Task) and not curr_task.done())
                if not is_running:
                    logger.info(f"Auto-resuming running DM campaign #{rc['id']} ({rc['name']})...")
                    _active_campaigns[rc["id"]] = asyncio.create_task(_run_campaign(rc["id"]))
                    running_cnt += 1
        if running_cnt:
            logger.info(f"Auto-resumed {running_cnt} active running DM campaigns")
    except Exception as e:
        logger.warning(f"Error auto-resuming DM campaigns: {e}")

    # Daily summary notification
    from daily_summary import send_daily_summary
    from apscheduler.triggers.cron import CronTrigger
    summary_time = await db.get_setting("daily_summary_time", "21:00")
    hour, minute = map(int, summary_time.split(":"))
    sch.get_scheduler().add_job(
        send_daily_summary,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=sch.TZ),
        id="daily_summary",
        name="Daily Summary",
        replace_existing=True,
    )
    logger.info(f"Daily summary scheduled at {summary_time}")

    # Start message queue worker
    mq.start_worker()

    # Start background task to connect accounts and load watchers
    _startup_task = asyncio.create_task(connect_accounts_background())

    logger.info("=" * 50)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8888"))
    logger.info(f"Dashboard: http://{host}:{port}")
    logger.info("=" * 50)

    yield

    # ── Shutdown ──
    logger.info("Shutting down...")
    if _startup_task and not _startup_task.done():
        _startup_task.cancel()
    mq.stop_worker()
    sch.stop_scheduler()
    await rw.stop_all()
    await drt.stop_reply_tracker()
    await tg.disconnect_all()
    await db.close_db()
    # Disconnect Discord bots
    try:
        import discord_watcher as dw
        import discord_reaction_watcher as drw
        import discord_reply_tracker as drt_discord
        await dw.stop_all_watchers()
        await drw.stop_all()
        await drt_discord.stop_reply_tracker()
    except Exception:
        pass
    try:
        if discord_routes._adapter:
            await discord_routes._adapter.disconnect_all()
    except Exception:
        pass
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
app.include_router(auth.router, dependencies=_auth_dep)
app.include_router(chats.router, dependencies=_auth_dep)
app.include_router(schedules.router, dependencies=_auth_dep)
app.include_router(messages.router, dependencies=_auth_dep)
app.include_router(logs.router, dependencies=_auth_dep)
app.include_router(watchers.router, dependencies=_auth_dep)
app.include_router(settings.router, dependencies=_auth_dep)
app.include_router(blacklist.router, dependencies=_auth_dep)
app.include_router(reactions.router, dependencies=_auth_dep)
app.include_router(inbox.router, dependencies=_auth_dep)
app.include_router(discord_routes.router, dependencies=_auth_dep)
app.include_router(members.router, dependencies=_auth_dep)
app.include_router(analytics.router, dependencies=_auth_dep)
app.include_router(proxy.router, dependencies=_auth_dep)
app.include_router(invite.router, dependencies=_auth_dep)

app.include_router(warmup_routes.router, dependencies=_auth_dep)
app.include_router(ai_followup.router, dependencies=_auth_dep)
app.include_router(ai_agents_routes.router, dependencies=_auth_dep)
app.include_router(changelog_routes.router, dependencies=_auth_dep)

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", CacheControlledStaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"), headers={"Cache-Control": "public, max-age=3600"})


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8888"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
