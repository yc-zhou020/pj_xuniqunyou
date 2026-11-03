import tempfile
import unittest
from pathlib import Path

from group_friend.config import Config
from group_friend.douyin import (Binding, ObservationError, ObservedText,
                                 SnapshotTracker, WindowsObserver, parse_window)
from group_friend.douyin_cli import replay


def node(path, role=20, name=None, action=None):
    return dict(Path=path, Role=role, Name=name, DefaultAction=action)


def window_fixture():
    base = "root/chat/0/0"
    nodes = [node("root", 16, "Test group"), node("root/title", 41, "Test group(8)"),
             node("root/chat/2/1/0", action="激活"), node(base)]
    # Reverse DOM order: oldest media row carries the sender label; its newer
    # text continuation has no repeated sender label. Card author != sender.
    nodes += [node(base + "/2"), node(base + "/2/1/0", 41, "Alice"),
              node(base + "/2/2/image", 40), node(base + "/2/2/author", 41, "Video author"),
              node(base + "/1", 41, "前天"), node(base + "/0"),
              node(base + "/0/0/text1", 41, "/ai "), node(base + "/0/0/text2", 41, "hello")]
    return dict(process_id=7, window_handle=9, process_started_at="start-1",
                result=dict(Nodes=nodes, Errors=[], Truncated=False))


def observed(text, sender="Alice"):
    return ObservedText(text, sender, False, "fixture")


class ParserTests(unittest.TestCase):
    def parse(self, window):
        return parse_window(window, expected_title="Test group", expected_heading="Test group(8)")

    def test_media_author_is_not_sender_and_split_text_is_joined(self):
        messages = self.parse(window_fixture())
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual((message.text, message.sender_name), ("/ai hello", "Alice"))
        self.assertTrue(message.sender_inherited)
        self.assertIsNone(message.sender_id)
        self.assertIsNone(message.message_id)
        self.assertIsNone(message.is_self)
        self.assertIsNone(message.timestamp)

    def test_changed_target_and_missing_composer_stop(self):
        for change in ("title", "heading", "composer"):
            with self.subTest(change=change):
                window = window_fixture()
                nodes = window["result"]["Nodes"]
                if change == "title":
                    nodes[0]["Name"] = "Different group"
                elif change == "heading":
                    nodes[1]["Name"] = "Different group(8)"
                else:
                    nodes[2]["DefaultAction"] = None
                with self.assertRaises(ObservationError):
                    self.parse(window)

    def test_avatar_and_admin_badge_are_not_message_content(self):
        for admin in (False, True):
            with self.subTest(admin=admin):
                window = window_fixture()
                base = 'root/chat/0/0/0'
                nodes = window['result']['Nodes']
                nodes[:] = [n for n in nodes if not n['Path'].startswith('root/chat/0/0/')]
                nodes.extend([node(base), node(base + '/0/avatar', 40),
                              node(base + '/1/0', 41, 'Bob')])
                body = base + ('/3' if admin else '/2')
                if admin:
                    nodes.append(node(base + '/2', 41, '群主'))
                nodes.extend([node(body), node(body + '/text', 41, 'actual text')])
                messages = self.parse(window)
                self.assertEqual([(m.sender_name, m.text) for m in messages], [('Bob', 'actual text')])
                self.assertFalse(messages[0].sender_inherited)

    def test_unknown_system_event_breaks_name_inference(self):
        window = window_fixture()
        next(n for n in window["result"]["Nodes"] if n["Name"] == "前天")["Name"] = "Message removed"
        self.assertIsNone(self.parse(window)[0].sender_name)

    def test_restart_and_incomplete_snapshot_stop_observer(self):
        binding = Binding(7, 9, "start-1", "Test group", "Test group(8)", "local-session")
        for change in ("restart", "truncated", "errors", "duplicate_window"):
            with self.subTest(change=change):
                window = window_fixture()
                report = dict(schema=1, text_included=True, windows=[window])
                if change == "restart":
                    window["process_started_at"] = "start-2"
                elif change == "truncated":
                    window["result"]["Truncated"] = True
                elif change == "errors":
                    window["result"]["Errors"] = ["unavailable"]
                else:
                    report["windows"].append(window)
                observer = WindowsObserver(binding, lambda _: report)
                with self.assertRaises(ObservationError):
                    observer.read()


class TrackerTests(unittest.TestCase):
    def test_baseline_restart_and_unchanged_never_emit_history(self):
        messages = [observed("a"), observed("b")]
        tracker = SnapshotTracker()
        self.assertEqual(tracker.update(messages), [])
        self.assertEqual(tracker.update(messages), [])
        self.assertEqual(SnapshotTracker().update(messages), [])

    def test_unique_append_with_old_rows_dropped(self):
        tracker = SnapshotTracker("local-session")
        tracker.update([observed("a"), observed("b")])
        events = tracker.update([observed("b"), observed("c")])
        self.assertEqual(events[0][0], "local-session:1")
        self.assertEqual(events[0][1].text, "c")
        self.assertIsNone(events[0][1].message_id)
        self.assertEqual(tracker.update([observed("b"), observed("c")]), [])

    def test_history_scroll_edit_repetition_and_empty_baseline_stop(self):
        cases = [(["a", "b"], ["older", "a", "b"]),
                 (["a", "b"], ["a", "edited"]),
                 (["a", "b"], ["a", "b", "b"]),
                 (["a"], ["a", "new", "new"]),
                 (["a", "a"], ["a", "a", "b"]),
                 ([], ["a"])]
        for old, new in cases:
            with self.subTest(old=old, new=new):
                tracker = SnapshotTracker()
                tracker.update([observed(x) for x in old])
                with self.assertRaises(ObservationError):
                    tracker.update([observed(x) for x in new])
                self.assertTrue(tracker.stopped)
                with self.assertRaises(ObservationError):
                    tracker.update([observed(x) for x in new])


class ReplayTests(unittest.TestCase):
    def test_pause_during_generation_suppresses_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "persona.txt"
            persona.write_text("AI friend", encoding="utf-8")
            pause = Path(tmp) / "PAUSE"
            config = Config("local", "local", persona_path=str(persona), pause_path=str(pause))
            class Model:
                def reply(self, persona, messages):
                    pause.touch()
                    return "reply"
            with self.assertRaisesRegex(ObservationError, "paused_after_generation"):
                replay([observed("hello")], 0, Model(), config)

    def test_manual_replay_uses_only_selected_text_and_limits_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            persona = Path(tmp) / "persona.txt"
            persona.write_text("AI friend", encoding="utf-8")
            config = Config("local", "local", persona_path=str(persona),
                            pause_path=str(Path(tmp) / "PAUSE"), max_reply_chars=12)
            test = self
            class Model:
                def reply(self, persona, messages):
                    test.assertEqual(messages, [{"role": "user", "content": "selected"}])
                    return "A long reply exceeding the configured limit"
            reply = replay([observed("private context"), observed("selected")], 1, Model(), config)
            self.assertTrue(reply.startswith("[AI] "))
            self.assertEqual(len(reply), 12)


if __name__ == "__main__":
    unittest.main()
