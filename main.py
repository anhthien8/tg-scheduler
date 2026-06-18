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
from routes import auth, chats, schedules, messages, logs, watchers, settings, blacklist, reactions
import reaction_watcher as rw

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # ── Startup ──
    logger.info("=" * 50)
    logger.info("TG Scheduler starting up...")

    # Init database
    await db.init_db()
    logger.info("Database initialized")

    # Load all accounts and connect their clients
    accounts = await db.get_all_accounts()
    logged_count = 0
    for acc in accounts:
        try:
            proxy_url = acc.get("proxy_url")  # Load per-account proxy from DB
            await tg.create_client(acc["id"], int(acc["api_id"]), acc["api_hash"], acc["session_name"], proxy_url=proxy_url)
            authorized = await asyncio.wait_for(tg.start_client(acc["id"]), timeout=30)
            if authorized:
                logged_count += 1
                await db.update_account_login_status(acc["id"], True)
            else:
                await db.update_account_login_status(acc["id"], False)
        except asyncio.TimeoutError:
            logger.warning(f"Account {acc['id']} ({acc['name']}): connect timed out after 30s")
        except Exception as e:
            logger.warning(f"Account {acc['id']} ({acc['name']}): connect failed: {e}")

    logger.info(f"Loaded {len(accounts)} accounts, {logged_count} already logged in")

    # Start scheduler
    sch.start_scheduler()
    if logged_count > 0:
        await sch.load_all_jobs()

    # Start message queue worker
    mq.start_worker()

    # Start keyword watchers
    await kw.start_all_watchers()

    # Start reaction watchers
    await rw.start_all()

    logger.info("=" * 50)
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8888"))
    logger.info(f"Dashboard: http://{host}:{port}")
    logger.info("=" * 50)

    yield

    # ── Shutdown ──
    logger.info("Shutting down...")
    mq.stop_worker()
    sch.stop_scheduler()
    await rw.stop_all()
    await tg.disconnect_all()
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

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8888"))
    uvicorn.run("main:app", host=host, port=port, reload=False)
