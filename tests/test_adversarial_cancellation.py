import asyncio
import os
import tempfile
import shutil
import pytest
import database

@pytest.mark.asyncio
async def test_cancelled_release_permit_leak():
    # Setup temporary database
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "test_leak.db")
    database.DB_PATH = test_db
    
    try:
        # Initialize pool with max_connections = 1
        pool = database.ConnectionPool(test_db, max_connections=1, timeout=1.0)
        database._pool = pool
        
        # 1. Acquire the connection
        conn = await pool.acquire()
        assert conn._conn is not None
        
        # 2. Close the connection so conn._conn is None
        await conn.close()
        assert conn._conn is None
        
        # 3. Simulate calling release inside a cancelled task.
        async def cancel_during_release():
            # Set pending cancellation on the current task
            current_task = asyncio.current_task()
            current_task.cancel()
            
            # Now call release. Since a cancellation is pending, the first await in release() (which is self._lock)
            # will immediately raise CancelledError.
            try:
                await pool.release(conn)
            except asyncio.CancelledError:
                pass
        
        task = asyncio.create_task(cancel_during_release())
        await task
        
        # 4. Now, verify if the permit was leaked.
        try:
            conn2 = await asyncio.wait_for(pool.acquire(), timeout=1.0)
            print("[PASS] Successfully acquired connection after cancelled release. No permit leak!")
            await pool.release(conn2)
            leaked = False
        except asyncio.TimeoutError:
            print("[FAIL] Could not acquire connection! Permit was leaked!")
            leaked = True
            
        assert not leaked, "Permit was leaked when release() was cancelled!"
        
    finally:
        await database.close_db()
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    asyncio.run(test_cancelled_release_permit_leak())
