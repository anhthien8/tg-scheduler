"""Offline regression checks: python -m unittest test_reaction_watcher_safety -v."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import AsyncMock, patch


def load_watcher():
    spec = importlib.util.spec_from_file_location(
        "isolated_reaction_watcher", Path(__file__).with_name("reaction_watcher.py")
    )
    module = importlib.util.module_from_spec(spec)
    # Never import production DB/client/config or open sessions.
    with patch.dict(sys.modules, {
        "database": ModuleType("database"),
        "telegram_client": ModuleType("telegram_client"),
    }):
        spec.loader.exec_module(module)
    return module


class ReactionSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rw = load_watcher()
        self.target = dict(id=10, channel_id=123, channel_link="@source",
                           is_active=1, account_ids=[1, 2], delay_min=0, delay_max=0)
        self.rw.db.get_reaction_target = AsyncMock(return_value=self.target)
        self.rw.db.was_msg_reacted = AsyncMock(return_value=False)
        self.rw.db.add_reaction_log = AsyncMock()

    async def dispatch(self, chat_id):
        await self.rw._make_reaction_handler(self.target)(
            SimpleNamespace(chat_id=chat_id, id=77))
        if self.rw._background_tasks:
            await asyncio.gather(*self.rw._background_tasks)

    async def test_missing_channel_id_never_dispatches_other_chat_message(self):
        self.target['channel_id'] = None
        self.rw._react_all_accounts = AsyncMock()
        await self.dispatch(-100999)
        self.rw._react_all_accounts.assert_not_awaited()

    async def test_matching_channel_dispatches_but_other_channel_does_not(self):
        self.rw._react_all_accounts = AsyncMock()
        await self.dispatch(-100999)
        self.rw._react_all_accounts.assert_not_awaited()
        await self.dispatch(-100123)
        self.rw._react_all_accounts.assert_awaited_once_with(self.target, 77, '@source')

    async def test_unresolved_entity_does_not_send_reaction(self):
        self.rw.tg.get_client = lambda _: SimpleNamespace(is_connected=lambda: True)
        self.rw._get_entity_only = AsyncMock(return_value=None)
        self.rw._do_react = AsyncMock()
        await self.rw._react_all_accounts(self.target, 77, '@source')
        self.rw._do_react.assert_not_awaited()

    async def test_db_dedup_failure_releases_account_reservation(self):
        self.rw.db.was_msg_reacted.side_effect = RuntimeError('offline DB failure')
        with self.assertRaisesRegex(RuntimeError, 'offline DB failure'):
            await self.rw._do_react(self.target, AsyncMock(), 1, 77, SimpleNamespace(id=123))
        self.assertNotIn((10, 1, 77), self.rw._reacted)

    async def test_concurrent_duplicate_events_dispatch_once(self):
        # Same channel post fires the handler on every account's client at once.
        started = []

        async def slow_react(t, msg_id, link):
            started.append(msg_id)
            await asyncio.sleep(0.05)

        self.rw._react_all_accounts = slow_react
        handler = self.rw._make_reaction_handler(self.target)
        ev = SimpleNamespace(chat_id=-100123, id=77)
        await asyncio.gather(handler(ev), handler(ev), handler(ev))
        if self.rw._background_tasks:
            await asyncio.gather(*self.rw._background_tasks)
        self.assertEqual(started, [77])

    async def test_invalid_message_for_one_account_does_not_stop_others(self):
        from telethon.errors import MessageIdInvalidError
        first, second = AsyncMock(), AsyncMock()
        first.side_effect = MessageIdInvalidError(request=None)
        for client in (first, second):
            client.is_connected = lambda: True
        self.rw.tg.get_client = {1: first, 2: second}.get
        self.rw._get_entity_only = AsyncMock(return_value=SimpleNamespace(id=123))
        with patch.object(self.rw.random, 'shuffle', lambda _: None):
            await self.rw._react_all_accounts(self.target, 77, '@source')
        first.assert_awaited_once()
        second.assert_awaited_once()
        self.assertFalse(hasattr(self.rw._react_all_accounts, '_msg_dead'))


if __name__ == '__main__':
    unittest.main()
