import dataclasses
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from group_friend.douyin import Binding, ObservationError
from group_friend.review import ReviewQueue
from group_friend.review_cli import main
from group_friend.review_snapshot import load_snapshot, save_snapshot
from test_douyin import window_fixture


class ReviewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / "snapshot.json"
        self.binding = Binding(7, 9, "start-1", "Test group", "Test group(8)", "session")
        self.report = dict(schema=1, text_included=True, windows=[window_fixture()])

    def save(self):
        return save_snapshot(self.snapshot, self.binding, lambda _: self.report)

    def test_round_trip_scopes_window_and_keeps_unknown_identity(self):
        self.report['windows'].append(dict(window_handle=99))
        messages = self.save()
        raw, loaded = load_snapshot(self.snapshot, self.binding)
        self.assertEqual(messages, loaded)
        self.assertEqual(len(json.loads(raw)['report']['windows']), 1)
        self.assertIsNone(loaded[0].is_self)
        self.assertIsNone(loaded[0].message_id)

    def test_same_window_new_session_or_process_cannot_reuse_snapshot(self):
        self.save()
        for change in (dict(session_id='new'), dict(process_id=8),
                       dict(process_started_at='start-2'), dict(group_heading='Test group(9)')):
            with self.subTest(change=change), self.assertRaisesRegex(ObservationError, 'snapshot_session_changed'):
                load_snapshot(self.snapshot, dataclasses.replace(self.binding, **change))

    def test_raw_historical_snapshot_cannot_generate_current_review(self):
        self.snapshot.write_text(json.dumps(self.report), encoding='utf-8')
        with self.assertRaisesRegex(ObservationError, 'review_requires_session_snapshot'):
            load_snapshot(self.snapshot, self.binding)

    def test_restart_or_incomplete_capture_writes_no_snapshot(self):
        for key, value in (('process_id', 8), ('process_started_at', 'new')):
            with self.subTest(key=key):
                self.report['windows'][0] = window_fixture()
                self.report['windows'][0][key] = value
                with self.assertRaises(ObservationError):
                    self.save()
                self.assertFalse(self.snapshot.exists())
        self.report['windows'][0] = window_fixture()
        self.report['windows'][0]['result']['Truncated'] = True
        with self.assertRaises(ObservationError):
            self.save()
        self.assertFalse(self.snapshot.exists())

    def test_existing_selection_file_is_never_overwritten(self):
        self.save()
        original = self.snapshot.read_bytes()
        with self.assertRaises(FileExistsError):
            self.save()
        self.assertEqual(self.snapshot.read_bytes(), original)

    def generate(self, *, rebind=False, index=0):
        self.save()
        binding_path = self.root / 'binding.json'
        self.binding.save(binding_path)
        persona = self.root / 'persona.txt'
        persona.write_text('AI friend', encoding='utf-8')
        config = self.root / 'config.json'
        config.write_text(json.dumps(dict(target_conversation='local', self_identity='bot', memory_enabled=False,
                                          persona_path=str(persona), pause_path=str(self.root / 'PAUSE'))), encoding='utf-8')
        queue = self.root / 'queue.sqlite3'
        def reply(*args, **kwargs):
            if rebind:
                dataclasses.replace(self.binding, session_id='new').save(binding_path)
            return '[AI] draft'
        output = io.StringIO()
        with patch('group_friend.review_cli.replay', side_effect=reply) as model, \
                patch('group_friend.review_cli.load_env'), redirect_stdout(output):
            code = main(['--queue', str(queue), '--binding', str(binding_path), '--config', str(config),
                         'generate', '--snapshot', str(self.snapshot), '--index', str(index), '--mock'])
        store = ReviewQueue(queue)
        self.addCleanup(store.close)
        return code, json.loads(output.getvalue()), store.list(), model

    def test_generate_only_creates_pending_draft(self):
        code, output, rows, _ = self.generate()
        self.assertEqual(code, 0)
        self.assertEqual(output['status'], 'pending_review')
        self.assertFalse(output['sent'])
        self.assertEqual([row['state'] for row in rows], ['pending'])

    def test_rebind_during_generation_never_enqueues(self):
        code, output, rows, _ = self.generate(rebind=True)
        self.assertEqual(code, 1)
        self.assertEqual(output['reason'], 'session_changed_recreate_reply')
        self.assertEqual(rows, [])

    def test_invalid_index_never_calls_model(self):
        code, output, rows, model = self.generate(index=-1)
        self.assertEqual(code, 1)
        self.assertEqual(output['reason'], 'message_index_out_of_range')
        self.assertEqual(rows, [])
        model.assert_not_called()
