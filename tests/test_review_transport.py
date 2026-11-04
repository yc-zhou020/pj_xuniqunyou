import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from group_friend.douyin import Binding
from group_friend.review_transport import WindowsReviewedTransport


class TransportDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding(7, 9, 'start', 'App', 'Contact', 'session')
        self.transport = WindowsReviewedTransport()

    def execute(self, report, method='send_private', returncode=0):
        calls = []
        def run(command, **kwargs):
            path = Path(command[command.index('-RequestPath') + 1])
            request = json.loads(path.read_text(encoding='utf-8'))
            calls.append((command, request, path))
            return SimpleNamespace(returncode=returncode, stdout=json.dumps(report))
        with patch('group_friend.review_transport.os', SimpleNamespace(name='nt')), \
                patch('group_friend.review_transport.subprocess.CREATE_NO_WINDOW', 0, create=True), \
                patch('group_friend.review_transport.subprocess.run', side_effect=run):
            outcome = getattr(self.transport, method)(self.binding, 'source', '[AI] draft', 'PAUSE',
                                                      {'texts': ['source', 'source'], 'index': 1})
        self.assertFalse(calls[0][2].exists())
        return outcome, calls[0]

    def test_inspect_only_uses_read_only_flag_and_preserves_exact_occurrence(self):
        result, (command, request, _) = self.execute(dict(outcome='inspected', stage='read_only_inspection',
            draft_matches=True, composer_focused=False, send_button_available=True), method='inspect_private')
        self.assertEqual(result, 'inspected')
        self.assertIn('-InspectOnly', command)
        self.assertEqual(request['source_anchor']['index'], 1)
        self.assertTrue(request['require_incoming'])
        self.assertEqual(self.transport.last_diagnostics['draft_matches'], True)

    def test_unknown_returns_fixed_stage_and_reason_without_private_error_text(self):
        result, _ = self.execute(dict(outcome='unknown', stage='invoking_send', reason='composer_focus_changed',
                                     exception='PRIVATE API BODY', source='PRIVATE TEXT'))
        self.assertEqual(result, 'unknown')
        self.assertEqual(self.transport.last_diagnostics, dict(stage='invoking_send', reason='composer_focus_changed'))

    def test_arbitrary_diagnostic_text_is_not_logged(self):
        self.execute(dict(outcome='unknown', stage='PRIVATE TEXT', reason='PRIVATE API BODY'))
        self.assertEqual(self.transport.last_diagnostics, {})

    def test_subprocess_failure_stays_unknown(self):
        outcome, _ = self.execute({}, returncode=1)
        self.assertEqual(outcome, 'unknown')
        self.assertEqual(self.transport.last_diagnostics['reason'], 'native_process_failed')
