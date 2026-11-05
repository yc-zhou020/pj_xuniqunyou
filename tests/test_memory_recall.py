import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from group_friend.config import Config
from group_friend.conversation_snapshot import ChatMessage, ConversationSnapshot
from group_friend.markdown_memory import MarkdownMemory
from group_friend.memory_recall import MemoryRecallPolicy, fuzzy_score, keywords


FRIEND = dict(name='Contact', sec_uid='participant')


def snapshot(texts, *, selected=None):
    messages = tuple(ChatMessage(text, 'incoming', message_id=str(i)) for i, text in enumerate(texts))
    return ConversationSnapshot(FRIEND, 1000, messages, (len(messages)-1,) if selected is None else selected)


class RecallPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = Config('contact', '陈')
        self.record = dict(text='我喜欢金枪鱼猫条', recorded_at=1)

    def test_probability_boundary_is_strictly_below_point_33(self):
        for draw, accepted in ((0,True),(.329,True),(.33,False),(.9,False)):
            with self.subTest(draw=draw):
                _, result = MemoryRecallPolicy(self.config, rng=lambda:draw).select(snapshot(['猫条']), [self.record], '')
                self.assertEqual(result.included, accepted)
                self.assertEqual(result.draw, draw)
                self.assertEqual(result.probability, .33)

    def test_one_draw_controls_all_matching_items(self):
        rng=Mock(return_value=.1)
        _,result=MemoryRecallPolicy(self.config,rng=rng).select(snapshot(['猫条']),[self.record,self.record],'')
        self.assertTrue(result.included)
        self.assertEqual(result.matched_items,2)
        rng.assert_called_once_with()

    def test_no_match_does_not_draw_or_fall_back_to_recent_memory(self):
        rng = Mock(side_effect=AssertionError('must not draw'))
        records, result = MemoryRecallPolicy(self.config, rng=rng).select(snapshot(['天气预报']), [self.record], '')
        self.assertEqual(records, [])
        self.assertFalse(result.included)
        self.assertEqual(result.reason, 'no_match')
        self.assertIsNone(result.draw)
        rng.assert_not_called()

    def test_only_last_six_messages_supply_matching_keywords(self):
        texts = ['海胆刺身', 'a', 'b', 'c', 'd', 'e', 'f']
        policy = MemoryRecallPolicy(self.config, rng=lambda:0)
        records = [dict(text='海胆刺身',recorded_at=1)]
        _, result = policy.select(snapshot(texts),records,'')
        self.assertEqual(result.context_messages, 6)
        self.assertFalse(result.included)
        texts[-2]='海胆刺身'
        _, result = policy.select(snapshot(texts),records,'')
        self.assertTrue(result.included)

    def test_historical_outgoing_text_can_trigger_matching(self):
        value = snapshot(['海胆刺身','继续说'])
        value = replace(value,messages=(replace(value.messages[0],direction='outgoing'),value.messages[1]))
        _, result = MemoryRecallPolicy(self.config,rng=lambda:0).select(value,[dict(text='海胆刺身',recorded_at=1)],'')
        self.assertTrue(result.included)

    def test_future_messages_do_not_trigger_recall(self):
        value = snapshot(['天气预报','金枪鱼猫条'],selected=(0,))
        _, result = MemoryRecallPolicy(self.config,rng=lambda:0).select(value,[self.record],'')
        self.assertFalse(result.included)

    def test_fuzzy_english_and_unicode_normalization(self):
        self.assertGreater(fuzzy_score(keywords('strawbery'),'strawberry'),.8)
        self.assertEqual(fuzzy_score(keywords('ＡＰＰＬＥ'),'apple'),1)
        self.assertEqual(fuzzy_score(keywords('海胆'),'海鲜'),0)

    def test_matching_manual_notes_uses_the_same_probability_gate(self):
        _, result = MemoryRecallPolicy(self.config,rng=lambda:.5).select(snapshot(['猫条']),[], '# Notes\n喜欢猫条')
        self.assertEqual(result.matched_items,1)
        self.assertFalse(result.included)

    def test_profile_heading_does_not_count_as_a_matching_note(self):
        _, result = MemoryRecallPolicy(self.config,rng=lambda:0).select(snapshot(['猫条']),[], '# 猫条笔记')
        self.assertFalse(result.included)

    def test_invalid_probabilities_and_random_values_are_rejected(self):
        for value in (-.1,1.1,True,float('nan'),float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.config,memory_recall_probability=value)
        with self.assertRaises(ValueError):
            MemoryRecallPolicy(self.config,rng=lambda:1).select(snapshot(['猫条']),[self.record],'')


class GatedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=Config('contact','陈',memory_path=str(Path(self.tmp.name)/'memory'))
        self.memory=MarkdownMemory(self.config)
        self.memory.remember(FRIEND,[ChatMessage('我喜欢金枪鱼猫条','incoming',message_id='archived')])
        (self.memory.folder(FRIEND)/'profile.md').write_text('# Notes\n人工长期笔记。')

    def test_rejected_draw_leaks_neither_events_nor_manual_notes(self):
        context,result=self.memory.context_with_decision(snapshot(['猫条']),rng=lambda:.9)
        self.assertIsNone(context)
        self.assertEqual(result.reason,'random_rejected')

    def test_accepted_draw_includes_matched_events_with_existing_budget(self):
        context,result=self.memory.context_with_decision(snapshot(['猫条']),rng=lambda:.1)
        self.assertTrue(result.included)
        self.assertLessEqual(len(context['content']),self.config.memory_context_chars)
        data=json.loads(context['content'])
        self.assertIn('金枪鱼',data['entries'][0]['text'])
        self.assertIn('人工长期笔记',data['profile'])

    def test_no_match_excludes_all_long_term_content(self):
        context,result=self.memory.context_with_decision(snapshot(['天气预报']),rng=lambda:0)
        self.assertIsNone(context)
        self.assertEqual(result.reason,'no_match')

    def test_matching_memory_on_current_page_is_not_an_off_page_candidate(self):
        source=ChatMessage('我喜欢金枪鱼猫条','incoming',message_id='archived')
        value=ConversationSnapshot(FRIEND,1000,(source,ChatMessage('继续','incoming',message_id='new')),(1,))
        context,result=self.memory.context_with_decision(value,rng=lambda:0)
        self.assertIsNone(context)
        self.assertEqual(result.matched_items,0)


class GatedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_recall_still_generates_reply_without_memory_payload(self):
        from group_friend.browser_runtime import run
        from group_friend.browser_state import BrowserState
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=Config('contact','陈',dry_run=True,memory_enabled=True,memory_recall_probability=0,
                          memory_path=str(root/'memory'),persona_path=str(root/'persona.md'),
                          pause_path=str(root/'PAUSE'))
            Path(config.persona_path).write_text('你是陈。')
            memory=MarkdownMemory(config)
            memory.remember(FRIEND,[ChatMessage('我喜欢金枪鱼猫条','incoming',message_id='archived')])
            settings=dict(state_path=str(root/'queue.db'),whitelist=[FRIEND],
                          sample_interval_seconds=5,min_send_interval_seconds=5)
            state=BrowserState(settings['state_path'])
            state.ingest(FRIEND,[ChatMessage('猫条','incoming',message_id='new')],reply_latest=True)
            state.close()
            args=argparse.Namespace(stage='generate',inspect=False,mock=False,once=True,reply_latest=False,duration=0)
            model=Mock()
            model.reply.return_value='[AI] 正常回复'
            logger=Mock()
            with patch('group_friend.browser_runtime.DeepSeek',return_value=model):
                self.assertEqual(await run(settings,config,args,logger),0)
            model.reply.assert_called_once()
            messages=model.reply.call_args.args[1]
            self.assertEqual(len(messages),1)
            self.assertNotIn('long_term_memory',messages[0]['content'])
            self.assertIn('猫条',messages[0]['content'])
            events=[json.loads(call.args[0]) for call in logger.info.call_args_list]
            recall=next(event for event in events if event.get('status')=='memory_recall')
            self.assertFalse(recall['included'])
            self.assertEqual(recall['reason'],'random_rejected')
            self.assertNotIn('金枪鱼',json.dumps(events,ensure_ascii=False))
