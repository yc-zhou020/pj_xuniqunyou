import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from group_friend.reply_worker import ReplyWorker
from group_friend.browser_engine import BrowserEngine
from group_friend.browser_errors import BrowserUIError
from group_friend.browser_state import BrowserState
from group_friend.config import Config
from group_friend.runtime_support import InstanceLock, ReplyError, validate_reply
from group_friend.conversation_snapshot import ChatMessage, ConversationSnapshot, ContinuityError

FRIEND = dict(name='Contact', douyin_id='account', sec_uid='native-participant')


def message(text, direction='incoming', mid=None):
    return ChatMessage(text, direction, message_id=mid)


class FakePage:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.draft = ''
        self.recovered = 0

    async def select(self, friend):
        if friend != FRIEND:
            raise RuntimeError('wrong_identity')

    async def read(self, friend):
        await self.select(friend)
        return list(self.messages)

    async def preflight(self, friend, snapshot, current, reply):
        snapshot.locate(current)
        if self.draft:
            raise RuntimeError('existing_draft')

    async def prepare(self, friend, snapshot, reply):
        snapshot.locate(self.messages)
        before = sum(m.direction == 'outgoing' and m.text == reply for m in self.messages)
        self.draft = reply
        return before

    async def submit(self, friend, snapshot, reply, before):
        self.sent.append(reply)
        self.messages.append(message(reply, 'outgoing', 'sent-' + str(len(self.sent))))
        self.draft = ''

    async def confirm(self, friend, reply, before, current):
        return not self.draft and sum(m.direction == 'outgoing' and m.text == reply for m in current) == before + 1

    async def recover(self):
        self.recovered += 1


class SnapshotTests(unittest.TestCase):
    def test_context_contains_page_history_manual_replies_and_only_selected_current(self):
        items = (message('old', mid='1'), message('manual answer', 'outgoing', '2'),
                 message('question', mid='3'), message('future peer message', mid='4'),
                 message('[AI] earlier automated answer', 'outgoing', '5'))
        snapshot = ConversationSnapshot(FRIEND, 1000, items, (2,))
        doc = json.loads(snapshot.model_messages()[0]['content'])
        self.assertEqual([row['content'] for row in doc['messages']],
                         ['old', 'manual answer', 'question', '[AI] earlier automated answer'])
        self.assertEqual([row['content'] for row in doc['messages'] if row['current']], ['question'])
        self.assertEqual(doc['order'], 'oldest_to_newest')

    def test_context_budget_is_enforced_on_serialized_payload(self):
        items = tuple(message('中' * 10000, mid=str(i)) for i in range(12))
        data = ConversationSnapshot(FRIEND, 1000, items, (10, 11)).model_messages(1500)
        self.assertLessEqual(len(data[0]['content']), 1500)
        self.assertEqual(len([m for m in json.loads(data[0]['content'])['messages'] if m['current']]), 2)

    def test_absent_platform_identifiers_remain_null(self):
        item = ChatMessage.from_browser(dict(text='hello', from_me=False))
        self.assertIsNone(item.message_id)
        self.assertIsNone(item.timestamp)
        self.assertEqual(ConversationSnapshot.loads(ConversationSnapshot(FRIEND, 1, (item,), (0,)).dumps()).messages[0], item)

    def test_media_never_claims_to_be_seen(self):
        item = ChatMessage('secret caption', 'incoming', kind='image')
        payload = ConversationSnapshot(FRIEND, 1, (item,), (0,)).model_messages()[0]['content']
        self.assertNotIn('secret caption', payload)
        self.assertIn('actual media content was not read', payload)

    def test_sliding_window_retains_selected_native_message(self):
        items = tuple(message(str(i), mid=str(i)) for i in range(5))
        snapshot = ConversationSnapshot(FRIEND, 1, items, (4,))
        positions, _ = snapshot.locate(items[2:] + (message('new', mid='5'),))
        self.assertEqual(positions, [2])

    def test_manual_takeover_but_not_confirmed_bot_send_cancels(self):
        snapshot = ConversationSnapshot(FRIEND, 1, (message('question', mid='1'),), (0,))
        mine = message('answer', 'outgoing', '2')
        self.assertTrue(snapshot.has_manual_reply(snapshot.messages + (mine,), set()))
        self.assertFalse(snapshot.has_manual_reply(snapshot.messages + (mine,), {mine.key}))

    def test_prepended_history_preserves_selected_source_positions(self):
        old = (message('old', mid='1'), message('question', mid='2'))
        snapshot = ConversationSnapshot(FRIEND, 1, old, (1,))
        current = (message('older history', mid='0'),) + old + (message('new', mid='3'),)
        self.assertEqual(snapshot.locate(current)[0], [2])
        self.assertEqual(snapshot.refreshed(current, 2).selected, (2,))

    def test_missing_tail_anchor_still_rejects_history_change(self):
        old = (message('old', mid='1'), message('question', mid='2'))
        snapshot = ConversationSnapshot(FRIEND, 1, old, (1,))
        with self.assertRaises(ContinuityError):
            snapshot.locate((message('older', mid='0'), old[0], message('new', mid='3')))

    def test_repeated_rows_without_unique_alignment_remain_blocked(self):
        old = (message('old'), message('repeat'), message('repeat'))
        snapshot = ConversationSnapshot(FRIEND, 1, old, (2,))
        with self.assertRaises(ContinuityError):
            snapshot.locate((message('repeat'), message('repeat'), message('new')))

    def test_media_loading_does_not_hide_an_unchanged_text_source(self):
        media = ChatMessage('', 'incoming', kind='unsupported', message_id='media')
        old = (message('question', mid='source'), media)
        snapshot = ConversationSnapshot(FRIEND, 1, old, (0,))
        current = (old[0], replace(media, kind='sticker'))
        self.assertEqual(snapshot.locate(current)[0], [0])
        with self.assertRaises(ContinuityError):
            snapshot.locate((replace(old[0], text='edited question'), current[1]))


class BrowserEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000.0
        self.store = BrowserState(':memory:', clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.adapter = FakePage([message('old', mid='old')])
        self.logs = []
        self.requests = []
        test = self
        class Logger:
            def info(self, text):
                test.logs.append(json.loads(text))
        self.config = Config('local', 'bot', dry_run=False, memory_enabled=False, cooldown_seconds=5,
                             pause_path=str(Path(self.tmp.name) / 'PAUSE'))
        self.settings = dict(whitelist=[FRIEND], min_send_interval_seconds=5, max_model_attempts=3)
        async def request(snapshot):
            self.requests.append(snapshot)
            return '[AI] reply-' + snapshot.messages[snapshot.selected[-1]].text
        self.engine = BrowserEngine(self.settings, self.config, self.store, self.adapter, request, Logger(), clock=lambda: self.now)

    def add_incoming(self, text, mid):
        self.adapter.messages.append(message(text, mid=mid))
        self.engine.sample(FRIEND, self.adapter.messages)

    def status(self):
        return [row[0] for row in self.store.db.execute('SELECT status FROM browser_jobs ORDER BY rowid')]

    async def test_startup_baseline_does_not_reply_to_history(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.assertFalse(await self.engine.step())
        self.assertEqual(self.adapter.sent, [])

    async def test_removed_whitelist_contact_cannot_receive_persisted_job(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('new', 'new')
        self.settings['whitelist'] = []
        await self.engine.step()
        self.assertEqual(self.status(), ['cancelled_whitelist'])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.adapter.sent, [])

    async def test_new_message_generates_and_sends_once(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('new', 'new')
        self.assertTrue(await self.engine.step())
        self.engine.sample(FRIEND, self.adapter.messages)
        self.assertFalse(await self.engine.step())
        self.assertEqual(self.adapter.sent, ['reply-new'])
        self.assertEqual(self.status(), ['visible_sent'])

    async def test_new_reply_gets_full_300_char_budget_after_removing_prefix(self):
        async def long_reply(snapshot):
            return '[AI] ' + '中' * 350
        self.engine.request_reply = long_reply
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('new', 'new')
        await self.engine.step()
        self.assertEqual(self.adapter.sent, ['中' * 300])

    async def test_existing_generated_draft_is_sent_without_legacy_prefix(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('new', 'new')
        job = self.store.next_job('pending')
        self.store.mark(job['id'], 'generated', reply='[AI] 旧草稿')
        await self.engine.send_step()
        self.assertEqual(self.adapter.sent, ['旧草稿'])
        self.assertEqual(self.requests, [])

    async def test_uncertain_reply_text_is_never_rewritten(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('new', 'new')
        job = self.store.next_job('pending')
        self.store.mark(job['id'], 'unknown', reply='[AI] 未决回复')
        self.assertFalse(await self.engine.send_step())
        self.assertEqual(self.store.job(job['id'])['reply'], '[AI] 未决回复')

    async def test_incoming_question_about_ai_marker_is_not_discarded(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('[AI] 是什么意思？', 'new')
        await self.engine.step()
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.adapter.sent[0].startswith('[AI]'))

    async def test_manual_answer_in_same_sample_cancels_before_generation(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.adapter.messages.extend([message('question', mid='new'), message('manual answer', 'outgoing', 'manual')])
        self.engine.sample(FRIEND, self.adapter.messages)
        await self.engine.step()
        self.assertEqual(self.status(), ['cancelled_manual'])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.adapter.sent, [])

    async def test_five_second_gap_defers_instead_of_discarding_new_message(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('first', '1')
        await self.engine.step()
        self.now += 1
        self.add_incoming('second', '2')
        await self.engine.step()
        self.assertEqual(self.status(), ['visible_sent', 'generated'])
        self.assertEqual(len(self.adapter.sent), 1)
        self.now += 4
        await self.engine.step()
        self.assertEqual(self.status(), ['visible_sent', 'visible_sent'])
        self.assertEqual(len(self.requests), 2)

    async def test_page_sampling_can_continue_during_slow_model_generation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(snapshot):
            entered.set()
            await release.wait()
            return '[AI] reply'
        self.engine.request_reply = slow
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('one', '1')
        work = asyncio.create_task(self.engine.step())
        await entered.wait()
        self.add_incoming('two', '2')
        self.assertEqual(self.status(), ['generating', 'pending'])
        release.set()
        await work
        self.assertEqual(self.status()[0], 'visible_sent')

    async def test_manual_reply_during_model_generation_cancels_queued_draft(self):
        async def manual(snapshot):
            self.adapter.messages.append(message('manual takeover', 'outgoing', 'manual'))
            return '[AI] stale'
        self.engine.request_reply = manual
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        await self.engine.step()
        self.assertEqual(self.status(), ['cancelled_manual'])
        self.assertEqual(self.adapter.sent, [])

    async def test_model_failures_retry_without_stopping_sampling(self):
        calls = 0
        async def fail(snapshot):
            nonlocal calls
            calls += 1
            raise OSError('PRIVATE BODY MUST NOT BE LOGGED')
        self.engine.request_reply = fail
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        for advance in (0, 5, 10):
            self.now += advance
            await self.engine.step()
        self.assertEqual(calls, 3)
        self.assertEqual(self.status(), ['failed'])
        self.assertNotIn('PRIVATE BODY', json.dumps(self.logs))

    async def test_uncertain_submit_is_never_retried(self):
        async def uncertain(*args):
            raise OSError('lost response')
        self.adapter.submit = uncertain
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        with self.assertRaises(OSError):
            await self.engine.step()
        self.assertEqual(self.status(), ['unknown'])
        self.assertFalse(await self.engine.step())
        self.add_incoming('next', '2')
        self.assertEqual(self.status(), ['unknown', 'pending'])

    async def test_draft_failure_before_click_keeps_generated_job_without_unknown(self):
        async def fail(*args):
            raise BrowserUIError('exact_draft_not_confirmed')
        original = self.adapter.prepare
        self.adapter.prepare = fail
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        with self.assertRaises(BrowserUIError):
            await self.engine.step()
        self.assertEqual(self.status(), ['generated'])
        self.assertFalse(self.store.blocked())
        self.assertEqual(self.adapter.sent, [])
        self.adapter.prepare = original
        await self.engine.step()
        self.assertEqual(self.status(), ['visible_sent'])
        self.assertEqual(len(self.requests), 1)

    async def test_uncertain_continuity_exception_is_not_downgraded_to_cancelled(self):
        async def uncertain(*args):
            raise ContinuityError('source_changed')
        self.adapter.submit = uncertain
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        await self.engine.step()
        self.assertEqual(self.status(), ['unknown'])

    async def test_pause_after_generation_prevents_any_send(self):
        async def pause(snapshot):
            Path(self.config.pause_path).touch()
            return '[AI] reply'
        self.engine.request_reply = pause
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        await self.engine.step()
        self.assertEqual(self.status(), ['generated'])
        self.assertEqual(self.adapter.sent, [])

    async def test_dry_run_never_submits(self):
        self.engine.config = replace(self.config, dry_run=True)
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        await self.engine.step()
        self.assertEqual(self.status(), ['dry_run'])
        self.assertEqual(self.adapter.sent, [])

    async def test_repeated_text_with_real_ids_is_not_lost(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('same', '1')
        await self.engine.step()
        self.now += 5
        self.add_incoming('same', '2')
        await self.engine.step()
        self.assertEqual(self.status(), ['visible_sent', 'visible_sent'])

    async def test_transient_empty_list_preserves_checkpoint(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        with self.assertRaisesRegex(ContinuityError, 'transient_empty'):
            self.engine.sample(FRIEND, [])
        self.add_incoming('new', '1')
        await self.engine.step()
        self.assertEqual(self.status(), ['visible_sent'])

    async def test_prepended_history_does_not_replay_old_messages(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.adapter.messages.insert(0, message('older history', mid='older'))
        self.assertEqual(self.engine.sample(FRIEND, self.adapter.messages), 0)
        self.add_incoming('new', 'new')
        await self.engine.step()
        self.assertEqual(self.adapter.sent, ['reply-new'])

    async def test_hourly_quota_holds_a_draft_without_paid_regeneration(self):
        self.engine.config = replace(self.config, hourly_limit=1)
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('one', '1')
        await self.engine.step()
        self.now += 5
        self.add_incoming('two', '2')
        await self.engine.step()
        self.assertEqual(self.status(), ['visible_sent', 'generated'])
        self.now = 4600
        await self.engine.step()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(self.adapter.sent), 2)

    async def test_terminal_payload_is_redacted_but_receipt_remains(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('private', '1')
        await self.engine.step()
        row = self.store.db.execute('SELECT status,snapshot,reply FROM browser_jobs').fetchone()
        self.assertEqual(tuple(row), ('visible_sent', None, None))

    async def test_generation_without_any_browser_persists_reply_for_sender(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        worker = ReplyWorker(self.settings, self.config, self.store,
                             self.engine.request_reply, self.engine.logger)
        self.assertTrue(await worker.step())
        self.assertEqual(self.status(), ['generated'])
        self.assertEqual(self.adapter.sent, [])
        self.assertEqual(len(self.requests), 1)
        self.engine.request_reply = None
        self.assertTrue(await self.engine.send_step())
        self.assertEqual(self.status(), ['visible_sent'])
        self.assertEqual(len(self.requests), 1)

    async def test_sender_does_not_generate_pending_work(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('question', '1')
        self.assertFalse(await self.engine.send_step())
        self.assertEqual(self.status(), ['pending'])
        self.assertEqual(self.requests, [])

    async def test_slow_next_generation_does_not_block_ready_reply(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('one', '1')
        await self.engine.generate_step()
        self.add_incoming('two', '2')
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(snapshot):
            entered.set()
            await release.wait()
            return '[AI] second'
        self.engine.request_reply = slow
        task = asyncio.create_task(self.engine.generate_step())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertTrue(await self.engine.send_step())
            self.assertEqual(self.status(), ['visible_sent', 'generating'])
            release.set()
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_older_model_retry_prevents_out_of_order_send(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('one', '1')
        self.add_incoming('two', '2')
        older = self.store.next_job('pending')
        self.store.mark(older['id'], 'pending', delay=5)
        await self.engine.generate_step()
        self.assertEqual(self.status(), ['pending', 'generated'])
        self.assertFalse(await self.engine.send_step())
        self.now += 5
        await self.engine.generate_step()
        await self.engine.send_step()
        self.assertEqual(self.adapter.sent, ['reply-one'])

    async def test_dry_run_generation_cannot_be_sent_by_later_live_sender(self):
        self.engine.sample(FRIEND, self.adapter.messages)
        self.add_incoming('one', '1')
        self.engine.config = replace(self.config, dry_run=True)
        await self.engine.generate_step()
        self.engine.config = self.config
        self.assertFalse(await self.engine.send_step())
        self.assertEqual(self.status(), ['dry_run'])
        self.assertEqual(self.adapter.sent, [])

    async def test_24_hour_virtual_time_with_transient_model_faults(self):
        self.engine.config = replace(self.config, hourly_limit=10000, daily_limit=20000)
        self.engine.logger.info = lambda _: None
        failed_once = set()
        sends_at = []
        async def sometimes(snapshot):
            source = snapshot.messages[snapshot.selected[-1]].message_id
            if int(source) % 17 == 0 and source not in failed_once:
                failed_once.add(source)
                raise TimeoutError()
            # Check the same prompt budget and real-history construction as production.
            self.assertLessEqual(len(snapshot.model_messages()[0]['content']), self.config.context_chars)
            return '[AI] ack-' + source
        self.engine.request_reply = sometimes
        original_submit = self.adapter.submit
        async def submit(*args):
            sends_at.append(self.now)
            return await original_submit(*args)
        self.adapter.submit = submit
        self.engine.sample(FRIEND, self.adapter.messages)
        requests = 0
        for tick in range(1, 17281):
            self.now += 5
            if tick % 6 == 0:
                requests += 1
                self.adapter.messages.append(message('question-' + str(requests), mid=str(requests)))
            self.adapter.messages = self.adapter.messages[-30:]
            self.engine.sample(FRIEND, self.adapter.messages)
            await self.engine.step()
        for _ in range(20):
            if self.store.next_job() is None and all(s == 'visible_sent' for s in self.status()):
                break
            self.now += 5
            await self.engine.step()
        self.assertEqual(len(sends_at), 2880)
        self.assertTrue(all(b - a >= 5 for a, b in zip(sends_at, sends_at[1:])))
        self.assertEqual(set(self.status()), {'visible_sent'})


class RuntimeSupportTests(unittest.TestCase):
    def test_lock_excludes_another_runner_and_releases_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'runtime.lock'
            with InstanceLock(path):
                with self.assertRaisesRegex(ValueError, 'runtime_already_running'):
                    with InstanceLock(path):
                        self.fail('Second runner acquired the lock')
            with InstanceLock(path):
                pass

    def test_reply_validation_removes_legacy_label_and_allows_300_body_chars(self):
        self.assertEqual(validate_reply(' reply '), 'reply')
        self.assertEqual(validate_reply('[AI] reply'), 'reply')
        self.assertEqual(validate_reply('[AI] [AI] reply'), 'reply')
        self.assertEqual(validate_reply('解释 [AI] 的含义'), '解释 [AI] 的含义')
        self.assertEqual(validate_reply('中' * 300), '中' * 300)
        self.assertEqual(validate_reply('[AI] ' + '中' * 300), '中' * 300)
        self.assertEqual(validate_reply('x', limit=1), 'x')
        self.assertEqual(Config('local', 'bot', max_reply_chars=1).max_reply_chars, 1)
        for reply in ('', '[AI]', '[AI]   ', 'line\nbreak', 'x' * 301):
            with self.subTest(reply=reply):
                with self.assertRaises(ReplyError):
                    validate_reply(reply)


class BrowserPersistenceTests(unittest.TestCase):
    def test_media_observation_survives_restart_without_new_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'queue.db'
            media = ChatMessage('', 'incoming', kind='unsupported', message_id='media')
            store = BrowserState(path)
            store.ingest(FRIEND, [media])
            store.close()
            restored = BrowserState(path)
            try:
                self.assertEqual(restored.ingest(FRIEND, [replace(media,kind='sticker')])[0], 0)
                self.assertIsNone(restored.next_job())
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM browser_cursors').fetchone()[0], 1)
            finally:
                restored.close()

    def test_legacy_media_cursor_upgrade_requires_exact_saved_snapshot(self):
        store = BrowserState(':memory:')
        try:
            media = ChatMessage('', 'incoming', kind='unsupported', message_id='media')
            store.db.execute('INSERT INTO browser_cursors VALUES(?,?,?)',
                             (FRIEND['sec_uid'],json.dumps([media.key]),1))
            store.db.commit()
            loaded = replace(media,kind='sticker')
            with self.assertRaises(ContinuityError):
                store.ingest(FRIEND,[loaded],previous_messages=[replace(media,message_id='wrong')])
            self.assertEqual(store.ingest(FRIEND,[loaded],previous_messages=[media])[0],0)
            self.assertIsNone(store.next_job())
        finally:
            store.close()

    def test_unidentified_media_changes_remain_ambiguous(self):
        store = BrowserState(':memory:')
        try:
            media = ChatMessage('', 'incoming', kind='unsupported')
            store.ingest(FRIEND,[media])
            with self.assertRaises(ContinuityError):
                store.ingest(FRIEND,[replace(media,kind='sticker')])
        finally:
            store.close()

    def test_changed_text_with_same_native_id_does_not_advance_cursor(self):
        store = BrowserState(':memory:')
        try:
            original = message('original',mid='text-id')
            store.ingest(FRIEND,[original])
            with self.assertRaises(ContinuityError):
                store.ingest(FRIEND,[replace(original,text='edited')])
            keys=json.loads(store.db.execute('SELECT keys FROM browser_cursors').fetchone()[0])
            self.assertEqual(keys,[original.key])
        finally:
            store.close()
    def test_restart_and_history_prepend_queue_only_new_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'queue.sqlite3'
            old = [message('previous', mid='1'), message('answered', 'outgoing', '2')]
            store = BrowserState(path)
            store.ingest(FRIEND, old)
            store.close()
            restored = BrowserState(path)
            try:
                current = [message('older history', mid='0'), *old, message('new', mid='3')]
                self.assertEqual(restored.ingest(FRIEND, current)[0], 1)
                snapshot = ConversationSnapshot.loads(restored.next_job('pending')['snapshot'])
                self.assertEqual([snapshot.messages[i].message_id for i in snapshot.selected], ['3'])
                self.assertEqual(restored.ingest(FRIEND, current)[0], 0)
            finally:
                restored.close()

    def test_legacy_uncertain_receipts_still_block_browser_sends(self):
        for table, column in (('auto_jobs', 'status'), ('replies', 'state')):
            with self.subTest(table=table):
                store = BrowserState(':memory:')
                try:
                    store.db.execute(f'CREATE TABLE {table} ({column} TEXT)')
                    store.db.execute(f'INSERT INTO {table} VALUES (?)', ('unknown',))
                    self.assertTrue(store.blocked())
                finally:
                    store.close()

    def test_stage_start_does_not_reset_other_active_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'queue.sqlite3'
            first = BrowserState(path)
            self.addCleanup(first.close)
            first.ingest(FRIEND, [message('question', mid='1')], reply_latest=True)
            job = first.next_job()
            first.mark(job['id'], 'generating')
            second = BrowserState(path, recover=('send',))
            self.addCleanup(second.close)
            self.assertEqual(second.job(job['id'])['status'], 'generating')
            first.mark(job['id'], 'sending')
            third = BrowserState(path, recover=('generate',))
            self.addCleanup(third.close)
            self.assertEqual(third.job(job['id'])['status'], 'sending')

    def test_generated_reply_survives_restart_and_outgoing_is_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'queue.sqlite3'
            first = BrowserState(path)
            self.addCleanup(first.close)
            first.ingest(FRIEND, [message('question', mid='1')], reply_latest=True)
            job = first.next_job()
            first.mark(job['id'], 'generated', reply='[AI] saved')
            second = BrowserState(path)
            self.addCleanup(second.close)
            self.assertEqual(second.next_job('generated')['reply'], '[AI] saved')
            self.assertEqual(first.known_outgoing(FRIEND['sec_uid']), set())
            outgoing = message('[AI] saved', 'outgoing', '2')
            second.confirmed(job['id'], [outgoing])
            self.assertEqual(first.known_outgoing(FRIEND['sec_uid']), {outgoing.key})

    def test_checkpoint_and_pending_job_survive_restart_without_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'queue.sqlite3'
            store = BrowserState(path)
            store.ingest(FRIEND, [message('old', mid='1')])
            items = [message('old', mid='1'), message('new', mid='2')]
            store.ingest(FRIEND, items)
            job = store.next_job()
            store.mark(job['id'], 'generating')
            store.close()
            restored = BrowserState(path)
            try:
                self.assertEqual(restored.next_job()['status'], 'pending')
                self.assertEqual(restored.ingest(FRIEND, items)[0], 0)
                restored.mark(job['id'], 'sending')
            finally:
                restored.close()
            restored = BrowserState(path)
            try:
                self.assertTrue(restored.blocked())
                self.assertEqual(restored.job(job['id'])['status'], 'unknown')
            finally:
                restored.close()
