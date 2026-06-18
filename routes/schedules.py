"""
Schedule management routes - CRUD, toggle, preview, send-now.
"""
from fastapi import APIRouter, HTTPException
from models import ScheduleCreate, ScheduleUpdate
import database as db
import scheduler as sch
import message_queue as mq
import telegram_client as tg

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("")
async def list_schedules():
    schedules = await db.get_all_schedules()
    for s in schedules:
        s["next_run"] = sch.get_next_run(s["id"])
    return {"schedules": schedules}


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: int):
    s = await db.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, "Schedule not found")
    s["next_run"] = sch.get_next_run(s["id"])
    return s


@router.post("")
async def create_schedule(data: ScheduleCreate):
    payload = data.model_dump()
    schedule_id = await db.create_schedule(payload)
    schedule = await db.get_schedule(schedule_id)
    if schedule and schedule["is_active"]:
        sch.add_schedule_job(schedule)
    return {"success": True, "id": schedule_id}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: int, data: ScheduleUpdate):
    existing = await db.get_schedule(schedule_id)
    if not existing:
        raise HTTPException(404, "Schedule not found")
    payload = data.model_dump()
    await db.update_schedule(schedule_id, payload)
    updated = await db.get_schedule(schedule_id)
    if updated["is_active"]:
        sch.add_schedule_job(updated)
    else:
        sch.remove_schedule_job(schedule_id)
    return {"success": True}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int):
    existing = await db.get_schedule(schedule_id)
    if not existing:
        raise HTTPException(404, "Schedule not found")
    sch.remove_schedule_job(schedule_id)
    await db.delete_schedule(schedule_id)
    return {"success": True}


@router.patch("/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: int):
    result = await db.toggle_schedule(schedule_id)
    if not result:
        raise HTTPException(404, "Schedule not found")
    if result["is_active"]:
        schedule = await db.get_schedule(schedule_id)
        sch.add_schedule_job(schedule)
    else:
        sch.remove_schedule_job(schedule_id)
    return result


@router.post("/{schedule_id}/send-now")
async def send_now(schedule_id: int):
    schedule = await db.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    await mq.enqueue_schedule(schedule_id)
    return {"success": True, "message": "Đã đưa vào hàng đợi gửi"}


@router.post("/{schedule_id}/preview")
async def preview(schedule_id: int):
    """Send schedule messages to Saved Messages of the account."""
    schedule = await db.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")

    account_id = schedule.get("account_id", 1)
    if not await tg.is_authorized(account_id):
        raise HTTPException(400, "Account chưa đăng nhập")

    me = await tg.get_me(account_id)
    if not me:
        raise HTTPException(400, "Cannot get account info")

    # Send to Saved Messages (self)
    q = mq.get_queue()
    for msg in sorted(schedule.get("messages", []), key=lambda m: m.get("msg_order", 0)):
        await q.put({
            "schedule_id": schedule_id,
            "account_id": account_id,
            "message": msg,
            "target": {
                "chat_id": me["user_id"],
                "chat_title": "Saved Messages",
                "chat_type": "self"
            },
            "retry_count": 0
        })

    return {"success": True, "message": f"Preview đã gửi đến Saved Messages (account: {me.get('first_name', '')})"}


@router.post("/{schedule_id}/reset-count")
async def reset_count(schedule_id: int):
    """Reset the send counter for a schedule."""
    schedule = await db.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    await db.reset_send_count(schedule_id)
    return {"success": True, "message": "Đã reset số lần gửi"}


@router.get("/{schedule_id}/blocked-targets")
async def get_blocked_targets(schedule_id: int):
    """Get all (account, chat) pairs blocked due to repeated failures."""
    schedule = await db.get_schedule(schedule_id)
    if not schedule:
        raise HTTPException(404, "Schedule not found")
    blocks = await db.get_blocked_targets(schedule_id)
    return {"blocked": blocks, "count": len(blocks)}


@router.post("/{schedule_id}/unblock-target")
async def unblock_target(schedule_id: int, account_id: int, chat_id: int):
    """Manually unblock a (account, chat) target."""
    await db.unblock_target(schedule_id, account_id, chat_id)
    return {"success": True, "message": "Đã mở khóa target"}

