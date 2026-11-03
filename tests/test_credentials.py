import getpass
import io
import unittest
import warnings
from contextlib import redirect_stdout
from unittest.mock import patch

from group_friend.credentials import AccountCredentials, CredentialError, account_hint
from group_friend.credentials_cli import enroll, main


class FakeStore:
    def __init__(self, configured=False):
        self.configured = configured
        self.writes = []

    def status(self):
        return {'configured': self.configured, 'account_hint': 'us***er'}

    def save(self, account, password, *, replace=False):
        self.writes.append((account, password, replace))
        self.configured = True


class CredentialTests(unittest.TestCase):
    def test_secret_container_repr_never_contains_account_or_password(self):
        secret = AccountCredentials('private-account', 'private-password')
        self.assertNotIn('private-account', repr(secret))
        self.assertNotIn('private-password', repr(secret))

    def test_enrollment_has_no_stdout_or_plaintext_files(self):
        store = FakeStore()
        values = iter(['private-account', 'private-password', 'private-password'])
        output = io.StringIO()
        with redirect_stdout(output):
            enroll(store, prompt=lambda _: next(values))
        self.assertEqual(store.writes, [('private-account', 'private-password', False)])
        self.assertEqual(output.getvalue(), '')

    def test_mismatch_and_existing_entry_never_write(self):
        store = FakeStore()
        values = iter(['account', 'password1', 'password2'])
        with self.assertRaisesRegex(CredentialError, 'confirmation_mismatch'):
            enroll(store, prompt=lambda _: next(values))
        self.assertEqual(store.writes, [])
        store.configured = True
        with self.assertRaisesRegex(CredentialError, 'already_configured'):
            enroll(store, prompt=lambda _: self.fail('Should not prompt'))

    def test_getpass_echo_fallback_aborts_before_save(self):
        store = FakeStore()
        def unsupported(_):
            warnings.warn('Cannot hide input', getpass.GetPassWarning)
            raise AssertionError('Fallback should not reach input')
        with self.assertRaises(getpass.GetPassWarning):
            enroll(store, prompt=unsupported)
        self.assertEqual(store.writes, [])

    def test_noninteractive_cli_and_password_argument_are_rejected(self):
        store = FakeStore()
        with patch('group_friend.credentials_cli.WindowsCredentialStore', return_value=store), \
             patch('sys.stdin.isatty', return_value=False), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['set']), 1)
            self.assertIn('interactive_local_terminal_required', output.getvalue())
        self.assertEqual(store.writes, [])
        with patch('sys.stderr', new=io.StringIO()) as errors, self.assertRaises(SystemExit):
            main(['set', '--password', 'never-accepted'])
        self.assertNotIn('never-accepted', errors.getvalue())

    def test_status_and_hints_are_masked(self):
        self.assertEqual(account_hint('1234'), '***')
        self.assertEqual(account_hint('13812345678'), '13***78')
        with patch('group_friend.credentials_cli.WindowsCredentialStore', return_value=FakeStore(True)), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['status']), 0)
            self.assertIn('us***er', output.getvalue())


if __name__ == '__main__':
    unittest.main()
