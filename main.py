"""
Main entry point - starts FastAPI + multi-account Telethon + APScheduler.
"""
import os
import secrets
import asyncio
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Security, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import database as db
import telegram_client as tg
import scheduler as sch
import message_queue as mq
import keyword_watcher as kw
from routes import auth, chats, schedules, messages, logs, watchers, settings, blacklist, reactions, inbox, members, analytics
from routes import discord as discord_routes
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
    """Verify X-API-Key header. Only enforced when DASHBOARD_SECRET_KEY is set."""
    if API_KEY and not secrets.compare_digest(key or "", API_KEY):
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

    # Init database
    await db.init_db()
    logger.info("Database initialized")

    # Start scheduler
    sch.start_scheduler()
    await sch.load_all_jobs()

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


app = FastAPI(title="TG Scheduler", lifespan=lifespan)

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

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8888"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
