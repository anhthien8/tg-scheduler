"""
Kiểm thử runtime_commands.py — hàng đợi IPC SQLite tối thiểu.

Tất cả test dùng DB tạm in-memory/thư mục tạm; không kết nối mạng, không import ứng dụng.
Schema được khởi tạo explicit qua init_schema(conn).
"""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path

import aiosqlite
import runtime_commands as queue


def _make_db_ctx(path):
    """Trả về coroutine mở connection mới đến file path."""
    return aiosqlite.connect(path)


class BaseQueueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.db')
        self.db = await aiosqlite.connect(self.path)
        await queue.init_schema(self.db)

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation(BaseQueueTest):
    async def test_unknown_command_rejected(self):
        with self.assertRaises(queue.ValidationError):
            await queue.enqueue(self.db, "evil.exec", {}, "k1")

    async def test_empty_idempotency_key_rejected(self):
        with self.assertRaises(queue.ValidationError):
            await queue.enqueue(self.db, "chats.refresh", {}, "")

    async def test_payload_not_dict_rejected(self):
        with self.assertRaises(queue.ValidationError):
            await queue.enqueue(self.db, "chats.refresh", [1, 2], "k2")  # type: ignore

    async def test_payload_oversized_rejected(self):
        big = {"x": "a" * (queue.MAX_PAYLOAD_BYTES + 1)}
        with self.assertRaises(queue.ValidationError):
            await queue.enqueue(self.db, "chats.refresh", big, "k3")

    async def test_sensitive_field_rejected(self):
        for key in ("password", "otp", "api_hash", "session_token", "secret_key"):
            with self.subTest(key=key):
                with self.assertRaises(queue.ValidationError):
                    await queue.enqueue(self.db, "chats.refresh", {key: "val"}, f"sens-{key}")

    async def test_non_dict_result_rejected_on_complete(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "k4")
        item = await queue.claim(self.db, "worker-1")
        with self.assertRaises(queue.ValidationError):
            await queue.complete(self.db, cid, item["owner_token"], result="string")  # type: ignore

    async def test_long_idempotency_key_rejected(self):
        with self.assertRaises(queue.ValidationError):
            await queue.enqueue(self.db, "chats.refresh", {}, "x" * (queue.MAX_IDEMPOTENCY_KEY_CHARS + 1))


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
class TestIdempotency(BaseQueueTest):
    async def test_same_key_same_payload_returns_same_id(self):
        a = await queue.enqueue(self.db, "chats.refresh", {"account_id": 1}, "req-1")
        b = await queue.enqueue(self.db, "chats.refresh", {"account_id": 1}, "req-1")
        self.assertEqual(a, b)

    async def test_same_key_different_payload_raises_conflict(self):
        await queue.enqueue(self.db, "chats.refresh", {"account_id": 1}, "req-2")
        with self.assertRaises(queue.IdempotencyConflict):
            await queue.enqueue(self.db, "chats.refresh", {"account_id": 99}, "req-2")

    async def test_same_key_different_command_raises_conflict(self):
        await queue.enqueue(self.db, "chats.refresh", {"account_id": 1}, "req-3")
        with self.assertRaises(queue.IdempotencyConflict):
            await queue.enqueue(self.db, "campaign.start", {"account_id": 1}, "req-3")

    async def test_re_enqueue_queued_item_no_duplicate(self):
        a = await queue.enqueue(self.db, "watchers.reload", {}, "req-4")
        b = await queue.enqueue(self.db, "watchers.reload", {}, "req-4")
        self.assertEqual(a, b)
        cur = await self.db.execute("SELECT count(*) FROM runtime_commands WHERE idempotency_key='req-4'")
        row = await cur.fetchone()
        self.assertEqual(row[0], 1)  # không tạo double row

    async def test_re_enqueue_after_succeeded_same_id(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "req-5")
        item = await queue.claim(self.db, "worker-1")
        await queue.complete(self.db, cid, item["owner_token"])
        # idempotency key đã tồn tại — phải trả id cũ, không insert mới
        same = await queue.enqueue(self.db, "chats.refresh", {}, "req-5")
        self.assertEqual(cid, same)


# ---------------------------------------------------------------------------
# Claim và ownership
# ---------------------------------------------------------------------------
class TestClaim(BaseQueueTest):
    async def test_claim_returns_item_with_token(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {"account_id": 5}, "clm-1")
        item = await queue.claim(self.db, "worker-A")
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], cid)
        self.assertEqual(item["status"], "running")
        self.assertIn("owner_token", item)

    async def test_empty_queue_claim_returns_none(self):
        result = await queue.claim(self.db, "worker-A")
        self.assertIsNone(result)

    async def test_complete_succeeds_with_correct_token(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "clm-2")
        item = await queue.claim(self.db, "worker-A")
        await queue.complete(self.db, cid, item["owner_token"], result={"updated": 3})
        row = await queue.read(self.db, cid)
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["result"]["updated"], 3)

    async def test_fail_succeeds_with_correct_token(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "clm-3")
        item = await queue.claim(self.db, "worker-A")
        await queue.fail(self.db, cid, item["owner_token"], error="Telegram RPC error 500")
        row = await queue.read(self.db, cid)
        self.assertEqual(row["status"], "failed")
        self.assertIn("Telegram", row["error_text"])

    async def test_late_completion_wrong_token_rejected(self):
        """Worker khác / token sai không được complete — bảo vệ owner."""
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "clm-4")
        await queue.claim(self.db, "worker-A")
        with self.assertRaises(queue.OwnershipError):
            await queue.complete(self.db, cid, "wrong-token-xyz")

    async def test_double_complete_second_rejected(self):
        """Complete lần thứ hai cùng token phải bị từ chối (status không còn là running)."""
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "clm-5")
        item = await queue.claim(self.db, "worker-A")
        await queue.complete(self.db, cid, item["owner_token"])
        with self.assertRaises(queue.OwnershipError):
            await queue.complete(self.db, cid, item["owner_token"])


# ---------------------------------------------------------------------------
# Concurrent claim — hai worker tranh nhau; mỗi task chỉ được claim một lần
# ---------------------------------------------------------------------------
class TestConcurrentClaim(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.db')
        self.db1 = await aiosqlite.connect(self.path)
        self.db2 = await aiosqlite.connect(self.path)
        await queue.init_schema(self.db1)

    async def asyncTearDown(self):
        await self.db1.close()
        await self.db2.close()
        self.tmp.cleanup()

    async def test_two_concurrent_claimers_no_double_claim(self):
        """
        Hai worker đồng thời claim một command; chỉ một thành công.
        """
        cid = await queue.enqueue(self.db1, "chats.refresh", {"account_id": 7}, "conc-1")
        # SQLite WAL cho phép concurrent reads nhưng BEGIN IMMEDIATE serialize writers
        r1, r2 = await asyncio.gather(
            queue.claim(self.db1, "worker-X"),
            queue.claim(self.db2, "worker-Y"),
            return_exceptions=True,
        )
        # Một trong hai nhận item, một nhận None; không có exception được ném
        results = [r for r in (r1, r2) if r is not None]
        nones = [r for r in (r1, r2) if r is None]
        self.assertFalse(any(isinstance(r, Exception) for r in (r1, r2)), (r1, r2))
        self.assertEqual(len(results), 1, "chỉ một worker được claim")
        self.assertEqual(results[0]["id"], cid)
        self.assertEqual(len(nones), 1)

    async def test_multiple_commands_distributed_among_workers(self):
        """Ba command, ba claim lần lượt — mỗi cái claimed riêng."""
        ids = {await queue.enqueue(self.db1, "chats.refresh", {"account_id": i}, f"dist-{i}") for i in range(3)}
        claimed = set()
        for w in ("W1", "W2", "W3"):
            item = await queue.claim(self.db1, w)
            self.assertIsNotNone(item)
            claimed.add(item["id"])
        self.assertEqual(claimed, ids)
        self.assertIsNone(await queue.claim(self.db1, "W4"))


# ---------------------------------------------------------------------------
# Recovery (crash simulation)
# ---------------------------------------------------------------------------
class TestRecovery(BaseQueueTest):
    async def test_expired_queued_becomes_failed(self):
        """
        Command queued quá hạn (expires_at đã qua) → recover() đánh dấu 'failed'.
        """
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "rec-1", ttl_seconds=60)
        counts = await queue.recover(self.db, now=time.time() + 120)
        self.assertEqual(counts["queued_expired"], 1)
        row = await queue.read(self.db, cid)
        self.assertEqual(row["status"], "failed")

    async def test_running_lease_expired_becomes_unknown(self):
        """
        Worker crash: lease hết → status = 'unknown'. KHÔNG retry tự động.
        """
        cid = await queue.enqueue(self.db, "campaign.start", {"campaign_id": 10}, "rec-2")
        item = await queue.claim(self.db, "crashed-worker", lease_seconds=10)
        self.assertIsNotNone(item)
        future = item["lease_until"] + 1
        counts = await queue.recover(self.db, now=future)
        self.assertEqual(counts["running_unknown"], 1)
        row = await queue.read(self.db, cid)
        self.assertEqual(row["status"], "unknown")
        self.assertIn("unknown", row["error_text"])

    async def test_valid_running_not_touched_by_recover(self):
        """Command đang chạy với lease còn hạn không bị recover."""
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "rec-3")
        item = await queue.claim(self.db, "alive-worker", lease_seconds=300)
        await queue.recover(self.db, now=item["claimed_at"] + 5)
        row = await queue.read(self.db, cid)
        self.assertEqual(row["status"], "running")

    async def test_late_complete_after_unknown_rejected(self):
        """
        Worker hoàn thành muộn sau khi đã bị đánh dấu unknown → OwnershipError.
        Owner token đã bị xóa, không được ghi kết quả lên command đã unknown.
        """
        cid = await queue.enqueue(self.db, "campaign.start", {"campaign_id": 2}, "rec-4")
        item = await queue.claim(self.db, "slow-worker", lease_seconds=5)
        future = item["lease_until"] + 1
        await queue.recover(self.db, now=future)
        with self.assertRaises(queue.OwnershipError):
            await queue.complete(self.db, cid, item["owner_token"])

    async def test_recover_no_effect_on_succeeded(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "rec-5")
        item = await queue.claim(self.db, "worker-ok")
        await queue.complete(self.db, cid, item["owner_token"])
        counts = await queue.recover(self.db, now=time.time() + 9999)
        self.assertEqual(counts["queued_expired"], 0)
        self.assertEqual(counts["running_unknown"], 0)
        self.assertEqual((await queue.read(self.db, cid))["status"], "succeeded")


# ---------------------------------------------------------------------------
# Read / status
# ---------------------------------------------------------------------------
class TestRead(BaseQueueTest):
    async def test_read_missing_returns_none(self):
        self.assertIsNone(await queue.read(self.db, "not-exist"))

    async def test_read_payload_deserialized(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {"account_id": 42}, "rr-1")
        row = await queue.read(self.db, cid)
        self.assertEqual(row["payload"]["account_id"], 42)
        self.assertNotIn("payload_json", row)

    async def test_error_truncated_at_limit(self):
        """error_text dài bị cắt tại MAX_ERROR_CHARS; không raise, không crash."""
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "rr-2")
        item = await queue.claim(self.db, "w")
        long_err = "E" * (queue.MAX_ERROR_CHARS * 2)
        await queue.fail(self.db, cid, item["owner_token"], error=long_err)
        row = await queue.read(self.db, cid)
        self.assertLessEqual(len(row["error_text"]), queue.MAX_ERROR_CHARS)


# ---------------------------------------------------------------------------
# No-TTL command (optional expires_at = None)
# ---------------------------------------------------------------------------
class TestNoTTL(BaseQueueTest):
    async def test_no_ttl_not_expired_by_recover(self):
        cid = await queue.enqueue(self.db, "chats.refresh", {}, "nottl-1", ttl_seconds=None)
        counts = await queue.recover(self.db, now=time.time() + 9999)
        self.assertEqual(counts["queued_expired"], 0)
        self.assertEqual((await queue.read(self.db, cid))["status"], "queued")


if __name__ == "__main__":
    unittest.main(verbosity=2)
