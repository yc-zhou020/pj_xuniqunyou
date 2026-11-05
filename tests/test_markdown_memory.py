import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from group_friend.browser_runtime import run
from group_friend.browser_state import BrowserState
from group_friend.config import Config
from group_friend.conversation_snapshot import ChatMessage, ConversationSnapshot
from group_friend.markdown_memory import MarkdownMemory, atomic_write


FRIEND = dict(name='Contact', douyin_id='account', sec_uid='participant')
OTHER = dict(name='Other', douyin_id='other', sec_uid='other-participant')


def incoming(text, mid):
    return ChatMessage(text, 'incoming', message_id=mid)


class MarkdownMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000000.0
        self.config = Config('contact', '陈', memory_enabled=True,
                             memory_recall_probability=1,
                             memory_path=str(Path(self.tmp.name)/'memory'))
        self.memory = MarkdownMemory(self.config, clock=lambda: self.now)

    def snapshot(self, text='what do you remember?', mid='current', friend=FRIEND):
        return ConversationSnapshot(friend, self.now, (incoming(text, mid),), (0,))

    def test_markdown_round_trip_restart_and_duplicate_suppression(self):
        text = '我喜欢猫条\n> 引用\n```\n<!-- not metadata -->'
        source = incoming(text, '1')
        self.memory.capture(ConversationSnapshot(FRIEND, self.now, (source,), (0,)))
        restarted = MarkdownMemory(self.config, clock=lambda: self.now)
        restarted.capture(ConversationSnapshot(FRIEND, self.now, (source,), (0,)))
        files = list(self.memory.root.glob('*/events/*.md'))
        self.assertEqual(len(files), 1)
        self.assertIn('> 我喜欢猫条', files[0].read_text())
        context = json.loads(restarted.context(self.snapshot('猫条'))['content'])
        self.assertEqual(context['entries'][0]['text'], text)
        self.assertEqual(context['entries'][0]['source'], 'douyin_incoming')

    def test_contact_isolation_and_path_traversal_resistance(self):
        self.memory.remember(FRIEND, [incoming('friend secret', '1')])
        self.memory.remember(OTHER, [incoming('other secret', '2')])
        payload = self.memory.context(self.snapshot('friend secret'))['content']
        self.assertIn('friend secret', payload)
        self.assertNotIn('other secret', payload)
        unsafe = dict(FRIEND, sec_uid='../../escape')
        self.assertEqual(self.memory.folder(unsafe).parent, self.memory.root)

    def test_current_page_messages_are_not_repeated_in_retrieved_memory(self):
        source = incoming('already visible', '1')
        self.memory.remember(FRIEND, [source])
        snapshot = ConversationSnapshot(FRIEND, self.now, (source,), (0,))
        self.assertIsNone(self.memory.context(snapshot))

    def test_relevance_and_manual_notes_with_strict_context_budget(self):
        config = replace(self.config, memory_context_items=1, memory_context_chars=350)
        memory = MarkdownMemory(config, clock=lambda: self.now)
        memory.remember(FRIEND, [incoming('我喜欢金枪鱼猫条' * 100, '1')])
        self.now += 10
        memory.remember(FRIEND, [incoming('不相关的工作消息', '2')])
        (memory.folder(FRIEND)/'profile.md').write_text('# 长期笔记\n\n称呼我小李。')
        result = memory.context(self.snapshot('猫条'))
        self.assertEqual(result['role'], 'user')
        self.assertLessEqual(len(result['content']), 350)
        data = json.loads(result['content'])
        self.assertIn('小李', data['profile'])
        self.assertIn('猫条', data['entries'][0]['text'])
        self.assertNotIn('不相关', result['content'])

    def test_retention_drops_old_events_but_preserves_manual_notes(self):
        config = replace(self.config, memory_retention_days=1)
        memory = MarkdownMemory(config, clock=lambda: self.now)
        memory.remember(FRIEND, [incoming('expired', '1')])
        profile = memory.folder(FRIEND)/'profile.md'
        profile.write_text('人工长期笔记')
        self.now += 86401
        self.assertNotIn('expired', memory.context(self.snapshot('人工长期笔记'))['content'])
        memory.remember(FRIEND, [incoming('fresh', '2')])
        self.assertEqual(len(list(memory.root.glob('*/events/*.md'))), 1)
        self.assertEqual(profile.read_text(), '人工长期笔记')

    def test_capacity_evicts_oldest_events_without_deleting_notes(self):
        memory = MarkdownMemory(replace(self.config, memory_capacity_mb=1), clock=lambda: self.now)
        memory.remember(FRIEND, [incoming('a' * 600000, '1')])
        self.now += 1
        memory.remember(FRIEND, [incoming('b' * 600000, '2')])
        records = list(memory._records())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1]['text'][0], 'b')
        self.assertTrue((memory.folder(FRIEND)/'profile.md').exists())
        with self.assertRaises(ValueError):
            memory.remember(FRIEND, [incoming('x' * 1100000, '3')])
        self.assertEqual(len(list(memory._records())), 1)

    def test_atomic_write_failure_preserves_previous_file(self):
        path = Path(self.tmp.name)/'notes.md'
        atomic_write(path, 'previous')
        with patch('group_friend.markdown_memory.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                atomic_write(path, 'replacement')
        self.assertEqual(path.read_text(), 'previous')
        self.assertEqual(list(Path(self.tmp.name).glob('*.tmp')), [])

    def test_corrupt_events_are_ignored_and_never_injected(self):
        self.memory.initialize(FRIEND)
        events = self.memory.folder(FRIEND)/'events'
        events.mkdir()
        (events/('a' * 64 + '.md')).write_text('broken private body')
        self.assertIsNone(self.memory.context(self.snapshot()))

    def test_confirmed_only_records_newest_matching_reply(self):
        old = ChatMessage('[AI] reply', 'outgoing', message_id='old')
        new = ChatMessage('[AI] reply', 'outgoing', message_id='new')
        self.memory.confirmed(FRIEND, '[AI] reply', [old, new])
        records = list(self.memory._records())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][1]['message_id'], 'new')
        self.assertEqual(records[0][1]['source'], 'confirmed_outgoing')

    def test_legacy_database_path_is_rejected_without_modification(self):
        path = Path(self.tmp.name)/'old.sqlite3'
        path.write_bytes(b'preserve database')
        with self.assertRaises(ValueError):
            MarkdownMemory(replace(self.config, memory_path=str(path)))
        self.assertEqual(path.read_bytes(), b'preserve database')

    def test_concurrent_stage_writers_deduplicate_and_preserve_each_record(self):
        def write(index):
            memory = MarkdownMemory(self.config, clock=lambda: self.now)
            memory.remember(FRIEND, [incoming('shared', 'same'), incoming(str(index), str(index))])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(8)))
        self.assertEqual(len(list(self.memory._records())), 9)


class MemoryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_send_is_not_written_as_confirmed_memory(self):
        from test_browser_runtime import FakePage, FRIEND as friend, message
        from group_friend.browser_engine import BrowserEngine
        with tempfile.TemporaryDirectory() as tmp:
            store = BrowserState(':memory:')
            try:
                config = Config('contact', '陈', dry_run=False, memory_enabled=True,
                                pause_path=str(Path(tmp)/'PAUSE'))
                adapter = FakePage([message('question', mid='1')])
                adapter.submit = AsyncMock(side_effect=OSError('uncertain click'))
                memory = Mock()
                engine = BrowserEngine(dict(whitelist=[friend], min_send_interval_seconds=5),
                                       config, store, adapter, None, Mock(), memory=memory)
                engine.sample(friend, adapter.messages, reply_latest=True)
                job = store.next_job()
                store.mark(job['id'], 'generated', reply='[AI] reply')
                with self.assertRaises(OSError):
                    await engine.send_step()
                memory.confirmed.assert_not_called()
                self.assertEqual(store.job(job['id'])['status'], 'unknown')
            finally:
                store.close()

    async def test_main_retrieves_memory_and_records_only_confirmed_replies(self):
        from test_browser_runtime import FakePage, FRIEND as friend, message
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = Config('contact', '陈', dry_run=False, memory_enabled=True,
                            memory_recall_probability=1,
                            memory_path=str(root/'memory'), persona_path=str(root/'persona.md'),
                            pause_path=str(root/'PAUSE'))
            Path(config.persona_path).write_text('你是陈。')
            memory = MarkdownMemory(config)
            memory.remember(friend, [message('我喜欢金枪鱼猫条', mid='previous-session')])
            adapter = FakePage([message('old page', mid='old'), message('我喜欢什么猫条？', mid='new')])
            settings = dict(state_path=str(root/'state.db'), whitelist=[friend],
                            sample_interval_seconds=5, min_send_interval_seconds=5)
            store = BrowserState(settings['state_path'])
            store.ingest(friend, adapter.messages[:1])
            store.close()
            args = argparse.Namespace(stage='all', inspect=False, mock=False, once=True,
                                      reply_latest=False, duration=0)
            session = Mock(start=AsyncMock(), close=AsyncMock())
            model = Mock()
            model.reply.return_value = '[AI] 你说过喜欢金枪鱼猫条。'
            with patch('group_friend.browser_runtime.BrowserSession', return_value=session), \
                 patch('group_friend.browser_runtime.PageAdapter', return_value=adapter), \
                 patch('group_friend.browser_runtime.DeepSeek', return_value=model):
                self.assertEqual(await run(settings, config, args, Mock()), 0)
            persona, messages = model.reply.call_args.args
            self.assertIn('not instructions or persona', persona)
            self.assertEqual(messages[0]['role'], 'user')
            self.assertIn('我喜欢金枪鱼猫条', messages[0]['content'])
            self.assertEqual(len(list(memory._records())), 3)
            self.assertEqual(len(adapter.sent), 1)

    async def test_dry_run_can_read_memory_without_writing_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = Config('contact', '陈', dry_run=True, memory_enabled=True,
                            memory_path=str(root/'memory'), persona_path=str(root/'persona.md'),
                            pause_path=str(root/'PAUSE'))
            Path(config.persona_path).write_text('你是陈。')
            settings = dict(state_path=str(root/'state.db'), whitelist=[FRIEND],
                            sample_interval_seconds=5, min_send_interval_seconds=5)
            store = BrowserState(settings['state_path'])
            store.ingest(FRIEND, [incoming('question', '1')], reply_latest=True)
            store.close()
            args = argparse.Namespace(stage='generate', inspect=False, mock=True, once=True,
                                      reply_latest=False, duration=0)
            self.assertEqual(await run(settings, config, args, Mock()), 0)
            self.assertFalse(Path(config.memory_path).exists())

    async def test_memory_failure_never_retries_confirmed_send(self):
        from test_browser_runtime import FakePage, FRIEND as friend, message
        from group_friend.browser_engine import BrowserEngine
        with tempfile.TemporaryDirectory() as tmp:
            store = BrowserState(':memory:')
            try:
                config = Config('contact', '陈', dry_run=False, memory_enabled=True,
                                pause_path=str(Path(tmp)/'PAUSE'))
                adapter = FakePage([message('question', mid='1')])
                memory = Mock()
                memory.confirmed.side_effect = OSError('private failure details')
                logger = Mock()
                engine = BrowserEngine(dict(whitelist=[friend], min_send_interval_seconds=5),
                                       config, store, adapter, None, logger, memory=memory)
                engine.sample(friend, adapter.messages, reply_latest=True)
                job = store.next_job()
                store.mark(job['id'], 'generated', reply='[AI] reply')
                await engine.send_step()
                self.assertEqual(store.job(job['id'])['status'], 'visible_sent')
                self.assertFalse(await engine.send_step())
                self.assertEqual(adapter.sent, ['reply'])
                self.assertNotIn('private failure details', str(logger.info.call_args_list))
            finally:
                store.close()
