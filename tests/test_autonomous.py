import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from group_friend.autonomous import InstanceLock, PrivateRuntime, RuntimeState, main, read_private
from group_friend.config import Config
from group_friend.douyin import Binding, ObservationError
from group_friend.review import ReviewError, ReviewQueue


def report(items):
    from test_douyin import node
    pane = 'root/private'
    base = pane + '/1/0/0/0'
    ns = [node('root', 16, 'App'), node('root/contacts/label', 41, 'Contact'),
          node(pane + '/0/1/1/0', 41, 'Contact'),
          dict(node(base), Bounds=[0, 0, 600, 600]), node(pane + '/1/0/0/3/1/0', action='激活')]
    for index, (text, side) in enumerate(reversed(items)):
        row = base + '/' + str(index)
        ns += [node(row), dict(node(row + '/0/avatar', 40), Bounds=[20 if side == 'incoming' else 550, 20, 32, 32]),
               node(row + '/1'), node(row + '/1/text', 41, text)]
    return dict(schema=1, text_included=True, windows=[dict(process_id=7, window_handle=9,
                process_started_at='start-1', is_offscreen=False, result=dict(Nodes=ns, Truncated=False, Errors=[]))])


def wrapped_report(items):
    from test_douyin import node
    result = report(items)
    base = 'root/private/1/0/0/0'
    nodes = result['windows'][0]['result']['Nodes']
    for n in nodes:
        if n['Path'].startswith(base + '/'):
            n['Path'] = base + '/0/' + n['Path'][len(base) + 1:]
    nodes.append(dict(node(base + '/0'), Bounds=[0, 0, 600, 600]))
    nodes.append(node(base + '/0/' + str(len(items)), 41, '15:00'))
    return result


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binding = Binding(7, 9, 'start-1', 'App', 'Contact', 'session')
        self.binding_path = self.root / 'binding.json'
        self.binding.save(self.binding_path)
        self.persona = self.root / 'persona.txt'
        self.persona.write_text('AI friend', encoding='utf-8')
        self.config = Config('local', 'bot', dry_run=False, memory_enabled=False, persona_path=str(self.persona),
                             pause_path=str(self.root / 'PAUSE'), cooldown_seconds=5)
        self.now = 1000
        self.state = RuntimeState(self.root / 'state.sqlite3', clock=lambda: self.now)
        self.addCleanup(self.state.close)
        self.current = report([('old', 'incoming')])
        self.logs = []
        self.calls = []
        test = self
        class Logger:
            def info(self, text):
                test.logs.append(json.loads(text))
        class Model:
            def reply(self, persona, context):
                test.calls.append(context)
                return 'hello from AI'
        class Transport:
            checked = 0
            sent = 0
            outcome = 'visible_sent'
            def check_private(self, *args):
                self.checked += 1
                return 'ready'
            def send_private(self, *args):
                self.sent += 1
                return self.outcome
        self.model = Model()
        self.transport = Transport()
        self.runtime = PrivateRuntime(self.config, [(self.binding_path, self.binding)], self.state,
                                      self.model, self.transport, Logger(), capture_fn=lambda _: copy.deepcopy(self.current))

    def statuses(self):
        return [row[0] for row in self.state.db.execute('SELECT status FROM auto_jobs')]

    def incoming(self, text='new'):
        self.runtime.tick()
        self.current = report([('old', 'incoming'), (text, 'incoming')])
        self.runtime.tick()

    def test_startup_and_restart_baselines_never_reply_to_history(self):
        self.runtime.tick()
        self.runtime.trackers.clear()
        self.runtime.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.transport.sent, 0)

    def test_new_incoming_sends_once_and_logs_metadata_only(self):
        self.incoming()
        self.runtime.tick()
        self.assertEqual(self.statuses(), ['visible_sent'])
        self.assertEqual(self.transport.sent, 1)
        self.assertEqual(self.calls[0], [{'role': 'user', 'content': 'new'}])
        self.assertTrue(all('source' not in row and 'reply' not in row for row in self.logs))

    def test_outgoing_and_ai_marked_text_never_call_model(self):
        self.runtime.tick()
        self.current = report([('old', 'incoming'), ('mine', 'outgoing'), ('[AI] echo', 'incoming')])
        self.runtime.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.transport.sent, 0)

    def test_window_recovery_happens_before_reading_and_preserves_tracker(self):
        class Manager:
            recovered = False
            def prepare(manager, binding):
                if manager.recovered:
                    return False
                manager.recovered = True
                self.current['windows'][0]['is_offscreen'] = False
                return True
        self.runtime.tick()
        self.current = report([('old', 'incoming'), ('new', 'incoming')])
        self.current['windows'][0]['is_offscreen'] = True
        self.runtime.config = replace(self.config, dry_run=True)
        self.runtime.window_manager = Manager()
        self.runtime.tick()
        self.assertEqual(self.calls[0][-1]['content'], 'new')
        self.assertTrue(any(row['status'] == 'window_restored_background' for row in self.logs))

    def test_focus_restore_failure_does_not_turn_confirmed_send_into_unknown(self):
        class Manager:
            def prepare(manager, binding):
                return False
            def foreground(manager):
                return 999
            def return_focus(manager, binding, previous):
                raise OSError('focus unavailable')
        self.runtime.window_manager = Manager()
        self.incoming()
        self.assertEqual(self.statuses(), ['visible_sent'])
        self.assertTrue(any(row['status'] == 'previous_window_restore_failed' for row in self.logs))

    def test_waiting_log_distinguishes_closed_private_pane(self):
        nodes = self.current['windows'][0]['result']['Nodes']
        next(n for n in nodes if n['Path'] == 'root/private/0/1/1/0')['Name'] = 'Other'
        self.runtime.tick()
        self.assertEqual(self.logs[-1]['reason'], 'not_whitelisted_private_pane')

    def test_inspect_never_creates_state_or_initializes_api(self):
        from dataclasses import asdict
        settings = self.root / 'runtime.json'
        reply_config = self.root / 'reply-config.json'
        reply_config.write_text(json.dumps(asdict(self.config)), encoding='utf-8')
        settings.write_text(json.dumps(dict(whitelist=[dict(name='Contact', binding=str(self.binding_path))],
                                           reply_config=str(reply_config), window_policy='passive')), encoding='utf-8')
        with patch('group_friend.autonomous.RuntimeState') as state, \
                patch('group_friend.autonomous.DeepSeek') as model, \
                patch('group_friend.autonomous.PrivateRuntime') as runtime, \
                patch('group_friend.autonomous.make_logger'), redirect_stdout(io.StringIO()):
            runtime.return_value.observe.return_value = []
            self.assertEqual(main(['--config', str(settings), '--inspect']), 0)
        model.assert_not_called()
        state.assert_not_called()

    def test_unknown_geometry_direction_never_replies(self):
        for window in self.current['windows']:
            for n in window['result']['Nodes']:
                if n['Role'] == 40:
                    n['Bounds'] = [290, 20, 32, 32]
        self.runtime.tick(reply_latest=True)
        self.assertEqual(self.calls, [])

    def test_minimized_or_other_private_target_waits_without_api(self):
        self.current['windows'][0]['is_offscreen'] = True
        self.runtime.tick(reply_latest=True)
        self.assertEqual(self.calls, [])
        self.current = report([('old', 'incoming')])
        ns = self.current['windows'][0]['result']['Nodes']
        next(n for n in ns if n['Path'] == 'root/private/0/1/1/0')['Name'] = 'Other'
        self.runtime.tick(reply_latest=True)
        self.assertEqual(self.calls, [])

    def test_hidden_then_restored_is_fresh_history_baseline(self):
        self.runtime.tick()
        self.current['windows'][0]['is_offscreen'] = True
        self.runtime.tick()
        self.current = report([('old', 'incoming'), ('during absence', 'incoming')])
        self.runtime.tick()
        self.assertEqual(self.calls, [])

    def test_scroll_wrapper_transition_keeps_text_and_direction_signatures(self):
        plain = read_private(report([('one', 'incoming'), ('two', 'outgoing')]), self.binding)
        wrapped = read_private(wrapped_report([('one', 'incoming'), ('two', 'outgoing')]), self.binding)
        self.assertEqual([m.signature for m in plain], [m.signature for m in wrapped])

    def test_new_incoming_with_scroll_wrapper_is_processed_once(self):
        self.runtime.tick()
        self.current = wrapped_report([('old', 'incoming'), ('new', 'incoming')])
        self.runtime.tick()
        self.runtime.tick()
        self.assertEqual(self.transport.sent, 1)
        self.assertEqual(self.calls[0][-1]['content'], 'new')

    def test_wrapper_change_alone_does_not_trigger_reply(self):
        self.runtime.tick()
        self.current = wrapped_report([('old', 'incoming')])
        self.runtime.tick()
        self.current = report([('old', 'incoming')])
        self.runtime.tick()
        self.assertEqual(self.calls, [])

    def test_single_message_bubble_is_not_mistaken_for_scroll_wrapper(self):
        self.assertEqual([m.text for m in read_private(self.current, self.binding)], ['old'])

    def test_actual_history_edit_still_stops_and_logs_counts(self):
        self.runtime.tick()
        self.current = wrapped_report([('edited', 'incoming')])
        with self.assertRaisesRegex(ObservationError, 'history_or_ambiguous_change'):
            self.runtime.tick()
        self.assertEqual(self.calls, [])
        change = next(row for row in self.logs if row['status'] == 'observation_changed')
        self.assertEqual((change['previous_messages'], change['observed_messages']), (1, 1))

    def test_pause_during_model_reply_prevents_input(self):
        def reply(*args):
            Path(self.config.pause_path).touch()
            return 'reply'
        self.model.reply = reply
        with self.assertRaisesRegex(ObservationError, 'paused'):
            self.incoming()
        self.assertEqual(self.transport.sent, 0)

    def test_binding_change_during_model_reply_prevents_input(self):
        def reply(*args):
            replace(self.binding, session_id='new').save(self.binding_path)
            return 'reply'
        self.model.reply = reply
        with self.assertRaisesRegex(ObservationError, 'binding_changed'):
            self.incoming()
        self.assertEqual(self.transport.sent, 0)

    def test_source_disappears_or_becomes_outgoing_before_send(self):
        def reply(*args):
            self.current = report([('new', 'outgoing')])
            return 'reply'
        self.model.reply = reply
        with self.assertRaisesRegex(ObservationError, 'incoming_source_changed'):
            self.incoming()
        self.assertEqual(self.transport.sent, 0)

    def test_dry_run_never_reaches_transport(self):
        self.runtime.config = replace(self.config, dry_run=True)
        self.incoming()
        self.assertEqual(self.statuses(), ['dry_run'])
        self.assertEqual(self.transport.checked, 0)
        self.assertEqual(self.transport.sent, 0)

    def test_unknown_blocks_further_sends_and_manual_review(self):
        self.transport.outcome = 'unknown'
        self.transport.last_diagnostics = dict(stage='invoking_send', reason='composer_focus_changed')
        with self.assertRaisesRegex(ObservationError, 'auto_send_unknown'):
            self.incoming()
        with self.assertRaisesRegex(ObservationError, 'unresolved_auto_send'):
            self.runtime.tick()
        diagnostic = next(row for row in self.logs if row['status'] == 'unknown')
        self.assertEqual(diagnostic['stage'], 'invoking_send')
        self.assertEqual(diagnostic['reason'], 'composer_focus_changed')
        queue = ReviewQueue(self.root / 'state.sqlite3')
        try:
            item = queue.enqueue(self.binding, 'source', 'reply')
            queue.approve(item, 1, target_heading='Contact', binding=self.binding)
            with self.assertRaisesRegex(ReviewError, 'unresolved_auto_send'):
                queue.claim_send(item, 1, self.binding)
        finally:
            queue.close()

    def test_manual_review_unknown_blocks_runtime(self):
        queue = ReviewQueue(self.root / 'state.sqlite3')
        try:
            item = queue.enqueue(self.binding, 'source', 'reply')
            queue.approve(item, 1, target_heading='Contact', binding=self.binding)
            queue.claim_send(item, 1, self.binding)
            queue.finish_send(item, 'unknown')
            with self.assertRaisesRegex(ObservationError, 'unresolved_auto_send'):
                self.runtime.tick()
        finally:
            queue.close()

    def test_crash_after_send_started_is_recovered_as_unknown(self):
        self.state.claim('crash', 'Contact', 'text')
        self.state.mark('crash', 'sending')
        other = RuntimeState(self.root / 'state.sqlite3')
        try:
            self.assertTrue(other.blocked())
            self.assertEqual(self.statuses(), ['unknown'])
        finally:
            other.close()

    def test_send_exception_persists_unknown_before_exit(self):
        def fail(*args):
            raise TimeoutError('private secret')
        self.transport.send_private = fail
        with self.assertRaises(TimeoutError):
            self.incoming()
        self.assertEqual(self.statuses(), ['unknown'])

    def test_reply_latest_is_explicit_and_persistently_claimed_once(self):
        self.runtime.tick(reply_latest=True)
        self.runtime.trackers.clear()
        self.runtime.tick(reply_latest=True)
        self.assertEqual(self.transport.sent, 1)

    def test_new_messages_during_send_are_not_swallowed_by_a_new_baseline(self):
        def send(*args):
            self.current = report([('old', 'incoming'), ('new', 'incoming'),
                                   ('[AI] reply', 'outgoing'), ('next', 'incoming')])
            return 'visible_sent'
        self.transport.send_private = send
        self.incoming()
        self.now += 10
        self.runtime.tick()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1][-1]['content'], 'next')

    def test_duplicate_text_append_is_new_occurrence_with_preserved_prefix(self):
        self.runtime.tick()
        self.current = report([('old', 'incoming'), ('old', 'incoming')])
        self.runtime.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.transport.sent, 1)

    def test_reply_latest_duplicate_text_passes_selected_occurrence_to_transport(self):
        self.current = report([('same', 'incoming'), ('other', 'incoming'), ('same', 'incoming')])
        seen = []
        def check(binding, source, reply, pause, anchor):
            seen.append(anchor)
            self.assertEqual(source, 'same')
            return 'ready'
        self.transport.check_private = check
        self.runtime.tick(reply_latest=True)
        self.assertEqual(seen, [dict(texts=['same', 'other', 'same'], index=2)])
        self.assertEqual(self.transport.sent, 1)

    def test_reordered_history_with_same_duplicate_counts_prevents_send(self):
        self.current = report([('same', 'incoming'), ('other', 'incoming'), ('same', 'incoming')])
        def reply(*args):
            self.current = report([('other', 'incoming'), ('same', 'incoming'), ('same', 'incoming')])
            return 'reply'
        self.model.reply = reply
        with self.assertRaisesRegex(ObservationError, 'incoming_source_changed'):
            self.runtime.tick(reply_latest=True)
        self.assertEqual(self.transport.sent, 0)

    def test_same_text_at_different_occurrence_does_not_reuse_bootstrap_claim(self):
        self.current = report([('same', 'incoming')])
        self.runtime.tick(reply_latest=True)
        self.now += 10
        self.runtime.trackers.clear()
        self.current = report([('same', 'incoming'), ('other', 'incoming'), ('same', 'incoming')])
        self.runtime.tick(reply_latest=True)
        self.assertEqual(self.transport.sent, 2)

    def test_retry_unsent_only_reclaims_pre_send_error(self):
        for status in ('error', 'unknown', 'sending', 'visible_sent', 'dry_run'):
            with self.subTest(status=status):
                key = 'job-' + status
                self.state.claim(key, 'Contact', 'text')
                self.state.mark(key, status)
                self.assertEqual(self.state.claim(key, 'Contact', 'text', retry_unsent=True), status == 'error')

    def test_limits_apply_before_model(self):
        self.incoming()
        self.current = report([('old', 'incoming'), ('new', 'incoming'), ('third', 'incoming')])
        self.runtime.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertIn('rate_limited', self.statuses())

    def test_lock_prevents_second_runtime(self):
        with InstanceLock(self.root / 'runner.lock'):
            with self.assertRaisesRegex(ValueError, 'already_running'):
                with InstanceLock(self.root / 'runner.lock'):
                    self.fail('Second runtime acquired lock')

    def test_continuation_direction_is_visual_inference_and_system_notice_resets_it(self):
        from test_douyin import private_fixture
        window = private_fixture()
        window['is_offscreen'] = False
        base = 'root/private/1/0/0/0'
        for n in window['result']['Nodes']:
            if n['Path'] == base:
                n['Bounds'] = [0, 0, 600, 500]
            if n['Role'] == 40:
                n['Bounds'] = [20, 0, 30, 30]
        binding = replace(self.binding, window_title='App')
        result = read_private(dict(schema=1, text_included=True, windows=[window]), binding)
        self.assertEqual([m.direction for m in result], ['incoming', 'incoming'])
        next(n for n in window['result']['Nodes'] if n['Path'] == base + '/2')['Name'] = 'message removed'
        result = read_private(dict(schema=1, text_included=True, windows=[window]), binding)
        self.assertEqual(result[-1].direction, 'unknown')

    def test_whitelist_mismatch_stops_before_model_initialization(self):
        settings = self.root / 'runtime.json'
        settings.write_text(json.dumps(dict(whitelist=[dict(name='Other', binding=str(self.binding_path))])), encoding='utf-8')
        with patch('group_friend.autonomous.DeepSeek') as model, redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--config', str(settings), '--once']), 1)
        model.assert_not_called()
