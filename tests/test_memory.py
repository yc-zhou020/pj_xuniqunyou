import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from group_friend.config import Config
from group_friend.core import Engine, Message, Store
from group_friend.douyin import Binding, ObservedText
from group_friend.douyin_cli import replay
from group_friend.memory import MemoryStorageError, MemoryStore, augment_context


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'memory.db'
        self.now = 1000
        self.memory = MemoryStore(self.path, capacity_bytes=1024*1024, retention_days=2, clock=lambda: self.now)
        self.addCleanup(lambda: self.memory.close())
        self.space = self.memory.create_space('First group')

    def test_restart_scope_isolation_and_source_dedup_not_text_dedup(self):
        entry = self.memory.add(self.space, '小明喜欢足球', source_key='message-1')
        self.assertEqual(self.memory.add(self.space, 'changed text', source_key='message-1'), entry)
        other_id = self.memory.add(self.space, '小明喜欢足球', source_key='message-2')
        self.assertNotEqual(entry, other_id)
        other = self.memory.create_space('Same name does not mean same group')
        self.assertEqual(self.memory.search(other, '足球'), [])
        self.memory.close()
        self.memory = MemoryStore(self.path, capacity_bytes=8*1024*1024, clock=lambda: self.now)
        self.assertEqual(len(self.memory.search(self.space, '足球')), 2)
        self.assertEqual(self.memory.stats()['capacity_bytes'], 1024*1024)
        pages = self.memory.db.execute('PRAGMA max_page_count').fetchone()[0]
        self.assertEqual(pages * 4096, 1024*1024)

    def test_pin_protects_from_eviction_but_not_expiry(self):
        pinned = self.memory.add(self.space, 'pinned note', kind='note', pinned=True)
        self.now += 2 * 86400
        self.assertEqual(self.memory.prune(), 1)
        self.assertEqual(self.memory.search(self.space, 'pinned'), [])
        self.assertEqual(self.memory.stats()['expired_entries'], 1)

    def test_capacity_evicts_oldest_unpinned_and_reuses_space(self):
        self.memory.resize(128*1024)
        pinned = self.memory.add(self.space, 'important football preference', kind='note', pinned=True)
        first = None
        for i in range(18):
            self.now += 1
            entry = self.memory.add(self.space, ('old-record-%d ' % i) + 'a'*14000)
            first = first or entry
            self.assertLessEqual(self.path.stat().st_size, 128*1024)
        ids = {row['id'] for row in self.memory.list(self.space, 100)}
        self.assertIn(pinned, ids)
        self.assertNotIn(first, ids)
        self.assertGreater(self.memory.stats()['evicted_entries'], 0)

    def test_pinned_full_rejects_new_entry_without_losing_pinned_data(self):
        self.memory.resize(128*1024)
        pinned = self.memory.add(self.space, 'a'*16000, pinned=True)
        raised = False
        for i in range(10):
            try:
                self.memory.add(self.space, str(i)+'b'*15999, pinned=True)
            except MemoryStorageError:
                raised = True
                break
        self.assertTrue(raised)
        self.assertIn(pinned, {row['id'] for row in self.memory.list(self.space, 100)})
        self.assertLessEqual(self.path.stat().st_size, 128*1024)

    def test_shrink_refuses_live_data_and_forget_removes_index(self):
        entry = self.memory.add(self.space, 'searchable football note' + 'a'*15900, pinned=True)
        self.memory.add(self.space, 'b'*16000, pinned=True)
        self.memory.add(self.space, 'c'*16000, pinned=True)
        with self.assertRaises(MemoryStorageError):
            self.memory.resize(65536)
        self.assertEqual(self.memory.stats()['capacity_bytes'], 1024*1024)
        self.assertEqual(self.memory.forget(self.space, entry), 1)
        self.assertEqual(self.memory.search(self.space, 'football'), [])
        self.assertEqual(self.memory.db.execute('SELECT COUNT(*) FROM terms WHERE entry_id=?', (entry,)).fetchone()[0], 0)

    def test_binding_reuse_is_explicit_and_never_based_on_group_name(self):
        binding = Binding(1,2,'start','group','group(8)','session1')
        self.assertIsNone(self.memory.space_for_binding(binding))
        self.memory.link_binding(binding, self.space)
        self.assertEqual(self.memory.space_for_binding(binding), self.space)
        rebound = dataclasses.replace(binding, session_id='session2')
        self.assertIsNone(self.memory.space_for_binding(rebound))
        self.memory.link_binding(rebound, self.space)
        self.assertEqual(self.memory.space_for_binding(rebound), self.space)

    def test_prompt_is_bounded_valid_json_user_data_and_ignores_other_space(self):
        self.memory.add(self.space, '足球 ' + '"\\'*1000, kind='note')
        other = self.memory.create_space('Other')
        self.memory.add(other, '足球 SECRET FROM OTHER GROUP')
        prompt, ids = self.memory.prompt(self.space, '足球', max_chars=300)
        self.assertTrue(ids)
        self.assertEqual(prompt[0]['role'], 'user')
        self.assertLessEqual(len(prompt[0]['content']), 300)
        self.assertNotIn('SECRET', prompt[0]['content'])
        body = json.loads(prompt[0]['content'])
        self.assertTrue(body['long_term_context']['untrusted_historical_data'])

    def test_recall_shares_total_context_budget_and_prefers_current_message(self):
        self.memory.add(self.space, 'football ' + 'old '*400)
        config = Config('group','bot',context_chars=500,memory_context_chars=400)
        result, ids = augment_context(self.memory,self.space,'football',
                                     [{'role':'user','content':'older recent '*50},
                                      {'role':'user','content':'CURRENT football question'}],config)
        self.assertTrue(ids)
        self.assertLessEqual(sum(len(x['content']) for x in result), 500)
        self.assertEqual(result[-1]['content'],'CURRENT football question')

    def test_retention_setting_applies_to_existing_entries(self):
        self.memory.add(self.space,'old note')
        self.now += 86401
        self.memory.set_retention(1)
        self.assertEqual(self.memory.stats()['entries'],0)

    def test_actor_label_is_searchable_and_remains_data_not_an_account_id(self):
        self.memory.add(self.space,'我喜欢足球',actor='小明')
        prompt,ids=self.memory.prompt(self.space,'小明喜欢什么')
        self.assertTrue(ids)
        entry=json.loads(prompt[0]['content'])['long_term_context']['items'][0]
        self.assertEqual(entry['actor_label'],'小明')
        self.assertNotIn('sender_id',entry)


class MemoryIntegrationTests(unittest.TestCase):
    def test_engine_persists_only_accepted_inbound_and_recalls_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona=root/'persona.txt'
            persona.write_text('AI friend',encoding='utf-8')
            config=Config('group','bot',persona_path=str(persona),pause_path=str(root/'PAUSE'),
                          state_path=str(root/'state.db'),memory_path=str(root/'memory.db'))
            class Transport:
                def send(self,*args):
                    raise AssertionError('Must remain dry-run')
            class Model:
                contexts=[]
                def reply(self,persona,messages):
                    self.contexts.append(messages)
                    return 'reply'
            model=Model()
            now=1000
            with MemoryStore(config.memory_path,clock=lambda:now) as memory:
                state=Store(config.state_path)
                try:
                    engine=Engine(config,state,model,Transport(),started_at=1000,clock=lambda:now,memory=memory)
                    message=Message('test','group','m1','alice','Alice','Alice likes football',now,False)
                    self.assertEqual(engine.handle(message)[0],'not_triggered')
                    self.assertEqual(engine.handle(dataclasses.replace(message,message_id='old',timestamp=999))[0],'history_or_invalid_time')
                    self.assertEqual(engine.handle(dataclasses.replace(message,message_id='self',is_self=True))[0],'self')
                    self.assertEqual(memory.stats()['entries'],1)
                    # New engine has empty short-term context but the same persistent memory.
                    engine=Engine(config,state,model,Transport(),started_at=1000,clock=lambda:now,memory=memory)
                    status,_=engine.handle(dataclasses.replace(message,message_id='m2',text='/ai What about football?'))
                    self.assertEqual(status,'dry_run')
                    self.assertTrue(engine.recalled_memory_ids)
                    self.assertIn('Alice likes football',str(model.contexts[-1]))
                    self.assertEqual(memory.stats()['entries'],2) # Model draft is not stored.
                finally:
                    state.close()

    def test_replay_recalls_only_linked_space_and_never_stores_model_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            persona=root/'persona.txt'
            persona.write_text('AI friend',encoding='utf-8')
            config=Config('group','bot',persona_path=str(persona),pause_path=str(root/'PAUSE'))
            with MemoryStore(root/'memory.db') as memory:
                scope=memory.create_space('Target')
                memory.add(scope,'football preference',kind='note')
                messages=[ObservedText('football question','Alice',False,'row')]
                test=self
                class Model:
                    def reply(self,persona,context):
                        test.assertIn('football preference',str(context))
                        test.assertTrue(all(m['role']=='user' for m in context))
                        return 'generated draft'
                trace={}
                replay(messages,0,Model(),config,memory=memory,memory_space=scope,
                       memory_source='snapshot:one',memory_trace=trace)
                self.assertTrue(trace['recalled_memory_ids'])
                rows=memory.list(scope)
                self.assertEqual(len(rows),2)
                self.assertNotIn('generated draft',str(rows))

    def test_only_confirmed_outgoing_reply_becomes_assistant_memory(self):
        for outcome in ('sent','unknown','failed'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                persona=root/'persona.txt'
                persona.write_text('AI friend',encoding='utf-8')
                config=Config('group','bot',dry_run=False,persona_path=str(persona),pause_path=str(root/'PAUSE'))
                class Model:
                    def reply(self,*args):
                        return 'model response'
                class Transport:
                    def verify_target(self,*args):
                        return True
                    def send(self,*args):
                        return outcome
                with MemoryStore(root/'memory.db',clock=lambda:1000) as memory:
                    state=Store(root/'state.db')
                    try:
                        engine=Engine(config,state,Model(),Transport(),clock=lambda:1000,memory=memory)
                        status,_=engine.handle(Message('test','group','1','alice','Alice','/ai hello',1000,False))
                        self.assertEqual(status,outcome)
                        scope=memory.space_for_conversation('test','group')
                        kinds=[r['kind'] for r in memory.list(scope)]
                        self.assertEqual(kinds.count('assistant'),int(outcome=='sent'))
                    finally:
                        state.close()

    def test_disabled_memory_never_persists_or_recalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            persona=root/'persona.txt'
            persona.write_text('AI friend',encoding='utf-8')
            config=Config('group','bot',memory_enabled=False,persona_path=str(persona),pause_path=str(root/'PAUSE'))
            class DisabledStore:
                def space_for_conversation(self,*args):
                    raise AssertionError('Disabled memory must not be used')
            class Model:
                def reply(self,*args):
                    return 'response'
            state=Store(root/'state.db')
            try:
                engine=Engine(config,state,Model(),None,clock=lambda:1000,memory=DisabledStore())
                self.assertEqual(engine.handle(Message('test','group','1','alice','Alice','/ai hello',1000,False))[0],'dry_run')
            finally:
                state.close()
