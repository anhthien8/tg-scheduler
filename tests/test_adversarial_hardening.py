import pytest
import asyncio
import os
import tempfile
import shutil
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import Response
from fastapi.testclient import TestClient

import database
import message_queue as mq
from main import app

# ── Dynamic Routes for Gzip & Payload Testing ────────────────────────────────

@app.get("/test-empty")
def endpoint_test_empty():
    return Response(content="", media_type="text/plain")

@app.get("/test-204", status_code=204)
def endpoint_test_204():
    return Response(status_code=204)


# ── Connection Pool Hardening Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_pool_connection_leak_on_append_cancellation():
    """
    Test that if ConnectionPool.acquire() is cancelled during or immediately after
    connection creation, the connection is not leaked (it gets closed).
    """
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_leak.db")
    try:
        pool = database.ConnectionPool(test_db, max_connections=1, timeout=1.0)
        
        # Track connections created
        created_connections = []
        original_create = pool._create_connection
        
        async def mock_create_connection():
            conn = await original_create()
            created_connections.append(conn)
            return conn
            
        pool._create_connection = mock_create_connection
        
        # Mock pool._connections.append to raise CancelledError via MockList
        class MockList(list):
            def append(self, item):
                raise asyncio.CancelledError()
            
        pool._connections = MockList()
        
        # Run acquire and expect CancelledError
        with pytest.raises(asyncio.CancelledError):
            await pool.acquire()
            
        # Verify permit is not leaked (semaphore value should be 1)
        assert pool._semaphore._value == 1
        
        # Verify that all connections created during the aborted call were closed
        assert len(created_connections) > 0
        for conn in created_connections:
            assert getattr(conn, "_connection", None) is None, "A SQLite connection was leaked (not closed) on cancellation!"
            
    finally:
        await pool.close_all()
        shutil.rmtree(temp_dir)


@pytest.mark.asyncio
async def test_pool_starvation_timeout_prevention():
    """
    Test how the connection pool handles acquisition timeout and starvation.
    If the pool has no available connections, it should raise or allow timeout wrapped
    calls without locking the internal lock permanently or leaking permits.
    """
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_starvation.db")
    try:
        pool = database.ConnectionPool(test_db, max_connections=1, timeout=0.5)
        
        # 1. Acquire the single connection
        conn1 = await pool.acquire()
        assert getattr(conn1, "_connection", None) is not None
        
        # 2. Try to acquire again with a short timeout
        # It should time out because max_connections = 1
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pool.acquire(), timeout=0.2)
            
        # 3. Verify permit count is still valid (internal semaphore is at 0, no permit leak)
        assert pool._semaphore._value == 0
        
        # 4. Release conn1 and make sure we can now acquire
        await pool.release(conn1)
        assert pool._semaphore._value == 1
        
        conn2 = await pool.acquire()
        assert getattr(conn2, "_connection", None) is not None
        await pool.release(conn2)
        
    finally:
        await pool.close_all()
        shutil.rmtree(temp_dir)


# ── Message Queue Worker Hardening Tests ─────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_worker_cancellation_double_task_done_leak():
    """
    Test that cancelling the queue worker while it is actively processing a message
    does not lead to ValueError (double task_done) or task_done leak.
    """
    # Reset queue for the current test event loop
    mq._worker_task = None
    mq._queue = asyncio.Queue()
    q = mq.get_queue()
            
    # Enqueue a test item
    await q.put({
        "schedule_id": 1,
        "account_id": 1,
        "message": {"msg_type": "text", "content": "Adversarial test message", "id": 1},
        "target": {"chat_id": 12345, "chat_title": "Test Chat"},
        "retry_count": 0
    })
    
    # Mock sending to hang so we can cancel the worker during execution
    async def mock_send(*args, **kwargs):
        await asyncio.sleep(5.0)
        return True
        
    with patch("message_queue._send_single_message", side_effect=mock_send):
        worker_task = mq.start_worker()
        
        # Give the worker a moment to retrieve the item from the queue
        await asyncio.sleep(0.1)
        
        # Cancel the worker while it is awaiting _send_single_message
        mq.stop_worker()
        
        # Wait for the task to finish
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # If the double task_done() ValueError occurs, it will propagate here
            pytest.fail(f"Queue worker raised exception on cancellation: {e}")
            
    # Clean up queue to avoid side effects
    while not q.empty():
        try:
            q.get_nowait()
            q.task_done()
        except (ValueError, asyncio.QueueEmpty):
            pass


# ── HTTP Layer / GZip / Cache-Control Adversarial Tests ──────────────────────

def test_gzip_empty_payload(client):
    """
    Test that GZip middleware correctly handles empty payloads (0 bytes) and 204 responses
    without causing issues or incorrectly applying gzip compression.
    """
    headers = {"Accept-Encoding": "gzip"}
    
    # 1. Test empty 200 response
    resp = client.get("/test-empty", headers=headers)
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.content == b""
    
    # 2. Test 204 response
    resp2 = client.get("/test-204", headers=headers)
    assert resp2.status_code == 204
    assert "content-encoding" not in resp2.headers


def test_static_files_range_requests(client):
    """
    Test that range requests for static files are correctly supported (serving chunks)
    and verify how it interacts with the Accept-Encoding: gzip header.
    Gzip middleware should not compress range responses as it breaks range offsets.
    """
    # First, get the full content to know the size
    full_resp = client.get("/static/index.html")
    assert full_resp.status_code == 200
    total_size = len(full_resp.content)
    assert total_size > 100
    
    # 1. Request bytes 10-50
    headers = {"Range": "bytes=10-50"}
    resp = client.get("/static/index.html", headers=headers)
    assert resp.status_code == 206
    assert resp.headers.get("content-range") == f"bytes 10-50/{total_size}"
    assert len(resp.content) == 41
    assert resp.content == full_resp.content[10:51]
    
    # 2. Request range with Accept-Encoding: gzip
    headers2 = {"Range": "bytes=10-50", "Accept-Encoding": "gzip"}
    resp2 = client.get("/static/index.html", headers=headers2)
    # The response should either be 206 (not compressed) or 200 (if gzip takes precedence, compressed).
    # But it must not be 206 compressed!
    if resp2.status_code == 206:
        assert "content-encoding" not in resp2.headers


def test_cache_control_headers_validity(client):
    """
    Verify Cache-Control headers:
    - Static files and index.html should have public caching.
    - Sensitive API routes should NOT have public caching (e.g. no-store, no-cache, or private).
    """
    # 1. Static file
    resp_static = client.get("/static/index.html")
    assert resp_static.status_code == 200
    assert resp_static.headers.get("cache-control") == "public, max-age=3600"
    
    # 2. Sensitive APIs
    resp_api = client.get("/api/auth/accounts")
    # It might be 200 (since we use pre-configured client with test secret key)
    assert resp_api.status_code == 200
    cache_header = resp_api.headers.get("cache-control", "")
    # It should not allow public caching of account details
    assert "public" not in cache_header.lower()
