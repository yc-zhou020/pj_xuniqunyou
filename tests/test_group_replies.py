import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from group_friend.browser_engine import BrowserEngine
from group_friend.browser_state import BrowserState
from group_friend.config import Config
from group_friend.conversation_snapshot import ChatMessage, ConversationSnapshot, digest
from group_friend.markdown_memory import MarkdownMemory
from group_friend.targets import active_targets, target_key
from group_friend.browser_reference.douyin_chat import FriendNotFound, DouyinPageError
from group_friend.browser_errors import ManualActionRequired
from group_friend.browser_session import load_settings


ALICE=dict(name='Alice',user_id='1',sec_uid='sec-1')
BOB=dict(name='Bob',user_id='2',sec_uid='sec-2')
SELF=dict(name='Self',user_id='99',sec_uid='sec-self',is_self=True)
GROUP=dict(kind='group',name='Group',group_id='123',members=[ALICE,BOB,SELF],enabled=True)


def incoming(text,mid,sender='1'):
    return ChatMessage(text,'incoming',message_id=mid,sender_id=sender,sender_name={'1':'Alice','2':'Bob'}.get(sender))


class GroupQueueTests(unittest.TestCase):
    def setUp(self):
        self.store=BrowserState(':memory:')
        self.addCleanup(self.store.close)

    def test_group_tasks_are_split_by_native_sender_in_arrival_order(self):
        self.store.ingest(GROUP,[incoming('baseline','old')])
        rows=[incoming('baseline','old'),incoming('a','a'),incoming('b','b','2'),incoming('c','c')]
        self.assertEqual(self.store.ingest(GROUP,rows)[0],3)
        jobs=self.store.db.execute('SELECT target,snapshot FROM browser_jobs ORDER BY rowid').fetchall()
        self.assertEqual([row[0] for row in jobs],['group:123']*3)
        snapshots=[ConversationSnapshot.loads(row[1]) for row in jobs]
        self.assertEqual([value.messages[value.selected[0]].sender_id for value in snapshots],['1','2','1'])
        self.assertEqual(self.store.ingest(GROUP,rows)[0],0)

    def test_unknown_self_and_missing_native_message_ids_are_not_queued(self):
        self.store.ingest(GROUP,[incoming('old','old')])
        rows=[incoming('old','old'),incoming('unknown','new','3'),incoming('self','self','99'),incoming('no id',None)]
        self.assertEqual(self.store.ingest(GROUP,rows)[0],0)

    def test_private_fingerprints_survive_sender_schema_extension(self):
        message=ChatMessage('old','incoming',message_id='1')
        legacy=dict(text='old',direction='incoming',kind='text',message_id='1',timestamp=None)
        self.assertEqual(message.key,digest(legacy))
        self.assertEqual(replace(message,sender_id='1',sender_name='renamed').key,message.key)

    def test_group_model_context_identifies_each_speaker(self):
        value=ConversationSnapshot(GROUP,1000,(incoming('a','1'),incoming('b','2','2')),(1,))
        payload=json.loads(value.model_messages()[0]['content'])
        self.assertEqual(payload['conversation_kind'],'group')
        self.assertEqual([row['sender_name'] for row in payload['messages']],['Alice','Bob'])
        self.assertEqual([row['sender_id'] for row in payload['messages']],['1','2'])

    def test_self_private_target_is_not_active(self):
        self.assertEqual(active_targets(dict(whitelist=[ALICE,SELF],group_whitelist=[GROUP])),[ALICE,GROUP])

    def test_group_only_settings_require_native_member_identity(self):
        root=Path(__file__).resolve().parents[1]
        settings=json.loads((root/'config/examples/browser.json').read_text())
        settings.update(whitelist=[],group_whitelist=[GROUP])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'browser.json'
            path.write_text(json.dumps(settings))
            self.assertEqual(active_targets(load_settings(path)),[GROUP])
            invalid=dict(GROUP,members=[dict(ALICE,sec_uid='')])
            settings['group_whitelist']=[invalid]
            path.write_text(json.dumps(settings))
            with self.assertRaises(ValueError):load_settings(path)


class GroupEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_browser_runtime import FakePage
        class GroupPage(FakePage):
            async def select(self,target):
                if target!=GROUP:
                    raise FriendNotFound('not visible')
        self.adapter=GroupPage([incoming('old','old')])
        self.store=BrowserState(':memory:')
        self.addCleanup(self.store.close)
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=Config('local','陈',memory_enabled=False,dry_run=False,pause_path=str(Path(self.tmp.name)/'PAUSE'))
        self.settings=dict(whitelist=[],group_whitelist=[GROUP],min_send_interval_seconds=5)
        self.request=AsyncMock(return_value='[AI] group answer')
        self.engine=BrowserEngine(self.settings,self.config,self.store,self.adapter,self.request,Mock())
        self.engine.sample(GROUP,self.adapter.messages)

    async def test_authorized_group_message_is_generated_and_sent_once(self):
        self.adapter.messages.append(incoming('new','new'))
        self.engine.sample(GROUP,self.adapter.messages)
        await self.engine.step()
        self.assertEqual(self.adapter.sent,['group answer'])
        self.assertFalse(await self.engine.step())
        self.assertFalse(self.store.blocked())

    async def test_removed_group_cancels_without_generating_or_sending(self):
        self.adapter.messages.append(incoming('new','new'))
        self.engine.sample(GROUP,self.adapter.messages)
        self.settings['group_whitelist']=[]
        await self.engine.step()
        self.request.assert_not_called()
        self.assertEqual(self.adapter.sent,[])
        self.assertEqual(self.store.db.execute('SELECT status FROM browser_jobs').fetchone()[0],'cancelled_whitelist')

    async def test_unknown_group_send_still_blocks_all_delivery(self):
        self.adapter.messages.append(incoming('new','new'))
        self.engine.sample(GROUP,self.adapter.messages)
        self.adapter.submit=AsyncMock(side_effect=OSError('lost click result'))
        with self.assertRaises(OSError):await self.engine.step()
        self.assertTrue(self.store.blocked())
        self.assertFalse(await self.engine.send_step())
        self.adapter.submit.assert_awaited_once()

    async def test_missing_private_conversation_does_not_block_group_sampling(self):
        self.settings['whitelist']=[dict(name='Missing',sec_uid='not-visible')]
        def log(text):
            if json.loads(text).get('status')=='sampled':self.engine.stop.set()
        self.engine.logger.info.side_effect=log
        await asyncio.wait_for(self.engine.sampler(5),1)
        self.assertEqual(self.engine.samples,2)

    async def test_private_continuity_error_does_not_starve_group_sampling(self):
        private=dict(name='Private',sec_uid='private',douyin_id='account')
        self.store.ingest(private,[ChatMessage('before edit','incoming',message_id='private-id')])
        self.settings['whitelist']=[private]
        self.adapter.messages.append(incoming('new group question','new'))
        original_read=self.adapter.read
        original_select=self.adapter.select
        async def select(target):
            if target != private:await original_select(target)
        async def read(target):
            if target == private:return [ChatMessage('after edit','incoming',message_id='private-id')]
            return await original_read(target)
        self.adapter.select=select
        self.adapter.read=read
        events=[]
        def log(text):
            event=json.loads(text)
            events.append(event)
            if event.get('status')=='sampled' and event.get('target')=='Group':self.engine.stop.set()
        self.engine.logger.info.side_effect=log
        await asyncio.wait_for(self.engine.sampler(5),1)
        self.assertEqual(self.store.next_job('pending')['target'],'group:123')
        self.assertTrue(any(e.get('status')=='observation_waiting' and e.get('target')=='Private' for e in events))

    async def test_private_ui_error_is_isolated_and_logs_stage_without_body(self):
        private=dict(name='Private',sec_uid='private',douyin_id='account')
        self.settings['whitelist']=[private]
        original_select=self.adapter.select
        async def select(target):
            if target==private:raise DouyinPageError('PRIVATE ERROR BODY')
            return await original_select(target)
        self.adapter.select=select
        events=[]
        def log(text):
            event=json.loads(text);events.append(event)
            if event.get('status')=='sampled' and event.get('target')=='Group':self.engine.stop.set()
        self.engine.logger.info.side_effect=log
        await asyncio.wait_for(self.engine.sampler(5),1)
        self.assertTrue(any(e.get('status')=='target_retry' and e.get('target')=='Private' and e.get('stage')=='select' for e in events))
        self.assertEqual(self.adapter.recovered,0)
        self.assertNotIn('PRIVATE ERROR BODY',json.dumps(events))

    async def test_verification_pauses_all_ui_sends_without_browser_recovery(self):
        self.adapter.messages.append(incoming('new','new'))
        self.engine.sample(GROUP,self.adapter.messages)
        await self.engine.generate_step()
        async def select(target):raise ManualActionRequired('risk_verification_required')
        self.adapter.select=select
        events=[]
        def log(text):
            event=json.loads(text);events.append(event)
            if event.get('status')=='manual_action_required':self.engine.stop.set()
        self.engine.logger.info.side_effect=log
        await asyncio.wait_for(self.engine.sampler(5),1)
        self.assertTrue(self.engine.manual_required)
        self.assertEqual(self.adapter.recovered,0)
        self.engine.stop.clear()
        self.assertFalse(await self.engine.send_step())
        self.assertEqual(self.adapter.sent,[])
        self.assertEqual(self.store.next_job('generated')['status'],'generated')


class GroupMemberMemoryTests(unittest.TestCase):
    def test_group_member_memories_stay_separate_from_each_other_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Config('local','陈',memory_path=str(Path(tmp)/'memory'),memory_recall_probability=1)
            memory=MarkdownMemory(config)
            a=ConversationSnapshot(GROUP,1000,(incoming('猫条偏好','a'),),(0,))
            b=ConversationSnapshot(GROUP,1000,(incoming('海胆偏好','b','2'),),(0,))
            memory.capture(a)
            memory.capture(b)
            current=ConversationSnapshot(GROUP,1001,(incoming('猫条','new'),),(0,))
            payload=memory.context(current,rng=lambda:0)['content']
            self.assertIn('猫条偏好',payload)
            self.assertNotIn('海胆偏好',payload)
            self.assertFalse(memory.folder(ALICE).exists())
            grouped=Path(tmp)/'memory/groups'/digest('123')[:24]
            self.assertTrue((grouped/digest(ALICE['sec_uid'])[:24]/'events').is_dir())

    def test_mixed_sender_snapshot_cannot_be_saved_under_one_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory=MarkdownMemory(Config('local','陈',memory_path=tmp))
            value=ConversationSnapshot(GROUP,1000,(incoming('a','a'),incoming('b','b','2')),(0,1))
            with self.assertRaises(ValueError):memory.capture(value)
