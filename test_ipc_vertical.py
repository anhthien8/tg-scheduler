"""Offline ASGI/SQLite IPC checks; no dotenv, sessions or engine startup."""
import asyncio
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch
import httpx


class IPCIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DATA_DIR': self.temp.name,
            'TG_RUNTIME_MODE': 'web', 'DASHBOARD_SECRET_KEY': 'offline', 'DISABLE_AUTH': '0'})
        self.env.start()
        with patch('dotenv.load_dotenv'):
            import database
            self.db = database
            await database.close_db()
            self.paths = patch.multiple(database, DB_DIR=self.temp.name,
                DB_PATH=os.path.join(self.temp.name, 'scheduler.db'))
            self.paths.start()
            import main
            self.main = importlib.reload(main)
        await self.db.init_db()
        async with self.db.get_db() as conn:
            await conn.execute("INSERT INTO accounts(id,name,phone,api_id,api_hash,session_name) VALUES(1,'Offline','1','secret','secret','secret')")
            await conn.commit()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.main.app),
            base_url='http://offline', headers={'X-API-Key': 'offline'})

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close_db()
        self.paths.stop()
        self.env.stop()
        self.temp.cleanup()

    async def test_reads_and_auth(self):
        import sys
        self.assertNotIn('telegram_client', sys.modules)
        for name in ('accounts', 'campaigns', 'scrape-jobs', 'watchers'):
            r = await self.client.get('/api/ipc/' + name)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn('secret', r.text)
        r = await self.client.get('/api/ipc/accounts', headers={'X-API-Key': 'bad'})
        self.assertEqual(r.status_code, 403)

    async def test_http_worker_roundtrip_dedup(self):
        from telegram_worker import consume_one
        headers = {'X-Idempotency-Key': 'one'}
        r = await self.client.post('/api/ipc/commands/chats.refresh', json={'account_id': 1}, headers=headers)
        self.assertEqual(r.status_code, 202, r.text)
        command_id = r.json()['command_id']
        calls = []
        async def mock_dispatch(command, payload):
            calls.append((command, payload))
            return {'chats': [], 'account_id': payload['account_id']}
        await consume_one('offline-worker', dispatch=mock_dispatch)
        r = await self.client.get('/api/ipc/commands/' + command_id)
        self.assertEqual(r.json()['status'], 'succeeded')
        self.assertNotIn('owner_token', r.json())
        repeated = await self.client.post('/api/ipc/commands/chats.refresh', json={'account_id': 1}, headers=headers)
        self.assertEqual(repeated.json()['command_id'], command_id)
        self.assertFalse(await consume_one('offline-worker', dispatch=mock_dispatch))
        self.assertEqual(len(calls), 1)

    async def test_invalid_ids_and_out_of_scope(self):
        for payload in ({'account_id': -1}, {'account_id': True}, {'account_id': 1, 'unexpected': 2}):
            r = await self.client.post('/api/ipc/commands/chats.refresh', json=payload)
            self.assertEqual(r.status_code, 422)
        r = await self.client.post('/api/ipc/commands/campaign.start', json={'campaign_id': 999})
        self.assertEqual(r.status_code, 404)
        r = await self.client.post('/api/chats/leave-channel', json={})
        self.assertEqual(r.status_code, 503)

    async def test_dead_worker_unknown_not_replayed(self):
        import runtime_commands as rc
        r = await self.client.post('/api/ipc/commands/chats.refresh', json={'account_id': 1})
        cid = r.json()['command_id']
        async with self.db.get_db() as conn:
            item = await rc.claim(conn, 'dead')
            await conn.execute('UPDATE runtime_commands SET lease_until=0 WHERE id=?', (cid,))
            await conn.commit()
        r = await self.client.get('/api/ipc/commands/' + cid)
        self.assertEqual(r.json()['status'], 'unknown')
        async with self.db.get_db() as conn:
            self.assertIsNone(await rc.claim(conn, 'new'))
            with self.assertRaises(rc.OwnershipError):
                await rc.complete(conn, cid, item['owner_token'], {})

    async def test_failing_handler_reports_failed_not_success(self):
        from telegram_worker import consume_one
        r = await self.client.post('/api/ipc/commands/watchers.reload', json={'watcher_id': 1})
        self.assertEqual(r.status_code, 404)  # no watcher seeded yet
        async with self.db.get_db() as conn:
            await conn.execute("INSERT INTO keyword_watchers(id,name,platform) VALUES(7,'w','telegram')")
            await conn.commit()
        r = await self.client.post('/api/ipc/commands/watchers.reload', json={'watcher_id': 7})
        cid = r.json()['command_id']

        async def boom(command, payload):
            raise RuntimeError('reload rejected by engine')
        await consume_one('offline-worker', dispatch=boom)
        body = (await self.client.get('/api/ipc/commands/' + cid)).json()
        self.assertEqual(body['status'], 'failed')
        self.assertIn('reload rejected by engine', body['error_text'])

    def test_dispatcher_table_is_explicit(self):
        import ipc_dispatcher
        self.assertEqual(set(ipc_dispatcher.HANDLERS),
                         {'chats.refresh', 'campaign.start', 'campaign.stop', 'watchers.reload'})
        with self.assertRaises(ValueError):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(ipc_dispatcher.dispatch('invite.run', {}))
            finally:
                loop.close()

if __name__ == '__main__':
    unittest.main()
