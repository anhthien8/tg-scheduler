"""DB-only split-runtime API for the first migrated operations."""
import secrets
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
import database as db
import runtime_commands as commands

router = APIRouter(prefix="/api/ipc", tags=["split-runtime"])

class AccountCommand(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    account_id: int = Field(gt=0)

class CampaignCommand(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    campaign_id: int = Field(gt=0)

class WatcherCommand(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    watcher_id: int = Field(gt=0)

async def _rows(sql, args=()):
    async with db.get_db() as conn:
        conn.row_factory = __import__('aiosqlite').Row
        cur = await conn.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

@router.get("/accounts")
async def accounts():
    # Explicit projection: credentials/session/proxy never cross this boundary.
    rows = await _rows("SELECT id,name,phone,is_logged_in,CASE WHEN COALESCE(is_paused,0)=0 THEN 1 ELSE 0 END AS is_active,is_premium,pause_reason,ai_agent_id FROM accounts ORDER BY id")
    return {"accounts": rows}

@router.get("/campaigns")
async def campaigns():
    return {"campaigns": await db.get_all_dm_campaigns()}

@router.get("/scrape-jobs")
async def scrape_jobs():
    return {"jobs": await db.get_scrape_jobs()}

@router.get("/watchers")
async def watchers():
    return {"watchers": await db.get_all_watchers_by_platform("telegram")}

async def _exists(table, item_id):
    rows = await _rows(f"SELECT 1 FROM {table} WHERE id=? LIMIT 1", (item_id,))
    return bool(rows)

async def _enqueue(command, payload, key, response):
    key = key or secrets.token_urlsafe(24)
    async with db.get_db() as conn:
        await commands.init_schema(conn)
        try:
            command_id = await commands.enqueue(conn, command, payload, key)
        except commands.IdempotencyConflict as exc:
            raise HTTPException(409, str(exc))
        except commands.ValidationError as exc:
            raise HTTPException(400, str(exc))
    response.status_code = 202
    return {"command_id": command_id, "status": "queued"}

@router.post("/commands/chats.refresh")
async def refresh_chats(body: AccountCommand, response: Response, x_idempotency_key: str | None = Header(None)):
    if not await _exists("accounts", body.account_id):
        raise HTTPException(404, "Account not found")
    return await _enqueue("chats.refresh", body.model_dump(), x_idempotency_key, response)

@router.post("/commands/campaign.start")
async def start_campaign(body: CampaignCommand, response: Response, x_idempotency_key: str | None = Header(None)):
    if not await _exists("dm_campaigns", body.campaign_id):
        raise HTTPException(404, "Campaign not found")
    return await _enqueue("campaign.start", body.model_dump(), x_idempotency_key, response)

@router.post("/commands/campaign.stop")
async def stop_campaign(body: CampaignCommand, response: Response, x_idempotency_key: str | None = Header(None)):
    if not await _exists("dm_campaigns", body.campaign_id):
        raise HTTPException(404, "Campaign not found")
    return await _enqueue("campaign.stop", body.model_dump(), x_idempotency_key, response)

@router.post("/commands/watchers.reload")
async def reload_watcher(body: WatcherCommand, response: Response, x_idempotency_key: str | None = Header(None)):
    if not await _exists("keyword_watchers", body.watcher_id):
        raise HTTPException(404, "Watcher not found")
    return await _enqueue("watchers.reload", body.model_dump(), x_idempotency_key, response)

@router.post("/commands/{command_name}")
async def reject_command(command_name: str):
    raise HTTPException(400, "Command not allowed")

@router.get("/commands/{command_id}")
async def command_status(command_id: str):
    async with db.get_db() as conn:
        await commands.init_schema(conn)
        # Expired lease → 'unknown' before the client reads it; never resend on its behalf.
        await commands.recover(conn)
        item = await commands.read(conn, command_id)
    if not item:
        raise HTTPException(404, "Command not found")
    for field in ("owner_token", "payload_hash", "worker_id"):
        item.pop(field, None)
    return item
