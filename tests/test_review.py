import dataclasses
import tempfile
import unittest
from pathlib import Path

from group_friend.douyin import Binding
from group_friend.config import Config
from group_friend.review import ReviewError, ReviewQueue, send_reviewed


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'queue.sqlite3'
        self.pause = Path(self.tmp.name) / 'PAUSE'
        self.now = 1000
        self.queue = ReviewQueue(self.path, clock=lambda: self.now)
        self.addCleanup(lambda: self.queue.close())
        self.binding = Binding(1, 2, 'start', 'group', 'group(8)', 'session')
        self.item = self.queue.enqueue(self.binding, 'source', 'reply')

    def approve(self, item=None, revision=1):
        self.queue.approve(item or self.item, revision, target_heading='group(8)', binding=self.binding)

    def test_pending_cannot_send_approval_does_not_claim(self):
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, self.binding)
        self.approve()
        self.assertEqual(self.queue.get(self.item)['state'], 'approved')

    def test_edit_revokes_approval_and_stale_revision_cannot_approve(self):
        self.approve()
        self.queue.edit(self.item, 1, 'edited')
        item = self.queue.get(self.item)
        self.assertEqual((item['state'], item['revision'], item['reply']), ('pending', 2, '[AI] edited'))
        self.assertIsNone(item['approved_hash'])
        with self.assertRaises(ReviewError):
            self.approve(revision=1)
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, self.binding)

    def test_target_session_expiry_and_text_tampering_stop(self):
        self.approve()
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, dataclasses.replace(self.binding, session_id='new'))
        self.now += 601
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, self.binding)
        self.now = 1000
        with self.queue.db:
            self.queue.db.execute("UPDATE replies SET reply='changed' WHERE id=?", (self.item,))
        with self.assertRaisesRegex(ReviewError, 'approved_text_changed'):
            self.queue.claim_send(self.item, 1, self.binding)

    def test_discard_clears_private_content_and_cannot_send(self):
        self.approve()
        self.queue.discard(self.item, 1)
        item = self.queue.get(self.item)
        self.assertEqual((item['state'], item['source'], item['reply']), ('discarded', '', ''))
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, self.binding)

    def test_two_connections_only_one_can_claim_and_restart_blocks(self):
        self.approve()
        other = ReviewQueue(self.path, clock=lambda: self.now)
        try:
            self.queue.claim_send(self.item, 1, self.binding)
            with self.assertRaisesRegex(ReviewError, 'unresolved_send'):
                other.claim_send(self.item, 1, self.binding)
            second = other.enqueue(self.binding, 'second', 'second reply')
            other.approve(second, 1, target_heading='group(8)', binding=self.binding)
            with self.assertRaisesRegex(ReviewError, 'unresolved_send'):
                other.claim_send(second, 1, self.binding)
        finally:
            other.close()

    def test_unknown_blocks_until_manual_resolution_no_retry(self):
        self.approve()
        self.queue.claim_send(self.item, 1, self.binding)
        self.queue.finish_send(self.item, 'unknown')
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, self.binding)
        self.queue.resolve(self.item, 'not_sent', 'Manually verified no outgoing message and cleared draft')
        with self.assertRaises(ReviewError):
            self.queue.claim_send(self.item, 1, self.binding)

    def test_pause_during_read_only_preflight_prevents_input(self):
        self.approve()
        pause = self.pause
        class Transport:
            def preflight(self, binding, source):
                pause.touch()
            def send(self, *args):
                raise AssertionError('Must not send')
        with self.assertRaisesRegex(ReviewError, 'paused'):
            send_reviewed(self.queue, self.item, 1, self.binding, Transport(), self.pause)
        self.assertEqual(self.queue.get(self.item)['state'], 'approved')

    def test_send_error_becomes_unknown_and_success_is_terminal(self):
        self.approve()
        class Transport:
            def preflight(self, *args):
                pass
            def send(self, *args):
                raise TimeoutError()
        with self.assertRaises(TimeoutError):
            send_reviewed(self.queue, self.item, 1, self.binding, Transport(), self.pause)
        self.assertEqual(self.queue.get(self.item)['state'], 'unknown')
        self.queue.resolve(self.item, 'not_sent', 'Checked manually')
        second = self.queue.enqueue(self.binding, 'next', 'reply')
        self.approve(second)
        class Success(Transport):
            def send(self, *args):
                return 'visible_sent'
        self.assertEqual(send_reviewed(self.queue, second, 1, self.binding, Success(), self.pause), 'visible_sent')
        with self.assertRaises(ReviewError):
            self.queue.claim_send(second, 1, self.binding)

    def test_purge_preserves_pending_approval_and_uncertain_receipts(self):
        self.approve()
        self.queue.claim_send(self.item, 1, self.binding)
        discarded = self.queue.enqueue(self.binding, 'private source', 'private reply')
        self.queue.discard(discarded, 1)
        self.assertEqual(self.queue.purge(0), 1)
        self.assertEqual(self.queue.get(self.item)['state'], 'sending')

    def test_rate_limit_survives_rebind_and_explicit_reapproval_renews_expiry(self):
        self.approve()
        self.now += 601
        self.approve()
        self.queue.claim_send(self.item, 1, self.binding)
        self.queue.finish_send(self.item, 'visible_sent')
        second = self.queue.enqueue(self.binding, 'second', 'reply')
        self.approve(second)
        with self.assertRaisesRegex(ReviewError, 'rate_limited'):
            self.queue.claim_send(second, 1, self.binding)
        self.now += 31
        with self.assertRaisesRegex(ReviewError, 'rate_limited'):
            self.queue.claim_send(second, 1, self.binding, hourly_limit=1)

    def test_wrong_target_never_approves_and_newline_never_enqueues(self):
        with self.assertRaisesRegex(ReviewError, 'target_or_session_changed'):
            self.queue.approve(self.item, 1, target_heading='other group', binding=self.binding)
        with self.assertRaises(ReviewError):
            self.queue.enqueue(self.binding, 'source', 'one\ntwo')

    def test_active_send_cannot_be_resolved_to_allow_concurrent_send(self):
        self.approve()
        self.queue.claim_send(self.item, 1, self.binding)
        with self.assertRaisesRegex(ReviewError, 'send_still_active'):
            self.queue.resolve(self.item, 'not_sent', 'Premature manual check')
        self.now += 91
        self.queue.resolve(self.item, 'not_sent', 'Checked after interrupted process exited')

    def test_default_dry_run_never_reaches_desktop(self):
        self.approve()
        class Transport:
            def preflight(self, *args):
                raise AssertionError('Dry-run must stop before desktop access')
        with self.assertRaisesRegex(ReviewError, 'dry_run_enabled'):
            send_reviewed(self.queue, self.item, 1, self.binding, Transport(), self.pause, Config('local','bot'))
        self.assertEqual(self.queue.get(self.item)['state'], 'approved')


if __name__ == '__main__':
    unittest.main()
