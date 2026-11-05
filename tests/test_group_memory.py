import base64
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from group_friend.config import Config
from group_friend.group_memory_initializer import initialize_memories, parse_observations, group_root
from group_friend.group_memory_reader import GroupCapture
from group_friend.llm import DeepSeek, ModelError
from group_friend.markdown_memory import MarkdownMemory


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n8AAAAAASUVORK5CYII=')
MEMBERS = [dict(user_id='1', sec_uid='sec-1', name='Alice'), dict(user_id='2', sec_uid='sec-2', name='Bob')]


class GroupMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config('local', '陈', memory_path=str(Path(self.tmp.name)/'memory'))
        self.capture = GroupCapture('group-native-id', 'Group', MEMBERS, {'1': PNG, '2': PNG}, [PNG])

    def client(self, empty=False):
        client = Mock()
        def vision(instruction, images, text):
            info = json.loads(text)
            rows = []
            for i, label in enumerate(info['images'], 1):
                if label['kind'] == 'member':
                    rows.append(dict(token=label['token'], observations=[] if empty else [dict(image=i, text='头像背景为浅色', quote='')]))
            return json.dumps(dict(members=rows, unresolved=[]))
        client.vision.side_effect = vision
        return client

    def test_initializes_every_member_and_reuses_cached_vision(self):
        client = self.client()
        report, root = initialize_memories(self.capture, self.config, client)
        self.assertEqual(report['members_initialized'], 2)
        self.assertEqual(report['members_with_observations'], 2)
        memory = MarkdownMemory(replace(self.config, memory_path=str(root)))
        note = memory.folder(MEMBERS[0])/'profile.md'
        note.write_text(note.read_text() + '\n人工维护的称呼。\n')
        initialize_memories(self.capture, self.config, client)
        client.vision.assert_called_once()
        self.assertIn('人工维护的称呼', note.read_text())
        self.assertEqual(note.read_text().count('<!-- group-vision:start -->'), 1)
        self.assertIn('待核对', note.read_text())
        self.assertTrue((root/'INITIALIZATION.md').exists())

    def test_silent_users_receive_notes_without_fabricated_history(self):
        report, root = initialize_memories(self.capture, self.config, self.client(empty=True))
        self.assertEqual(report['members_initialized'], 2)
        self.assertEqual(report['members_with_observations'], 0)
        self.assertEqual(len(list(root.glob('*/profile.md'))), 2)

    def test_group_memory_is_separate_from_private_and_other_groups(self):
        private = MarkdownMemory(self.config)
        private.initialize(MEMBERS[0])
        note = private.folder(MEMBERS[0])/'profile.md'
        note.write_text('Private notes')
        _, group = initialize_memories(self.capture, self.config, self.client())
        self.assertEqual(note.read_text(), 'Private notes')
        self.assertNotEqual(group, group_root(self.config, 'another-group'))

    def test_unknown_model_identity_is_rejected(self):
        client = Mock()
        client.vision.return_value = '{"members":[{"token":"invented","observations":[]}]}'
        with self.assertRaises(ValueError):
            initialize_memories(self.capture, self.config, client)

    def test_duplicate_nickname_chat_evidence_is_not_attributed(self):
        members = [dict(m, name='same', token=f'm{i}') for i,m in enumerate(MEMBERS,1)]
        raw = json.dumps(dict(members=[dict(token='m1', observations=[dict(image=1,speaker='same',text='something',quote='')])]))
        result, unresolved = parse_observations(raw, members, members, [dict(kind='chat')])
        self.assertEqual(result['m1'], [])
        self.assertTrue(unresolved)

    def test_member_card_cannot_be_assigned_to_a_different_member(self):
        members = [dict(m, token=f'm{i}') for i,m in enumerate(MEMBERS,1)]
        raw = json.dumps(dict(members=[dict(token='m1', observations=[dict(image=1,text='wrong person',quote='')])]))
        result, unresolved = parse_observations(raw, members, members, [dict(kind='member',token='m2')])
        self.assertEqual(result['m1'], [])
        self.assertTrue(unresolved)

    def test_invalid_source_number_is_rejected(self):
        members = [dict(MEMBERS[0],token='m1')]
        raw = json.dumps(dict(members=[dict(token='m1', observations=[dict(image=999,text='no source')])]))
        with self.assertRaises(ValueError):
            parse_observations(raw, members, members, [dict(kind='member',token='m1')])


class NativeVisionTests(unittest.TestCase):
    def client(self):
        with patch('group_friend.llm.api_settings', return_value=('test-key','https://api.deepseek.com','deepseek-pro')):
            client = DeepSeek(Config('local','陈',api_retries=0))
        response = Mock()
        response.read.return_value = b'{"choices":[{"message":{"content":"{\\"members\\":[]}"}}]}'
        client.opener = Mock()
        client.opener.open.return_value.__enter__ = Mock(return_value=response)
        client.opener.open.return_value.__exit__ = Mock(return_value=False)
        return client

    def test_sends_real_image_content_parts_to_official_flash(self):
        client = self.client()
        self.assertEqual(json.loads(client.vision('instruction',[PNG],'labels')), {'members':[]})
        request = client.opener.open.call_args.args[0]
        data = json.loads(request.data)
        self.assertEqual(data['model'], 'deepseek-flash')
        self.assertEqual(request.full_url, 'https://api.deepseek.com/chat/completions')
        image = data['messages'][1]['content'][1]
        self.assertEqual(image['type'], 'image_url')
        self.assertTrue(image['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(base64.b64decode(image['image_url']['url'].split(',',1)[1]), PNG)

    def test_invalid_or_missing_images_fail_before_network(self):
        client = self.client()
        for images in ([], [b'not an image']):
            with self.assertRaises(ModelError):
                client.vision('instruction',images)
        client.opener.open.assert_not_called()
