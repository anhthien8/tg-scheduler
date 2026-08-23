import asyncio
import os
import tempfile
import shutil
import pytest
import database

@pytest.mark.asyncio
async def test_pool_starvation_deadlock():
    # Setup temporary database
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_starvation.db")
    database.DB_PATH = test_db
    
    try:
        # Initialize pool with max_connections = 1
        database._pool = database.ConnectionPool(test_db, max_connections=1, timeout=1.0)
        
        # 1. Acquire the only available connection
        conn1 = await database._pool.acquire()
        assert getattr(conn1, "_connection", None) is not None
        
        # 2. Start a background task to acquire a connection (this should block)
        acquire_task = asyncio.create_task(database._pool.acquire())
        
        # Give the background task a moment to run and enter the await queue.get() state
        await asyncio.sleep(0.1)
        
        # 3. Simulate conn1 closing (e.g. database error or manual close)
        await conn1.close()
        assert getattr(conn1, "_connection", None) is None
        
        # 4. Release conn1
        await database._pool.release(conn1)
        
        # Under correct pool logic, the waiting task should be unblocked and given a new connection.
        try:
            conn2 = await asyncio.wait_for(acquire_task, timeout=2.0)
            print("[PASS] Connection was successfully acquired (no starvation).")
            await database._pool.release(conn2)
            starved = False
        except asyncio.TimeoutError:
            print("[FAIL] Task was starved and timed out!")
            starved = True
            
        assert not starved, "The pool starvation deadlock occurred."
        
    finally:
        # Cleanup
        await database.close_db()
        shutil.rmtree(temp_dir)

@pytest.mark.asyncio
async def test_cancellation_no_permit_leak():
    # Setup temporary database
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_cancellation.db")
    database.DB_PATH = test_db
    
    try:
        # Initialize pool with max_connections = 1
        pool = database.ConnectionPool(test_db, max_connections=1, timeout=1.0)
        database._pool = pool
        
        # Start a task to acquire a connection
        task = asyncio.create_task(pool.acquire())
        
        # Yield to let the task start and yield during connect
        await asyncio.sleep(0.01)
        
        # Cancel the task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
            
        # If there was a permit leak, the semaphore would still be locked/empty.
        # Let's verify we can now acquire a connection successfully without blocking.
        try:
            conn = await asyncio.wait_for(pool.acquire(), timeout=1.0)
            assert conn._conn is not None
            await pool.release(conn)
            leaked = False
        except asyncio.TimeoutError:
            leaked = True
            
        assert not leaked, "Permit was leaked during cancellation!"
        
    finally:
        # Cleanup
        await database.close_db()
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    asyncio.run(test_pool_starvation_deadlock())
    asyncio.run(test_cancellation_no_permit_leak())
