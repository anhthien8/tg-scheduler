"""Kiểm tra cancellation khi connection thật đã mở nhưng chưa trả về pool."""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import database


class CancelledAcquireLeakTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_connect_closes_connection_and_returns_permit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'cancel.db')
            pool = database.ConnectionPool(path, max_connections=1)
            opened = asyncio.Event()
            finish = asyncio.Event()
            connections = []
            real_connect = database.aiosqlite.connect

            async def delayed_connect(*args, **kwargs):
                conn = await real_connect(*args, **kwargs)
                connections.append(conn)
                opened.set()
                await finish.wait()
                return conn

            try:
                with patch.object(database.aiosqlite, 'connect', delayed_connect):
                    task = asyncio.create_task(pool.acquire())
                    await asyncio.wait_for(opened.wait(), 5)
                    task.cancel()
                    finish.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 5)
                self.assertIsNone(connections[0]._connection)
                conn = await asyncio.wait_for(pool.acquire(), 5)
                await pool.release(conn)
                await pool.close_all()
                os.remove(path)
            finally:
                await pool.close_all()
                # Dọn cả khi assertion thất bại, không bỏ qua lỗi cleanup.
                for conn in connections:
                    await conn.close()


if __name__ == '__main__':
    unittest.main()
