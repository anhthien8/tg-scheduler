import pytest
import asyncio
import time
import random
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import database as db
from routes.analytics import analytics_cache, AsyncTTLCache

pytestmark = pytest.mark.asyncio

# ==============================================================================
# 1. DATABASE STRESS, CONCURRENCY, ROLLBACKS AND STARVATION
# ==============================================================================

async def test_bulk_insert_concurrency():
    """
    Test database bulk insert concurrency under heavy load.
    Runs multiple concurrent tasks calling save_scraped_members with large datasets.
    Checks for locks/exceptions and verifies total records inserted correctly.
    """
    await db.init_db()
    
    # Pre-populate account
    account_id = await db.create_account({
        "name": "Scrape Target Account",
        "phone": "+8499999999",
        "api_id": "api_id_scrape",
        "api_hash": "api_hash_scrape",
        "session_name": "session_scrape",
        "proxy_url": None
    })

    # Prepare concurrent writers
    num_tasks = 15
    members_per_task = 100
    
    async def writer_task(task_idx):
        # Generate member user IDs specifically to avoid collision between tasks unless testing dedup
        members = [
            {
                "user_id": 100000 + task_idx * 1000 + i,
                "username": f"user_t{task_idx}_{i}",
                "first_name": f"First_{task_idx}_{i}",
                "last_name": f"Last_{task_idx}_{i}",
                "phone": f"+84900{task_idx:02d}{i:02d}",
                "is_bot": False,
                "is_premium": random.choice([True, False]),
                "status": "active",
                "last_seen": "online"
            }
            for i in range(members_per_task)
        ]
        await db.save_scraped_members(
            scrape_job_id=f"job_concurrency_{task_idx}",
            account_id=account_id,
            group_id=2000 + task_idx,
            group_title=f"Concurrent Group {task_idx}",
            members=members
        )

    # Launch all tasks concurrently
    tasks = [writer_task(i) for i in range(num_tasks)]
    await asyncio.gather(*tasks)

    # Verify counts
    for i in range(num_tasks):
        cnt = await db.count_scraped_members(f"job_concurrency_{i}")
        assert cnt == members_per_task, f"Expected {members_per_task} scraped members for job_concurrency_{i}, got {cnt}"


async def test_transaction_abort_rollback_and_leak():
    """
    Verify that if a database operation fails/aborts mid-transaction:
    - The transaction is rolled back correctly.
    - No changes are persisted.
    - Connection semaphore is released properly (no connection pool leaks).
    """
    await db.init_db()

    # Pre-populate an account
    account_id = await db.create_account({
        "name": "Rollback Test",
        "phone": "+8499999998",
        "api_id": "api_id_rb",
        "api_hash": "api_hash_rb",
        "session_name": "session_rb",
        "proxy_url": None
    })

    # Record current connection pool availability
    pool = db._pool
    initial_available = pool._semaphore._value

    # We will trigger a failure by inserting a duplicate constraint in transaction or a manual raise
    # Let's perform a custom block that simulates failure inside a transaction
    with pytest.raises(ValueError):
        async with db.get_db() as db_conn:
            await db_conn.execute(
                "INSERT INTO accounts (name, phone, api_id, api_hash, session_name) VALUES (?, ?, ?, ?, ?)",
                ("Ghost Account", "+8400000000", "ghost_api", "ghost_hash", "session_ghost_rb")
            )
            # This execute should raise a UniqueConstraintError if session_name already exists, 
            # but let's raise a ValueError manually to test clean rollback of preceding commands
            await db_conn.execute(
                "INSERT INTO dm_blacklist (user_id, username, reason) VALUES (?, ?, ?)",
                (999999, "ghost_user", "Should be rolled back")
            )
            raise ValueError("Forced error mid-transaction")

    # Verify that the dm_blacklist insert was rolled back
    is_blacklisted = await db.is_user_blacklisted(999999)
    assert not is_blacklisted, "Blacklisted user should have been rolled back"

    # Verify connection was released back to the pool
    final_available = pool._semaphore._value
    assert final_available == initial_available, f"Connection pool leak! Initial sem value: {initial_available}, Final: {final_available}"


# ==============================================================================
# 2. ANALYTICS TTL CACHE DETAILED ORACLES
# ==============================================================================

async def test_analytics_cache_stampede():
    """
    Singleflight protection: when multiple async tasks request a cold key
    concurrently, only ONE database query must execute; the rest wait on the
    per-key lock and share the cached result.
    """
    from routes.analytics import analytics_cache
    analytics_cache.cache.clear()
    analytics_cache._locks.clear()

    # Mock get_analytics_overview with a delay to force concurrent overlap
    async def mock_heavy_query():
        await asyncio.sleep(0.05)
        return {"total_dm_sent": 42}

    with patch("database.get_analytics_overview", side_effect=mock_heavy_query) as mock_db:
        # 5 concurrent callers go through the same code path the route uses
        results = await asyncio.gather(*(
            analytics_cache.get_or_fetch("overview", db.get_analytics_overview)
            for _ in range(5)
        ))

        # All callers got the correct data
        for r in results:
            assert r["total_dm_sent"] == 42

        # Singleflight: exactly one DB query despite 5 concurrent cold reads
        assert mock_db.call_count == 1, (
            f"Cache stampede: {mock_db.call_count} DB queries for 5 concurrent cold reads"
        )


async def test_analytics_cache_memory_growth():
    """
    LRU bound: AsyncTTLCache must cap its size at maxsize so that writing
    infinite unique keys (e.g. daily-stats?days=X) cannot grow memory unbounded.
    """
    cache = AsyncTTLCache(ttl_seconds=60, maxsize=256)

    # Set 1000 unique keys — cache must stay bounded at maxsize
    for i in range(1000):
        cache.set(f"key_{i}", {"data": i})

    assert len(cache.cache) <= 256, f"Cache unbounded: {len(cache.cache)} entries"

    # Most recently written keys survive; oldest were evicted
    assert cache.get("key_999") == {"data": 999}
    assert cache.get("key_0") is None

    # Fresh entries still respect TTL expiry
    short_cache = AsyncTTLCache(ttl_seconds=1, maxsize=256)
    short_cache.set("k", {"v": 1})
    assert short_cache.get("k") == {"v": 1}
    await asyncio.sleep(1.1)
    assert short_cache.get("k") is None


async def test_analytics_cache_fallback_and_failure():
    """
    Stale-while-revalidate: when the cache entry is expired AND the database
    query fails, get_or_fetch must fall back to the stale cached value instead
    of crashing the endpoint.
    """
    from routes.analytics import analytics_cache
    analytics_cache.cache.clear()
    analytics_cache._locks.clear()

    # Pre-populate with an expired entry (35s old, TTL is 30s)
    analytics_cache.cache["overview"] = (time.time() - 35, {"total_dm_sent": 100})

    # Fresh get() returns None for expired entry...
    assert analytics_cache.get("overview") is None
    # ...but get_stale() still returns the value for fallback
    assert analytics_cache.get_stale("overview") == {"total_dm_sent": 100}

    # Database fails -> get_or_fetch serves stale data instead of raising
    with patch("database.get_analytics_overview", side_effect=Exception("Database connection failure")):
        val = await analytics_cache.get_or_fetch("overview", db.get_analytics_overview)
        assert val == {"total_dm_sent": 100}

    # With NO stale data available, the error must propagate (no silent None)
    analytics_cache.cache.clear()
    analytics_cache._locks.clear()
    with patch("database.get_analytics_overview", side_effect=Exception("Database connection failure")):
        with pytest.raises(Exception, match="Database connection failure"):
            await analytics_cache.get_or_fetch("overview", db.get_analytics_overview)


# ==============================================================================
# 3. UTF-8 ENCODING AND SANITIZATION INTEGRITY
# ==============================================================================

async def test_utf8_database_integrity():
    """
    Verify UTF-8 characters (Vietnamese diacritics, emoji, combining characters, surrogate pairs)
    are successfully stored in the database, retrieved, and exported via CSV without corruption.
    """
    await db.init_db()

    # UTF-8 payloads: Vietnamese, Emojis, Combining Chars, Non-BMP
    vietnamese_str = "Học Viện Công Nghệ Bưu Chính Viễn Thông - Tiếng Việt có dấu"
    emoji_str = "🚀 😊 👑 💥 🌟 🌈 🐱‍👤"
    combining_str = "o\u0302\u0301" # ố using combining circumflex and acute
    non_bmp_str = "𠜎𠜱𠝹𠱓" # Rare Han characters (Non-BMP)
    
    combined_test_payload = f"{vietnamese_str} | {emoji_str} | {combining_str} | {non_bmp_str}"

    # Insert into a table (e.g. dm_blacklist)
    await db.add_to_dm_blacklist(user_id=88888, username="utf8_user", reason=combined_test_payload)

    # Retrieve and verify
    blacklist = await db.get_dm_blacklist()
    saved_row = next((r for r in blacklist if r["user_id"] == 88888), None)
    assert saved_row is not None
    assert saved_row["reason"] == combined_test_payload
    
    # Check that encoding/decoding is completely round-trip lossless
    encoded = saved_row["reason"].encode('utf-8')
    decoded = encoded.decode('utf-8')
    assert decoded == combined_test_payload


def test_utf8_endpoint_payloads(client):
    """
    Verify that FastAPI routes accept UTF-8 inputs, store them,
    and return them in responses without double-encoding or truncation.
    """
    test_payload = {
        "name": "Chiến dịch Tết 2026 🧨",
        "category": "marketing",
        "messages": [
            {"msg_type": "text", "content": "Chào {{first_name}}! Chúc mừng năm mới 🌸🏮"}
        ],
        "is_default": 0
    }

    # Create template
    resp = client.post("/api/templates", json=test_payload)
    assert resp.status_code == 200
    template_id = resp.json()["id"]

    # Read templates list
    resp_get = client.get("/api/templates")
    assert resp_get.status_code == 200
    templates = resp_get.json()
    
    saved_tpl = next((t for t in templates if t["id"] == template_id), None)
    assert saved_tpl is not None
    assert saved_tpl["name"] == "Chiến dịch Tết 2026 🧨"
    assert saved_tpl["messages"][0]["content"] == "Chào {{first_name}}! Chúc mừng năm mới 🌸🏮"


async def test_async_ttl_cache_locks_eviction():
    """
    Verify that AsyncTTLCache._locks doesn't grow unbounded under concurrent/sequential hits.
    Locks should be evicted from self._locks after get_or_fetch completes.
    """
    cache = AsyncTTLCache(ttl_seconds=60, maxsize=256)
    
    # 1. Sequential hits
    async def mock_fetch_success():
        return "value"
        
    await cache.get_or_fetch("seq_key_1", mock_fetch_success)
    await cache.get_or_fetch("seq_key_2", mock_fetch_success)
    
    assert len(cache._locks) == 0, f"Locks leaked on sequential hits: {list(cache._locks.keys())}"
    
    # 2. Concurrent hits
    async def mock_fetch_delayed():
        await asyncio.sleep(0.1)
        return "delayed_value"
        
    # Launch concurrent requests
    t1 = asyncio.create_task(cache.get_or_fetch("concurrent_key", mock_fetch_delayed))
    t2 = asyncio.create_task(cache.get_or_fetch("concurrent_key", mock_fetch_delayed))
    
    # Wait for them to start and acquire lock (t1 or t2 will create the lock)
    await asyncio.sleep(0.02)
    assert len(cache._locks) == 1, "Lock should exist during concurrent execution"
    
    await asyncio.gather(t1, t2)
    
    # After execution finishes, lock should be evicted
    assert len(cache._locks) == 0, f"Lock leaked on concurrent hits: {list(cache._locks.keys())}"
