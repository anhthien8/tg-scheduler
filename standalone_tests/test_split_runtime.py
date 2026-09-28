"""Offline boundary and lifecycle tests for Phase 1 split runtime foundation.

Run with:  .venv/Scripts/python.exe -m unittest test_split_runtime -v
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


_FORBIDDEN_MODULES = (
    'telegram_client', 'keyword_watcher', 'scheduler',
    'message_queue', 'reaction_watcher', 'dm_reply_tracker',
    'kol_channel_watcher', 'command_bot',
)


def _purge_main_modules():
    """Remove main, engine_lifecycle, and all engine imports so reimport starts fresh."""
    for name in ('main', 'database', 'engine_lifecycle', 'telegram_worker') + _FORBIDDEN_MODULES:
        sys.modules.pop(name, None)
    for name in list(sys.modules):
        if name.startswith('routes.'):
            sys.modules.pop(name, None)


class SplitRuntimeTests(unittest.TestCase):

    def test_combined_mode_backward_compatible(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
        }, clear=False), patch('dotenv.load_dotenv'):
            os.environ.pop('TG_RUNTIME_MODE', None)
            _purge_main_modules()
            app_module = importlib.import_module('main')
            self.assertEqual(app_module.TG_RUNTIME_MODE, 'combined')
            self.assertIsNotNone(app_module.tg)
            self.assertIsNotNone(app_module.kw)

    def test_web_mode_blocks_engine_imports(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
            'TG_RUNTIME_MODE': 'web',
        }, clear=False), patch('dotenv.load_dotenv'):
            _purge_main_modules()
            app_module = importlib.import_module('main')
            leaked = [name for name in _FORBIDDEN_MODULES if name in sys.modules]
            self.assertFalse(leaked, f"engine modules leaked at import: {leaked}")

    def test_web_mode_lifespan_no_engines_and_no_lock(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
            'TG_RUNTIME_MODE': 'web',
        }, clear=False), patch('dotenv.load_dotenv'):
            _purge_main_modules()
            app_module = importlib.import_module('main')

            async def check():
                async with app_module.lifespan(app_module.app):
                        leaked = [name for name in _FORBIDDEN_MODULES if name in sys.modules]
                        self.assertFalse(leaked, f"engine modules leaked after lifespan: {leaked}")
                        # In web mode, no OS lock should be created or held
                        lock_file = os.path.join(data, "engine.lock")
                        self.assertFalse(os.path.exists(lock_file))
            asyncio.run(check())

    def test_web_mode_watcher_route_503(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
            'TG_RUNTIME_MODE': 'web',
        }, clear=False), patch('dotenv.load_dotenv'):
            _purge_main_modules()
            app_module = importlib.import_module('main')

            async def check():
                import httpx
                async with app_module.lifespan(app_module.app):
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app_module.app),
                            base_url='http://offline'
                        ) as client:
                            resp = await client.post(
                                '/api/watchers',
                                headers={'X-API-Key': 'offline-test'},
                                json={},
                            )
                self.assertEqual(resp.status_code, 503)
                self.assertIn('combined', resp.text)
            asyncio.run(check())

    def test_web_mode_health_endpoint_auth_and_stale(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
            'TG_RUNTIME_MODE': 'web',
        }, clear=False), patch('dotenv.load_dotenv'):
            _purge_main_modules()
            app_module = importlib.import_module('main')

            async def check():
                import httpx
                stale_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
                stale_payload = json.dumps({"state": "ready", "pid": 1234, "updated_at": stale_time})

                with patch.object(app_module.db, 'get_setting', AsyncMock(return_value=stale_payload)):
                    async with app_module.lifespan(app_module.app):
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app_module.app),
                            base_url='http://offline'
                        ) as client:
                            # 1. Unauthenticated request should get 403
                            unauth = await client.get('/api/health/telegram-worker')
                            self.assertEqual(unauth.status_code, 403)

                            # 2. Authenticated request should get 200 + stale classification
                            resp = await client.get('/api/health/telegram-worker', headers={'X-API-Key': 'offline-test'})
                            self.assertEqual(resp.status_code, 200)
                            body = resp.json()
                            self.assertEqual(body['runtime_mode'], 'web')
                            self.assertEqual(body['worker']['state'], 'ready')
                            self.assertTrue(body['worker']['stale'])
                            self.assertGreaterEqual(body['worker']['age_seconds'], 100)
            asyncio.run(check())

    def test_web_mode_read_only_boundary(self):
        """GET logs allowed; POST settings blocked; GET watchers blocked."""
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
            'TG_RUNTIME_MODE': 'web',
        }, clear=False), patch('dotenv.load_dotenv'):
            _purge_main_modules()
            app_module = importlib.import_module('main')

            async def check():
                import httpx
                async with app_module.lifespan(app_module.app):
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app_module.app),
                            base_url='http://offline'
                        ) as client:
                            headers = {'X-API-Key': 'offline-test'}
                            blocked_post = await client.post('/api/settings/foo', headers=headers, json={'value': '1'})
                            blocked_get = await client.get('/api/watchers', headers=headers)
                self.assertEqual(blocked_post.status_code, 503)
                self.assertEqual(blocked_get.status_code, 503)
            asyncio.run(check())

    def test_singleton_lock_blocks_second_owner(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import engine_lifecycle as lc

            lc.acquire_data_lock()
            try:
                # Second acquire attempt in another process context
                lock_path = os.path.join(data, "engine.lock")
                f2 = open(lock_path, "a+")
                try:
                    f2.seek(0)
                    if sys.platform == "win32":
                        import msvcrt
                        with self.assertRaises(OSError):
                            msvcrt.locking(f2.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        with self.assertRaises(OSError):
                            fcntl.flock(f2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    f2.close()
            finally:
                lc.release_data_lock()

            # After release, re-acquire succeeds
            lc.acquire_data_lock()
            lc.release_data_lock()

    def test_worker_heartbeat_only_ready_after_startup(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import telegram_worker as worker

            statuses_written = []

            async def mock_set_setting(key, val):
                if key == "telegram_worker_status":
                    statuses_written.append(json.loads(val))

            async def dummy_startup():
                # Startup finishes cleanly
                await asyncio.sleep(0.01)

            async def check():
                with patch.object(worker.db, 'set_setting', side_effect=mock_set_setting), \
                     patch.object(worker.lifecycle, 'start_engines', AsyncMock(return_value=asyncio.create_task(dummy_startup()))), \
                     patch.object(worker.lifecycle, 'stop_engines', AsyncMock()):
                    await worker.start_runtime()
                    # States should be 'starting' then 'ready'
                    states = [s['state'] for s in statuses_written]
                    self.assertEqual(states[0], 'starting')
                    self.assertEqual(states[1], 'ready')
                    await worker.stop_runtime()
                    states_after = [s['state'] for s in statuses_written]
                    self.assertEqual(states_after[-1], 'stopped')
            asyncio.run(check())

    def test_worker_retains_error_state_on_failure(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import telegram_worker as worker

            statuses_written = []

            async def mock_set_setting(key, val):
                if key == "telegram_worker_status":
                    statuses_written.append(json.loads(val))

            async def check():
                with patch.object(worker.db, 'set_setting', side_effect=mock_set_setting), \
                     patch.object(worker.lifecycle, 'start_engines', AsyncMock(side_effect=RuntimeError("Engine failure"))), \
                     patch.object(worker.lifecycle, 'stop_engines', AsyncMock()):
                    with self.assertRaises(RuntimeError):
                        await worker.run()

                    # Final recorded status should be 'error', not overwritten by 'stopped'
                    last_status = statuses_written[-1]
                    self.assertEqual(last_status['state'], 'error')
                    self.assertIn("Engine failure", last_status['error'])
            asyncio.run(check())

    def test_stop_engines_awaits_cancellation(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import engine_lifecycle as lc

            task_cancelled = False

            async def slow_background():
                nonlocal task_cancelled
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    task_cancelled = True
                    raise

            async def check():
                bg_task = asyncio.create_task(slow_background())
                await asyncio.sleep(0)  # let task enter sleep(100)
                mock_sch = MagicMock()
                mock_mq = MagicMock()
                with patch.dict(sys.modules, {
                    'scheduler': mock_sch,
                    'message_queue': mock_mq,
                    'reaction_watcher': MagicMock(stop_all=AsyncMock()),
                    'dm_reply_tracker': MagicMock(stop_reply_tracker=AsyncMock()),
                    'telegram_client': MagicMock(disconnect_all=AsyncMock()),
                }):
                    await lc.stop_engines(bg_task)
                    self.assertTrue(task_cancelled)
                    self.assertTrue(bg_task.done())
            asyncio.run(check())

    def test_worker_lock_failure_does_not_cleanup_or_write_status(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import telegram_worker as worker

            async def check():
                with patch.object(worker.lifecycle, 'acquire_data_lock', side_effect=RuntimeError('locked')), \
                     patch.object(worker.db, 'init_db', AsyncMock()) as init_db, \
                     patch.object(worker.db, 'set_setting', AsyncMock()) as set_setting, \
                     patch.object(worker.db, 'close_db', AsyncMock()) as close_db, \
                     patch.object(worker.lifecycle, 'stop_engines', AsyncMock()) as stop_engines, \
                     patch.object(worker.lifecycle, 'release_data_lock') as release_lock:
                    with self.assertRaises(RuntimeError):
                        await worker.run()
                    init_db.assert_not_awaited()
                    set_setting.assert_not_awaited()
                    stop_engines.assert_not_awaited()
                    close_db.assert_not_awaited()
                    release_lock.assert_not_called()
            asyncio.run(check())

    def test_worker_observes_heartbeat_task_failure_while_waiting(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import telegram_worker as worker

            statuses_written = []

            async def mock_set_setting(key, val):
                if key == 'telegram_worker_status':
                    statuses_written.append(json.loads(val))

            async def failing_heartbeat():
                raise RuntimeError('heartbeat died')

            async def _noop_consume_loop():
                # This test exercises heartbeat-failure behavior only; the real
                # consumer opens a real aiosqlite connection via db.get_db(), which
                # close_db (mocked below) would then never actually close, leaking
                # a handle onto the TemporaryDirectory. Keep the consumer alive
                # (so cancellation-on-shutdown is still exercised) without touching DB.
                await asyncio.sleep(100)

            async def check():
                with patch.object(worker.db, 'set_setting', side_effect=mock_set_setting), \
                     patch.object(worker, '_heartbeat', failing_heartbeat), \
                     patch.object(worker, '_consume_loop', _noop_consume_loop), \
                     patch.object(worker.lifecycle, 'start_engines', AsyncMock(return_value=asyncio.create_task(asyncio.sleep(0)))), \
                     patch.object(worker.lifecycle, 'stop_engines', AsyncMock()):
                    with self.assertRaises(RuntimeError):
                        await asyncio.wait_for(worker.run(), timeout=0.5)
                self.assertEqual(statuses_written[-1]['state'], 'error')
                self.assertIn('heartbeat died', statuses_written[-1]['error'])
            asyncio.run(check())

    def test_worker_health_invalid_json_shapes_are_classified(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import engine_lifecycle as lc

            async def check():
                for raw in ('42', '[1, 2]', json.dumps({'state': 'ready', 'updated_at': 'not-a-date'})):
                    with patch.object(lc.db, 'get_setting', AsyncMock(return_value=raw)):
                        health = await lc.read_worker_health()
                    self.assertIn(health['state'], ('invalid', 'ready'))
                    if health['state'] == 'ready':
                        self.assertEqual(health['timestamp_status'], 'invalid')
            asyncio.run(check())

    def test_worker_close_db_failure_still_releases_lock(self):
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import telegram_worker as worker

            async def check():
                worker._owns_runtime = True
                with patch.object(worker.lifecycle, 'stop_engines', AsyncMock()), \
                     patch.object(worker.lifecycle, 'write_status', AsyncMock()), \
                     patch.object(worker.db, 'close_db', AsyncMock(side_effect=RuntimeError('close failed'))), \
                     patch.object(worker.lifecycle, 'release_data_lock') as release_lock:
                    with self.assertRaises(RuntimeError):
                        await worker.stop_runtime()
                    release_lock.assert_called_once()
                    self.assertFalse(worker._owns_runtime)
            asyncio.run(check())

    def test_combined_startup_exception_releases_lock_and_closes_db(self):
        """init_db failure in combined mode must still release the singleton lock."""
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data, 'DASHBOARD_SECRET_KEY': 'offline-test',
            'TG_RUNTIME_MODE': 'combined',
        }, clear=False), patch('dotenv.load_dotenv'):
            _purge_main_modules()
            app_module = importlib.import_module('main')
            import engine_lifecycle as lc

            async def check():
                with patch.object(app_module.db, 'init_db', AsyncMock(side_effect=RuntimeError('db boom'))), \
                     patch.object(app_module.db, 'close_db', AsyncMock()) as close_db, \
                     patch.object(lc, 'start_engines', AsyncMock()), \
                     patch.object(lc, 'stop_engines', AsyncMock()):
                    with self.assertRaises(RuntimeError):
                        async with app_module.lifespan(app_module.app):
                            pass
                    close_db.assert_awaited()
                # Lock must be free: a fresh acquire succeeds
                lc.acquire_data_lock()
                lc.release_data_lock()
            asyncio.run(check())

    def test_lock_blocks_real_second_process(self):
        """Cross-process proof: a child process cannot acquire the held lock."""
        import subprocess
        with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {
            'DATA_DIR': data,
        }, clear=False):
            _purge_main_modules()
            import engine_lifecycle as lc

            lc.acquire_data_lock()
            try:
                child = subprocess.run(
                    [sys.executable, '-c',
                     'import engine_lifecycle as lc\n'
                     'try:\n'
                     '    lc.acquire_data_lock()\n'
                     '    print("ACQUIRED")\n'
                     'except RuntimeError:\n'
                     '    print("BLOCKED")\n'],
                    cwd=os.path.dirname(os.path.abspath(__file__)),
                    env={**os.environ, 'DATA_DIR': data},
                    capture_output=True, text=True, timeout=60,
                )
                self.assertIn('BLOCKED', child.stdout, child.stdout + child.stderr)
            finally:
                lc.release_data_lock()



if __name__ == '__main__':
    unittest.main()
