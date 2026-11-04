from dataclasses import replace
import unittest

from group_friend.douyin import Binding, ObservationError
from group_friend.window_manager import WindowManager, creation_ticks


class FakeWindows:
    def __init__(self, binding):
        self.who = (binding.process_id, creation_ticks(binding.process_started_at), binding.window_title)
        self.is_minimized = True
        self.is_visible = True
        self.is_cloaked = False
        self.current = 999
        self.actions = []
        self.restore_works = True
        self.restore_activates = False

    def identity(self, hwnd):
        return self.who
    def minimized(self, hwnd):
        return self.is_minimized
    def visible(self, hwnd):
        return self.is_visible
    def cloaked(self, hwnd):
        return self.is_cloaked
    def foreground(self):
        return self.current
    def restore(self, hwnd):
        self.actions.append(('restore', hwnd))
        if self.restore_works:
            self.is_minimized = False
            self.is_visible = True
        if self.restore_activates:
            self.current = hwnd
    def lower(self, hwnd):
        self.actions.append(('lower', hwnd))
        return True
    def activate(self, hwnd):
        self.actions.append(('activate', hwnd))
        self.current = hwnd
        return True


class WindowManagerTests(unittest.TestCase):
    def setUp(self):
        self.binding = Binding(7, 9, '2026-11-04T00:38:04.7823504Z', 'App', 'Contact', 'session')
        self.api = FakeWindows(self.binding)
        self.manager = WindowManager(self.api, sleep=lambda _: None)

    def test_filetime_preserves_seventh_fractional_digit(self):
        self.assertEqual(creation_ticks('1970-01-01T00:00:00Z'), 116444736000000000)
        self.assertEqual(creation_ticks('2026-11-04T00:38:04.7823504Z') -
                         creation_ticks('2026-11-04T00:38:04.782350Z'), 4)

    def test_invalid_start_time_never_moves_window(self):
        with self.assertRaises(ObservationError):
            self.manager.prepare(replace(self.binding, process_started_at='invalid'))
        self.assertEqual(self.api.actions, [])

    def test_restore_minimized_window_without_activating_it(self):
        self.assertTrue(self.manager.prepare(self.binding))
        self.assertEqual(self.api.current, 999)
        self.assertEqual(self.api.actions, [('restore', 9), ('lower', 9)])

    def test_unexpected_activation_returns_to_previous_window(self):
        self.api.restore_activates = True
        self.manager.prepare(self.binding)
        self.assertEqual(self.api.current, 999)
        self.assertIn(('activate', 999), self.api.actions)

    def test_visible_window_is_left_untouched(self):
        self.api.is_minimized = False
        self.assertFalse(self.manager.prepare(self.binding))
        self.assertEqual(self.api.actions, [])

    def test_reused_handle_or_restarted_process_is_never_restored(self):
        self.api.who = (7, creation_ticks(self.binding.process_started_at) + 1, 'App')
        with self.assertRaisesRegex(ObservationError, 'client_restarted'):
            self.manager.prepare(self.binding)
        self.assertEqual(self.api.actions, [])

    def test_other_desktop_is_not_silently_switched(self):
        self.api.is_cloaked = True
        with self.assertRaisesRegex(ObservationError, 'window_on_other_desktop'):
            self.manager.prepare(self.binding)
        self.assertEqual(self.api.actions, [])

    def test_failed_restore_is_bounded_and_reported(self):
        self.api.restore_works = False
        with self.assertRaisesRegex(ObservationError, 'window_restore_failed'):
            self.manager.prepare(self.binding)

    def test_return_focus_after_send(self):
        self.api.current = 9
        self.assertTrue(self.manager.return_focus(self.binding, 999))
        self.assertEqual(self.api.current, 999)

    def test_user_switching_apps_during_send_keeps_their_new_focus(self):
        self.api.current = 888
        self.assertFalse(self.manager.return_focus(self.binding, 999))
        self.assertEqual(self.api.actions, [])
