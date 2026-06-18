"""
Send log routes - history and stats.
"""
from fastapi import APIRouter
from typing import Optional
import database as db

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def list_logs(limit: int = 50, offset: int = 0,
                    schedule_id: Optional[int] = None,
                    status: Optional[str] = None,
                    account_id: Optional[int] = None):
    """Get send logs with pagination and filters."""
    result = await db.get_send_logs(
        limit=limit, offset=offset,
        schedule_id=schedule_id, status=status,
        account_id=account_id
    )
    return result


@router.get("/stats")
async def log_stats():
    """Get overall statistics."""
    stats = await db.get_log_stats()
    return stats
