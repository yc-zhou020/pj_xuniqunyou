import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from group_friend import live_test as test_live_reply
from group_friend.config import Config


class LiveReplyTests(unittest.TestCase):
    def test_prepare_uses_relocated_templates_without_overwriting_local_files(self):
        root = Path(__file__).resolve().parents[1]
        browser_template = (root / 'config/examples/browser.json').read_bytes()
        reply_template = (root / 'config/examples/reply.json').read_bytes()
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), \
             contextlib.redirect_stdout(io.StringIO()):
            examples = Path('config/examples')
            examples.mkdir(parents=True)
            (examples / 'browser.json').write_bytes(browser_template)
            (examples / 'reply.json').write_bytes(reply_template)
            Path('.env.example').write_text('DEEPSEEK_API_KEY=placeholder\n')
            test_live_reply.prepare('browser.local.json')
            self.assertEqual(Path('browser.local.json').read_bytes(), browser_template)
            self.assertEqual(Path('config.local.json').read_bytes(), reply_template)
            Path('config.local.json').write_text('{"user_setting": true}')
            Path('.env').write_text('user-provided-value')
            test_live_reply.prepare('browser.local.json')
            self.assertEqual(Path('config.local.json').read_text(), '{"user_setting": true}')
            self.assertEqual(Path('.env').read_text(), 'user-provided-value')

    def test_test_requires_confirmed_send_and_does_not_replay_latest_history(self):
        config = Config('contact', 'bot', dry_run=True, memory_enabled=False)
        settings = dict(log_path='unused')
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                async def runtime(settings, actual_config, args, logger):
                    self.assertFalse(actual_config.dry_run)
                    self.assertFalse(args.reply_latest)
                    self.assertEqual(args.stage, 'all')
                    if confirm:
                        logger.info('{"status":"visible_sent"}')
                    return 0
                with patch.object(test_live_reply, 'InstanceLock', side_effect=lambda path: contextlib.nullcontext()), \
                     patch.object(test_live_reply, 'prerequisites', return_value=(settings, config)), \
                     patch.object(test_live_reply, 'make_logger', return_value=Mock()), \
                     patch.object(test_live_reply, 'run', side_effect=runtime), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(test_live_reply.main([]), 0 if confirm else 2)

    def test_missing_configuration_does_not_start_browser_or_model(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(test_live_reply, 'run') as runtime, \
             patch.object(test_live_reply, 'InstanceLock', side_effect=lambda path: contextlib.nullcontext()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(test_live_reply.main(['--config', str(Path(tmp)/'missing.json')]), 1)
            runtime.assert_not_called()

    def test_unknown_send_prevents_passing_even_with_previous_success(self):
        logger = test_live_reply.TestLogger(Mock())
        logger.info('{"status":"visible_sent"}')
        logger.info('{"status":"unknown"}')
        self.assertTrue(logger.blocked)
        self.assertEqual(logger.confirmed, 1)
