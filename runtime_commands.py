"""Hàng đợi IPC SQLite tối thiểu cho web/worker.

Contract bảo mật:
- Chỉ nhận command trong ``ALLOWED_COMMANDS``; không eval/import động.
- Payload phải là object JSON nhỏ và không chứa trường secret/OTP/token/password.
- Handler phải sanitize result/error trước khi gọi complete/fail; module tiếp tục chặn
  khóa nhạy cảm và giới hạn kích thước, nhưng không thể nhận biết bí mật trong free text.
- Command Telegram có side effect chuyển sang ``unknown`` khi lease hết hạn; không tự retry.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

import aiosqlite

DB_PATH = None  # Adapter dùng database.DB_PATH/get_db; test truyền connection temp rõ ràng.
MAX_PAYLOAD_BYTES = 16_384
MAX_RESULT_BYTES = 16_384
MAX_ERROR_CHARS = 2_000
MAX_IDEMPOTENCY_KEY_CHARS = 200
ALLOWED_COMMANDS = frozenset({
    "chats.refresh",
    "watchers.reload",
    "campaign.start",
    "campaign.stop",
    "campaign.resume",
    "invite.run",
    "reaction.run",
    "warmup.run",
})
_SENSITIVE_PARTS = ("password", "passwd", "secret", "otp", "api_hash", "session", "token", "code")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_commands (
 id TEXT PRIMARY KEY, command TEXT NOT NULL, payload_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','unknown')),
 created_at REAL NOT NULL, expires_at REAL, claimed_at REAL, lease_until REAL,
 finished_at REAL, worker_id TEXT, owner_token TEXT, result_json TEXT, error_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_runtime_commands_claim
 ON runtime_commands(status, created_at);
"""

class ValidationError(ValueError): pass
class IdempotencyConflict(RuntimeError): pass
class OwnershipError(RuntimeError): pass


def _json(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} phải là JSON object")
    for key, child in _walk(value):
        if any(part in key.lower() for part in _SENSITIVE_PARTS):
            raise ValidationError(f"{label} chứa trường nhạy cảm bị cấm")
        if isinstance(child, (bytes, bytearray)):
            raise ValidationError(f"{label} không được chứa bytes")
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} không phải JSON hợp lệ") from exc
    if len(text.encode("utf-8")) > limit:
        raise ValidationError(f"{label} vượt giới hạn {limit} bytes")
    return text


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationError("khóa JSON phải là chuỗi")
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield "", child
            yield from _walk(child)


async def init_schema(db: aiosqlite.Connection) -> None:
    """Khởi tạo explicit trên connection do caller sở hữu."""
    await db.executescript(SCHEMA)
    await db.commit()


async def enqueue(db, command: str, payload: dict, idempotency_key: str, *, ttl_seconds: float | None = 300) -> str:
    if command not in ALLOWED_COMMANDS:
        raise ValidationError("command không được cho phép")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > MAX_IDEMPOTENCY_KEY_CHARS:
        raise ValidationError("idempotency_key không hợp lệ")
    body = _json(payload, MAX_PAYLOAD_BYTES, "payload")
    digest = hashlib.sha256((command + "\0" + body).encode()).hexdigest()
    command_id, now = secrets.token_hex(16), time.time()
    expires = now + ttl_seconds if ttl_seconds is not None else None
    try:
        await db.execute("INSERT INTO runtime_commands(id,command,payload_json,payload_hash,idempotency_key,status,created_at,expires_at) VALUES(?,?,?,?,?,'queued',?,?)", (command_id, command, body, digest, idempotency_key, now, expires))
        await db.commit()
        return command_id
    except aiosqlite.IntegrityError:
        await db.rollback()
        cur = await db.execute("SELECT id,payload_hash FROM runtime_commands WHERE idempotency_key=?", (idempotency_key,))
        row = await cur.fetchone()
        if row and row[1] == digest:
            return row[0]
        raise IdempotencyConflict("idempotency_key đã dùng với command/payload khác")


async def read(db, command_id: str) -> dict | None:
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM runtime_commands WHERE id=?", (command_id,))
    row = await cur.fetchone()
    if not row:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    item["result"] = json.loads(item.pop("result_json")) if item["result_json"] else None
    return item


async def claim(db, worker_id: str, *, lease_seconds: float = 30, now: float | None = None) -> dict | None:
    if not worker_id or lease_seconds <= 0:
        raise ValidationError("worker_id/lease không hợp lệ")
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(24)
    await db.execute("BEGIN IMMEDIATE")
    try:
        cur = await db.execute("SELECT id FROM runtime_commands WHERE status='queued' AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at,id LIMIT 1", (now,))
        row = await cur.fetchone()
        if not row:
            await db.commit(); return None
        await db.execute("UPDATE runtime_commands SET status='running',claimed_at=?,lease_until=?,worker_id=?,owner_token=? WHERE id=? AND status='queued'", (now, now + lease_seconds, worker_id, token, row[0]))
        await db.commit()
    except BaseException:
        await db.rollback(); raise
    item = await read(db, row[0])
    item["owner_token"] = token
    return item


async def _finish(db, command_id: str, owner_token: str, status: str, result: dict | None, error: str | None) -> None:
    result_text = _json(result or {}, MAX_RESULT_BYTES, "result") if result is not None else None
    if error is not None:
        if not isinstance(error, str): raise ValidationError("error phải là chuỗi")
        error = error[:MAX_ERROR_CHARS]
    cur = await db.execute("UPDATE runtime_commands SET status=?,result_json=?,error_text=?,finished_at=?,lease_until=NULL,owner_token=NULL WHERE id=? AND status='running' AND owner_token=?", (status, result_text, error, time.time(), command_id, owner_token))
    await db.commit()
    if cur.rowcount != 1:
        raise OwnershipError("command không còn thuộc owner token này")


async def complete(db, command_id: str, owner_token: str, result: dict | None = None) -> None:
    await _finish(db, command_id, owner_token, "succeeded", result, None)


async def fail(db, command_id: str, owner_token: str, error: str) -> None:
    await _finish(db, command_id, owner_token, "failed", None, error)


async def recover(db, *, now: float | None = None) -> dict[str, int]:
    """Expire queued; running hết lease thành unknown, tuyệt đối không retry side effect."""
    now = time.time() if now is None else now
    queued = await db.execute("UPDATE runtime_commands SET status='failed',error_text='expired before claim',finished_at=? WHERE status='queued' AND expires_at IS NOT NULL AND expires_at<=?", (now, now))
    running = await db.execute("UPDATE runtime_commands SET status='unknown',error_text='worker lease expired; outcome unknown',finished_at=?,owner_token=NULL WHERE status='running' AND lease_until<=?", (now, now))
    await db.commit()
    return {"queued_expired": queued.rowcount, "running_unknown": running.rowcount}
