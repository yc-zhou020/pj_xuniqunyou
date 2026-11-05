"""Exercise stage wiring with a temporary database, no network or browser."""
import argparse
import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from group_friend import browser_runtime
from group_friend.browser_state import BrowserState
from group_friend.config import Config
from group_friend.conversation_snapshot import ChatMessage


FRIEND = dict(name='Contact', douyin_id='account', sec_uid='participant')


class StageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_runtime_cancel_and_restart_with_prepended_history(self):
        from test_browser_runtime import FakePage, FRIEND as target, message
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            settings = dict(state_path=str(folder/'queue.db'), whitelist=[target],
                            sample_interval_seconds=5, min_send_interval_seconds=5)
            config = Config('contact', 'bot', memory_enabled=False, dry_run=False,
                            persona_path=str(folder/'persona.txt'), pause_path=str(folder/'PAUSE'))
            Path(config.persona_path).write_text('persona')
            args = argparse.Namespace(stage='all', inspect=False, mock=False,
                                      once=False, reply_latest=False, duration=0)
            adapter = FakePage([message('history', mid='1')])
            session = Mock(start=AsyncMock(), close=AsyncMock())
            model = Mock()
            model.reply.return_value = '[AI] main runtime answer'
            ready = asyncio.Event()
            events = []
            def log(text):
                event = json.loads(text)
                events.append(event)
                if event.get('status') == 'ready':
                    ready.set()
            logger = Mock()
            logger.info.side_effect = log
            with patch.object(browser_runtime, 'BrowserSession', return_value=session), \
                 patch.object(browser_runtime, 'PageAdapter', return_value=adapter), \
                 patch.object(browser_runtime, 'DeepSeek', return_value=model):
                task = asyncio.create_task(browser_runtime.run(settings, config, args, logger))
                try:
                    await asyncio.wait_for(ready.wait(), 2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertTrue(any(e.get('status') == 'stopped' and e.get('reason') == 'interrupted' for e in events))
                session.close.assert_awaited_once()
                model.reply.assert_not_called()
                adapter.messages.insert(0, message('older loaded history', mid='0'))
                adapter.messages.append(message('new after restart', mid='2'))
                args.once = True
                self.assertEqual(await browser_runtime.run(settings, config, args, logger), 0)
                self.assertEqual(adapter.sent, ['main runtime answer'])
                model.reply.assert_called_once()
                self.assertEqual(await browser_runtime.run(settings, config, args, logger), 0)
                self.assertEqual(len(adapter.sent), 1)
                model.reply.assert_called_once()
                self.assertTrue(any(e.get('status') == 'ready' and e.get('resumed') for e in events))

    async def test_independent_stage_dependencies(self):
        for stage in ('receive', 'generate', 'send'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                settings = dict(state_path=str(folder / 'queue.db'), whitelist=[FRIEND],
                                sample_interval_seconds=5, min_send_interval_seconds=5)
                config = Config('contact', 'bot', memory_enabled=False,
                                persona_path=str(folder / 'persona.txt'),
                                pause_path=str(folder / 'PAUSE'))
                if stage == 'generate':
                    Path(config.persona_path).write_text('persona')
                source = ChatMessage('question', 'incoming', message_id='1')
                with_state = BrowserState(settings['state_path'])
                if stage != 'receive':
                    with_state.ingest(FRIEND, [source], reply_latest=True)
                if stage == 'send':
                    with_state.mark(with_state.next_job()['id'], 'generated', reply='[AI] saved')
                # Seeded tasks must remain eligible even if the host wall clock
                # is adjusted while the asynchronous test is running.
                with_state.db.execute('UPDATE browser_jobs SET due=0')
                with_state.db.commit()
                with_state.close()
                args = argparse.Namespace(stage=stage, inspect=False, mock=False,
                                          once=True, reply_latest=True, duration=0)
                session = Mock(start=AsyncMock(), close=AsyncMock())
                adapter = Mock(select=AsyncMock(), read=AsyncMock(return_value=[source]))
                model = Mock()
                model.reply.return_value = '[AI] generated'
                with patch.object(browser_runtime, 'BrowserSession', return_value=session) as browser, \
                     patch.object(browser_runtime, 'PageAdapter', return_value=adapter), \
                     patch.object(browser_runtime, 'DeepSeek', return_value=model) as deepseek:
                    result = await browser_runtime.run(settings, config, args, Mock())
                    self.assertEqual(result, 0)
                    if stage == 'generate':
                        browser.assert_not_called()
                        model.reply.assert_called_once()
                        adapter.read.assert_not_called()
                    else:
                        deepseek.assert_not_called()
                        session.start.assert_awaited_once()
                        session.close.assert_awaited_once()
                restored = BrowserState(settings['state_path'])
                try:
                    statuses = [row[0] for row in restored.db.execute('SELECT status FROM browser_jobs')]
                    self.assertEqual(statuses, ['pending'] if stage == 'receive' else ['dry_run'])
                finally:
                    restored.close()


class MainCommandTests(unittest.TestCase):
    def test_live_flag_overrides_dry_run_in_memory_only(self):
        config = Config('contact', 'bot', memory_enabled=False, dry_run=True)
        settings = dict(whitelist=[FRIEND], reply_config='unused', log_path='unused')
        for flags, dry_run in (([], True), (['--live'], False), (['--dry-run'], True), (['--mock'], True)):
            with self.subTest(flags=flags), \
                 patch.object(browser_runtime, 'load_settings', return_value=settings), \
                 patch.object(browser_runtime.Config, 'load', return_value=config), \
                 patch.object(browser_runtime, 'load_env'), \
                 patch.object(browser_runtime, 'api_settings', return_value=('unused', 'https://api.deepseek.com', 'test')), \
                 patch.object(browser_runtime, 'make_logger', return_value=Mock()), \
                 patch.object(browser_runtime, 'InstanceLock', side_effect=lambda path: contextlib.nullcontext()), \
                 patch.object(browser_runtime, 'run', new_callable=AsyncMock, return_value=0) as runtime:
                self.assertEqual(browser_runtime.main(flags), 0)
                self.assertEqual(runtime.call_args.args[1].dry_run, dry_run)
                self.assertTrue(config.dry_run)

    def test_live_cannot_be_combined_with_mock_or_dry_run(self):
        for flag in ('--mock', '--dry-run'):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    browser_runtime.main(['--live', flag])
                self.assertEqual(error.exception.code, 2)
