"""
Main entry point - starts FastAPI + multi-account Telethon + APScheduler.
"""
import os
import logging
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

import database as db
import telegram_client as tg
import scheduler as sch
import message_queue as mq
from routes import auth, chats, schedules, messages, logs

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("tg-scheduler")


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
            await tg.create_client(acc["id"], int(acc["api_id"]), acc["api_hash"], acc["session_name"])
            authorized = await tg.start_client(acc["id"])
            if authorized:
                logged_count += 1
                await db.update_account_login_status(acc["id"], True)
            else:
                await db.update_account_login_status(acc["id"], False)
        except Exception as e:
            logger.warning(f"Account {acc['id']} ({acc['name']}): connect failed: {e}")

    logger.info(f"Loaded {len(accounts)} accounts, {logged_count} already logged in")

    # Start scheduler
    sch.start_scheduler()
    if logged_count > 0:
        await sch.load_all_jobs()

    # Start message queue worker
    mq.start_worker()

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
    await tg.disconnect_all()
    logger.info("Goodbye!")


app = FastAPI(title="TG Scheduler", lifespan=lifespan)

# Include API routes
app.include_router(auth.router)
app.include_router(chats.router)
app.include_router(schedules.router)
app.include_router(messages.router)
app.include_router(logs.router)

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
