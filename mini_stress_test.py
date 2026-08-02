import asyncio
import os
import shutil
import tempfile
import sys

# Setup path so it runs in project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import database

async def prove_deadlock():
    print("Starting Connection Pool Deadlock Proof...")
    temp_dir = tempfile.mkdtemp()
    test_db = os.path.join(temp_dir, "deadlock_test.db")
    database.DB_PATH = test_db
    
    try:
        await database.init_db()
        pool = database._pool
        print(f"Connection pool initialized. Max connections: {pool.max_connections}")
        
        # 1. Acquire all connections to fill the pool
        acquired_conns = []
        for i in range(pool.max_connections):
            conn = await pool.acquire()
            acquired_conns.append(conn)
        print(f"Acquired {len(acquired_conns)} connections. Pool count: {len(pool._connections)}")
        
        # 2. Start a background task that waits for a connection
        async def waiter():
            print("Waiter task attempting to acquire a connection (should block)...")
            conn = await pool.acquire()
            print("Waiter task successfully acquired connection!")
            await pool.release(conn)
            return True
            
        waiter_task = asyncio.create_task(waiter())
        
        # Give the waiter task a moment to block on the queue
        await asyncio.sleep(0.1)
        
        # 3. Simulate all holding tasks closing their connections and releasing them.
        # This simulates a scenario where connections are closed due to errors, timeouts,
        # or cleanup, and then released back to the pool.
        print("Closing and releasing all acquired connections...")
        for conn in acquired_conns:
            await conn.close()
            await pool.release(conn)
            
        print(f"All connections closed & released. Pool active connections count: {len(pool._connections)}")
        print(f"Queue size: {pool._queue.qsize()}")
        
        # 4. Check if the waiter task can get a connection.
        # Since the pool has 0 active connections (well below max_connections),
        # the waiter task should easily be able to get a connection.
        # However, due to the deadlock bug, the waiter task is stuck waiting on the queue.
        try:
            await asyncio.wait_for(waiter_task, timeout=2.0)
            print("[SUCCESS] Waiter task completed without deadlock.")
        except asyncio.TimeoutError:
            print("[FAIL] Deadlock detected! Waiter task is stuck waiting on the queue forever, even though the pool is empty.")
            raise AssertionError("Connection pool deadlock/starvation bug verified!")
            
    finally:
        await database.close_db()
        shutil.rmtree(temp_dir)
        print("Cleanup completed.")

if __name__ == "__main__":
    asyncio.run(prove_deadlock())
